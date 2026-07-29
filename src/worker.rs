use crate::broker::{Broker, QueueAdmission};
use crate::task::{Task, TaskKind, TaskStatus};
use pyo3::prelude::*;
use std::cell::Cell;
use std::sync::atomic::Ordering;
use std::sync::{Arc, atomic::AtomicBool};

thread_local! {
    static IN_PROCESS_WORKER: Cell<bool> = const { Cell::new(false) };
}

struct WorkerContext;

impl WorkerContext {
    fn enter() -> Self {
        IN_PROCESS_WORKER.set(true);
        Self
    }
}

impl Drop for WorkerContext {
    fn drop(&mut self) {
        IN_PROCESS_WORKER.set(false);
    }
}

pub(crate) fn is_in_process_worker() -> bool {
    IN_PROCESS_WORKER.get()
}

fn record_shutdown_cancellation(broker: &Broker, task_id: usize, task: &Arc<Task>) {
    task.cancelled.store(true, Ordering::Release);
    broker.cancelled_count.fetch_add(1, Ordering::Relaxed);
    *task
        .result
        .lock()
        .unwrap_or_else(|error| error.into_inner()) = Some(Err("Task cancelled".to_string()));
    *task
        .completed_mutex
        .lock()
        .unwrap_or_else(|error| error.into_inner()) = true;
    task.completed_cvar.notify_all();
    #[cfg(unix)]
    crate::async_waker::notify_waker(task_id);

    if task.autofree.load(Ordering::Acquire) {
        crate::broker::free_task(task_id);
    }
}

fn cancel_pending_for_shutdown(broker: &Broker, task_id: usize, task: &Arc<Task>) -> bool {
    if task
        .status
        .compare_exchange(
            TaskStatus::Pending as u8,
            TaskStatus::Cancelled as u8,
            Ordering::SeqCst,
            Ordering::SeqCst,
        )
        .is_err()
    {
        return false;
    }

    record_shutdown_cancellation(broker, task_id, task);
    true
}

#[cfg(any(test, debug_assertions))]
struct StartClaimHook {
    reached: std::sync::Barrier,
    resume: std::sync::Barrier,
    reached_isolated_loop: std::sync::Mutex<Option<bool>>,
}

#[cfg(any(test, debug_assertions))]
static START_CLAIM_HOOK: std::sync::OnceLock<std::sync::Mutex<Option<Arc<StartClaimHook>>>> =
    std::sync::OnceLock::new();

#[cfg(test)]
fn install_start_claim_hook(hook: Option<Arc<StartClaimHook>>) {
    *START_CLAIM_HOOK
        .get_or_init(|| std::sync::Mutex::new(None))
        .lock()
        .unwrap_or_else(|error| error.into_inner()) = hook;
}

#[cfg(any(test, debug_assertions))]
fn pause_before_start_claim(isolated_loop: bool) {
    let hook = START_CLAIM_HOOK
        .get_or_init(|| std::sync::Mutex::new(None))
        .lock()
        .unwrap_or_else(|error| error.into_inner())
        .clone();
    if let Some(hook) = hook {
        *hook
            .reached_isolated_loop
            .lock()
            .unwrap_or_else(|error| error.into_inner()) = Some(isolated_loop);
        hook.reached.wait();
        hook.resume.wait();
    }
}

#[cfg(debug_assertions)]
pub(crate) fn arm_start_claim_test_hook() -> Result<(), String> {
    let mut installed = START_CLAIM_HOOK
        .get_or_init(|| std::sync::Mutex::new(None))
        .lock()
        .unwrap_or_else(|error| error.into_inner());
    if installed.is_some() {
        return Err("start-claim test hook is already armed".to_string());
    }
    *installed = Some(Arc::new(StartClaimHook {
        reached: std::sync::Barrier::new(2),
        resume: std::sync::Barrier::new(2),
        reached_isolated_loop: std::sync::Mutex::new(None),
    }));
    Ok(())
}

