use interprocess::local_socket::{LocalSocketListener, LocalSocketStream};
use pyo3::types::PyAnyMethods;
use std::io::{Read, Write};
use std::process::{Child, Command};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Duration;

pub(crate) struct IpcWorker {
    pub(crate) child: Child,
    pub(crate) stream: LocalSocketStream,
    pub(crate) socket_path: String,
    pub(crate) tasks_run: usize,
    pub(crate) registered_wasms: std::collections::HashSet<String>,
    pub(crate) registered_dylibs: std::collections::HashSet<String>,
    pub(crate) last_used: std::time::Instant,
}

impl Drop for IpcWorker {
    fn drop(&mut self) {
        #[cfg(unix)]
        {
            let pid = self.child.id();
            cleanup_worker_shm(pid);
        }
        let _ = self.child.kill();
        let _ = self.child.wait();
        if cfg!(unix) && std::path::Path::new(&self.socket_path).exists() {
            let _ = std::fs::remove_file(&self.socket_path);
        }
    }
}

pub(crate) struct IsolatedProcessPool {
    workers: Mutex<Vec<IpcWorker>>,
}

static PROCESS_POOL: OnceLock<Arc<IsolatedProcessPool>> = OnceLock::new();

#[cfg(unix)]
pub(crate) fn cleanup_worker_shm(pid: u32) {
    if let Ok(entries) = std::fs::read_dir("/dev/shm") {
        let prefix = format!("pyroxide_shm_res_{pid}_");
        for entry in entries.flatten() {
            let is_match = entry
                .file_name()
                .into_string()
                .map(|name| name.starts_with(&prefix))
                .unwrap_or(false);
            if is_match {
                let _ = std::fs::remove_file(entry.path());
            }
        }
    }
}

pub(crate) fn get_process_pool() -> Arc<IsolatedProcessPool> {
    PROCESS_POOL
        .get_or_init(|| {
            #[cfg(unix)]
            {
                if let Ok(entries) = std::fs::read_dir("/tmp") {
                    for entry in entries.flatten() {
                        let is_stale_socket = entry
                            .file_name()
                            .into_string()
                            .map(|filename| {
                                filename.starts_with("pyro3_ipc_") && filename.ends_with(".sock")
                            })
                            .unwrap_or(false);
                        if is_stale_socket
                            && interprocess::local_socket::LocalSocketStream::connect(entry.path())
                                .is_err()
                        {
                            let _ = std::fs::remove_file(entry.path());
                        }
                    }
                }
                if let Ok(entries) = std::fs::read_dir("/dev/shm") {
                    for entry in entries.flatten() {
                        if let Ok(filename) = entry.file_name().into_string()
                            && filename.starts_with("pyroxide_shm_")
                        {
                            let parts: Vec<&str> = filename.split('_').collect();
                            let pid_str = if filename.starts_with("pyroxide_shm_res_") {
                                parts.get(3)
                            } else {
                                parts.get(2)
                            };
                            if let Some(pid_str) = pid_str
                                && let Ok(pid) = pid_str.parse::<i32>()
                                && unsafe { libc::kill(pid, 0) } != 0
                            {
                                let _ = std::fs::remove_file(entry.path());
                            }
                        }
                    }
                }
            }
            let pool = Arc::new(IsolatedProcessPool {
                workers: Mutex::new(Vec::new()),
            });
            spawn_idle_reaper(pool.clone());
            pool
        })
        .clone()
}

impl IsolatedProcessPool {
    /// Acquires a warm worker, or spawns a new one if none is available.
    pub fn acquire_worker(&self) -> Result<IpcWorker, String> {
        {
            let mut guard = self.workers.lock().unwrap_or_else(|e| e.into_inner());
            while let Some(mut worker) = guard.pop() {
                // Check if worker child is still alive
                if let Ok(None) = worker.child.try_wait() {
                    return Ok(worker);
                }
                // Worker process is dead, drop it and keep checking
            }
        }

        // Spawn a new worker
        self.spawn_new_worker()
    }

