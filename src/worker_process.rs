use std::io::{Read, Write};
use std::net::TcpStream;

use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyModule};

#[cfg(unix)]
use std::os::unix::net::UnixStream;

enum IpcStream {
    #[cfg(unix)]
    Unix(UnixStream),
    Tcp(TcpStream),
}

impl Read for IpcStream {
    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
        match self {
            #[cfg(unix)]
            IpcStream::Unix(s) => s.read(buf),
            IpcStream::Tcp(s) => s.read(buf),
        }
    }
}

impl Write for IpcStream {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        match self {
            #[cfg(unix)]
            IpcStream::Unix(s) => s.write(buf),
            IpcStream::Tcp(s) => s.write(buf),
        }
    }

    fn flush(&mut self) -> std::io::Result<()> {
        match self {
            #[cfg(unix)]
            IpcStream::Unix(s) => s.flush(),
            IpcStream::Tcp(s) => s.flush(),
        }
    }
}

/// Start the high-performance worker IPC loop.
/// This connects to the master socket/pipe and executes incoming tasks.
pub fn start_worker_loop(socket_path: &str) -> Result<(), String> {
    let mut stream = if socket_path.starts_with("127.0.0.1:") {
        let tcp = TcpStream::connect(socket_path)
            .map_err(|e| format!("Failed to connect to TCP worker port: {e}"))?;
        IpcStream::Tcp(tcp)
    } else {
        #[cfg(unix)]
        {
            let unix = UnixStream::connect(socket_path)
                .map_err(|e| format!("Failed to connect to Unix socket {socket_path}: {e}"))?;
            IpcStream::Unix(unix)
        }
        #[cfg(not(unix))]
        {
            return Err("Unix domain sockets are not supported on this platform".to_string());
        }
    };

    loop {
        // Read Task Type (1 byte)
        let mut type_buf = [0u8; 1];
        if stream.read_exact(&mut type_buf).is_err() {
            // Stream closed, worker terminates gracefully
            break;
        }
        let task_type = type_buf[0];

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

        // Process Task
        let (success, response_bytes) = execute_worker_task(task_type, &metadata, payload_bytes);

        // Write Response: [Success: 1 byte] [Data Len: 8 bytes] [Data Bytes]
        let mut response_header = vec![if success { 1u8 } else { 0u8 }];
        response_header.extend_from_slice(&(response_bytes.len() as u64).to_be_bytes());

        stream
            .write_all(&response_header)
            .map_err(|e| format!("Failed to write response header: {e}"))?;
        stream
            .write_all(&response_bytes)
            .map_err(|e| format!("Failed to write response data: {e}"))?;
        stream
            .flush()
            .map_err(|e| format!("Failed to flush stream: {e}"))?;
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
            // Dynamic Shared Library Task (Metadata contains plugin_name, payload is raw input string/bytes)
            let plugin_name = metadata;
            let processed = crate::execute_dylib(plugin_name, &payload);

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
