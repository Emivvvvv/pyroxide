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

pub(crate) fn get_process_pool() -> Arc<IsolatedProcessPool> {
    PROCESS_POOL
        .get_or_init(|| {
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
            let mut guard = self.workers.lock().unwrap();
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

    /// Releases a worker back to the pool, or recycles/disposes it if limits are exceeded.
    pub fn release_worker(&self, mut worker: IpcWorker) {
        // Recycle worker after 100 tasks to avoid memory leaks
        if worker.tasks_run >= 100 {
            drop(worker); // kills child process and cleans socket file
            return;
        }

        // Check if worker child is still alive
        if let Ok(None) = worker.child.try_wait() {
            worker.last_used = std::time::Instant::now();
            let mut guard = self.workers.lock().unwrap();
            guard.push(worker);
        } else {
            drop(worker);
        }
    }

    fn spawn_new_worker(&self) -> Result<IpcWorker, String> {
        let python_path = pyo3::Python::attach(|py| -> String {
            let sys = py.import("sys").unwrap();
            sys.getattr("executable").unwrap().extract().unwrap()
        });

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

        let mut child = Command::new(&python_path)
            .env("PYROXIDE_WORKER", "1")
            .args(["-m", "pyroxide.worker", "--socket", &socket_path])
            .spawn()
            .map_err(|e| format!("Failed to spawn pyroxide worker child process: {e}"))?;

        listener
            .set_nonblocking(true)
            .map_err(|e| format!("Failed to set non-blocking on listener: {e}"))?;

        let mut stream = None;
        let start = std::time::Instant::now();
        let timeout = Duration::from_secs(5);

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
    let data_len = u64::from_be_bytes(res_header[2..10].try_into().unwrap()) as usize;

    let mut data = vec![0u8; data_len];
    stream.read_exact(&mut data).map_err(|e| e.to_string())?;

    if !success {
        return Err(String::from_utf8(data).unwrap_or_else(|_| "Unknown error".to_string()));
    }

    Ok(())
}

fn spawn_idle_reaper(pool: Arc<IsolatedProcessPool>) {
    std::thread::spawn(move || {
        loop {
            // Wake up every 2 seconds to check
            std::thread::sleep(Duration::from_secs(2));

            let timeout_secs = std::env::var("PYROXIDE_IDLE_TIMEOUT_SEC")
                .ok()
                .and_then(|s| s.parse::<u64>().ok())
                .unwrap_or(60);
            let idle_timeout = Duration::from_secs(timeout_secs);

            let mut victims = Vec::new();

            if let Ok(mut workers_guard) = pool.workers.lock() {
                let now = std::time::Instant::now();
                let mut i = 0;
                while i < workers_guard.len() {
                    if now.duration_since(workers_guard[i].last_used) > idle_timeout {
                        let dead_worker = workers_guard.remove(i);
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
