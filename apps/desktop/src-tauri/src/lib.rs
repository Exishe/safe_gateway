//! SafeGuard Gateway desktop shell (ADR 0027).
//!
//! It starts the Python gateway as a sidecar, waits for `/health`, and shows the
//! gateway's own UI from `http://127.0.0.1:8787/` in a window. A tray icon offers the
//! dashboard, the status, a start-at-login switch and Quit. The shell holds no
//! personal data and makes no network request except to that loopback address.

mod health;
mod sidecar;
mod supervisor;
mod view;

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;

use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{
    AppHandle, Manager, RunEvent, Url, WebviewUrl, WebviewWindow, WebviewWindowBuilder, WindowEvent,
};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt};

use crate::sidecar::{executable_in, ProcessBackend, GATEWAY_URL};
use crate::supervisor::{Policy, State};
use crate::view::{navigation_allowed, tray_text, view_for, View};

const MAIN: &str = "main";

struct Running {
    stop: Arc<AtomicBool>,
    handle: JoinHandle<()>,
}

/// The supervisor thread, started and stopped on demand.
#[derive(Default)]
struct Sidecar {
    running: Mutex<Option<Running>>,
    quitting: AtomicBool,
}

impl Sidecar {
    fn start(&self, backend: impl FnOnce(Arc<AtomicBool>) -> ProcessBackend + Send + 'static) {
        let mut slot = self.running.lock().expect("sidecar lock");
        if slot.is_some() {
            return;
        }
        let stop = Arc::new(AtomicBool::new(false));
        let mut backend = backend(stop.clone());
        let handle = std::thread::spawn(move || {
            supervisor::run(&mut backend, &Policy::default());
        });
        *slot = Some(Running { stop, handle });
    }

    /// Ask the supervisor to stop, and wait until the sidecar process is gone.
    fn stop(&self) {
        let running = self.running.lock().expect("sidecar lock").take();
        if let Some(Running { stop, handle }) = running {
            stop.store(true, Ordering::SeqCst);
            let _ = handle.join();
        }
    }
}

/// Where the sidecar program is. A debug build may be pointed at another program with
/// `SAFEGUARD_SIDECAR`; a release build ignores it.
fn sidecar_path(app: &AppHandle) -> PathBuf {
    if cfg!(debug_assertions) {
        if let Some(path) = std::env::var_os("SAFEGUARD_SIDECAR") {
            return PathBuf::from(path);
        }
    }
    let resources = app.path().resource_dir().unwrap_or_default();
    executable_in(&resources)
}

fn sidecar_log(app: &AppHandle) -> Option<PathBuf> {
    let dir = app.path().app_log_dir().ok()?;
    std::fs::create_dir_all(&dir).ok()?;
    Some(dir.join("sidecar.log"))
}

/// Point the window at `view`, unless it is already there.
fn show(window: &WebviewWindow, local: &Url, view: View) {
    let target = match view {
        View::Gateway => Url::parse(GATEWAY_URL).expect("constant URL"),
        View::Local(fragment) => {
            let mut url = local.clone();
            url.set_fragment(Some(&fragment));
            url
        }
    };
    if window.url().ok().as_ref() != Some(&target) {
        let _ = window.navigate(target);
    }
}

fn start_sidecar(app: &AppHandle, window: WebviewWindow, local: Url, status: MenuItem<tauri::Wry>) {
    let exe = sidecar_path(app);
    let log = sidecar_log(app);
    app.state::<Sidecar>().start(move |stop| {
        let quiet = stop.clone();
        let on_state = move |state: &State| {
            // Once a stop is asked for, the main thread may be waiting for this thread:
            // touching the window or the tray now could deadlock, and nobody needs it.
            if quiet.load(Ordering::SeqCst) {
                return;
            }
            let _ = status.set_text(tray_text(state));
            show(&window, &local, view_for(state));
        };
        ProcessBackend::new(exe, log, stop, Box::new(on_state))
    });
}