#[cfg(debug_assertions)]
pub(crate) fn wait_start_claim_test_hook() -> Result<bool, String> {
    let hook = START_CLAIM_HOOK
        .get_or_init(|| std::sync::Mutex::new(None))
        .lock()
        .unwrap_or_else(|error| error.into_inner())
        .clone()
        .ok_or_else(|| "start-claim test hook is not armed".to_string())?;
    hook.reached.wait();
    hook.reached_isolated_loop
        .lock()
        .unwrap_or_else(|error| error.into_inner())
        .ok_or_else(|| "start-claim test hook did not record a worker loop".to_string())
}

#[cfg(debug_assertions)]
pub(crate) fn resume_start_claim_test_hook() -> Result<(), String> {
    let hook = START_CLAIM_HOOK
        .get_or_init(|| std::sync::Mutex::new(None))
        .lock()
        .unwrap_or_else(|error| error.into_inner())
        .take()
        .ok_or_else(|| "start-claim test hook is not armed".to_string())?;
    hook.resume.wait();
    Ok(())
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum StartClaimResult {
    Started,
    CancelledForShutdown,
    Unavailable,
}

fn transition_pending_to_running(
    status: &std::sync::atomic::AtomicU8,
    cancel_pending_on_shutdown: &AtomicBool,
    _isolated_loop: bool,
) -> StartClaimResult {
    #[cfg(any(test, debug_assertions))]
    pause_before_start_claim(_isolated_loop);

    match status.compare_exchange(
        TaskStatus::Pending as u8,
        TaskStatus::Running as u8,
        Ordering::SeqCst,
        Ordering::SeqCst,
    ) {
        Ok(_) => {
            // The claim, shutdown store, and this check have one global order.
            // A shutdown store ordered before the claim is therefore visible
            // here, and the not-yet-executed task is cancelled.
            if cancel_pending_on_shutdown.load(Ordering::SeqCst)
                && status
                    .compare_exchange(
                        TaskStatus::Running as u8,
                        TaskStatus::Cancelled as u8,
                        Ordering::SeqCst,
                        Ordering::SeqCst,
                    )
                    .is_ok()
            {
                StartClaimResult::CancelledForShutdown
            } else {
                StartClaimResult::Started
            }
        }
        Err(_) => StartClaimResult::Unavailable,
    }
}

fn claim_pending_for_execution(
    broker: &Broker,
    task_id: usize,
    task: &Arc<Task>,
    cancel_pending_on_shutdown: &AtomicBool,
    isolated_loop: bool,
) -> bool {
    if cancel_pending_on_shutdown.load(Ordering::SeqCst)
        && cancel_pending_for_shutdown(broker, task_id, task)
    {
        return false;
    }
    if task.cancelled.load(Ordering::Acquire) {
        if task.autofree.load(Ordering::Acquire) {
            crate::broker::free_task(task_id);
        }
        return false;
    }

    match transition_pending_to_running(&task.status, cancel_pending_on_shutdown, isolated_loop) {
        StartClaimResult::Started => {
            broker.running_count.fetch_add(1, Ordering::Relaxed);
            true
        }
        StartClaimResult::CancelledForShutdown => {
            record_shutdown_cancellation(broker, task_id, task);
            false
        }
        StartClaimResult::Unavailable => {
            if task.autofree.load(Ordering::Acquire) {
                crate::broker::free_task(task_id);
            }
            false
        }
    }
}

pub(crate) enum NativePayload {
    Str(String),
    Bytes(Vec<u8>),
}

pub(crate) use crate::ipc::ShmemGuard;

fn worker_loop(
    broker: Arc<Broker>,
    receiver: crossbeam_channel::Receiver<usize>,
    admission: Arc<QueueAdmission>,
    cancel_pending_on_shutdown: Arc<AtomicBool>,
) {
    let _worker_context = WorkerContext::enter();
    while let Ok(task_id) = receiver.recv() {
        if task_id == crate::broker::STOP_TASK_ID {
            break;
        }
        admission.release(1);

        // 1. Get task from Slab using a read lock
        let task = broker.tasks.get(task_id).map(|e| Arc::clone(&*e));

        if let Some(task) = task {
            if !claim_pending_for_execution(
                &broker,
                task_id,
                &task,
                &cancel_pending_on_shutdown,
                false,
            ) {
                continue;
            }

            // Route isolated tasks to the process pool
            if task.isolated {
                let task_clone = Arc::clone(&task);
                execute_isolated_task(task_id, &task_clone);
                continue;
            }

            // 3. Execute the task (Python Callable or Native Execution) with panic safety.
            //
            // NOTE: catch_unwind only catches Rust panics, NOT native crashes (e.g. SIGSEGV
            // from a misbehaving C/Rust FFI plugin). True crash containment for native
            // code requires running in isolated mode (task.isolated = true), which routes
            // execution to a separate child process via the process pool.
            let task_clone = Arc::clone(&task);

            let exec_result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(move || {
                // Simulate Rust worker panic for testing catch_unwind.
                // Only active when PYROXIDE_PANIC_TRIGGER env var is set.
                if std::env::var("PYROXIDE_PANIC_TRIGGER").is_ok() {
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
                }

                match &task_clone.kind {
                    TaskKind::PythonCall { callable } => Python::attach(|py| {
                        let bound_cb = callable.bind(py);
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
                    }),
                    TaskKind::Wasm {
                        module: module_name,
                        function: func_name,
                        memory_limit_bytes,
                        timeout_ms,
                    } => {
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

                        let processed = extracted.and_then(|payload| {
                            let input_bytes = match &payload {
                                NativePayload::Str(s) => s.as_bytes(),
                                NativePayload::Bytes(b) => b.as_slice(),
                            };

                            let limit_bytes = memory_limit_bytes
                                .unwrap_or_else(crate::config::get_wasm_memory_limit_bytes);
                            let timeout_ms =
                                timeout_ms.unwrap_or_else(crate::config::get_wasm_timeout_ms);

                            let cancel_checker = || task_clone.cancelled.load(Ordering::Acquire);
                            let output_bytes = crate::backends::wasm::execute_wasm_guest(
                                module_name,
                                func_name,
                                input_bytes,
                                limit_bytes,
                                timeout_ms,
                                Some(&cancel_checker),
                            )?;

                            match payload {
                                NativePayload::Str(_) => {
                                    let s = String::from_utf8(output_bytes).map_err(|e| {
                                        format!("Invalid UTF-8 output from WASM: {e}")
                                    })?;
                                    Ok(NativePayload::Str(s))
                                }
                                NativePayload::Bytes(_) => Ok(NativePayload::Bytes(output_bytes)),
                            }
                        });

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
                    TaskKind::Dylib {
                        plugin: plugin_name,
                        symbol: symbol_name,
                        ffi_sig,
                    } => {
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

                        let processed = extracted.and_then(|payload| {
                            let input_bytes = match &payload {
                                NativePayload::Str(s) => s.as_bytes(),
                                NativePayload::Bytes(b) => b.as_slice(),
                            };

                            if task_clone.cancelled.load(Ordering::Acquire) {
                                return Err("Task cancelled".to_string());
                            }

                            let output_bytes = if let Some(sig) = ffi_sig {
                                crate::backends::dylib::execute_dylib_ffi(
                                    plugin_name,
                                    symbol_name,
                                    &sig.0,
                                    &sig.1,
                                    input_bytes,
                                )?
                            } else {
                                crate::backends::dylib::execute_dylib(
                                    plugin_name,
                                    symbol_name,
                                    input_bytes,
                                )?
                            };

                            match payload {
                                NativePayload::Str(_) => {
                                    let s = String::from_utf8(output_bytes).map_err(|e| {
                                        format!("Invalid UTF-8 output from dylib: {e}")
                                    })?;
                                    Ok(NativePayload::Str(s))
                                }
                                NativePayload::Bytes(_) => Ok(NativePayload::Bytes(output_bytes)),
                            }
                        });

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
                }
            }));

            let resolved_result = match exec_result {
                Ok(res) => res,
                Err(_) => Err("Rust worker panicked during task execution".to_string()),
            };

            let final_status = match &resolved_result {
                Ok(_) => TaskStatus::Completed as u8,
                Err(_) => TaskStatus::Failed as u8,
            };

            // 4. Store result FIRST, then update status.
            //    This ensures when a reader sees Completed/Failed, the result
            //    is already available. If cancel_task wins the CAS race below,
            //    it will overwrite the result with "Task cancelled".
            {
                let mut res_guard = task.result.lock().unwrap_or_else(|e| e.into_inner());
                if task.status.load(Ordering::Acquire) != TaskStatus::Cancelled as u8 {
                    *res_guard = Some(resolved_result);
                }
            }

            // 5. Update status (preserving Cancelled status)
            let mut current = task.status.load(Ordering::Acquire);
            loop {
                if current == TaskStatus::Cancelled as u8 {
                    break;
                }
                match task.status.compare_exchange_weak(
                    current,
                    final_status,
                    Ordering::Release,
                    Ordering::Acquire,
                ) {
                    Ok(_) => {
                        broker.record_task_completion(final_status);
                        break;
                    }
                    Err(actual) => current = actual,
                }
            }
            broker.running_count.fetch_sub(1, Ordering::Relaxed);

            // 6. Signal the Condvar to wake up waiting Python thread
            {
                let mut completed = task
                    .completed_mutex
                    .lock()
                    .unwrap_or_else(|e| e.into_inner());
                *completed = true;
            }
            task.completed_cvar.notify_all();
            #[cfg(unix)]
            crate::async_waker::notify_waker(task_id);

            // Auto-free task if requested by TaskHandle.__del__
            if task.autofree.load(Ordering::Acquire) {
                crate::broker::free_task(task_id);
            }
        }
    }
}

fn isolated_worker_loop(
    broker: Arc<Broker>,
    receiver: crossbeam_channel::Receiver<usize>,
    admission: Arc<QueueAdmission>,
    cancel_pending_on_shutdown: Arc<AtomicBool>,
) {
    while let Ok(task_id) = receiver.recv() {
        if task_id == crate::broker::STOP_TASK_ID {
            break;
        }
        admission.release(1);

        let task = broker.tasks.get(task_id).map(|entry| Arc::clone(&*entry));
        let Some(task) = task else {
            continue;
        };

        if !claim_pending_for_execution(&broker, task_id, &task, &cancel_pending_on_shutdown, true)
        {
            continue;
        }

        let task_clone = Arc::clone(&task);
        execute_isolated_task(task_id, &task_clone);
    }
}

pub(crate) fn spawn_workers(
    count: usize,
    broker: Arc<Broker>,
    receiver: crossbeam_channel::Receiver<usize>,
    admission: Arc<QueueAdmission>,
    cancel_pending_on_shutdown: Arc<AtomicBool>,
) -> Vec<std::thread::JoinHandle<()>> {
    (0..count)
        .map(|_| {
            let broker = broker.clone();
            let receiver = receiver.clone();
            let admission = Arc::clone(&admission);
            let cancel_pending_on_shutdown = Arc::clone(&cancel_pending_on_shutdown);

            std::thread::spawn(move || {
                worker_loop(broker, receiver, admission, cancel_pending_on_shutdown)
            })
        })
        .collect()
}

pub(crate) fn spawn_isolated_workers(
    count: usize,
    broker: Arc<Broker>,
    receiver: crossbeam_channel::Receiver<usize>,
    admission: Arc<QueueAdmission>,
    cancel_pending_on_shutdown: Arc<AtomicBool>,
) -> Vec<std::thread::JoinHandle<()>> {
    (0..count)
        .map(|_| {
            let broker = Arc::clone(&broker);
            let receiver = receiver.clone();
            let admission = Arc::clone(&admission);
            let cancel_pending_on_shutdown = Arc::clone(&cancel_pending_on_shutdown);

            std::thread::spawn(move || {
                isolated_worker_loop(broker, receiver, admission, cancel_pending_on_shutdown)
            })
        })
        .collect()
}

#[cfg(unix)]
fn wait_readable(
    stream: &interprocess::local_socket::LocalSocketStream,
    timeout_ms: i32,
) -> Result<bool, std::io::Error> {
    use std::os::fd::AsRawFd;
    let fd = stream.as_raw_fd();
    let mut fds = libc::pollfd {
        fd,
        events: libc::POLLIN,
        revents: 0,
    };
    // SAFETY: `fds` points to one initialized `pollfd` for this call.
    let ret = unsafe { libc::poll(&mut fds, 1, timeout_ms) };
    if ret < 0 {
        Err(std::io::Error::last_os_error())
    } else {
        Ok(ret > 0)
    }
}

use pyo3::types::PyBytes;

fn execute_isolated_task(task_id: usize, task: &Arc<Task>) {
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        execute_isolated_task_inner(task)
    }))
    .unwrap_or_else(|_| Err("Rust isolated coordinator panicked".to_string()));

    let final_status = match &result {
        Ok(_) => TaskStatus::Completed as u8,
        Err(_) => TaskStatus::Failed as u8,
    };

    // Store result FIRST, then update status (see worker_loop for rationale).
    {
        let mut res_guard = task.result.lock().unwrap_or_else(|e| e.into_inner());
        if task.status.load(Ordering::Acquire) != TaskStatus::Cancelled as u8 {
            *res_guard = Some(result);
        }
    }

    let mut current = task.status.load(Ordering::Acquire);
    loop {
        if current == TaskStatus::Cancelled as u8 {
            break;
        }
        match task.status.compare_exchange_weak(
            current,
            final_status,
            Ordering::Release,
            Ordering::Acquire,
        ) {
            Ok(_) => {
                let engine = crate::broker::get_engine();
                engine.broker.record_task_completion(final_status);
                break;
            }
            Err(actual) => current = actual,
        }
    }

    let engine = crate::broker::get_engine();
    engine.broker.running_count.fetch_sub(1, Ordering::Relaxed);

    // Signal completion only after all observable task counters are final.
    {
        let mut completed = task
            .completed_mutex
            .lock()
            .unwrap_or_else(|e| e.into_inner());
        *completed = true;
    }
    task.completed_cvar.notify_all();
    #[cfg(unix)]
    crate::async_waker::notify_waker(task_id);

    // Auto-free task if requested by TaskHandle.__del__
    if task.autofree.load(Ordering::Acquire) {
        crate::broker::free_task(task_id);
    }
}

