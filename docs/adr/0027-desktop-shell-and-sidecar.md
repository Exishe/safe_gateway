# 0027. Desktop shell (Tauri 2) and the Python sidecar

- Status: Accepted
- Date: 2026-11-10
- Builds on: ADR 0007, ADR 0008, ADR 0019, ADR 0024

## Context

Phase 6 part 2 puts the gateway and its web UI into an installable app for the
small offices we sell to. They should not have to install Python, run a terminal
or keep a console window open. The UI already exists and is served by
`safeguard serve` on `http://127.0.0.1:8787/` (ADR 0024). Code signing costs
money, so the installers ship unsigned (hard rule 2).

## Decision

1. **The shell is a thin window plus a tray icon.** `apps/desktop/` is a Tauri 2
   app: Rust, plus three static pages (`ui/`) for the loading, restarting and error
   views. It holds no personal data, has no logic about masking and talks only to
   `127.0.0.1:8787`. The window shows the gateway's own UI by navigating to that
   address once `/health` answers; it does not embed a second copy of the UI.

2. **The engine is a PyInstaller one-folder build of `safeguard serve`**
   (`tools/build_sidecar.py`, entry `tools/sidecar_entry.py`). One folder, not one
   file: a one-file build unpacks about a gigabyte into a temporary folder on every
   start, and runs as a bootloader plus a child, which makes stopping it cleanly
   harder. The web build is bundled inside (`ui/`), and `default_ui_dir()` reads it
   from `sys._MEIPASS` when frozen. Models are **not** bundled: they are pulled by
   `safeguard models pull` on first run (ADR 0006), so the installer needs no
   Hugging Face or spaCy download at build time and the engine's no-egress rule
   (hard rule 3) is untouched.

3. **The sidecar is a bundled resource, started with `std::process::Command`, not
   with `tauri-plugin-shell`.** Tauri's `externalBin` takes a single file, which does
   not fit a one-folder build. More important, without the shell plugin there is no
   JavaScript-reachable way to start any process, so the capability file is empty
   (no `shell`, `fs`, `http` or other permission). The Rust code starts exactly one
   program, with no arguments, from the app's resource folder. A debug build may be
   pointed at another program with `SAFEGUARD_SIDECAR`; a release build ignores it.

