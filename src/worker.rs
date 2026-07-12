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
            // 2. Set status to Running atomically
            task.status
                .store(TaskStatus::Running as u8, Ordering::Release);

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
                            Err(err) => Err(format!("{err}")),
                        }
                    })
                } else {
                    // Native Execution (GIL-free)
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
                    let processed = extracted.map(|payload| match payload {
                        NativePayload::Str(s) => {
                            if s == "TRIGGER_PANIC" {
                                panic!("Simulated Rust worker panic!");
                            }
                            if let Some(stripped) = s.strip_prefix("SLEEP:") {
                                if let Ok(ms) = stripped.parse::<u64>() {
                                    std::thread::sleep(std::time::Duration::from_millis(ms));
                                }
                            } else {
                                std::thread::sleep(std::time::Duration::from_millis(1));
                            }
                            let upper = s.to_uppercase();
                            NativePayload::Str(upper)
                        }
                        NativePayload::Bytes(mut b) => {
                            std::thread::sleep(std::time::Duration::from_millis(1));
                            b.make_ascii_uppercase();
                            NativePayload::Bytes(b)
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

            // 4. Update result and status
            let final_status = match &resolved_result {
                Ok(_) => TaskStatus::Completed,
                Err(_) => TaskStatus::Failed,
            };

            {
                let mut res_guard = task.result.lock().unwrap();
                *res_guard = Some(resolved_result);
            }

            task.status.store(final_status as u8, Ordering::Release);

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