fn execute_isolated_task_inner(task: &Arc<Task>) -> Result<Py<PyAny>, String> {
    use std::io::{Read, Write};

    match &task.kind {
        TaskKind::Wasm {
            module: wasm_module,
            ..
        } => {
            if !crate::registry::has_wasm_registration(wasm_module) {
                return Err(format!("WASM module '{wasm_module}' not found in registry"));
            }
        }
        TaskKind::Dylib {
            plugin: plugin_name,
            ..
        } => {
            if !crate::registry::has_dylib_registration(plugin_name) {
                return Err(format!("Dylib '{plugin_name}' not found in registry"));
            }
        }
        TaskKind::PythonCall { .. } => {}
    }

    if std::env::var("PYROXIDE_PANIC_TRIGGER").is_ok() {
        let should_panic = Python::attach(|py| {
            task.payload
                .bind(py)
                .extract::<String>()
                .map(|value| value == "TRIGGER_ISOLATED_PANIC")
                .unwrap_or(false)
        });
        if should_panic {
            panic!("Simulated isolated coordinator panic");
        }
    }

    // 1. Prepare serialization payload based on task type
    use crate::ipc::protocol::RequestMetadata;

    let (meta, payload_bytes) =
        Python::attach(|py| -> Result<(RequestMetadata, Vec<u8>), String> {
            match &task.kind {
                TaskKind::PythonCall { callable: cb } => {
                    let pickle = PyModule::import(py, "pickle").map_err(|e| e.to_string())?;
                    let tuple = pyo3::types::PyTuple::new(py, [cb.bind(py), task.payload.bind(py)])
                        .map_err(|e| e.to_string())?;
                    let pickled_tuple = pickle
                        .call_method1("dumps", (tuple,))
                        .map_err(|e| e.to_string())?;
                    let bytes: Vec<u8> = pickled_tuple
                        .extract()
                        .map_err(|e: pyo3::PyErr| e.to_string())?;

                    Ok((RequestMetadata::Python, bytes))
                }
                TaskKind::Wasm {
                    module: module_name,
                    function: func_name,
                    memory_limit_bytes,
                    timeout_ms,
                } => {
                    let limit_bytes = memory_limit_bytes
                        .unwrap_or_else(crate::config::get_wasm_memory_limit_bytes);
                    let timeout_ms = timeout_ms.unwrap_or_else(crate::config::get_wasm_timeout_ms);
                    let bound_payload = task.payload.bind(py);
                    let bytes = if let Ok(s) = bound_payload.extract::<String>() {
                        s.into_bytes()
                    } else if let Ok(b) = bound_payload.extract::<Vec<u8>>() {
                        b
                    } else {
                        return Err("Unsupported payload type for WASM".to_string());
                    };
                    Ok((
                        RequestMetadata::Wasm {
                            module: module_name.clone(),
                            function: func_name.clone(),
                            memory_limit: limit_bytes,
                            timeout_ms,
                        },
                        bytes,
                    ))
                }
                TaskKind::Dylib {
                    plugin: plugin_name,
                    symbol: symbol_name,
                    ffi_sig,
                } => {
                    let bound_payload = task.payload.bind(py);
                    let bytes = if let Ok(s) = bound_payload.extract::<String>() {
                        s.into_bytes()
                    } else if let Ok(b) = bound_payload.extract::<Vec<u8>>() {
                        b
                    } else {
                        return Err("Unsupported payload type for dynamic library".to_string());
                    };
                    Ok((
                        RequestMetadata::Dylib {
                            plugin: plugin_name.clone(),
                            symbol: symbol_name.clone(),
                            signature: ffi_sig.clone(),
                        },
                        bytes,
                    ))
                }
            }
        })?;

    // 2. Acquire a process pool worker
    let pool = crate::process_pool::get_process_pool();
    let mut worker = pool
        .acquire_worker()
        .map_err(|e| format!("Failed to acquire worker process: {e}"))?;

    // Lazy sync registries to worker if missing
    match &task.kind {
        TaskKind::Wasm {
            module: wasm_module,
            ..
        } => {
            match crate::registry::get_wasm_registration_sync(
                wasm_module,
                worker.registered_wasms.get(wasm_module).copied(),
            ) {
                crate::registry::RegistrySync::Missing => {
                    pool.release_worker(worker);
                    return Err(format!("WASM module '{wasm_module}' not found in registry"));
                }
                crate::registry::RegistrySync::Current => {}
                crate::registry::RegistrySync::Changed(registration) => {
                    let sync_meta = RequestMetadata::RegisterWasm {
                        module: wasm_module.clone(),
                    };
                    crate::process_pool::send_registration_task(
                        &mut worker.stream,
                        &sync_meta,
                        &registration.value,
                    )
                    .map_err(|e| format!("Failed to sync WASM module {wasm_module}: {e}"))?;
                    worker
                        .registered_wasms
                        .insert(wasm_module.clone(), registration.generation);
                }
            }
        }
        TaskKind::Dylib {
            plugin: plugin_name,
            ..
        } => {
            match crate::registry::get_dylib_registration_sync(
                plugin_name,
                worker.registered_dylibs.get(plugin_name).copied(),
            ) {
                crate::registry::RegistrySync::Missing => {
                    pool.release_worker(worker);
                    return Err(format!("Dylib '{plugin_name}' not found in registry"));
                }
                crate::registry::RegistrySync::Current => {}
                crate::registry::RegistrySync::Changed(registration) => {
                    if worker.registered_dylibs.remove(plugin_name).is_some() {
                        let unreg_meta = RequestMetadata::UnregisterDylib {
                            plugin: plugin_name.clone(),
                        };
                        crate::process_pool::send_registration_task(
                            &mut worker.stream,
                            &unreg_meta,
                            &[],
                        )
                        .map_err(|e| format!("Failed to remove stale dylib {plugin_name}: {e}"))?;
                    }

                    let reg_meta = RequestMetadata::RegisterDylib {
                        plugin: plugin_name.clone(),
                        library_path: registration.value.library_path,
                        free_fn_name: registration.value.free_fn_name,
                    };
                    crate::process_pool::send_registration_task(&mut worker.stream, &reg_meta, &[])
                        .map_err(|e| format!("Failed to sync dylib {plugin_name}: {e}"))?;
                    worker
                        .registered_dylibs
                        .insert(plugin_name.clone(), registration.generation);
                }
            }
        }
        TaskKind::PythonCall { .. } => {}
    }

    // 3. Write request frame: [Type: 1 byte] [Flags: 1 byte] [Extra Len: 4 bytes] [Payload Len: 8 bytes] [Metadata] [Payload]
    crate::config::checked_ipc_len(
        payload_bytes.len() as u64,
        crate::config::get_max_ipc_frame_bytes(),
        "payload",
    )?;
    let use_shm = payload_bytes.len() >= crate::config::get_shm_threshold();
    let mut flags = crate::ipc::protocol::FrameFlags::inline();
    let mut actual_payload = payload_bytes.clone();
    let mut _created_shm: Option<ShmemGuard> = None;

    if use_shm {
        let shm_name = format!(
            "pyroxide_shm_{}_{}",
            std::process::id(),
            rand::random::<u32>()
        );
        match ShmemGuard::create(payload_bytes.len(), &shm_name) {
            Ok(shmem) if shmem.copy_from_slice(&payload_bytes).is_ok() => {
                flags = crate::ipc::protocol::FrameFlags::shared_memory();
                actual_payload = shm_name.into_bytes();
                _created_shm = Some(shmem);
            }
            Ok(_) | Err(_) => {}
        }
    }

    crate::ipc::frame::write_request(&mut worker.stream, &meta, flags, &actual_payload)
        .map_err(|error| format!("IPC write error: {error}"))?;

    // 4. Read response frame with cancellation checking
    let _ = worker.stream.set_nonblocking(true);
    let mut resp_header = [0u8; 10];
    let mut header_read = 0;

    while header_read < 10 {
        if task.cancelled.load(Ordering::Acquire) {
            #[allow(unused_variables)]
            let pid = worker.child.id();
            let _ = worker.child.kill();
            let _ = worker.child.wait();
            #[cfg(unix)]
            crate::process_pool::cleanup_worker_shm(pid);
            return Err("Task cancelled".to_string());
        }

        match worker.stream.read(&mut resp_header[header_read..]) {
            Ok(0) => {
                return Err("Worker process closed connection (crashed/EOF) on read".to_string());
            }
            Ok(n) => {
                header_read += n;
            }
            Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                #[cfg(unix)]
                {
                    match wait_readable(&worker.stream, 5) {
                        Ok(_) => {}
                        Err(e) if e.kind() == std::io::ErrorKind::Interrupted => {}
                        Err(e) => return Err(format!("IPC poll error: {e}")),
                    }
                }
                #[cfg(not(unix))]
                {
                    std::thread::sleep(std::time::Duration::from_micros(100));
                }
            }
            Err(e) => {
                return Err(format!("IPC read error: {e}"));
            }
        }
    }

    let response_header = crate::ipc::protocol::ResponseHeader::decode(resp_header)?;
    let success = response_header.success;
    let response_flags = response_header.flags;
    let data_len = response_header.payload_len;

    let mut data_bytes = vec![0u8; data_len];
    let mut data_read = 0;

    while data_read < data_len {
        if task.cancelled.load(Ordering::Acquire) {
            #[allow(unused_variables)]
            let pid = worker.child.id();
            let _ = worker.child.kill();
            let _ = worker.child.wait();
            #[cfg(unix)]
            crate::process_pool::cleanup_worker_shm(pid);
            return Err("Task cancelled".to_string());
        }

        match worker.stream.read(&mut data_bytes[data_read..]) {
            Ok(0) => {
                return Err("Worker process closed connection (crashed/EOF) on read".to_string());
            }
            Ok(n) => {
                data_read += n;
            }
            Err(ref e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                #[cfg(unix)]
                {
                    match wait_readable(&worker.stream, 5) {
                        Ok(_) => {}
                        Err(e) if e.kind() == std::io::ErrorKind::Interrupted => {}
                        Err(e) => return Err(format!("IPC poll error: {e}")),
                    }
                }
                #[cfg(not(unix))]
                {
                    std::thread::sleep(std::time::Duration::from_micros(100));
                }
            }
            Err(e) => {
                return Err(format!("IPC read error: {e}"));
            }
        }
    }

    // Restore blocking mode
    let _ = worker.stream.set_nonblocking(false);

    fn unpack_worker_response(
        py: Python<'_>,
        data: &[u8],
        task: &Arc<Task>,
    ) -> Result<Py<PyAny>, String> {
        match &task.kind {
            TaskKind::PythonCall { .. } => {
                let pickle = PyModule::import(py, "pickle").map_err(|e| e.to_string())?;
                let val = pickle
                    .call_method1("loads", (PyBytes::new(py, data),))
                    .map_err(|e| e.to_string())?;
                Ok(val.unbind())
            }
            _ => {
                let is_str = task.payload.bind(py).extract::<String>().is_ok();
                if is_str {
                    let s = std::str::from_utf8(data)
                        .map_err(|e| format!("Invalid UTF-8 output from worker: {e}"))?;
                    let py_str = pyo3::types::PyString::new(py, s);
                    Ok(py_str.into_any().unbind())
                } else {
                    let py_bytes = pyo3::types::PyBytes::new(py, data);
                    Ok(py_bytes.into_any().unbind())
                }
            }
        }
    }

    let final_res = if success && response_flags.uses_shared_memory() {
        let shm_name =
            String::from_utf8(data_bytes).map_err(|e| format!("Invalid SHM name string: {e}"))?;
        match ShmemGuard::open(&shm_name) {
            Ok(shmem) => {
                let size = crate::config::checked_ipc_len(
                    shmem.len() as u64,
                    crate::config::get_max_ipc_frame_bytes(),
                    "shared-memory response",
                )?;
                let slice = &shmem.as_slice()[..size];

                let py_res = Python::attach(|py| unpack_worker_response(py, slice, task));

                let _ = worker.stream.write_all(&[1u8]);
                let _ = worker.stream.flush();
                py_res
            }
            Err(e) => {
                let _ = worker.stream.write_all(&[0u8]);
                let _ = worker.stream.flush();
                Err(format!("Failed to open response SHM {shm_name}: {e}"))
            }
        }
    } else if success {
        Python::attach(|py| unpack_worker_response(py, &data_bytes, task))
    } else {
        let err_msg =
            String::from_utf8(data_bytes).unwrap_or_else(|_| "Unknown worker error".to_string());
        Err(err_msg)
    };

    worker.tasks_run += 1;
    pool.release_worker(worker);

    final_res
}

