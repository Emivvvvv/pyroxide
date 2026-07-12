use crate::broker::{Broker, TaskStatus};
use pyo3::prelude::*;
use std::sync::Arc;
use std::sync::atomic::Ordering;

enum NativePayload {
    Str(String),
    Bytes(Vec<u8>),
}

fn worker_loop(broker: Arc<Broker>, receiver: crossbeam_channel::Receiver<usize>) {
    while let Ok(task_id) = receiver.recv() {
        // 1. Get task from Slab using a read lock
        let task = {
            let slab = broker.tasks.read().unwrap();
            slab.get(task_id).cloned()
        };

        if let Some(task) = task {
            // Check cancellation before starting
            if task.cancelled.load(Ordering::Acquire) {
                continue;
            }

            // Try to transition status from Pending to Running. If it fails, task was cancelled.
            match task.status.compare_exchange(
                TaskStatus::Pending as u8,
                TaskStatus::Running as u8,
                Ordering::Release,
                Ordering::Acquire,
            ) {
                Ok(_) => {}
                Err(_) => {
                    continue;
                }
            }

            // 3. Execute the task (Python Callable or Native Execution) with panic safety
            let task_clone = Arc::clone(&task);

            let exec_result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(move || {
                // Simulate Rust worker panic inside the main binary for testing catch_unwind
                let should_panic = Python::attach(|py| {
                    let bound_payload = task_clone.payload.bind(py);
                    if let Ok(s) = bound_payload.extract::<String>() {
                        s == "TRIGGER_PANIC"
                    } else {
                        false
                    }
                });
                if should_panic {
                    panic!("Simulated Rust worker panic!");
                }

                if let Some(ref cb) = task_clone.callable {
                    // Execute Python Callable (requires GIL)
                    Python::attach(|py| {
                        let bound_cb = cb.bind(py);
                        let bound_payload = task_clone.payload.bind(py);

                        match bound_cb.call1((bound_payload,)) {
                            Ok(val) => Ok(val.into_any().unbind()),
                            Err(err) => {
                                let tb_str = match err.traceback(py) {
                                    Some(tb) => tb
                                        .format()
                                        .unwrap_or_else(|_| "No traceback available".to_string()),
                                    None => "No traceback available".to_string(),
                                };
                                Err(format!("{err}\n\nOriginal Background Traceback:\n{tb_str}"))
                            }
                        }
                    })
                } else if let Some(ref module_name) = task_clone.wasm_module {
                    // WASM Execution:
                    let func_name = task_clone
                        .wasm_func
                        .clone()
                        .unwrap_or_else(|| "run".to_string());

                    // 1. Extract payload with GIL held
                    let extracted = Python::attach(|py| {
                        let bound_payload = task_clone.payload.bind(py);
                        if let Ok(s) = bound_payload.extract::<String>() {
                            Ok(NativePayload::Str(s))
                        } else if let Ok(b) = bound_payload.extract::<Vec<u8>>() {
                            Ok(NativePayload::Bytes(b))
                        } else {
                            Err("WASM execution: Unsupported payload type".to_string())
                        }
                    });

                    // 2. Process payload inside WebAssembly engine without GIL
                    let processed = extracted.and_then(|payload| {
                        let input_bytes = match &payload {
                            NativePayload::Str(s) => s.as_bytes(),
                            NativePayload::Bytes(b) => b.as_slice(),
                        };

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
                            .get_typed_func::<(i32, i32), i64>(&mut store, &func_name)
                            .map_err(|e| format!("WASM missing export '{func_name}': {e}"))?;

                        let memory = instance
                            .get_memory(&mut store, "memory")
                            .ok_or_else(|| "WASM missing export 'memory'".to_string())?;

                        let input_len = input_bytes.len() as i32;

                        if task_clone.cancelled.load(Ordering::Acquire) {
                            return Err("Task cancelled".to_string());
                        }

                        // Allocate guest memory
                        let guest_ptr = alloc_fn
                            .call(&mut store, input_len)
                            .map_err(|e| format!("WASM alloc failed: {e}"))?;

                        // Write bytes into WASM linear memory
                        memory
                            .write(&mut store, guest_ptr as usize, input_bytes)
                            .map_err(|e| format!("Failed to write to WASM memory: {e}"))?;

                        if task_clone.cancelled.load(Ordering::Acquire) {
                            let _ = dealloc_fn.call(&mut store, (guest_ptr, input_len));
                            return Err("Task cancelled".to_string());
                        }

                        // Run execution
                        let packed_result = run_fn
                            .call(&mut store, (guest_ptr, input_len))
                            .map_err(|e| format!("WASM execution failed: {e}"))?;

                        // Unpack pointer and length
                        let out_ptr = (packed_result >> 32) as i32;
                        let out_len = (packed_result & 0xFFFFFFFF) as i32;

                        if task_clone.cancelled.load(Ordering::Acquire) {
                            let _ = dealloc_fn.call(&mut store, (guest_ptr, input_len));
                            let _ = dealloc_fn.call(&mut store, (out_ptr, out_len));
                            return Err("Task cancelled".to_string());
                        }

                        // Read output bytes
                        let mut output_bytes = vec![0u8; out_len as usize];
                        memory
                            .read(&store, out_ptr as usize, &mut output_bytes)
                            .map_err(|e| format!("Failed to read from WASM memory: {e}"))?;

                        // Free memory in guest
                        let _ = dealloc_fn.call(&mut store, (guest_ptr, input_len));
                        let _ = dealloc_fn.call(&mut store, (out_ptr, out_len));

                        match payload {
                            NativePayload::Str(_) => {
                                let s = String::from_utf8(output_bytes)
                                    .map_err(|e| format!("Invalid UTF-8 output from WASM: {e}"))?;
                                Ok(NativePayload::Str(s))
                            }
                            NativePayload::Bytes(_) => Ok(NativePayload::Bytes(output_bytes)),
                        }
                    });

                    // 3. Re-acquire GIL to construct the Python return value
                    Python::attach(|py| match processed {
                        Ok(NativePayload::Str(s)) => {
                            let py_str = pyo3::types::PyString::new(py, &s);
                            Ok(py_str.into_any().unbind())
                        }
                        Ok(NativePayload::Bytes(b)) => {
                            let py_bytes = pyo3::types::PyBytes::new(py, &b);
                            Ok(py_bytes.into_any().unbind())
                        }
                        Err(err) => Err(err),
                    })
                } else if let Some(ref plugin_name) = task_clone.dylib {
                    // Dynamic Shared Library (dylib) Execution:
                    // 1. Extract payload with GIL held
                    let extracted = Python::attach(|py| {
                        let bound_payload = task_clone.payload.bind(py);
                        if let Ok(s) = bound_payload.extract::<String>() {
                            Ok(NativePayload::Str(s))
                        } else if let Ok(b) = bound_payload.extract::<Vec<u8>>() {
                            Ok(NativePayload::Bytes(b))
                        } else {
                            Err("Dylib execution: Unsupported payload type".to_string())
                        }
                    });

                    // 2. Call dynamic library symbol without GIL
                    let processed = extracted.and_then(|payload| {
                        let input_bytes = match &payload {
                            NativePayload::Str(s) => s.as_bytes(),
                            NativePayload::Bytes(b) => b.as_slice(),
                        };

                        let registry = crate::get_dylib_registry()
                            .ok_or_else(|| "Dylib registry not initialized".to_string())?;
                        let map = registry
                            .read()
                            .map_err(|e| format!("Registry poisoned: {e}"))?;
                        let plugin = map
                            .get(plugin_name)
                            .ok_or_else(|| format!("Dylib '{plugin_name}' not registered"))?;

                        let mut out_len: usize = 0;

                        if task_clone.cancelled.load(Ordering::Acquire) {
                            return Err("Task cancelled".to_string());
                        }

                        // Execute the dynamic library function pointer directly
                        let out_ptr = unsafe {
                            (plugin.run_fn)(input_bytes.as_ptr(), input_bytes.len(), &mut out_len)
                        };

                        if out_ptr.is_null() {
                            return Err("Dylib execution returned null pointer".to_string());
                        }

                        let output_bytes =
                            unsafe { std::slice::from_raw_parts(out_ptr, out_len).to_vec() };

                        // Free the memory on the dynamic library's allocator
                        unsafe {
                            (plugin.free_fn)(out_ptr, out_len);
                        }

                        match payload {
                            NativePayload::Str(_) => {
                                let s = String::from_utf8(output_bytes)
                                    .map_err(|e| format!("Invalid UTF-8 output from dylib: {e}"))?;
                                Ok(NativePayload::Str(s))
                            }
                            NativePayload::Bytes(_) => Ok(NativePayload::Bytes(output_bytes)),
                        }
                    });

                    // 3. Re-acquire GIL to construct the Python return value
                    Python::attach(|py| match processed {
                        Ok(NativePayload::Str(s)) => {
                            let py_str = pyo3::types::PyString::new(py, &s);
                            Ok(py_str.into_any().unbind())
                        }
                        Ok(NativePayload::Bytes(b)) => {
                            let py_bytes = pyo3::types::PyBytes::new(py, &b);
                            Ok(py_bytes.into_any().unbind())
                        }
                        Err(err) => Err(err),
                    })
                } else {
                    Err(
                        "Invalid task configuration: no callable, wasm module, or dylib specified"
                            .to_string(),
                    )
                }
            }));

            let resolved_result = match exec_result {
                Ok(res) => res,
                Err(_) => Err("Rust worker panicked during task execution".to_string()),
            };

            // 4. Update result and status (preserving Cancelled status)
            let mut current = task.status.load(Ordering::Acquire);
            loop {
                if current == TaskStatus::Cancelled as u8 {
                    break;
                }
                let final_status = match &resolved_result {
                    Ok(_) => TaskStatus::Completed as u8,
                    Err(_) => TaskStatus::Failed as u8,
                };
                match task.status.compare_exchange_weak(
                    current,
                    final_status,
                    Ordering::Release,
                    Ordering::Acquire,
                ) {
                    Ok(_) => break,
                    Err(actual) => current = actual,
                }
            }

            {
                let mut res_guard = task.result.lock().unwrap();
                *res_guard = Some(resolved_result);
            }

            // 5. Signal the Condvar to wake up waiting Python thread
            {
                let mut completed = task.completed_mutex.lock().unwrap();
                *completed = true;
            }
            task.completed_cvar.notify_all();
        }
    }
}

pub(crate) fn spawn_workers(
    count: usize,
    broker: Arc<Broker>,
    receiver: crossbeam_channel::Receiver<usize>,
) -> Vec<std::thread::JoinHandle<()>> {
    (0..count)
        .map(|_| {
            let broker = broker.clone();
            let receiver = receiver.clone();

            std::thread::spawn(move || worker_loop(broker, receiver))
        })
        .collect()
}
