use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::Duration;

use tauri::menu::{Menu, MenuItem};
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};

const PORT: u16 = 8765;

static SERVER_PROCESS: Mutex<Option<Child>> = Mutex::new(None);

// ── Server binary location ───────────────────────────────────────────────────

fn server_binary(app: &tauri::App) -> PathBuf {
    let bin_name = if cfg!(target_os = "windows") {
        "server.exe"
    } else {
        "server"
    };

    // In production: bundled next to the app resources
    if let Ok(resource_dir) = app.path().resource_dir() {
        let bundled = resource_dir.join("server").join(bin_name);
        if bundled.exists() {
            return bundled;
        }
    }

    // Development fallback: look next to the executable
    let exe_dir = std::env::current_exe()
        .unwrap()
        .parent()
        .unwrap()
        .to_path_buf();
    exe_dir.join("server").join(bin_name)
}

// ── Server lifecycle ─────────────────────────────────────────────────────────

fn start_server(binary: PathBuf) {
    match Command::new(&binary)
        .arg(PORT.to_string())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
    {
        Ok(child) => {
            *SERVER_PROCESS.lock().unwrap() = Some(child);
        }
        Err(e) => {
            eprintln!("[TrackManager] Failed to start server at {:?}: {}", binary, e);
        }
    }
}

/// Poll TCP until the server is accepting connections (max ~30s).
fn wait_for_server() {
    let addr = format!("127.0.0.1:{}", PORT);
    for _ in 0..60 {
        if TcpStream::connect(&addr).is_ok() {
            // Give uvicorn a moment to finish startup
            thread::sleep(Duration::from_millis(400));
            return;
        }
        thread::sleep(Duration::from_millis(500));
    }
    eprintln!("[TrackManager] Server did not start in time on port {}", PORT);
}

fn kill_server() {
    if let Some(mut child) = SERVER_PROCESS.lock().unwrap().take() {
        child.kill().ok();
    }
}

// ── Tauri app ────────────────────────────────────────────────────────────────

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            // Start the Python server
            let binary = server_binary(app);
            start_server(binary);
            wait_for_server();

            // Main window pointing to our local server
            let url = format!("http://127.0.0.1:{}", PORT);
            WebviewWindowBuilder::new(
                app,
                "main",
                WebviewUrl::External(url.parse().unwrap()),
            )
            .title("Track Manager")
            .inner_size(1280.0, 800.0)
            .min_inner_size(900.0, 600.0)
            .build()?;

            // System tray
            let show_item =
                MenuItem::with_id(app, "show", "Abrir Track Manager", true, None::<&str>)?;
            let quit_item = MenuItem::with_id(app, "quit", "Salir", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&show_item, &quit_item])?;

            let handle = app.handle().clone();
            TrayIconBuilder::new()
                .menu(&menu)
                .tooltip("Track Manager")
                .on_menu_event(move |_tray, event| match event.id.as_ref() {
                    "show" => {
                        if let Some(w) = handle.get_webview_window("main") {
                            w.show().ok();
                            w.set_focus().ok();
                        }
                    }
                    "quit" => {
                        kill_server();
                        handle.exit(0);
                    }
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    // Click on tray icon → show window
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        let app = tray.app_handle();
                        if let Some(w) = app.get_webview_window("main") {
                            w.show().ok();
                            w.set_focus().ok();
                        }
                    }
                })
                .build(app)?;

            Ok(())
        })
        // Close button hides to tray instead of quitting
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                window.hide().unwrap();
                api.prevent_close();
            }
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");

    // Ensure server is killed when the process exits
    kill_server();
}
