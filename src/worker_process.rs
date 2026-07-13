use interprocess::local_socket::LocalSocketStream;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyModule};
use std::io::{Read, Write};

/// Start the high-performance worker IPC loop.
/// This connects to the master socket/pipe and executes incoming tasks.
pub fn start_worker_loop(socket_path: &str) -> Result<(), String> {
    let mut stream = LocalSocketStream::connect(socket_path)
        .map_err(|e| format!("Failed to connect to local socket {socket_path}: {e}"))?;

    // Keep track of the last response SHM so it stays alive until the broker reads it,
    // and is dropped when we start processing the next task or exit.
    let mut _last_response_shm: Option<shared_memory::Shmem> = None;

    loop {
        // Read Task Type (1 byte)
        let mut type_buf = [0u8; 1];
        if stream.read_exact(&mut type_buf).is_err() {
            // Stream closed, worker terminates gracefully
            break;
        }
        let task_type = type_buf[0];

        // Drop the previous response SHM now that the master has definitely finished reading it and started a new task
        _last_response_shm = None;

        // Read Flags (1 byte)
        let mut flags_buf = [0u8; 1];
        stream
            .read_exact(&mut flags_buf)
            .map_err(|e| format!("Failed to read flags: {e}"))?;
        let flags = flags_buf[0];

        // Read Extra Len (4 bytes)
        let mut extra_len_buf = [0u8; 4];
        stream
            .read_exact(&mut extra_len_buf)
            .map_err(|e| format!("Failed to read extra_len: {e}"))?;
        let extra_len = u32::from_be_bytes(extra_len_buf) as usize;

        // Read Payload Len (8 bytes)
        let mut payload_len_buf = [0u8; 8];
        stream
            .read_exact(&mut payload_len_buf)
            .map_err(|e| format!("Failed to read payload_len: {e}"))?;
        let payload_len = u64::from_be_bytes(payload_len_buf) as usize;

        // Read Extra Metadata Bytes
        let mut extra_bytes = vec![0u8; extra_len];
        stream
            .read_exact(&mut extra_bytes)
            .map_err(|e| format!("Failed to read extra bytes: {e}"))?;
        let metadata = String::from_utf8(extra_bytes).unwrap_or_default();

        // Read Payload Bytes
        let mut payload_bytes = vec![0u8; payload_len];
        stream
            .read_exact(&mut payload_bytes)
            .map_err(|e| format!("Failed to read payload bytes: {e}"))?;

        // Resolve SHM payload if flags say so
        let actual_payload = if (flags & 1) == 1 {
            let shm_name =
                String::from_utf8(payload_bytes).map_err(|e| format!("Invalid SHM name: {e}"))?;
            let shmem = shared_memory::ShmemConf::new()
                .os_id(&shm_name)
                .open()
                .map_err(|e| format!("Failed to open request SHM {shm_name}: {e}"))?;
            let ptr = shmem.as_ptr();
            let size = shmem.len();
            // Safety: SHM was just opened; data is copied into a Vec before shmem drops.
            unsafe { std::slice::from_raw_parts(ptr, size) }.to_vec()
        } else {
            payload_bytes
        };

        // Process Task
        let (success, response_bytes) = execute_worker_task(task_type, &metadata, actual_payload);

        let use_shm = success && response_bytes.len() >= crate::get_shm_threshold();
        let mut res_flags = 0u8;
        let mut actual_response = response_bytes.clone();
        let mut shm_to_keep = None;

        if use_shm {
            let shm_name = format!(
                "pyroxide_shm_res_{}_{}",
                std::process::id(),
                rand::random::<u32>()
            );
            match shared_memory::ShmemConf::new()
                .size(response_bytes.len())
                .os_id(&shm_name)
                .create()
            {
                Ok(shmem) => {
                    // Safety: valid, non-overlapping buffers of the same size.
                    unsafe {
                        std::ptr::copy_nonoverlapping(
                            response_bytes.as_ptr(),
                            shmem.as_ptr(),
                            response_bytes.len(),
                        );
                    }
                    res_flags |= 1;
                    actual_response = shm_name.into_bytes();
                    shm_to_keep = Some(shmem);
                }
                Err(_) => {
                    res_flags = 0;
                }
            }
        }

        // Write Response: [Success: 1 byte] [Flags: 1 byte] [Data Len: 8 bytes] [Data Bytes]
        let mut response_header = vec![if success { 1u8 } else { 0u8 }, res_flags];
        response_header.extend_from_slice(&(actual_response.len() as u64).to_be_bytes());

        stream
            .write_all(&response_header)
            .map_err(|e| format!("Failed to write response header: {e}"))?;
        stream
            .write_all(&actual_response)
            .map_err(|e| format!("Failed to write response data: {e}"))?;
        stream
            .flush()
            .map_err(|e| format!("Failed to flush stream: {e}"))?;

        if shm_to_keep.is_some() {
            // Wait for master's acknowledgment before we continue (so master has read the SHM safely)
            let mut ack = [0u8; 1];
            let _ = stream.read_exact(&mut ack);
            _last_response_shm = shm_to_keep;
        }
    }

    Ok(())
}

