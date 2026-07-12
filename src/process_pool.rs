use pyo3::types::PyAnyMethods;
use std::io::{Read, Write};
use std::process::{Child, Command};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Duration;

#[cfg(unix)]
use std::os::unix::net::{UnixListener, UnixStream};

#[cfg(not(unix))]
use std::net::{TcpListener, TcpStream};

pub(crate) enum IpcStream {
    #[cfg(unix)]
    Unix(UnixStream),
    #[cfg(not(unix))]
    Tcp(TcpStream),
}

impl Read for IpcStream {
    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
        match self {
            #[cfg(unix)]
            IpcStream::Unix(s) => s.read(buf),
            #[cfg(not(unix))]
            IpcStream::Tcp(s) => s.read(buf),
        }
    }
}

impl Write for IpcStream {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        match self {
            #[cfg(unix)]
            IpcStream::Unix(s) => s.write(buf),
            #[cfg(not(unix))]
            IpcStream::Tcp(s) => s.write(buf),
        }
    }

    fn flush(&mut self) -> std::io::Result<()> {
        match self {
            #[cfg(unix)]
            IpcStream::Unix(s) => s.flush(),
            #[cfg(not(unix))]
            IpcStream::Tcp(s) => s.flush(),
        }
    }
}

pub(crate) struct IpcWorker {
    pub(crate) child: Child,
    pub(crate) stream: IpcStream,
    pub(crate) socket_path: String,
    pub(crate) tasks_run: usize,
    pub(crate) registered_wasms: std::collections::HashSet<String>,
    pub(crate) registered_dylibs: std::collections::HashSet<String>,
}

impl Drop for IpcWorker {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
        #[cfg(unix)]
        {
            if !self.socket_path.starts_with("127.0.0.1:") {
                let _ = std::fs::remove_file(&self.socket_path);
            }
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
            Arc::new(IsolatedProcessPool {
                workers: Mutex::new(Vec::new()),
            })
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

        #[cfg(unix)]
        {
            let socket_path = format!("/tmp/pyro3_ipc_{rand_num}.sock");
            let _ = std::fs::remove_file(&socket_path);

            let listener = UnixListener::bind(&socket_path)
                .map_err(|e| format!("Failed to bind Unix socket {socket_path}: {e}"))?;

            let child = Command::new(&python_path)
                .env("PYROXIDE_WORKER", "1")
                .args(["-m", "pyroxide.worker", "--socket", &socket_path])
                .spawn()
                .map_err(|e| format!("Failed to spawn pyroxide worker child process: {e}"))?;

            // Accept connection from child
            let (stream, _) = listener
                .accept()
                .map_err(|e| format!("Failed to accept Unix worker connection: {e}"))?;
            let _ = stream.set_read_timeout(Some(Duration::from_secs(60)));

            let mut worker = IpcWorker {
                child,
                stream: IpcStream::Unix(stream),
                socket_path,
                tasks_run: 0,
                registered_wasms: std::collections::HashSet::new(),
                registered_dylibs: std::collections::HashSet::new(),
            };

            sync_registries(&mut worker)?;

            Ok(worker)
        }

        #[cfg(not(unix))]
        {
            // Windows TCP fallback
            let listener = TcpListener::bind("127.0.0.1:0")
                .map_err(|e| format!("Failed to bind TCP listener: {e}"))?;
            let local_addr = listener.local_addr().unwrap();
            let socket_path = format!("127.0.0.1:{}", local_addr.port());

            let child = Command::new(&python_path)
                .env("PYROXIDE_WORKER", "1")
                .args(["-m", "pyroxide.worker", "--socket", &socket_path])
                .spawn()
                .map_err(|e| format!("Failed to spawn pyroxide worker child process: {e}"))?;

            let (stream, _) = listener
                .accept()
                .map_err(|e| format!("Failed to accept TCP worker connection: {e}"))?;
            let _ = stream.set_read_timeout(Some(Duration::from_secs(60)));

            let mut worker = IpcWorker {
                child,
                stream: IpcStream::Tcp(stream),
                socket_path,
                tasks_run: 0,
                registered_wasms: std::collections::HashSet::new(),
                registered_dylibs: std::collections::HashSet::new(),
            };

            sync_registries(&mut worker)?;

            Ok(worker)
        }
    }
}

fn sync_registries(worker: &mut IpcWorker) -> Result<(), String> {
    // Sync WASM modules
    for (name, wasm_bytes) in crate::get_wasm_bytes() {
        send_registration_task(&mut worker.stream, 10, &name, &wasm_bytes)
            .map_err(|e| format!("Failed to sync WASM module {name} to worker: {e}"))?;
        worker.registered_wasms.insert(name);
    }

    // Sync dylibs
    for (name, path) in crate::get_dylib_paths() {
        send_registration_task(&mut worker.stream, 11, &name, path.as_bytes())
            .map_err(|e| format!("Failed to sync dylib {name} to worker: {e}"))?;
        worker.registered_dylibs.insert(name);
    }

    Ok(())
}

pub(crate) fn send_registration_task(
    stream: &mut IpcStream,
    task_type: u8,
    metadata: &str,
    payload: &[u8],
) -> Result<(), String> {
    let mut header = vec![task_type];
    header.extend_from_slice(&(metadata.len() as u32).to_be_bytes());
    header.extend_from_slice(&(payload.len() as u64).to_be_bytes());

    stream.write_all(&header).map_err(|e| e.to_string())?;
    stream
        .write_all(metadata.as_bytes())
        .map_err(|e| e.to_string())?;
    stream.write_all(payload).map_err(|e| e.to_string())?;
    stream.flush().map_err(|e| e.to_string())?;

    // Read response: [Success: 1 byte] [Data Len: 8 bytes]
    let mut res_header = [0u8; 9];
    stream
        .read_exact(&mut res_header)
        .map_err(|e| format!("Failed to read registration response header: {e}"))?;
    let success = res_header[0] == 1;
    let data_len = u64::from_be_bytes(res_header[1..9].try_into().unwrap()) as usize;

    let mut data = vec![0u8; data_len];
    stream.read_exact(&mut data).map_err(|e| e.to_string())?;

    if !success {
        return Err(String::from_utf8(data).unwrap_or_else(|_| "Unknown error".to_string()));
    }

    Ok(())
}