    pub fn release_worker(&self, mut worker: IpcWorker) {
        if get_max_tasks_per_worker() > 0 && worker.tasks_run >= get_max_tasks_per_worker() {
            drop(worker); // kills child process and cleans socket file
            return;
        }

        // Check if worker child is still alive
        if let Ok(None) = worker.child.try_wait() {
            worker.last_used = std::time::Instant::now();
            let mut guard = self.workers.lock().unwrap_or_else(|e| e.into_inner());
            guard.push(worker);
        } else {
            drop(worker);
        }
    }

    fn spawn_new_worker(&self) -> Result<IpcWorker, String> {
        let python_path = pyo3::Python::attach(|py| -> Result<String, String> {
            let sys = py
                .import("sys")
                .map_err(|e| format!("Failed to import sys: {e}"))?;
            let exe = sys
                .getattr("executable")
                .map_err(|e| format!("Failed to get sys.executable: {e}"))?;
            exe.extract::<String>()
                .map_err(|e| format!("Failed to extract sys.executable: {e}"))
        })?;

        let rand_num: u32 = rand::random();

        let socket_path = if cfg!(unix) {
            format!("/tmp/pyro3_ipc_{rand_num}.sock")
        } else {
            format!("pyro3_ipc_{rand_num}")
        };

        if cfg!(unix) {
            let _ = std::fs::remove_file(&socket_path);
        }

        let listener = LocalSocketListener::bind(socket_path.as_str())
            .map_err(|e| format!("Failed to bind local socket {socket_path}: {e}"))?;

        let mut cmd = Command::new(&python_path);
        cmd.env("PYROXIDE_WORKER", "1")
            .env("PYROXIDE_PARENT_PID", std::process::id().to_string())
            .args(["-m", "pyroxide.worker", "--socket", &socket_path]);

        let mut child = cmd
            .spawn()
            .map_err(|e| format!("Failed to spawn pyroxide worker child process: {e}"))?;

        listener
            .set_nonblocking(true)
            .map_err(|e| format!("Failed to set non-blocking on listener: {e}"))?;

        let mut stream = None;
        let start = std::time::Instant::now();
        let timeout = Duration::from_secs(get_startup_timeout_secs());

        while start.elapsed() < timeout {
            if let Ok(Some(status)) = child.try_wait() {
                return Err(format!(
                    "Worker child process exited on startup with status: {status}"
                ));
            }

            match listener.accept() {
                Ok(s) => {
                    stream = Some(s);
                    break;
                }
                Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                    std::thread::sleep(Duration::from_millis(50));
                }
                Err(e) => {
                    let _ = child.kill();
                    return Err(format!("Failed to accept local socket connection: {e}"));
                }
            }
        }

        let stream = match stream {
            Some(s) => s,
            None => {
                let _ = child.kill();
                let _ = child.wait();
                return Err("Timeout waiting for worker process to connect".to_string());
            }
        };

        stream
            .set_nonblocking(false)
            .map_err(|e| format!("Failed to set stream back to blocking: {e}"))?;

        let mut worker = IpcWorker {
            child,
            stream,
            socket_path,
            tasks_run: 0,
            registered_wasms: std::collections::HashSet::new(),
            registered_dylibs: std::collections::HashSet::new(),
            last_used: std::time::Instant::now(),
        };

        sync_registries(&mut worker)?;

        Ok(worker)
    }
}

fn sync_registries(worker: &mut IpcWorker) -> Result<(), String> {
    for (name, wasm_bytes) in crate::get_wasm_bytes() {
        send_registration_task(&mut worker.stream, 10, &name, &wasm_bytes)
            .map_err(|e| format!("Failed to sync WASM module {name} to worker: {e}"))?;
        worker.registered_wasms.insert(name);
    }

    for (name, path) in crate::get_dylib_paths() {
        send_registration_task(&mut worker.stream, 11, &name, path.as_bytes())
            .map_err(|e| format!("Failed to sync dylib {name} to worker: {e}"))?;
        worker.registered_dylibs.insert(name);
    }

    Ok(())
}

