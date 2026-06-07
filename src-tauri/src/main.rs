#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::net::{SocketAddr, TcpStream};
#[cfg(windows)]
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::Duration;

use tauri::menu::{Menu, MenuItem};
use tauri::path::BaseDirectory;
use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
use tauri::{AppHandle, Manager, State};

struct BackendProcess(Mutex<Option<Child>>);

struct BackendLaunchSpec {
    program: PathBuf,
    args: Vec<String>,
    working_dir: PathBuf,
    path_prefixes: Vec<PathBuf>,
}

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x08000000;

fn hide_child_window(command: &mut Command) {
    #[cfg(windows)]
    {
        command.creation_flags(CREATE_NO_WINDOW);
    }
}

fn resolve_resource_path(app: &AppHandle, relative: &str) -> Result<PathBuf, String> {
    let bundled = app
        .path()
        .resolve(relative, BaseDirectory::Resource)
        .map_err(|err| err.to_string())?;
    if bundled.exists() {
        return Ok(bundled);
    }

    let executable_dir = std::env::current_exe()
        .map_err(|err| err.to_string())?
        .parent()
        .map(Path::to_path_buf)
        .ok_or_else(|| "unable to resolve executable directory".to_string())?;
    let adjacent = executable_dir.join("resources").join(relative);
    if adjacent.exists() {
        return Ok(adjacent);
    }

    Err(format!(
        "Resource not found: '{}' (checked '{}' and '{}')",
        relative,
        bundled.display(),
        adjacent.display()
    ))
}

fn spawn_backend(app: &AppHandle) -> Result<Child, String> {
    let app_data_dir = app
        .path()
        .resolve(".assistant_data", BaseDirectory::AppData)
        .map_err(|err| err.to_string())?;
    fs::create_dir_all(&app_data_dir).map_err(|err| err.to_string())?;
    hydrate_default_settings(app, &app_data_dir).map_err(|err| err.to_string())?;

    let logs_dir = app_data_dir.join("logs");
    fs::create_dir_all(&logs_dir).map_err(|err| err.to_string())?;
    let stdout_log = open_log_file(&logs_dir.join("backend.log")).map_err(|err| err.to_string())?;
    let stderr_log = open_log_file(&logs_dir.join("backend-error.log")).map_err(|err| err.to_string())?;

    let launch = resolve_backend_launch(app)?;
    let injected_path = prepend_paths(&launch.path_prefixes);

    let mut command = Command::new(&launch.program);
    hide_child_window(&mut command);
    command
        .args(&launch.args)
        .current_dir(&launch.working_dir)
        .env("ASSISTANT_DATA_DIR", &app_data_dir)
        .env("ASSISTANT_ASR_DEVICE", "cuda")
        .env("PATH", injected_path)
        .stdin(Stdio::null())
        .stdout(Stdio::from(stdout_log))
        .stderr(Stdio::from(stderr_log));

    for key in [
        "ASSISTANT_LLM_API_BASE",
        "ASSISTANT_LLM_API_MODEL",
        "ASSISTANT_LLM_API_KEY",
    ] {
        if let Ok(value) = std::env::var(key) {
            if !value.trim().is_empty() {
                command.env(key, value);
            }
        }
    }

    command
        .spawn()
        .map_err(|err| format!("failed to spawn backend: {err}"))
}

