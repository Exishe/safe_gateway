//! What the window and the tray show for each supervisor state.

use crate::supervisor::State;

/// The window shows either the gateway's own UI or a local page of this app.
#[derive(Debug, PartialEq, Eq)]
pub enum View {
    Gateway,
    /// The fragment `ui/index.html` reads: `loading`, `restarting?...`, `error?...`.
    Local(String),
}

/// Keep unreserved characters, escape the rest, so a reason cannot break out of the
/// fragment. The page reads it back with `decodeURIComponent` and shows it as text.
pub fn percent_encode(text: &str) -> String {
    let mut out = String::new();
    for byte in text.bytes() {
        if byte.is_ascii_alphanumeric() || b"-_.~".contains(&byte) {
            out.push(char::from(byte));
        } else {
            out.push_str(&format!("%{byte:02X}"));
        }
    }
    out
}

pub fn view_for(state: &State) -> View {
    match state {
        State::Ready(_) => View::Gateway,
        State::Starting { .. } | State::Stopped => View::Local("loading".into()),
        State::Restarting { attempt, reason, .. } => {
            View::Local(format!("restarting?attempt={attempt}&reason={}", percent_encode(reason)))
        }
        State::Failed { reason } => View::Local(format!("error?reason={}", percent_encode(reason))),
    }
}

/// Where the window may go: this app's own pages and the gateway on its loopback port,
/// nothing else. Any other address, a link in the gateway UI included, is refused.
pub fn navigation_allowed(scheme: &str, host: Option<&str>, port: Option<u16>) -> bool {
    match (scheme, host, port) {
        // This app's pages: `tauri://localhost` (macOS, Linux), `http(s)://tauri.localhost`
        // (Windows, where WebView2 has no custom schemes).
        ("tauri", Some("localhost"), _) | ("http" | "https", Some("tauri.localhost"), _) => true,
        ("http", Some("127.0.0.1"), Some(8787)) => true,
        _ => false,
    }
}

/// The status line in the tray menu.
pub fn tray_text(state: &State) -> String {
    match state {
        State::Starting { .. } => "Starting...".into(),
        State::Ready(health) => health.summary(),
        State::Restarting { attempt, .. } => format!("Restarting (try {attempt})..."),
        State::Failed { .. } => "Stopped: could not start".into(),
        State::Stopped => "Stopped".into(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::health::Health;
    use std::time::Duration;

    fn ready() -> State {
        State::Ready(Health {
            status: "ok".into(),
            version: "0.6.0".into(),
            provider: "openai".into(),
            local: false,
            audit: true,
        })
    }

    #[test]
    fn only_a_ready_gateway_shows_the_gateway_ui() {
        assert_eq!(view_for(&ready()), View::Gateway);
        assert_eq!(view_for(&State::Starting { attempt: 1 }), View::Local("loading".into()));
        assert_eq!(view_for(&State::Stopped), View::Local("loading".into()));
    }

    #[test]
    fn a_restart_names_the_try_and_the_reason() {
        let state = State::Restarting {
            attempt: 2,
            delay: Duration::from_secs(4),
            reason: "exited (code 1)".into(),
        };
        assert_eq!(
            view_for(&state),
            View::Local("restarting?attempt=2&reason=exited%20%28code%201%29".into())
        );
    }

    #[test]
    fn a_failure_shows_the_error_view_with_the_reason() {
        let state = State::Failed { reason: "port in use".into() };
        assert_eq!(view_for(&state), View::Local("error?reason=port%20in%20use".into()));
    }

    #[test]
    fn encoding_leaves_no_character_that_could_end_the_fragment() {
        let encoded = percent_encode("a&b=c#d?e é/\\\"<>");
        assert!(encoded
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"-_.~%".contains(&b)));
        assert_eq!(percent_encode("é"), "%C3%A9");
    }

    #[test]
    fn the_window_may_only_show_its_own_pages_and_the_gateway() {
        assert!(navigation_allowed("tauri", Some("localhost"), None));
        assert!(navigation_allowed("http", Some("tauri.localhost"), None));
        assert!(navigation_allowed("http", Some("127.0.0.1"), Some(8787)));
        assert!(!navigation_allowed("http", Some("127.0.0.1"), Some(9999)));
        assert!(!navigation_allowed("https", Some("127.0.0.1"), Some(8787)));
        assert!(!navigation_allowed("http", Some("localhost"), Some(8787)));
        assert!(!navigation_allowed("https", Some("example.com"), None));
        assert!(!navigation_allowed("http", Some("tauri.localhost.evil.example"), None));
        assert!(!navigation_allowed("file", None, None));
    }

    #[test]
    fn the_tray_line_follows_the_state() {
        assert_eq!(tray_text(&ready()), "Running - openai (cloud model)");
        assert_eq!(tray_text(&State::Starting { attempt: 1 }), "Starting...");
        assert_eq!(
            tray_text(&State::Restarting {
                attempt: 3,
                delay: Duration::from_secs(1),
                reason: String::new()
            }),
            "Restarting (try 3)..."
        );
        assert_eq!(tray_text(&State::Failed { reason: "x".into() }), "Stopped: could not start");
        assert_eq!(tray_text(&State::Stopped), "Stopped");
    }
}
