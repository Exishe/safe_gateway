//! The sidecar's life: start it, wait until `/health` answers, restart it with backoff
//! when it dies, give up after a few tries, stop it when asked (ADR 0027).
//!
//! Everything that touches the clock, a process or the network sits behind `Backend`,
//! so the rules below are tested with a fake backend and virtual time.

use std::time::Duration;

use crate::health::Health;

/// How patient the supervisor is.
#[derive(Debug, Clone)]
pub struct Policy {
    /// Consecutive failed runs before giving up.
    pub max_attempts: u32,
    /// How long a fresh process may take to answer `/health` (it loads models).
    pub startup_timeout: Duration,
    pub poll_interval: Duration,
    pub backoff_base: Duration,
    pub backoff_max: Duration,
    /// A run that stayed healthy this long counts as good: the failure count resets.
    pub stable_after: Duration,
    /// Failed health checks in a row, once ready, before the process is restarted.
    pub failed_probes_to_restart: u32,
}

impl Default for Policy {
    fn default() -> Self {
        Self {
            max_attempts: 5,
            startup_timeout: Duration::from_secs(180),
            poll_interval: Duration::from_millis(500),
            backoff_base: Duration::from_secs(1),
            backoff_max: Duration::from_secs(30),
            stable_after: Duration::from_secs(60),
            failed_probes_to_restart: 6,
        }
    }
}

/// What the shell shows: the window and the tray follow this.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum State {
    Starting {
        attempt: u32,
    },
    Ready(Health),
    Restarting {
        attempt: u32,
        delay: Duration,
        reason: String,
    },
    Failed {
        reason: String,
    },
    Stopped,
}

pub trait Child {
    /// `Some(reason)` once the process has exited.
    fn exited(&mut self) -> Option<String>;
    /// Ask it to stop, wait a little, then force it. Safe to call twice.
    fn terminate(&mut self);
}

pub trait Backend {
    type Child: Child;
    fn spawn(&mut self) -> std::io::Result<Self::Child>;
    fn probe(&mut self) -> Option<Health>;
    /// Monotonic time since some fixed start.
    fn now(&self) -> Duration;
    fn sleep(&mut self, duration: Duration);
    fn stop_requested(&self) -> bool;
    fn publish(&mut self, state: &State);
}

/// Delay before restart number `failures` (1 for the first): base, 2x, 4x ... capped.
pub fn backoff_delay(policy: &Policy, failures: u32) -> Duration {
    let doublings = failures.saturating_sub(1).min(16); // 2^16 x base is far past any cap
    policy
        .backoff_base
        .saturating_mul(1 << doublings)
        .min(policy.backoff_max)
}

/// How one process run ended.
enum Outcome {
    Stopped,
    Died { reason: String, stable: bool },
}

/// Watch one process from spawn to its end.
fn watch<B: Backend>(backend: &mut B, policy: &Policy, child: &mut B::Child) -> Outcome {
    let started = backend.now();
    let mut ready_since: Option<Duration> = None;
    let mut last: Option<Health> = None;
    let mut missed = 0_u32;
    loop {
        if backend.stop_requested() {
            child.terminate();
            return Outcome::Stopped;
        }
        let stable = |now: Duration, since: Option<Duration>| {
            since.is_some_and(|at| now.saturating_sub(at) >= policy.stable_after)
        };
        if let Some(reason) = child.exited() {
            let stable = stable(backend.now(), ready_since);
            return Outcome::Died { reason, stable };
        }
        match backend.probe() {
            Some(health) => {
                missed = 0;
                ready_since.get_or_insert_with(|| backend.now());
                if last.as_ref() != Some(&health) {
                    backend.publish(&State::Ready(health.clone()));
                    last = Some(health);
                }
            }
            None if ready_since.is_some() => {
                missed += 1;
                if missed >= policy.failed_probes_to_restart {
                    child.terminate();
                    let stable = stable(backend.now(), ready_since);
                    let reason = "the health check stopped answering".to_owned();
                    return Outcome::Died { reason, stable };
                }
            }
            None if backend.now().saturating_sub(started) >= policy.startup_timeout => {
                child.terminate();
                let reason = format!(
                    "the gateway did not answer on /health within {} s",
                    policy.startup_timeout.as_secs()
                );
                return Outcome::Died {
                    reason,
                    stable: false,
                };
            }
            None => {}
        }
        backend.sleep(policy.poll_interval);
    }
}

