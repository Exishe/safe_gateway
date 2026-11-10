//! What the shell does when the operating system asks it to stop (ADR 0027).
//!
//! Quit and Cmd+Q already stop the sidecar. A SIGTERM (a Linux logout), a Ctrl+C in the
//! terminal a debug build was started from, and the Windows console, logoff and
//! shutdown events did not. This module holds the decision as plain functions, so it is
//! tested without a signal or a window; `lib.rs` installs the handlers and acts on the
//! plan. A gateway the shell adopted (one that was already answering on the port) is
//! never stopped.

use std::time::Duration;

/// How long a handler that must not return early waits for the sidecar to stop. The
/// sidecar's own grace period is five seconds; this leaves room for the kill after it.
pub const WAIT_LIMIT: Duration = Duration::from_secs(8);

/// Why the system asked the shell to stop.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Trigger {
    /// SIGTERM: a session logout or `kill`.
    Terminate,
    /// SIGINT, Ctrl+C or Ctrl+Break.
    Interrupt,
    /// SIGHUP: the terminal the shell was started from went away.
    Hangup,
    /// Windows: the console window was closed.
    #[cfg_attr(not(windows), allow(dead_code))]
    ConsoleClose,
    /// Windows: the user is logging off.
    #[cfg_attr(not(windows), allow(dead_code))]
    Logoff,
    /// Windows: the system is shutting down.
    #[cfg_attr(not(windows), allow(dead_code))]
    Shutdown,
}

impl Trigger {
    /// Whether Windows ends the process as soon as its handler returns. Such a handler
    /// has to wait for the sidecar to stop; a Unix signal handler does not.
    pub fn ends_process_on_return(self) -> bool {
        matches!(self, Self::ConsoleClose | Self::Logoff | Self::Shutdown)
    }
}

/// What to do about a trigger.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Plan {
    /// Begin quitting now. False when a quit is already under way.
    pub start_quit: bool,
    /// Stop the sidecar as part of it. False for a gateway the shell adopted.
    pub stop_sidecar: bool,
    /// Do not return from the handler until the quit has finished.
    pub wait: bool,
}

pub fn plan(trigger: Trigger, already_quitting: bool, adopted: bool) -> Plan {
    Plan {
        start_quit: !already_quitting,
        stop_sidecar: !already_quitting && !adopted,
        wait: trigger.ends_process_on_return(),
    }
}

// Signal numbers are the same on Linux and macOS, the two Unix systems the shell targets.
const SIGHUP: i32 = 1;
const SIGINT: i32 = 2;
const SIGTERM: i32 = 15;

/// The trigger for a Unix signal number; `None` for any other signal.
pub fn trigger_for_signal(signal: i32) -> Option<Trigger> {
    match signal {
        SIGTERM => Some(Trigger::Terminate),
        SIGINT => Some(Trigger::Interrupt),
        SIGHUP => Some(Trigger::Hangup),
        _ => None,
    }
}

// `SetConsoleCtrlHandler` event numbers (winbase.h). Only the Windows handler uses them,
// but the mapping is tested on every platform.
#[cfg_attr(not(windows), allow(dead_code))]
const CTRL_C_EVENT: u32 = 0;
#[cfg_attr(not(windows), allow(dead_code))]
const CTRL_BREAK_EVENT: u32 = 1;
#[cfg_attr(not(windows), allow(dead_code))]
const CTRL_CLOSE_EVENT: u32 = 2;
#[cfg_attr(not(windows), allow(dead_code))]
const CTRL_LOGOFF_EVENT: u32 = 5;
#[cfg_attr(not(windows), allow(dead_code))]
const CTRL_SHUTDOWN_EVENT: u32 = 6;

/// The trigger for a Windows console control event; `None` for any other.
#[cfg_attr(not(windows), allow(dead_code))]
pub fn trigger_for_console_event(event: u32) -> Option<Trigger> {
    match event {
        CTRL_C_EVENT | CTRL_BREAK_EVENT => Some(Trigger::Interrupt),
        CTRL_CLOSE_EVENT => Some(Trigger::ConsoleClose),
        CTRL_LOGOFF_EVENT => Some(Trigger::Logoff),
        CTRL_SHUTDOWN_EVENT => Some(Trigger::Shutdown),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_first_terminate_starts_a_quit_that_stops_the_sidecar_it_started() {
        let plan = plan(Trigger::Terminate, false, false);
        assert!(plan.start_quit);
        assert!(plan.stop_sidecar);
        assert!(!plan.wait, "a Unix signal handler returns at once");
    }

    #[test]
    fn an_adopted_gateway_is_never_stopped_but_the_shell_still_quits() {
        for trigger in [
            Trigger::Terminate,
            Trigger::Interrupt,
            Trigger::Hangup,
            Trigger::ConsoleClose,
            Trigger::Logoff,
            Trigger::Shutdown,
        ] {
            let plan = plan(trigger, false, true);
            assert!(plan.start_quit, "{trigger:?}");
            assert!(
                !plan.stop_sidecar,
                "{trigger:?} must not stop an adopted gateway"
            );
        }
    }

    #[test]
    fn a_second_trigger_during_a_quit_starts_nothing_more() {
        let plan = plan(Trigger::Interrupt, true, false);
        assert!(!plan.start_quit);
        assert!(!plan.stop_sidecar);
        assert!(!plan.wait);
    }

    #[test]
    fn windows_events_that_end_the_process_wait_for_the_stop() {
        for trigger in [Trigger::ConsoleClose, Trigger::Logoff, Trigger::Shutdown] {
            assert!(trigger.ends_process_on_return(), "{trigger:?}");
            assert!(plan(trigger, false, false).wait, "{trigger:?}");
            // Also when the quit was started by something else: the handler must still
            // not return before it has finished.
            let during = plan(trigger, true, false);
            assert!(!during.start_quit && during.wait, "{trigger:?}");
        }
        for trigger in [Trigger::Terminate, Trigger::Interrupt, Trigger::Hangup] {
            assert!(!trigger.ends_process_on_return(), "{trigger:?}");
        }
    }

    #[test]
    fn unix_signals_map_to_triggers() {
        assert_eq!(trigger_for_signal(15), Some(Trigger::Terminate));
        assert_eq!(trigger_for_signal(2), Some(Trigger::Interrupt));
        assert_eq!(trigger_for_signal(1), Some(Trigger::Hangup));
        for other in [0, 3, 9, 10, 14, 17] {
            assert_eq!(trigger_for_signal(other), None, "signal {other}");
        }
    }

    #[test]
    fn windows_console_events_map_to_triggers() {
        assert_eq!(trigger_for_console_event(0), Some(Trigger::Interrupt));
        assert_eq!(trigger_for_console_event(1), Some(Trigger::Interrupt));
        assert_eq!(trigger_for_console_event(2), Some(Trigger::ConsoleClose));
        assert_eq!(trigger_for_console_event(5), Some(Trigger::Logoff));
        assert_eq!(trigger_for_console_event(6), Some(Trigger::Shutdown));
        for other in [3, 4, 7, 100] {
            assert_eq!(trigger_for_console_event(other), None, "event {other}");
        }
    }
}