#[cfg(test)]
mod tests {
    use super::{
        StartClaimHook, StartClaimResult, install_start_claim_hook, transition_pending_to_running,
    };
    use crate::task::TaskStatus;
    use std::sync::atomic::{AtomicBool, AtomicU8, Ordering};
    use std::sync::{Arc, Barrier, mpsc};
    use std::time::Duration;

    #[test]
    fn shutdown_wins_the_claim_gap_in_both_execution_modes() {
        let mut observations = Vec::new();

        for isolated in [false, true] {
            let status = Arc::new(AtomicU8::new(TaskStatus::Pending as u8));
            let cancel_pending = Arc::new(AtomicBool::new(false));
            let hook = Arc::new(StartClaimHook {
                reached: Barrier::new(2),
                resume: Barrier::new(2),
                reached_isolated_loop: std::sync::Mutex::new(None),
            });
            install_start_claim_hook(Some(Arc::clone(&hook)));

            let (outcome_tx, outcome_rx) = mpsc::channel();
            let claiming_status = Arc::clone(&status);
            let claiming_cancel_pending = Arc::clone(&cancel_pending);
            let claimant = std::thread::spawn(move || {
                let result = transition_pending_to_running(
                    &claiming_status,
                    &claiming_cancel_pending,
                    isolated,
                );
                outcome_tx.send(result).unwrap();
            });

            hook.reached.wait();
            cancel_pending.store(true, Ordering::SeqCst);
            hook.resume.wait();

            let result = outcome_rx.recv_timeout(Duration::from_secs(1)).unwrap();
            claimant.join().unwrap();
            install_start_claim_hook(None);
            observations.push((isolated, result, status.load(Ordering::Acquire)));
        }

        assert_eq!(
            observations,
            vec![
                (
                    false,
                    StartClaimResult::CancelledForShutdown,
                    TaskStatus::Cancelled as u8,
                ),
                (
                    true,
                    StartClaimResult::CancelledForShutdown,
                    TaskStatus::Cancelled as u8,
                ),
            ]
        );
    }
}