pub(crate) fn send_registration_task(
    stream: &mut LocalSocketStream,
    task_type: u8,
    metadata: &str,
    payload: &[u8],
) -> Result<(), String> {
    let mut header = vec![task_type, 0u8];
    header.extend_from_slice(&(metadata.len() as u32).to_be_bytes());
    header.extend_from_slice(&(payload.len() as u64).to_be_bytes());

    stream.write_all(&header).map_err(|e| e.to_string())?;
    stream
        .write_all(metadata.as_bytes())
        .map_err(|e| e.to_string())?;
    stream.write_all(payload).map_err(|e| e.to_string())?;
    stream.flush().map_err(|e| e.to_string())?;

    let mut res_header = [0u8; 10];
    stream
        .read_exact(&mut res_header)
        .map_err(|e| format!("Failed to read registration response header: {e}"))?;
    let success = res_header[0] == 1;
    let _res_flags = res_header[1];
    let data_len = u64::from_be_bytes(res_header[2..10].try_into().unwrap_or([0u8; 8])) as usize;

    let mut data = vec![0u8; data_len];
    stream.read_exact(&mut data).map_err(|e| e.to_string())?;

    if !success {
        return Err(String::from_utf8(data).unwrap_or_else(|_| "Unknown error".to_string()));
    }

    Ok(())
}

fn get_max_tasks_per_worker() -> usize {
    static VALUE: OnceLock<usize> = OnceLock::new();
    *VALUE.get_or_init(|| {
        std::env::var("PYROXIDE_MAX_TASKS_PER_WORKER")
            .ok()
            .and_then(|s| s.parse().ok())
            .unwrap_or(100)
    })
}

fn get_startup_timeout_secs() -> u64 {
    static VALUE: OnceLock<u64> = OnceLock::new();
    *VALUE.get_or_init(|| {
        std::env::var("PYROXIDE_WORKER_STARTUP_TIMEOUT_SEC")
            .ok()
            .and_then(|s| s.parse().ok())
            .unwrap_or(5)
    })
}

fn get_idle_timeout_secs() -> u64 {
    static VALUE: OnceLock<u64> = OnceLock::new();
    *VALUE.get_or_init(|| {
        std::env::var("PYROXIDE_IDLE_TIMEOUT_SEC")
            .ok()
            .and_then(|s| s.parse().ok())
            .unwrap_or(60)
    })
}

fn get_min_workers() -> usize {
    static VALUE: OnceLock<usize> = OnceLock::new();
    *VALUE.get_or_init(|| {
        std::env::var("PYROXIDE_MIN_WORKERS")
            .ok()
            .and_then(|s| s.parse().ok())
            .unwrap_or(0)
    })
}

fn spawn_idle_reaper(pool: Arc<IsolatedProcessPool>) {
    std::thread::spawn(move || {
        loop {
            // Wake up every 2 seconds to check
            std::thread::sleep(Duration::from_secs(2));

            let idle_timeout = Duration::from_secs(get_idle_timeout_secs());
            let min_workers = get_min_workers();

            let mut victims = Vec::new();

            if let Ok(mut workers_guard) = pool.workers.lock() {
                let now = std::time::Instant::now();
                let mut i = 0;
                while i < workers_guard.len() {
                    if workers_guard.len() > min_workers
                        && now.duration_since(workers_guard[i].last_used) > idle_timeout
                    {
                        let dead_worker = workers_guard.swap_remove(i);
                        victims.push(dead_worker);
                    } else {
                        i += 1;
                    }
                }
            } // Lock is dropped here

            for mut victim in victims {
                let _ = victim.child.kill();
                let _ = victim.child.wait();
            }
        }
    });
}
