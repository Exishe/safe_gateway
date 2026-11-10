//! Hooks the operating system's stop requests to a callback (ADR 0027).
//!
//! What a request means is decided in `shutdown`; this file only receives them. On Unix
//! a thread reads SIGTERM, SIGINT and SIGHUP through `signal-hook`, which keeps the
//! handler itself async-signal-safe. On Windows `SetConsoleCtrlHandler` delivers the
//! console, close, logoff and shutdown events on a thread Windows creates, and Windows
//! ends the process when the handler returns, so the callback may block.

use crate::shutdown::Trigger;

#[cfg(unix)]
pub fn install(on_trigger: impl Fn(Trigger) + Send + 'static) {
    use signal_hook::consts::{SIGHUP, SIGINT, SIGTERM};
    use signal_hook::iterator::Signals;

    use crate::shutdown::trigger_for_signal;

    let Ok(mut signals) = Signals::new([SIGTERM, SIGINT, SIGHUP]) else {
        // The shell still works; only a signal would not stop the sidecar.
        eprintln!(
            "safeguard-desktop: cannot watch for SIGTERM; the gateway keeps running on a logout"
        );
        return;
    };
    std::thread::spawn(move || {
        for signal in signals.forever() {
            if let Some(trigger) = trigger_for_signal(signal) {
                on_trigger(trigger);
            }
        }
    });
}

#[cfg(windows)]
pub fn install(on_trigger: impl Fn(Trigger) + Send + Sync + 'static) {
    use std::sync::OnceLock;

    use crate::shutdown::trigger_for_console_event;

    type Callback = Box<dyn Fn(Trigger) + Send + Sync>;
    static CALLBACK: OnceLock<Callback> = OnceLock::new();

    #[link(name = "kernel32")]
    extern "system" {
        fn SetConsoleCtrlHandler(
            handler: Option<unsafe extern "system" fn(u32) -> i32>,
            add: i32,
        ) -> i32;
    }

    unsafe extern "system" fn on_event(event: u32) -> i32 {
        let (Some(trigger), Some(callback)) = (trigger_for_console_event(event), CALLBACK.get())
        else {
            return 0; // not ours: let the next handler see it
        };
        callback(trigger);
        1
    }

    if CALLBACK.set(Box::new(on_trigger)).is_err() {
        return;
    }
    // SAFETY: `on_event` has the signature Windows expects and lives for the whole run.
    let added = unsafe { SetConsoleCtrlHandler(Some(on_event), 1) };
    if added == 0 {
        eprintln!("safeguard-desktop: cannot watch for logoff and close events");
    }
}

#[cfg(not(any(unix, windows)))]
pub fn install(_on_trigger: impl Fn(Trigger) + Send + 'static) {}
