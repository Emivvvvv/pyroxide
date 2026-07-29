use crate::ipc::ShmemGuard;
use crate::ipc::frame::{read_request, write_response};
use crate::ipc::protocol::{FrameFlags, RequestMetadata};
use interprocess::local_socket::LocalSocketStream;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyModule};
use std::io::Read;

#[cfg(unix)]
fn cleanup_socket_path(socket_path: &str) {
    let path = std::path::Path::new(socket_path);
    let _ = std::fs::remove_file(path);
    if path.file_name() == Some(std::ffi::OsStr::new("worker.sock")) {
        if let Some(parent) = path.parent() {
            let is_private_directory = parent
                .file_name()
                .and_then(std::ffi::OsStr::to_str)
                .is_some_and(|name| name.starts_with("pyroxide-ipc-"));
            if is_private_directory {
                let _ = std::fs::remove_dir(parent);
            }
        }
    }
}

#[cfg(target_os = "windows")]
// SAFETY: these declarations match the documented Win32 process API
// signatures and are called with handles and pointers checked below.
unsafe extern "system" {
    fn OpenProcess(
        dwDesiredAccess: u32,
        bInheritHandle: i32,
        dwProcessId: u32,
    ) -> *mut std::ffi::c_void;
    fn CloseHandle(hObject: *mut std::ffi::c_void) -> i32;
    fn GetExitCodeProcess(hProcess: *mut std::ffi::c_void, lpExitCode: *mut u32) -> i32;
    fn WaitForSingleObject(hHandle: *mut std::ffi::c_void, dwMilliseconds: u32) -> u32;
}

/// Start the high-performance worker IPC loop.
/// This connects to the master socket/pipe and executes incoming tasks.
pub fn start_worker_loop(socket_path: &str) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    {
        // SAFETY: `getppid` has no pointer arguments or caller preconditions.
        let ppid = unsafe { libc::getppid() };
        let socket_path = socket_path.to_string();
        // SAFETY: the kqueue descriptor and event buffers are initialized in
        // this closure and remain valid for each corresponding system call.
        std::thread::spawn(move || unsafe {
            let kq = libc::kqueue();
            if kq == -1 {
                std::process::exit(1);
            }
            let ke = libc::kevent {
                ident: ppid as usize,
                filter: libc::EVFILT_PROC,
                flags: libc::EV_ADD | libc::EV_ENABLE,
                fflags: libc::NOTE_EXIT,
                data: 0,
                udata: std::ptr::null_mut(),
            };
            libc::kevent(kq, &ke, 1, std::ptr::null_mut(), 0, std::ptr::null());
            let mut ke_out: libc::kevent = std::mem::zeroed();
            let res = libc::kevent(kq, std::ptr::null(), 0, &mut ke_out, 1, std::ptr::null());
            if res > 0 {
                cleanup_socket_path(&socket_path);
                std::process::exit(1);
            }
        });
    }

    #[cfg(all(unix, not(target_os = "macos")))]
    {
        // SAFETY: `getppid` has no pointer arguments or caller preconditions.
        let ppid = unsafe { libc::getppid() };
        let socket_path = socket_path.to_string();
        std::thread::spawn(move || {
            loop {
                std::thread::sleep(std::time::Duration::from_millis(500));
                // SAFETY: `getppid` has no pointer arguments or caller
                // preconditions.
                if unsafe { libc::getppid() } != ppid {
                    cleanup_socket_path(&socket_path);
                    std::process::exit(1);
                }
            }
        });
    }

    #[cfg(target_os = "windows")]
    {
        if let Ok(parent_pid_str) = std::env::var("PYROXIDE_PARENT_PID") {
            if let Ok(parent_pid) = parent_pid_str.parse::<u32>() {
                std::thread::spawn(move || {
                    // SAFETY: the Win32 calls use the parsed PID, validate the
                    // returned handle, and close that handle exactly once.
                    unsafe {
                        let handle = OpenProcess(0x00100000 /* SYNCHRONIZE */, 0, parent_pid);
                        if handle.is_null() {
                            std::process::exit(1);
                        }
                        WaitForSingleObject(handle, 0xFFFFFFFF);
                        CloseHandle(handle);
                        std::process::exit(1);
                    }
                });
            }
        }
    }

    let mut stream = LocalSocketStream::connect(socket_path)
        .map_err(|e| format!("Failed to connect to local socket {socket_path}: {e}"))?;

    // Keep track of the last response SHM so it stays alive until the broker reads it,
    // and is dropped when we start processing the next task or exit.
    let mut _last_response_shm: Option<ShmemGuard> = None;

    loop {
        // Drop the previous response SHM now that the master has definitely finished reading it and started a new task
        _last_response_shm = None;

        let Some((meta, flags, payload_bytes)) = read_request(&mut stream)? else {
            // Stream closed between tasks, so the worker terminates gracefully.
            break;
        };

        let request_shm = if flags.uses_shared_memory() {
            let shm_name = std::str::from_utf8(&payload_bytes)
                .map_err(|e| format!("Invalid SHM name: {e}"))?
                .to_owned();
            Some(
                ShmemGuard::open(&shm_name)
                    .map_err(|e| format!("Failed to open request SHM {shm_name}: {e}"))?,
            )
        } else {
            None
        };
        let actual_payload_slice: &[u8] = if let Some(guard) = request_shm.as_ref() {
            let size = crate::config::checked_ipc_len(
                guard.len() as u64,
                crate::config::get_max_ipc_frame_bytes(),
                "shared-memory payload",
            )?;
            &guard.as_slice()[..size]
        } else {
            &payload_bytes
        };

        // Process Task
        let (mut success, mut response_bytes) = execute_worker_task(&meta, actual_payload_slice);

        if let Err(error) = crate::config::checked_ipc_len(
            response_bytes.len() as u64,
            crate::config::get_max_ipc_frame_bytes(),
            "response",
        ) {
            success = false;
            response_bytes = error.into_bytes();
        }

        let use_shm = success && response_bytes.len() >= crate::config::get_shm_threshold();
        let mut response_flags = FrameFlags::inline();
        let mut actual_response = response_bytes.clone();
        let mut shm_to_keep: Option<ShmemGuard> = None;

        if use_shm {
            let shm_name = format!(
                "pyroxide_shm_res_{}_{}",
                std::process::id(),
                rand::random::<u32>()
            );
            match ShmemGuard::create(response_bytes.len(), &shm_name) {
                Ok(shmem) if shmem.copy_from_slice(&response_bytes).is_ok() => {
                    response_flags = FrameFlags::shared_memory();
                    actual_response = shm_name.into_bytes();
                    shm_to_keep = Some(shmem);
                }
                Ok(_) | Err(_) => {}
            }
        }

        write_response(&mut stream, success, response_flags, &actual_response)?;

        if shm_to_keep.is_some() {
            // Wait for master's acknowledgment before we continue (so master has read the SHM safely)
            let mut ack = [0u8; 1];
            let _ = stream.read_exact(&mut ack);
            _last_response_shm = shm_to_keep;
        }
    }

    Ok(())
}