4. **Lifecycle rules live in plain functions with unit tests**
   (`supervisor.rs`, `health.rs`, `view.rs`), behind a `Backend` trait so a fake
   clock and a fake process test them:
   - start the sidecar, poll `GET /health` every 500 ms for up to 180 s (a first
     start loads libraries and can be slow);
   - restart on exit or after six failed health checks in a row, with a delay of 1,
     2, 4, 8, 16, then 30 s;
   - give up after 5 failed runs in a row and show the error view; a run that stayed
     healthy for 60 s resets the count;
   - Quit stops it: SIGTERM, up to 5 s for uvicorn to close the audit log, then
     kill; on Windows, kill. Stopping runs on its own thread so the main thread can
     keep serving the window and tray calls the supervisor makes while winding down.
   - If something already answers `/health` on the port (a previous run that was
     killed, or the user's own `safeguard serve`), the shell adopts it and never
     stops it.

5. **Window and tray.** Closing the window hides it; the gateway keeps running in
   the tray. The tray menu has Open dashboard, a status line from `/health`
   (provider, local or cloud model), Restart gateway, a Start at login switch
   (`tauri-plugin-autostart`) and Quit. The loading, restarting and error views are
   chosen by a URL fragment the shell sets (`#error?reason=...`), so the local pages
   need no IPC; the page writes the reason with `textContent` only.

6. **Security.**
   - No remote content. The window may navigate only to its own pages and to
     `http://127.0.0.1:8787` (`on_navigation`, tested); any other link is refused.
   - The CSP for the app's own pages is `default-src 'none'`, scripts and styles
     `'self'`, `connect-src` and `frame-src` limited to `http://127.0.0.1:8787`. The
     gateway's UI carries its own CSP (ADR 0024).
   - The default capability grants nothing. The gateway UI loaded from the loopback
     address has no access to Tauri commands.
   - No updater: it needs a signing key and a server. Updates are a new installer.
   - No `devtools` feature, no asset protocol.

7. **Unsigned installers, built only on tags and by hand** (ADR 0007): `.dmg`
   (macos-14), `.msi` (windows-latest) and `.deb` (ubuntu), uploaded as workflow
   artifacts. AppImage was left out: it downloads linuxdeploy tools from GitHub at
   build time, which makes the job depend on one more external download for a
   format the first customers do not need. The README for each platform's first-run
   warning is in `docs/INSTALL.md`.

8. **Dependency groups and licenses.** PyInstaller is in its own uv group
   (`sidecar`), not in `dev`, so `uv sync` and `tools.check_licenses` (which checks
   the runtime closure) do not see it. It is GPL-2.0-or-later with a bootloader
   exception that lets the frozen program carry any license; hard rule 1 allows
   tooling like this. Its hooks package is a build-time tool as well. Rust crates and
   the web UI's npm packages are checked in `tools.gen_notices`, which fails if a
   crate has no permissive alternative. `Unicode-3.0` (ICU data crates) was added to
   the policy: permissive and OSI-approved.

9. **THIRD_PARTY_NOTICES.md is generated** (`tools/gen_notices.py`) from installed
   Python metadata, the web UI's lock file and `cargo metadata`; the hand-written
   exceptions live in `tools/notices_manual.md`. CI fails when the file is stale.

## Consequences

- The sidecar is large: PyTorch (for GLiNER, ADR 0015) is most of it. Measured on
  Linux x86-64 on 2026-11-10 (`du -sm`, MiB): the first one-folder build was 1173 MiB
  (`torch` 680, spaCy 121, transformers 48). `tools.build_sidecar` now prunes the
  finished folder (`prune_bundle`): PyTorch's C++ test binaries (`torch/bin`), its
  `torch/test` tree and `*_test.so` libraries, headers, GPU runtime libraries, `triton`,
  `*.a` archives and packages' `tests` folders. The same build after pruning is 1008
  MiB (`torch` 518, 172 MB freed). A frozen `serve --no-audit` answered `/health` and
  `POST /mask` masked an email and a card. The NER models were not pulled in that
  check, so GLiNER inference through the pruned build is not exercised here. This
  Linux build is the CPU-only PyTorch wheel, so it had no CUDA libraries, no
  `torch/include` and no `*.a` files; those patterns only matter for other builds.
  spaCy's language data is 3 MB, so it was kept (a document can be in any language).
  The `.deb` size was measured before pruning only (458 MiB).
- Stop requests from the system (amended 2026-11-10). The shell used to ignore a
  SIGTERM (a Linux logout), which left the sidecar for the next start to adopt. Now
  `os_signals.rs` receives SIGTERM, SIGINT and SIGHUP on Unix (`signal-hook`) and the
  console, close, logoff and shutdown events on Windows (`SetConsoleCtrlHandler`), and
  `shutdown::plan` decides, as a pure function with unit tests: start the same quit as
  the tray's Quit; stop the sidecar only if the shell started it (`stop_sidecar` is false
  for an adopted gateway); a second trigger during a quit starts nothing; and a Windows
  close, logoff or shutdown handler waits (up to 8 s) for the stop, because Windows ends
  the process when the handler returns. Checked end to end on Linux under Xvfb with the
  debug build: SIGTERM to a shell that had started the gateway stopped both, and
  SIGTERM to a shell that had adopted a gateway stopped only the shell. The Windows
  path compiles only on Windows and is not exercised here; the tag build and a person on
  Windows must check it. Quit and Cmd+Q were exercised only through the fake backend, as
  before.
- Unsigned apps trigger Gatekeeper and SmartScreen warnings on first run. This is
  documented, not hidden, and revisited if the owner approves a signing certificate
  (`docs/APPROVAL_NEEDED.md`).
- A first run needs a network connection once, for `safeguard models pull`. The
  shell does not run it; the gateway's UI says what is missing. The pull button in the web UI's
  Settings came later (ADR 0029).
- The shell has no way to change the gateway's configuration: the user edits
  `~/.safeguard/config.toml` (ADR 0020), as with the command-line install.
- Platform behaviour that a Linux container cannot show (Windows console flag,
  macOS tray, Gatekeeper, SmartScreen, autostart registration) is only verified by
  the tag build and a person on that platform.
