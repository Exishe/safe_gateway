//! The real `Backend`: the PyInstaller build of `safeguard serve` as a child process.
//!
//! The shell starts this one program with fixed arguments (none: the sidecar serves
//! on 127.0.0.1:8787 by default). It does not use the shell plugin, so no page and no
//! script can ask the shell to run anything (ADR 0027).

use std::fs::{File, OpenOptions};
use std::io;
use std::net::{Ipv4Addr, SocketAddr};
use std::path::{Path, PathBuf};
use std::process::{Child as Process, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::thread::sleep;
use std::time::{Duration, Instant};

use crate::health::{self, Health};
use crate::supervisor::{Backend, Child, State};

/// The gateway's port. The web UI, its CSP and the same-origin rule all name it.
pub const PORT: u16 = 8787;

pub fn gateway_addr() -> SocketAddr {
    SocketAddr::from((Ipv4Addr::LOCALHOST, PORT))
}

pub const GATEWAY_URL: &str = "http://127.0.0.1:8787/";

const PROBE_TIMEOUT: Duration = Duration::from_secs(2);
#[cfg(unix)]
const STOP_GRACE: Duration = Duration::from_secs(5);

/// The program inside the one-folder build.
pub fn executable_in(resource_dir: &Path) -> PathBuf {
    let name = if cfg!(windows) { "safeguard-sidecar.exe" } else { "safeguard-sidecar" };
    resource_dir.join("safeguard-sidecar").join(name)
}

/// A gateway already answering on the port (a previous run that was killed, or the
/// user's own `safeguard serve`). The shell adopts it and never stops it.
struct Adopted;

enum Running {
    Spawned(Process),
    Adopted(Adopted),
}

pub struct SidecarChild {
    running: Running,
}

impl Child for SidecarChild {
    fn exited(&mut self) -> Option<String> {
        match &mut self.running {
            Running::Adopted(_) => None,
            Running::Spawned(process) => match process.try_wait() {
                Ok(None) => None,
                Ok(Some(status)) => Some(format!("the gateway exited ({status})")),
                Err(error) => Some(format!("cannot check the gateway process: {error}")),
            },
        }
    }

    fn terminate(&mut self) {
        let Running::Spawned(process) = &mut self.running else {
            return;
        };
        if matches!(process.try_wait(), Ok(Some(_))) {
            return;
        }
        // SIGTERM first where it exists, so uvicorn closes the audit log properly.
        #[cfg(unix)]
        {
            let _ = Command::new("kill").arg("-TERM").arg(process.id().to_string()).status();
            let deadline = Instant::now() + STOP_GRACE;
            while Instant::now() < deadline {
                if matches!(process.try_wait(), Ok(Some(_))) {
                    return;
                }
                sleep(Duration::from_millis(100));
            }
        }
        let _ = process.kill();
        let _ = process.wait();
    }
}

pub struct ProcessBackend {
    exe: PathBuf,
    log: Option<PathBuf>,
    stop: Arc<AtomicBool>,
    started: Instant,
    on_state: Box<dyn FnMut(&State) + Send>,
}

impl ProcessBackend {
    pub fn new(
        exe: PathBuf,
        log: Option<PathBuf>,
        stop: Arc<AtomicBool>,
        on_state: Box<dyn FnMut(&State) + Send>,
    ) -> Self {
        Self { exe, log, stop, started: Instant::now(), on_state }
    }

    fn log_file(&self) -> Option<File> {
        let path = self.log.as_ref()?;
        OpenOptions::new().create(true).append(true).open(path).ok()
    }
}

impl Backend for ProcessBackend {
    type Child = SidecarChild;

    fn spawn(&mut self) -> io::Result<SidecarChild> {
        if health::fetch(gateway_addr(), PROBE_TIMEOUT).is_ok() {
            return Ok(SidecarChild { running: Running::Adopted(Adopted) });
        }
        let mut command = Command::new(&self.exe);
        command.stdin(Stdio::null());
        match self.log_file() {
            Some(file) => {
                command.stdout(file.try_clone()?).stderr(file);
            }
            None => {
                command.stdout(Stdio::null()).stderr(Stdio::null());
            }
        }
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
        }
        let process = command.spawn()?;
        Ok(SidecarChild { running: Running::Spawned(process) })
    }

    fn probe(&mut self) -> Option<Health> {
        health::fetch(gateway_addr(), PROBE_TIMEOUT).ok()
    }

    fn now(&self) -> Duration {
        self.started.elapsed()
    }

    fn sleep(&mut self, duration: Duration) {
        sleep(duration);
    }

    fn stop_requested(&self) -> bool {
        self.stop.load(Ordering::SeqCst)
    }

    fn publish(&mut self, state: &State) {
        (self.on_state)(state);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_sidecar_lives_in_its_own_folder_under_the_resources() {
        let exe = executable_in(Path::new("/opt/app/resources"));
        assert!(exe.starts_with("/opt/app/resources/safeguard-sidecar"));
        let name = exe.file_name().unwrap().to_string_lossy().into_owned();
        assert!(name.starts_with("safeguard-sidecar"));
    }

    #[test]
    fn the_gateway_address_is_loopback_only() {
        assert!(gateway_addr().ip().is_loopback());
        assert_eq!(gateway_addr().to_string(), "127.0.0.1:8787");
        assert!(GATEWAY_URL.starts_with("http://127.0.0.1:8787"));
    }

    #[cfg(unix)]
    #[test]
    fn a_missing_program_is_a_spawn_error_not_a_panic() {
        let stop = Arc::new(AtomicBool::new(false));
        let mut backend =
            ProcessBackend::new(PathBuf::from("/nonexistent/sidecar"), None, stop, Box::new(|_| {}));
        // Nothing listens on 8787 in the test run, or the shell would adopt it.
        if health::fetch(gateway_addr(), Duration::from_millis(200)).is_err() {
            assert!(backend.spawn().is_err());
        }
    }
}