fn execute_worker_task(meta: &RequestMetadata, payload: &[u8]) -> (bool, Vec<u8>) {
    match meta {
        RequestMetadata::Python => {
            // Python Callable Task
            let result = Python::attach(|py| -> PyResult<Vec<u8>> {
                let pickle = PyModule::import(py, "pickle")?;
                let tuple: Bound<'_, pyo3::types::PyTuple> = pickle
                    .call_method1("loads", (PyBytes::new(py, payload),))?
                    .extract()?;

                let func = tuple.get_item(0)?;
                let arg = tuple.get_item(1)?;

                match func.call1((arg,)) {
                    Ok(val) => {
                        let pickled_val = pickle.call_method1("dumps", (val,))?;
                        let bytes: Vec<u8> = pickled_val.extract()?;
                        Ok(bytes)
                    }
                    Err(err) => {
                        let tb_str = match err.traceback(py) {
                            Some(tb) => tb
                                .format()
                                .unwrap_or_else(|_| "No traceback available".to_string()),
                            None => "No traceback available".to_string(),
                        };
                        Err(pyo3::exceptions::PyValueError::new_err(format!(
                            "{err}\n\nOriginal Background Traceback:\n{tb_str}"
                        )))
                    }
                }
            });

            match result {
                Ok(bytes) => (true, bytes),
                Err(err) => (false, err.to_string().into_bytes()),
            }
        }
        RequestMetadata::Wasm {
            module,
            function,
            memory_limit,
            timeout_ms,
        } => {
            match crate::backends::wasm::execute_wasm_guest(
                module,
                function,
                payload,
                *memory_limit,
                *timeout_ms,
                None,
            ) {
                Ok(bytes) => (true, bytes),
                Err(err) => (false, err.into_bytes()),
            }
        }
        RequestMetadata::Dylib {
            plugin,
            symbol,
            signature,
        } => {
            let processed = if let Some((args, ret)) = signature {
                crate::backends::dylib::execute_dylib_ffi(
                    plugin,
                    symbol,
                    args,
                    ret.as_str(),
                    payload,
                )
            } else {
                crate::backends::dylib::execute_dylib(plugin, symbol, payload)
            };

            match processed {
                Ok(bytes) => (true, bytes),
                Err(err) => (false, err.into_bytes()),
            }
        }
        RequestMetadata::RegisterWasm { module } => {
            match crate::backends::wasm::register_wasm_module_internal(
                module.clone(),
                payload.to_vec(),
            ) {
                Ok(_) => (true, Vec::new()),
                Err(e) => (false, e.into_bytes()),
            }
        }
        RequestMetadata::RegisterDylib {
            plugin,
            library_path,
            free_fn_name,
        } => {
            match crate::backends::dylib::register_dylib_internal(
                plugin.clone(),
                library_path.clone(),
                free_fn_name.clone(),
            ) {
                Ok(_) => (true, Vec::new()),
                Err(e) => (false, e.into_bytes()),
            }
        }
        RequestMetadata::UnregisterDylib { plugin } => {
            match crate::backends::dylib::unregister_dylib_internal(plugin) {
                Ok(_) => (true, Vec::new()),
                Err(e) => (false, e.into_bytes()),
            }
        }
    }
}