fn execute_worker_task(task_type: u8, metadata: &str, payload: Vec<u8>) -> (bool, Vec<u8>) {
    match task_type {
        0 => {
            // Python Callable Task (Metadata contains func description, payload is pickled func + arguments)
            let result = Python::attach(|py| -> PyResult<Vec<u8>> {
                let pickle = PyModule::import(py, "pickle")?;

                // Unpack metadata: "has_callable:bool"
                // For python tasks, payload is: (pickled_func, pickled_payload)
                let tuple: Bound<'_, pyo3::types::PyTuple> = pickle
                    .call_method1("loads", (PyBytes::new(py, &payload),))?
                    .extract()?;

                let pickled_func: Bound<'_, PyBytes> = tuple.get_item(0)?.extract()?;
                let pickled_arg: Bound<'_, PyBytes> = tuple.get_item(1)?.extract()?;

                let func: Py<PyAny> = pickle.call_method1("loads", (pickled_func,))?.unbind();
                let arg: Py<PyAny> = pickle.call_method1("loads", (pickled_arg,))?.unbind();

                // Run callable
                let bound_func = func.bind(py);
                let bound_arg = arg.bind(py);

                match bound_func.call1((bound_arg,)) {
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
        1 => {
            // WebAssembly Task (Metadata contains module_name:func_name, payload is raw input string/bytes)
            let parts: Vec<&str> = metadata.split(':').collect();
            if parts.len() < 2 {
                return (
                    false,
                    "Invalid WebAssembly metadata format"
                        .to_string()
                        .into_bytes(),
                );
            }
            let module_name = parts[0];
            let func_name = parts[1];

            // Setup WASM Engine & run sandboxed VM inside child process
            let processed = (|| -> Result<Vec<u8>, String> {
                let module = crate::get_wasm_module(module_name)
                    .ok_or_else(|| format!("WASM module '{module_name}' not registered"))?;

                let engine = crate::get_wasm_engine();
                let mut store = wasmtime::Store::new(engine, ());
                let linker = wasmtime::Linker::new(engine);
                let instance = linker
                    .instantiate(&mut store, &module)
                    .map_err(|e| format!("Failed to instantiate WASM: {e}"))?;

                let alloc_fn = instance
                    .get_typed_func::<i32, i32>(&mut store, "alloc")
                    .map_err(|e| format!("WASM missing export 'alloc': {e}"))?;
                let dealloc_fn = instance
                    .get_typed_func::<(i32, i32), ()>(&mut store, "dealloc")
                    .map_err(|e| format!("WASM missing export 'dealloc': {e}"))?;
                let run_fn = instance
                    .get_typed_func::<(i32, i32), i64>(&mut store, func_name)
                    .map_err(|e| format!("WASM missing export '{func_name}': {e}"))?;

                let memory = instance
                    .get_memory(&mut store, "memory")
                    .ok_or_else(|| "WASM missing export 'memory'".to_string())?;

                let input_len = payload.len() as i32;

                // Allocate guest memory
                let guest_ptr = alloc_fn
                    .call(&mut store, input_len)
                    .map_err(|e| format!("WASM alloc failed: {e}"))?;

                // Write payload into WASM linear memory
                memory
                    .write(&mut store, guest_ptr as usize, &payload)
                    .map_err(|e| format!("Failed to write to WASM memory: {e}"))?;

                // Run execution
                let packed_result = run_fn
                    .call(&mut store, (guest_ptr, input_len))
                    .map_err(|e| format!("WASM execution failed: {e}"))?;

                // Unpack pointer and length
                let out_ptr = (packed_result >> 32) as i32;
                let out_len = (packed_result & 0xFFFFFFFF) as i32;

                // Read output bytes
                let mut output_bytes = vec![0u8; out_len as usize];
                memory
                    .read(&store, out_ptr as usize, &mut output_bytes)
                    .map_err(|e| format!("Failed to read from WASM memory: {e}"))?;

                // Free memory in guest
                let _ = dealloc_fn.call(&mut store, (guest_ptr, input_len));
                let _ = dealloc_fn.call(&mut store, (out_ptr, out_len));

                Ok(output_bytes)
            })();

            match processed {
                Ok(bytes) => (true, bytes),
                Err(err) => (false, err.into_bytes()),
            }
        }
        2 => {
            // Dynamic Shared Library Task (Metadata contains plugin_name:symbol_name, payload is raw input string/bytes)
            let parts: Vec<&str> = metadata.split(':').collect();
            if parts.is_empty() {
                return (
                    false,
                    "Invalid dynamic library metadata format"
                        .to_string()
                        .into_bytes(),
                );
            }
            let plugin_name = parts[0];
            let symbol_name = if parts.len() > 1 {
                parts[1]
            } else {
                "pyroxide_plugin_run"
            };

            let processed = if parts.len() > 2 {
                let sig_part = parts[2];
                let sig_parts: Vec<&str> = sig_part.split('|').collect();
                if sig_parts.len() != 2 {
                    Err("Invalid FFI signature metadata format".to_string())
                } else {
                    let args: Vec<String> = sig_parts[0]
                        .split(',')
                        .map(|s| s.to_string())
                        .filter(|s| !s.is_empty())
                        .collect();
                    let ret = sig_parts[1];
                    crate::execute_dylib_ffi(plugin_name, symbol_name, &args, ret, &payload)
                }
            } else {
                crate::execute_dylib(plugin_name, symbol_name, &payload)
            };

            match processed {
                Ok(bytes) => (true, bytes),
                Err(err) => (false, err.into_bytes()),
            }
        }
        10 => {
            // Register WASM module in worker
            let module_name = metadata.to_string();
            match crate::register_wasm_module_internal(module_name, payload) {
                Ok(_) => (true, Vec::new()),
                Err(e) => (false, e.into_bytes()),
            }
        }
        11 => {
            // Register Dylib in worker
            let plugin_name = metadata.to_string();
            let library_path = String::from_utf8(payload).unwrap_or_default();
            match crate::register_dylib_internal(plugin_name, library_path) {
                Ok(_) => (true, Vec::new()),
                Err(e) => (false, e.into_bytes()),
            }
        }
        _ => (false, "Unknown task type".to_string().into_bytes()),
    }
}
