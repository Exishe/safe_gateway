//! `GET /health` on the loopback gateway, with std only: no HTTP client crate.
//!
//! The gateway answers a tiny JSON object on 127.0.0.1, so a hand-written HTTP/1.0
//! request is enough and keeps the dependency list (and THIRD_PARTY_NOTICES.md) short.

use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::time::Duration;

use serde::Deserialize;

/// What the gateway says about itself. Unknown fields are ignored.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
pub struct Health {
    pub status: String,
    #[serde(default)]
    pub version: String,
    #[serde(default)]
    pub provider: String,
    #[serde(default)]
    pub local: bool,
    #[serde(default)]
    pub audit: bool,
}

impl Health {
    /// One line for the tray: which provider, and whether the text stays on this machine.
    pub fn summary(&self) -> String {
        let place = if self.local {
            "local model"
        } else {
            "cloud model"
        };
        format!("Running - {} ({})", self.provider, place)
    }
}

#[derive(Debug, PartialEq, Eq)]
pub enum ProbeError {
    Connect(String),
    Io(String),
    BadResponse(String),
}

/// Parse a raw HTTP response into its status code and body.
pub fn split_response(raw: &[u8]) -> Result<(u16, String), ProbeError> {
    let text = String::from_utf8_lossy(raw);
    let (head, body) = text
        .split_once("\r\n\r\n")
        .ok_or_else(|| ProbeError::BadResponse("no header end".into()))?;
    let status_line = head.lines().next().unwrap_or_default();
    let code = status_line
        .split_whitespace()
        .nth(1)
        .and_then(|word| word.parse::<u16>().ok())
        .ok_or_else(|| ProbeError::BadResponse(format!("bad status line: {status_line}")))?;
    Ok((code, body.to_owned()))
}

/// A healthy gateway answers 200 with `"status": "ok"`.
pub fn parse_health(raw: &[u8]) -> Result<Health, ProbeError> {
    let (code, body) = split_response(raw)?;
    if code != 200 {
        return Err(ProbeError::BadResponse(format!("HTTP {code}")));
    }
    let health: Health =
        serde_json::from_str(&body).map_err(|error| ProbeError::BadResponse(error.to_string()))?;
    if health.status != "ok" {
        return Err(ProbeError::BadResponse(format!("status {}", health.status)));
    }
    Ok(health)
}

/// Ask `addr` for `/health`. Only ever called with a loopback address.
pub fn fetch(addr: SocketAddr, timeout: Duration) -> Result<Health, ProbeError> {
    let mut stream = TcpStream::connect_timeout(&addr, timeout)
        .map_err(|error| ProbeError::Connect(error.to_string()))?;
    let io = |error: std::io::Error| ProbeError::Io(error.to_string());
    stream.set_read_timeout(Some(timeout)).map_err(io)?;
    stream.set_write_timeout(Some(timeout)).map_err(io)?;
    // The gateway checks Host against loopback (ADR 0019), so say which one.
    let request = format!("GET /health HTTP/1.0\r\nHost: {addr}\r\nConnection: close\r\n\r\n");
    stream.write_all(request.as_bytes()).map_err(io)?;
    let mut raw = Vec::new();
    stream.read_to_end(&mut raw).map_err(io)?;
    parse_health(&raw)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;
    use std::thread;

    const OK: &str = "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\n\r\n\
        {\"status\":\"ok\",\"version\":\"0.6.0\",\"provider\":\"ollama\",\"local\":true,\"audit\":true}";

    #[test]
    fn parses_the_gateways_health_answer() {
        let health = parse_health(OK.as_bytes()).unwrap();
        assert_eq!(health.version, "0.6.0");
        assert_eq!(health.provider, "ollama");
        assert!(health.local && health.audit);
    }

    #[test]
    fn summary_says_local_or_cloud() {
        let mut health = parse_health(OK.as_bytes()).unwrap();
        assert_eq!(health.summary(), "Running - ollama (local model)");
        health.local = false;
        health.provider = "openai".into();
        assert_eq!(health.summary(), "Running - openai (cloud model)");
    }

    #[test]
    fn refuses_anything_but_200_and_status_ok() {
        let not_found = b"HTTP/1.1 404 Not Found\r\n\r\n{}";
        assert!(matches!(
            parse_health(not_found),
            Err(ProbeError::BadResponse(_))
        ));
        let degraded = b"HTTP/1.1 200 OK\r\n\r\n{\"status\":\"starting\"}";
        assert!(matches!(
            parse_health(degraded),
            Err(ProbeError::BadResponse(_))
        ));
        assert!(matches!(
            parse_health(b"garbage"),
            Err(ProbeError::BadResponse(_))
        ));
        assert!(matches!(
            parse_health(b"HTTP/1.1 200 OK\r\n\r\nnot json"),
            Err(ProbeError::BadResponse(_))
        ));
    }

    fn serve_once(answer: &'static str) -> SocketAddr {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = listener.local_addr().unwrap();
        thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut buffer = [0_u8; 1024];
            let read = stream.read(&mut buffer).unwrap();
            let request = String::from_utf8_lossy(&buffer[..read]).into_owned();
            assert!(request.starts_with("GET /health HTTP/1.0\r\n"));
            assert!(request.contains("Host: 127.0.0.1:"));
            stream.write_all(answer.as_bytes()).unwrap();
        });
        addr
    }

    #[test]
    fn fetch_talks_to_a_listener_on_loopback() {
        let addr = serve_once(OK);
        let health = fetch(addr, Duration::from_secs(2)).unwrap();
        assert_eq!(health.status, "ok");
    }

    #[test]
    fn fetch_reports_a_closed_port_as_a_connect_error() {
        let addr = {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            listener.local_addr().unwrap()
        };
        assert!(matches!(
            fetch(addr, Duration::from_millis(500)),
            Err(ProbeError::Connect(_))
        ));
    }
}