/// Sleep `delay` in poll-sized slices so a quit is not held up by a long backoff.
/// Returns false if a stop was requested meanwhile.
fn pause<B: Backend>(backend: &mut B, policy: &Policy, delay: Duration) -> bool {
    let mut left = delay;
    while !left.is_zero() {
        if backend.stop_requested() {
            return false;
        }
        let slice = left.min(policy.poll_interval);
        backend.sleep(slice);
        left -= slice;
    }
    !backend.stop_requested()
}

/// Run until stopped or given up. Returns the final state.
pub fn run<B: Backend>(backend: &mut B, policy: &Policy) -> State {
    let mut failures = 0_u32;
    let finish = |backend: &mut B, state: State| {
        backend.publish(&state);
        state
    };
    loop {
        if backend.stop_requested() {
            return finish(backend, State::Stopped);
        }
        backend.publish(&State::Starting {
            attempt: failures + 1,
        });
        let outcome = match backend.spawn() {
            Ok(mut child) => watch(backend, policy, &mut child),
            Err(error) => Outcome::Died {
                reason: format!("cannot start the gateway: {error}"),
                stable: false,
            },
        };
        let (reason, stable) = match outcome {
            Outcome::Stopped => return finish(backend, State::Stopped),
            Outcome::Died { reason, stable } => (reason, stable),
        };
        // A crash after a long healthy run is a fresh problem, not one more failed try.
        let delay = if stable {
            failures = 0;
            backoff_delay(policy, 1)
        } else {
            failures += 1;
            if failures >= policy.max_attempts {
                return finish(backend, State::Failed { reason });
            }
            backoff_delay(policy, failures)
        };
        backend.publish(&State::Restarting {
            attempt: failures + 1,
            delay,
            reason,
        });
        if !pause(backend, policy, delay) {
            return finish(backend, State::Stopped);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::{Cell, RefCell};
    use std::collections::VecDeque;
    use std::rc::Rc;

    fn secs(n: u64) -> Duration {
        Duration::from_secs(n)
    }

    fn health() -> Health {
        Health {
            status: "ok".into(),
            version: "0.6.0".into(),
            provider: "ollama".into(),
            local: true,
            audit: true,
        }
    }

    /// What one fake process does, in time since its own start.
    #[derive(Clone, Copy)]
    struct Script {
        ready_at: Option<Duration>,
        dies_at: Option<Duration>,
    }

    struct FakeChild {
        clock: Rc<Cell<Duration>>,
        started: Duration,
        dies_at: Option<Duration>,
        killed: Rc<RefCell<u32>>,
        dead: bool,
    }

    impl Child for FakeChild {
        fn exited(&mut self) -> Option<String> {
            if self.dead {
                return Some("terminated".into());
            }
            match self.dies_at {
                Some(at) if self.clock.get() >= self.started + at => {
                    self.dead = true;
                    Some("exited with code 1".into())
                }
                _ => None,
            }
        }
        fn terminate(&mut self) {
            if !self.dead {
                self.dead = true;
                *self.killed.borrow_mut() += 1;
            }
        }
    }

    struct Fake {
        clock: Rc<Cell<Duration>>,
        scripts: VecDeque<Result<Script, &'static str>>,
        current: Option<(Script, Duration)>,
        stop_at: Option<Duration>,
        spawned: u32,
        killed: Rc<RefCell<u32>>,
        states: Vec<State>,
        sleeps: Vec<Duration>,
    }

    impl Fake {
        fn new(scripts: Vec<Result<Script, &'static str>>) -> Self {
            Self {
                clock: Rc::new(Cell::new(Duration::ZERO)),
                scripts: scripts.into(),
                current: None,
                stop_at: None,
                spawned: 0,
                killed: Rc::new(RefCell::new(0)),
                states: Vec::new(),
                sleeps: Vec::new(),
            }
        }
    }

    impl Backend for Fake {
        type Child = FakeChild;
        fn spawn(&mut self) -> std::io::Result<FakeChild> {
            self.spawned += 1;
            // After the scripts run out, every further process dies at once.
            let next = self.scripts.pop_front().unwrap_or(Ok(Script {
                ready_at: None,
                dies_at: Some(Duration::ZERO),
            }));
            match next {
                Err(message) => Err(std::io::Error::other(message)),
                Ok(script) => {
                    let now = self.clock.get();
                    self.current = Some((script, now));
                    Ok(FakeChild {
                        clock: self.clock.clone(),
                        started: now,
                        dies_at: script.dies_at,
                        killed: self.killed.clone(),
                        dead: false,
                    })
                }
            }
        }
        fn probe(&mut self) -> Option<Health> {
            let (script, started) = self.current?;
            let ready_at = script.ready_at?;
            (self.clock.get() >= started + ready_at).then(health)
        }
        fn now(&self) -> Duration {
            self.clock.get()
        }
        fn sleep(&mut self, duration: Duration) {
            self.sleeps.push(duration);
            self.clock.set(self.clock.get() + duration);
        }
        fn stop_requested(&self) -> bool {
            self.stop_at.is_some_and(|at| self.clock.get() >= at)
        }
        fn publish(&mut self, state: &State) {
            self.states.push(state.clone());
        }
    }

    fn healthy_forever(ready_at: Duration) -> Result<Script, &'static str> {
        Ok(Script {
            ready_at: Some(ready_at),
            dies_at: None,
        })
    }

    fn dies_at(at: Duration, ready_at: Option<Duration>) -> Result<Script, &'static str> {
        Ok(Script {
            ready_at,
            dies_at: Some(at),
        })
    }

    #[test]
    fn backoff_doubles_from_the_base_and_stops_at_the_cap() {
        let policy = Policy::default();
        let delays: Vec<u64> = (1..=8)
            .map(|n| backoff_delay(&policy, n).as_secs())
            .collect();
        assert_eq!(delays, [1, 2, 4, 8, 16, 30, 30, 30]);
        assert_eq!(backoff_delay(&policy, 0), policy.backoff_base);
        assert_eq!(
            backoff_delay(&policy, 500),
            policy.backoff_max,
            "no overflow"
        );
    }

    #[test]
    fn reports_ready_once_health_answers_then_stops_cleanly() {
        let mut fake = Fake::new(vec![healthy_forever(secs(3))]);
        fake.stop_at = Some(secs(10));
        let end = run(&mut fake, &Policy::default());
        assert_eq!(end, State::Stopped);
        assert_eq!(
            fake.states,
            [
                State::Starting { attempt: 1 },
                State::Ready(health()),
                State::Stopped
            ]
        );
        assert_eq!(*fake.killed.borrow(), 1, "the sidecar is stopped on quit");
        assert_eq!(fake.spawned, 1);
    }

    #[test]
    fn a_crash_restarts_with_growing_delays() {
        // Ready, then dies after 5 s; again; then stay up.
        let scripts = vec![
            dies_at(secs(5), Some(secs(1))),
            dies_at(secs(5), Some(secs(1))),
            healthy_forever(secs(1)),
        ];
        let mut fake = Fake::new(scripts);
        fake.stop_at = Some(secs(60));
        let end = run(&mut fake, &Policy::default());
        assert_eq!(end, State::Stopped);
        let delays: Vec<Duration> = fake
            .states
            .iter()
            .filter_map(|s| match s {
                State::Restarting { delay, .. } => Some(*delay),
                _ => None,
            })
            .collect();
        assert_eq!(delays, [secs(1), secs(2)]);
        assert_eq!(fake.spawned, 3);
        assert!(matches!(
            fake.states[fake.states.len() - 2],
            State::Ready(_)
        ));
    }

    #[test]
    fn gives_up_after_max_attempts_with_the_last_reason() {
        let mut fake = Fake::new(vec![]); // every process dies at once
        let policy = Policy {
            max_attempts: 3,
            ..Policy::default()
        };
        let end = run(&mut fake, &policy);
        assert_eq!(fake.spawned, 3);
        match end {
            State::Failed { reason } => assert!(reason.contains("exited"), "{reason}"),
            other => panic!("expected Failed, got {other:?}"),
        }
        assert_eq!(fake.states.last(), Some(&end_state(&fake)));
    }

    fn end_state(fake: &Fake) -> State {
        fake.states.last().cloned().unwrap()
    }

    #[test]
    fn a_process_that_cannot_be_spawned_counts_as_a_failed_attempt() {
        let mut fake = Fake::new(vec![Err("no such file"), Err("no such file")]);
        let policy = Policy {
            max_attempts: 2,
            ..Policy::default()
        };
        match run(&mut fake, &policy) {
            State::Failed { reason } => assert!(reason.contains("no such file"), "{reason}"),
            other => panic!("expected Failed, got {other:?}"),
        }
        assert_eq!(fake.spawned, 2);
    }

    #[test]
    fn a_process_that_never_answers_is_killed_after_the_startup_timeout() {
        let hang = Ok(Script {
            ready_at: None,
            dies_at: None,
        });
        let mut fake = Fake::new(vec![hang]);
        let policy = Policy {
            max_attempts: 1,
            ..Policy::default()
        };
        match run(&mut fake, &policy) {
            State::Failed { reason } => assert!(reason.contains("did not answer"), "{reason}"),
            other => panic!("expected Failed, got {other:?}"),
        }
        assert_eq!(*fake.killed.borrow(), 1);
        assert!(fake.now() >= policy.startup_timeout);
    }

    #[test]
    fn a_long_healthy_run_resets_the_failure_count() {
        // Two quick failures, one good hour that ends in a crash, then two more
        // quick failures: with max_attempts 3 it only gives up if the count was reset
        // wrongly. The good run must make the count start again.
        let scripts = vec![
            dies_at(Duration::ZERO, None),
            dies_at(Duration::ZERO, None),
            dies_at(secs(3600), Some(secs(1))),
            dies_at(Duration::ZERO, None),
            dies_at(Duration::ZERO, None),
            healthy_forever(secs(1)),
        ];
        let mut fake = Fake::new(scripts);
        fake.stop_at = Some(secs(4000));
        let policy = Policy {
            max_attempts: 3,
            ..Policy::default()
        };
        let end = run(&mut fake, &policy);
        assert_eq!(end, State::Stopped);
        assert_eq!(fake.spawned, 6);
    }

    #[test]
    fn stopping_during_the_backoff_does_not_start_another_process() {
        let mut fake = Fake::new(vec![dies_at(Duration::ZERO, None)]);
        fake.stop_at = Some(Duration::from_millis(100));
        let end = run(&mut fake, &Policy::default());
        assert_eq!(end, State::Stopped);
        assert_eq!(fake.spawned, 1);
    }

    #[test]
    fn a_ready_process_whose_health_stops_answering_is_restarted() {
        // Alive but silent after being ready: killed and replaced.
        struct Wedges(Fake, bool);
        impl Backend for Wedges {
            type Child = FakeChild;
            fn spawn(&mut self) -> std::io::Result<FakeChild> {
                self.0.spawn()
            }
            fn probe(&mut self) -> Option<Health> {
                // The first process answers for 5 s, then goes silent for good.
                if self.0.spawned == 1 && self.0.clock.get() > secs(5) {
                    self.1 = true;
                    return None;
                }
                self.0.probe()
            }
            fn now(&self) -> Duration {
                self.0.now()
            }
            fn sleep(&mut self, d: Duration) {
                self.0.sleep(d);
            }
            fn stop_requested(&self) -> bool {
                self.0.stop_requested()
            }
            fn publish(&mut self, s: &State) {
                self.0.publish(s);
            }
        }
        let mut inner = Fake::new(vec![healthy_forever(secs(1)), healthy_forever(secs(1))]);
        inner.stop_at = Some(secs(40));
        let mut backend = Wedges(inner, false);
        let end = run(&mut backend, &Policy::default());
        assert_eq!(end, State::Stopped);
        assert!(backend.1);
        assert_eq!(backend.0.spawned, 2);
        assert!(backend
            .0
            .states
            .iter()
            .any(|s| matches!(s, State::Restarting { reason, .. } if reason.contains("health"))));
    }
}