fn open_dashboard(app: &AppHandle) {
    if let Some(window) = app.get_webview_window(MAIN) {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

fn toggle_autostart(app: &AppHandle, item: &CheckMenuItem<tauri::Wry>) {
    let manager = app.autolaunch();
    let enabled = manager.is_enabled().unwrap_or(false);
    let _ = if enabled { manager.disable() } else { manager.enable() };
    // Show what the system now says, not what we hoped for.
    let _ = item.set_checked(manager.is_enabled().unwrap_or(false));
}

/// Stop the sidecar, then exit. Done on a thread of its own: the main thread must stay
/// free to run the window and tray calls the supervisor makes while it winds down.
fn quit(app: &AppHandle) {
    if app.state::<Sidecar>().quitting.swap(true, Ordering::SeqCst) {
        return;
    }
    let app = app.clone();
    std::thread::spawn(move || {
        app.state::<Sidecar>().stop();
        app.exit(0);
    });
}

fn restart(app: &AppHandle, window: WebviewWindow, local: Url, status: MenuItem<tauri::Wry>) {
    let app = app.clone();
    std::thread::spawn(move || {
        app.state::<Sidecar>().stop();
        start_sidecar(&app, window, local, status);
    });
}

fn setup(app: &mut tauri::App) -> Result<(), Box<dyn std::error::Error>> {
    let handle = app.handle().clone();

    let window = WebviewWindowBuilder::new(app, MAIN, WebviewUrl::App("index.html".into()))
        .title("SafeGuard Gateway")
        .inner_size(1180.0, 820.0)
        .min_inner_size(420.0, 520.0)
        .on_navigation(|url| navigation_allowed(url.scheme(), url.host_str(), url.port()))
        .build()?;
    let local = window.url()?;
    // Closing the window keeps the gateway running in the tray; Quit stops it.
    let hidden = window.clone();
    window.on_window_event(move |event| {
        if let WindowEvent::CloseRequested { api, .. } = event {
            api.prevent_close();
            let _ = hidden.hide();
        }
    });

    let open = MenuItem::with_id(app, "open", "Open dashboard", true, None::<&str>)?;
    let status = MenuItem::with_id(app, "status", "Starting...", false, None::<&str>)?;
    let restart_item = MenuItem::with_id(app, "restart", "Restart gateway", true, None::<&str>)?;
    let login_on = app.autolaunch().is_enabled().unwrap_or(false);
    let login = CheckMenuItem::with_id(app, "autostart", "Start at login", true, login_on, None::<&str>)?;
    let quit_item = MenuItem::with_id(app, "quit", "Quit SafeGuard Gateway", true, None::<&str>)?;
    let first_line = PredefinedMenuItem::separator(app)?;
    let second_line = PredefinedMenuItem::separator(app)?;
    let menu = Menu::with_items(
        app,
        &[&open, &status, &first_line, &restart_item, &login, &second_line, &quit_item],
    )?;

    let login_item = login.clone();
    let restart_status = status.clone();
    let restart_window = window.clone();
    let restart_local = local.clone();
    let mut builder = TrayIconBuilder::new()
        .tooltip("SafeGuard Gateway")
        .menu(&menu)
        .show_menu_on_left_click(true)
        .on_menu_event(move |app, event| match event.id().as_ref() {
            "open" => open_dashboard(app),
            "autostart" => toggle_autostart(app, &login_item),
            "restart" => {
                restart(app, restart_window.clone(), restart_local.clone(), restart_status.clone());
            }
            "quit" => quit(app),
            _ => {}
        });
    if let Some(icon) = app.default_window_icon() {
        builder = builder.icon(icon.clone());
    }
    builder.build(app)?;

    start_sidecar(&handle, window, local, status);
    Ok(())
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_autostart::init(MacosLauncher::LaunchAgent, Some(vec![])))
        .manage(Sidecar::default())
        .setup(setup)
        .build(tauri::generate_context!())
        .expect("cannot build the SafeGuard Gateway app");
    app.run(|app, event| {
        match event {
            // Cmd+Q and the like: stop the sidecar first, then really exit.
            RunEvent::ExitRequested { api, .. }
                if !app.state::<Sidecar>().quitting.load(Ordering::SeqCst) =>
            {
                api.prevent_exit();
                quit(app);
            }
            RunEvent::Exit => app.state::<Sidecar>().stop(),
            _ => {}
        }
    });
}