fn backend_is_healthy() -> bool {
    let address: SocketAddr = match "127.0.0.1:8765".parse() {
        Ok(value) => value,
        Err(_) => return false,
    };

    let mut stream = match TcpStream::connect_timeout(&address, Duration::from_millis(500)) {
        Ok(stream) => stream,
        Err(_) => return false,
    };

    let _ = stream.set_read_timeout(Some(Duration::from_secs(1)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(1)));

    if stream
        .write_all(b"GET /health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        .is_err()
    {
        return false;
    }

    let mut response = String::new();
    if stream.read_to_string(&mut response).is_err() {
        return false;
    }

    response.starts_with("HTTP/1.1 200") || response.starts_with("HTTP/1.0 200")
}

fn resolve_backend_launch(app: &AppHandle) -> Result<BackendLaunchSpec, String> {
    if cfg!(debug_assertions) {
        let workspace_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .map(Path::to_path_buf)
            .ok_or_else(|| "unable to resolve workspace directory".to_string())?;
        let python = workspace_dir.join(".venv").join("Scripts").join("python.exe");
        if python.exists() {
            return Ok(BackendLaunchSpec {
                program: python,
                args: vec!["-m".into(), "backend.app".into()],
                working_dir: workspace_dir.clone(),
                path_prefixes: vec![
                    workspace_dir
                        .join(".venv")
                        .join("Lib")
                        .join("site-packages")
                        .join("nvidia")
                        .join("cublas")
                        .join("bin"),
                    workspace_dir
                        .join(".venv")
                        .join("Lib")
                        .join("site-packages")
                        .join("nvidia")
                        .join("cuda_runtime")
                        .join("bin"),
                    workspace_dir
                        .join(".venv")
                        .join("Lib")
                        .join("site-packages")
                        .join("nvidia")
                        .join("cudnn")
                        .join("bin"),
                ],
            });
        }
    }

    let backend_dir = resolve_resource_path(app, "backend-runtime/assistant-backend")?;
    let backend_exe = backend_dir.join("assistant-backend.exe");
    if !backend_exe.exists() {
        return Err(format!(
            "Packaged backend runtime not found: {}",
            backend_exe.display()
        ));
    }

    Ok(BackendLaunchSpec {
        program: backend_exe,
        args: Vec::new(),
        working_dir: backend_dir.clone(),
        path_prefixes: vec![
            backend_dir.clone(),
            backend_dir.join("_internal"),
            backend_dir.join("_internal").join("nvidia").join("cublas").join("bin"),
            backend_dir.join("_internal").join("nvidia").join("cuda_runtime").join("bin"),
            backend_dir.join("_internal").join("nvidia").join("cuda_nvrtc").join("bin"),
            backend_dir.join("_internal").join("nvidia").join("cudnn").join("bin"),
            backend_dir.join("_internal").join("ctranslate2"),
            backend_dir.join("nvidia").join("cublas").join("bin"),
            backend_dir.join("nvidia").join("cuda_runtime").join("bin"),
            backend_dir.join("nvidia").join("cuda_nvrtc").join("bin"),
            backend_dir.join("nvidia").join("cudnn").join("bin"),
        ],
    })
}

fn prepend_paths(paths: &[PathBuf]) -> String {
    let current_path = std::env::var("PATH").unwrap_or_default();
    let existing: Vec<PathBuf> = std::env::split_paths(&current_path).collect();
    let mut merged: Vec<PathBuf> = Vec::new();

    for path in paths {
        if path.exists() && !merged.iter().any(|existing_path| existing_path == path) {
            merged.push(path.clone());
        }
    }

    for path in existing {
        if !merged.iter().any(|existing_path| existing_path == &path) {
            merged.push(path);
        }
    }

    std::env::join_paths(merged)
        .unwrap_or_default()
        .to_string_lossy()
        .to_string()
}

fn open_log_file(path: &Path) -> std::io::Result<File> {
    OpenOptions::new().create(true).append(true).open(path)
}

fn hydrate_default_settings(app: &AppHandle, app_data_dir: &Path) -> std::io::Result<()> {
    let config_dir = app_data_dir.join("config");
    fs::create_dir_all(&config_dir)?;
    let settings_path = config_dir.join("settings.json");
    if settings_path.exists() {
        return Ok(());
    }

    if let Ok(default_settings) = resolve_resource_path(app, "default-settings.json") {
        if default_settings.exists() {
            fs::copy(default_settings, settings_path)?;
        }
    }

    Ok(())
}

fn stop_backend(state: &State<BackendProcess>) {
    if let Ok(mut guard) = state.0.lock() {
        if let Some(child) = guard.as_mut() {
            let _ = child.kill();
        }
        *guard = None;
    }
}

fn ensure_desktop_shortcut() {
    if cfg!(debug_assertions) {
        return;
    }

    let desktop = match std::env::var("USERPROFILE") {
        Ok(profile) => PathBuf::from(profile).join("Desktop"),
        Err(_) => return,
    };
    let exe = match std::env::current_exe() {
        Ok(path) => path,
        Err(_) => return,
    };
    let shortcut = desktop.join("Local Voice Memory Assistant.lnk");
    if shortcut.exists() {
        return;
    }

    let command = format!(
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{shortcut}');$s.TargetPath='{target}';$s.WorkingDirectory='{working}';$s.IconLocation='{target},0';$s.Save()",
        shortcut = shortcut.display(),
        target = exe.display(),
        working = exe.parent().unwrap_or_else(|| Path::new("")).display()
    );

    let mut shell = Command::new("powershell");
    hide_child_window(&mut shell);
    let _ = shell
        .arg("-NoProfile")
        .arg("-ExecutionPolicy")
        .arg("Bypass")
        .arg("-Command")
        .arg(command)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn();
}

fn build_tray(app: &AppHandle) -> Result<(), String> {
    let show = MenuItem::with_id(app, "show", "Open", true, None::<&str>)
        .map_err(|err| err.to_string())?;
    let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)
        .map_err(|err| err.to_string())?;
    let menu = Menu::with_items(app, &[&show, &quit]).map_err(|err| err.to_string())?;

    TrayIconBuilder::new()
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| match event.id().as_ref() {
            "show" => {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            }
            "quit" => {
                app.exit(0);
            }
            _ => {}
        })
        .on_tray_icon_event(|tray, event| {
            if let TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            } = event
            {
                if let Some(window) = tray.app_handle().get_webview_window("main") {
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            }
        })
        .build(app)
        .map_err(|err| err.to_string())?;

    Ok(())
}

fn main() {
    let app = tauri::Builder::default()
        .manage(BackendProcess(Mutex::new(None)))
        .setup(|app| {
            if !backend_is_healthy() {
                let child = spawn_backend(app.handle())?;
                let state: State<BackendProcess> = app.state();
                if let Ok(mut guard) = state.0.lock() {
                    *guard = Some(child);
                };
            }
            ensure_desktop_shortcut();
            build_tray(app.handle())?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = window.hide();
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app, event| {
        if let tauri::RunEvent::ExitRequested { .. } = event {
            let state: State<BackendProcess> = app.state();
            stop_backend(&state);
        }
    });
}
