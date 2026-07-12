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
                } else {
                    // Native Execution:
                    // 1. Extract payload with GIL held
                    let extracted = Python::attach(|py| {
                        let bound_payload = task_clone.payload.bind(py);
                        if let Ok(s) = bound_payload.extract::<String>() {
                            Ok(NativePayload::Str(s))
                        } else if let Ok(b) = bound_payload.extract::<Vec<u8>>() {
                            Ok(NativePayload::Bytes(b))
                        } else {
                            Err("Native execution: Unsupported payload type".to_string())
                        }
                    });

                    // 2. Process payload without GIL (outside Python::attach)
                    let processed = extracted.and_then(|payload| match payload {
                        NativePayload::Str(s) => {
                            if s == "TRIGGER_PANIC" {
                                panic!("Simulated Rust worker panic!");
                            }
                            if let Some(stripped) = s.strip_prefix("SLEEP:") {
                                if let Ok(ms) = stripped.parse::<u64>() {
                                    let sleep_chunk = std::time::Duration::from_millis(10);
                                    let mut elapsed = std::time::Duration::ZERO;
                                    let total = std::time::Duration::from_millis(ms);
                                    while elapsed < total {
                                        if task_clone.cancelled.load(Ordering::Acquire) {
                                            return Err("Task cancelled".to_string());
                                        }
                                        std::thread::sleep(sleep_chunk);
                                        elapsed += sleep_chunk;
                                    }
                                }
                            } else {
                                std::thread::sleep(std::time::Duration::from_millis(1));
                            }
                            if task_clone.cancelled.load(Ordering::Acquire) {
                                return Err("Task cancelled".to_string());
                            }
                            let upper = s.to_uppercase();
                            Ok(NativePayload::Str(upper))
                        }
                        NativePayload::Bytes(mut b) => {
                            std::thread::sleep(std::time::Duration::from_millis(1));
                            if task_clone.cancelled.load(Ordering::Acquire) {
                                return Err("Task cancelled".to_string());
                            }
                            b.make_ascii_uppercase();
                            Ok(NativePayload::Bytes(b))
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
