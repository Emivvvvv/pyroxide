use std::sync::atomic::{AtomicBool, AtomicU8, AtomicU64, AtomicUsize, Ordering};
use std::sync::{Arc, Condvar, Mutex, OnceLock, RwLock};
use std::thread;
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use crate::worker::{spawn_isolated_workers, spawn_workers};
use pyo3::prelude::*;
use sharded_slab::Slab;

pub(crate) const STOP_TASK_ID: usize = usize::MAX;

#[derive(Debug, Clone, Copy, PartialEq)]
#[repr(u8)]
pub(crate) enum TaskStatus {
    Pending = 0,
    Running = 1,
    Completed = 2,
    Failed = 3,
    Cancelled = 4,
}

impl TaskStatus {
    pub fn to_status_string(val: u8) -> String {
        match val {
            0 => "Pending".to_string(),
            1 => "Running".to_string(),
            2 => "Completed".to_string(),
            3 => "Failed".to_string(),
            4 => "Cancelled".to_string(),
            _ => "Unknown".to_string(),
        }
    }
}

pub(crate) struct QueueAdmission {
    capacity: usize,
    available: Mutex<usize>,
    changed: Condvar,
    closed: AtomicBool,
    #[cfg(test)]
    before_wait: Mutex<Option<Arc<QueueAdmissionWaitHook>>>,
}

#[cfg(test)]
struct QueueAdmissionWaitHook {
    state: Mutex<QueueAdmissionWaitState>,
    changed: Condvar,
}

#[cfg(test)]
#[derive(Default)]
struct QueueAdmissionWaitState {
    reached: bool,
    resumed: bool,
}

#[cfg(test)]
impl QueueAdmissionWaitHook {
    fn new() -> Self {
        Self {
            state: Mutex::new(QueueAdmissionWaitState::default()),
            changed: Condvar::new(),
        }
    }

    fn wait(&self) {
        let mut state = self.state.lock().unwrap_or_else(|error| error.into_inner());
        state.reached = true;
        self.changed.notify_all();
        while !state.resumed {
            state = self
                .changed
                .wait(state)
                .unwrap_or_else(|error| error.into_inner());
        }
    }

    fn wait_until_reached(&self, timeout: Duration) -> bool {
        let deadline = Instant::now() + timeout;
        let mut state = self.state.lock().unwrap_or_else(|error| error.into_inner());
        while !state.reached {
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return false;
            }
            let (next_state, wait) = self
                .changed
                .wait_timeout(state, remaining)
                .unwrap_or_else(|error| error.into_inner());
            state = next_state;
            if wait.timed_out() && !state.reached {
                return false;
            }
        }
        true
    }

    fn resume(&self) {
        let mut state = self.state.lock().unwrap_or_else(|error| error.into_inner());
        state.resumed = true;
        self.changed.notify_all();
    }
}

impl QueueAdmission {
    fn new(capacity: usize) -> Self {
        Self {
            capacity,
            available: Mutex::new(capacity),
            changed: Condvar::new(),
            closed: AtomicBool::new(false),
            #[cfg(test)]
            before_wait: Mutex::new(None),
        }
    }

    fn reserve(&self, count: usize, timeout: Duration) -> bool {
        if count > self.capacity || self.closed.load(Ordering::Acquire) {
            return false;
        }

        let mut available = self.available.lock().unwrap_or_else(|e| e.into_inner());
        if self.closed.load(Ordering::Acquire) {
            return false;
        }
        if timeout.is_zero() {
            if *available < count {
                return false;
            }
            *available -= count;
            return true;
        }

        let deadline = Instant::now().checked_add(timeout);
        const UNBOUNDED_WAIT_CHUNK: Duration = Duration::from_secs(24 * 60 * 60);
        while *available < count {
            if self.closed.load(Ordering::Acquire) {
                return false;
            }
            let remaining = match deadline {
                Some(deadline) => {
                    let remaining = deadline.saturating_duration_since(Instant::now());
                    if remaining.is_zero() {
                        return false;
                    }
                    remaining
                }
                None => UNBOUNDED_WAIT_CHUNK,
            };
            #[cfg(test)]
            if let Some(hook) = self
                .before_wait
                .lock()
                .unwrap_or_else(|error| error.into_inner())
                .clone()
            {
                hook.wait();
            }
            let (guard, wait) = self
                .changed
                .wait_timeout(available, remaining)
                .unwrap_or_else(|e| e.into_inner());
            available = guard;
            if deadline.is_some() && wait.timed_out() && *available < count {
                return false;
            }
        }
        if self.closed.load(Ordering::Acquire) {
            return false;
        }
        *available -= count;
        true
    }

    pub(crate) fn release(&self, count: usize) {
        let mut available = self.available.lock().unwrap_or_else(|e| e.into_inner());
        *available = (*available + count).min(self.capacity);
        self.changed.notify_all();
    }

    fn close(&self) {
        let _available = self
            .available
            .lock()
            .unwrap_or_else(|error| error.into_inner());
        self.closed.store(true, Ordering::Release);
        self.changed.notify_all();
    }

    fn is_closed(&self) -> bool {
        self.closed.load(Ordering::Acquire)
    }

    fn queued(&self) -> usize {
        let available = self
            .available
            .lock()
            .unwrap_or_else(|error| error.into_inner());
        self.capacity.saturating_sub(*available)
    }

    #[cfg(test)]
    fn install_before_wait_hook(&self, hook: Arc<QueueAdmissionWaitHook>) {
        *self
            .before_wait
            .lock()
            .unwrap_or_else(|error| error.into_inner()) = Some(hook);
    }
}

pub(crate) struct Task {
    pub(crate) status: AtomicU8,
    pub(crate) callable: Option<Py<PyAny>>,
    pub(crate) payload: Py<PyAny>,
    pub(crate) result: Mutex<Option<Result<Py<PyAny>, String>>>,
    pub(crate) completed_cvar: Condvar,
    pub(crate) completed_mutex: Mutex<bool>,
    pub(crate) cancelled: AtomicBool,
    pub(crate) autofree: AtomicBool,
    pub(crate) wasm_module: Option<String>,
    pub(crate) wasm_func: Option<String>,
    pub(crate) dylib: Option<String>,
    pub(crate) dylib_symbol: Option<String>,
    pub(crate) ffi_sig: Option<(Vec<String>, String)>,
    pub(crate) isolated: bool,
    pub(crate) wasm_memory_limit_bytes: Option<usize>,
    pub(crate) wasm_timeout_ms: Option<u64>,
}

pub(crate) struct Broker {
    pub(crate) tasks: Slab<Arc<Task>>,
    pub(crate) task_count: AtomicUsize,
    pub(crate) submitted_count: AtomicU64,
    pub(crate) rejected_count: AtomicU64,
    pub(crate) running_count: AtomicUsize,
    pub(crate) completed_count: AtomicU64,
    pub(crate) failed_count: AtomicU64,
    pub(crate) cancelled_count: AtomicU64,
}

impl Broker {
    fn new() -> Self {
        Self {
            tasks: Slab::new(),
            task_count: AtomicUsize::new(0),
            submitted_count: AtomicU64::new(0),
            rejected_count: AtomicU64::new(0),
            running_count: AtomicUsize::new(0),
            completed_count: AtomicU64::new(0),
            failed_count: AtomicU64::new(0),
            cancelled_count: AtomicU64::new(0),
        }
    }

    pub(crate) fn record_task_completion(&self, status: u8) {
        if status == TaskStatus::Completed as u8 {
            self.completed_count.fetch_add(1, Ordering::Relaxed);
        } else if status == TaskStatus::Failed as u8 {
            self.failed_count.fetch_add(1, Ordering::Relaxed);
        }
    }
}

pub(crate) struct Engine {
    pub(crate) broker: Arc<Broker>,
    sender: crossbeam_channel::Sender<usize>,
    isolated_sender: crossbeam_channel::Sender<usize>,
    pub(crate) admission: Arc<QueueAdmission>,
    submission_gate: RwLock<()>,
    shutdown_started: AtomicBool,
    cancel_pending_on_shutdown: Arc<AtomicBool>,
    workers: Mutex<Option<WorkerHandles>>,
    shutdown_complete: Arc<(Mutex<bool>, Condvar)>,
}

type WorkerHandles = (Vec<JoinHandle<()>>, Vec<JoinHandle<()>>);

static ENGINE: OnceLock<Engine> = OnceLock::new();
static RUNTIME_PID: OnceLock<u32> = OnceLock::new();

pub(crate) fn get_engine() -> &'static Engine {
    ENGINE.get_or_init(|| {
        let queue_capacity = get_queue_capacity();
        let (sender, receiver) = crossbeam_channel::bounded::<usize>(queue_capacity);
        let (isolated_sender, isolated_receiver) =
            crossbeam_channel::bounded::<usize>(queue_capacity);
        let broker = Arc::new(Broker::new());
        let admission = Arc::new(QueueAdmission::new(queue_capacity));
        let cancel_pending_on_shutdown = Arc::new(AtomicBool::new(false));

        let num_workers = get_worker_count();

        let _workers = spawn_workers(
            num_workers,
            broker.clone(),
            receiver,
            Arc::clone(&admission),
            Arc::clone(&cancel_pending_on_shutdown),
        );
        let _isolated_workers = spawn_isolated_workers(
            get_max_processes(),
            broker.clone(),
            isolated_receiver,
            Arc::clone(&admission),
            Arc::clone(&cancel_pending_on_shutdown),
        );

        Engine {
            broker,
            sender,
            isolated_sender,
            admission,
            submission_gate: RwLock::new(()),
            shutdown_started: AtomicBool::new(false),
            cancel_pending_on_shutdown,
            workers: Mutex::new(Some((_workers, _isolated_workers))),
            shutdown_complete: Arc::new((Mutex::new(false), Condvar::new())),
        }
    })
}

pub(crate) fn check_engine_process() -> PyResult<()> {
    let current_pid = std::process::id();
    let creator_pid = *RUNTIME_PID.get_or_init(|| current_pid);
    if creator_pid != current_pid {
        return Err(crate::ForkSafetyError::new_err(
            "Pyroxide was initialized before fork; initialize it in the child process instead",
        ));
    }
    Ok(())
}

fn shutdown_error() -> PyErr {
    pyo3::exceptions::PyRuntimeError::new_err("Pyroxide engine has been shut down")
}

pub(crate) fn shutdown_engine(wait: bool, cancel_pending: bool) -> PyResult<()> {
    check_engine_process()?;
    if wait && crate::worker::is_in_process_worker() {
        return Err(pyo3::exceptions::PyRuntimeError::new_err(
            "shutdown(wait=True) cannot be called from a Pyroxide worker; use wait=False or call shutdown from another thread",
        ));
    }
    let engine = get_engine();

    let workers = {
        let _submission_guard = engine
            .submission_gate
            .write()
            .unwrap_or_else(|error| error.into_inner());
        if cancel_pending {
            // This store shares the sequentially consistent order with each
            // Pending -> Running claim and its post-claim flag check. If this
            // store precedes a claim, that worker's later check must observe it.
            engine
                .cancel_pending_on_shutdown
                .store(true, Ordering::SeqCst);
        }
        engine.admission.close();

        if !engine.shutdown_started.swap(true, Ordering::AcqRel) {
            engine
                .workers
                .lock()
                .unwrap_or_else(|error| error.into_inner())
                .take()
        } else {
            None
        }
    };

    if let Some((workers, isolated_workers)) = workers {
        let sender = engine.sender.clone();
        let isolated_sender = engine.isolated_sender.clone();
        let completion = Arc::clone(&engine.shutdown_complete);
        thread::spawn(move || {
            for _ in 0..workers.len() {
                let _ = sender.send(STOP_TASK_ID);
            }
            for _ in 0..isolated_workers.len() {
                let _ = isolated_sender.send(STOP_TASK_ID);
            }
            for worker in workers {
                let _ = worker.join();
            }
            for worker in isolated_workers {
                let _ = worker.join();
            }
            crate::process_pool::shutdown_process_pool();
            crate::stop_wasm_ticker();
            let (lock, changed) = &*completion;
            let mut complete = lock.lock().unwrap_or_else(|error| error.into_inner());
            *complete = true;
            changed.notify_all();
        });
    }

    if wait {
        let (lock, changed) = &*engine.shutdown_complete;
        let mut complete = lock.lock().unwrap_or_else(|error| error.into_inner());
        while !*complete {
            complete = changed
                .wait(complete)
                .unwrap_or_else(|error| error.into_inner());
        }
    }
    Ok(())
}

fn get_worker_count() -> usize {
    static WORKER_COUNT: OnceLock<usize> = OnceLock::new();
    *WORKER_COUNT.get_or_init(|| {
        std::env::var("PYROXIDE_WORKERS")
            .ok()
            .and_then(|v| v.parse().ok())
            .filter(|value| *value > 0)
            .unwrap_or_else(|| {
                thread::available_parallelism()
                    .map(|n| n.get())
                    .unwrap_or(4)
            })
    })
}

fn get_queue_capacity() -> usize {
    static VALUE: OnceLock<usize> = OnceLock::new();
    *VALUE.get_or_init(|| {
        std::env::var("PYROXIDE_QUEUE_CAPACITY")
            .ok()
            .and_then(|value| value.parse::<usize>().ok())
            .filter(|value| *value > 0)
            .unwrap_or(10_000)
    })
}

fn get_max_processes() -> usize {
    static VALUE: OnceLock<usize> = OnceLock::new();
    *VALUE.get_or_init(|| {
        let default = thread::available_parallelism()
            .map(|count| count.get().min(8))
            .unwrap_or(4);
        std::env::var("PYROXIDE_MAX_PROCESSES")
            .ok()
            .and_then(|value| value.parse::<usize>().ok())
            .filter(|value| *value > 0)
            .unwrap_or(default.max(1))
    })
}

fn resolve_queue_timeout_ms(queue_timeout_ms: Option<u64>) -> u64 {
    queue_timeout_ms.unwrap_or_else(crate::get_queue_timeout_ms)
}

fn queue_full_error(timeout_ms: u64) -> PyErr {
    let message = if timeout_ms == 0 {
        "Task queue is full"
    } else {
        "Task queue is full (timeout exceeded)"
    };
    pyo3::exceptions::PyBufferError::new_err(message)
}

fn enqueue_task(task: Arc<Task>, queue_timeout_ms: Option<u64>) -> PyResult<usize> {
    check_engine_process()?;
    let engine = get_engine();
    if engine.shutdown_started.load(Ordering::Acquire) || engine.admission.is_closed() {
        return Err(shutdown_error());
    }
    let isolated = task.isolated;
    let timeout_ms = resolve_queue_timeout_ms(queue_timeout_ms);
    if !engine
        .admission
        .reserve(1, Duration::from_millis(timeout_ms))
    {
        return if engine.admission.is_closed() {
            Err(shutdown_error())
        } else {
            engine.broker.rejected_count.fetch_add(1, Ordering::Relaxed);
            Err(queue_full_error(timeout_ms))
        };
    }
    let _submission_guard = engine
        .submission_gate
        .read()
        .unwrap_or_else(|error| error.into_inner());
    if engine.shutdown_started.load(Ordering::Acquire) {
        engine.admission.release(1);
        return Err(shutdown_error());
    }

    let task_id = match engine.broker.tasks.insert(task) {
        Some(task_id) => task_id,
        None => {
            engine.admission.release(1);
            engine.broker.rejected_count.fetch_add(1, Ordering::Relaxed);
            return Err(pyo3::exceptions::PyBufferError::new_err(
                "Task registry is full",
            ));
        }
    };
    engine.broker.task_count.fetch_add(1, Ordering::Relaxed);

    let sender = if isolated {
        &engine.isolated_sender
    } else {
        &engine.sender
    };
    let send_res = sender.try_send(task_id).map_err(|err| match err {
        crossbeam_channel::TrySendError::Full(_) => "Task queue is full".to_string(),
        crossbeam_channel::TrySendError::Disconnected(_) => {
            "Task queue channel is disconnected".to_string()
        }
    });

    if let Err(err) = send_res {
        Python::attach(|_| {
            engine.broker.tasks.remove(task_id);
            engine.broker.task_count.fetch_sub(1, Ordering::Relaxed);
        });
        engine.admission.release(1);
        engine.broker.rejected_count.fetch_add(1, Ordering::Relaxed);
        return Err(pyo3::exceptions::PyBufferError::new_err(err));
    }

    engine
        .broker
        .submitted_count
        .fetch_add(1, Ordering::Relaxed);
    Ok(task_id)
}

pub(crate) fn submit_task(
    callable: Option<Py<PyAny>>,
    payload: Py<PyAny>,
    isolated: bool,
    queue_timeout_ms: Option<u64>,
) -> PyResult<usize> {
    let task = Arc::new(Task {
        status: AtomicU8::new(TaskStatus::Pending as u8),
        callable,
        payload,
        result: Mutex::new(None),
        completed_cvar: Condvar::new(),
        completed_mutex: Mutex::new(false),
        cancelled: AtomicBool::new(false),
        autofree: AtomicBool::new(false),
        wasm_module: None,
        wasm_func: None,
        dylib: None,
        dylib_symbol: None,
        ffi_sig: None,
        isolated,
        wasm_memory_limit_bytes: None,
        wasm_timeout_ms: None,
    });

    enqueue_task(task, queue_timeout_ms)
}

fn reserve_batch_and_build<T, F>(
    admission: &QueueAdmission,
    batch_len: usize,
    timeout: Duration,
    task_factory: F,
) -> PyResult<Option<T>>
where
    F: FnOnce() -> PyResult<T>,
{
    if !admission.reserve(batch_len, timeout) {
        return Ok(None);
    }
    match task_factory() {
        Ok(tasks) => Ok(Some(tasks)),
        Err(error) => {
            admission.release(batch_len);
            Err(error)
        }
    }
}

fn enqueue_batch<F>(
    batch_len: usize,
    isolated: bool,
    queue_timeout_ms: Option<u64>,
    task_factory: F,
) -> PyResult<Vec<usize>>
where
    F: FnOnce() -> PyResult<Vec<Arc<Task>>>,
{
    check_engine_process()?;
    let engine = get_engine();
    if engine.shutdown_started.load(Ordering::Acquire) || engine.admission.is_closed() {
        return Err(shutdown_error());
    }

    let timeout_ms = resolve_queue_timeout_ms(queue_timeout_ms);
    let Some(tasks) = reserve_batch_and_build(
        &engine.admission,
        batch_len,
        Duration::from_millis(timeout_ms),
        task_factory,
    )?
    else {
        return if engine.admission.is_closed() {
            Err(shutdown_error())
        } else {
            engine
                .broker
                .rejected_count
                .fetch_add(batch_len as u64, Ordering::Relaxed);
            Err(queue_full_error(timeout_ms))
        };
    };
    if tasks.len() != batch_len {
        engine.admission.release(batch_len);
        return Err(pyo3::exceptions::PyRuntimeError::new_err(
            "Internal batch task count mismatch",
        ));
    }

    let _submission_guard = engine
        .submission_gate
        .read()
        .unwrap_or_else(|error| error.into_inner());
    if engine.shutdown_started.load(Ordering::Acquire) {
        engine.admission.release(batch_len);
        return Err(shutdown_error());
    }

    let mut ids = Vec::with_capacity(batch_len);
    for task in tasks {
        let Some(task_id) = engine.broker.tasks.insert(task) else {
            Python::attach(|_| {
                for &inserted_id in &ids {
                    engine.broker.tasks.remove(inserted_id);
                }
            });
            engine
                .broker
                .task_count
                .fetch_sub(ids.len(), Ordering::Relaxed);
            engine.admission.release(batch_len);
            engine
                .broker
                .rejected_count
                .fetch_add(batch_len as u64, Ordering::Relaxed);
            return Err(pyo3::exceptions::PyBufferError::new_err(
                "Task registry is full",
            ));
        };
        engine.broker.task_count.fetch_add(1, Ordering::Relaxed);
        ids.push(task_id);
    }

    for &task_id in &ids {
        let sender = if isolated {
            &engine.isolated_sender
        } else {
            &engine.sender
        };
        if let Err(err) = sender.try_send(task_id) {
            let message = match err {
                crossbeam_channel::TrySendError::Full(_) => "Task queue is full",
                crossbeam_channel::TrySendError::Disconnected(_) => {
                    "Task queue channel is disconnected"
                }
            };
            Python::attach(|_| {
                for &inserted_id in &ids {
                    engine.broker.tasks.remove(inserted_id);
                }
            });
            engine
                .broker
                .task_count
                .fetch_sub(ids.len(), Ordering::Relaxed);
            engine.admission.release(batch_len);
            engine
                .broker
                .rejected_count
                .fetch_add(batch_len as u64, Ordering::Relaxed);
            return Err(pyo3::exceptions::PyBufferError::new_err(message));
        }
    }

    engine
        .broker
        .submitted_count
        .fetch_add(batch_len as u64, Ordering::Relaxed);
    Ok(ids)
}

pub(crate) fn submit_batch(
    callable: Option<Py<PyAny>>,
    payloads: Py<pyo3::types::PyList>,
    batch_len: usize,
    isolated: bool,
    queue_timeout_ms: Option<u64>,
) -> PyResult<Vec<usize>> {
    enqueue_batch(batch_len, isolated, queue_timeout_ms, move || {
        Python::attach(|py| {
            payloads
                .bind(py)
                .iter()
                .map(|payload| {
                    Ok(Arc::new(Task {
                        status: AtomicU8::new(TaskStatus::Pending as u8),
                        callable: callable.as_ref().map(|value| value.clone_ref(py)),
                        payload: payload.unbind(),
                        result: Mutex::new(None),
                        completed_cvar: Condvar::new(),
                        completed_mutex: Mutex::new(false),
                        cancelled: AtomicBool::new(false),
                        autofree: AtomicBool::new(false),
                        wasm_module: None,
                        wasm_func: None,
                        dylib: None,
                        dylib_symbol: None,
                        ffi_sig: None,
                        isolated,
                        wasm_memory_limit_bytes: None,
                        wasm_timeout_ms: None,
                    }))
                })
                .collect()
        })
    })
}

pub(crate) fn submit_wasm_task(
    module_name: String,
    func_name: String,
    payload: Py<PyAny>,
    isolated: bool,
    wasm_memory_limit_bytes: Option<usize>,
    wasm_timeout_ms: Option<u64>,
    queue_timeout_ms: Option<u64>,
) -> PyResult<usize> {
    let task = Arc::new(Task {
        status: AtomicU8::new(TaskStatus::Pending as u8),
        callable: None,
        payload,
        result: Mutex::new(None),
        completed_cvar: Condvar::new(),
        completed_mutex: Mutex::new(false),
        cancelled: AtomicBool::new(false),
        autofree: AtomicBool::new(false),
        wasm_module: Some(module_name),
        wasm_func: Some(func_name),
        dylib: None,
        dylib_symbol: None,
        ffi_sig: None,
        isolated,
        wasm_memory_limit_bytes,
        wasm_timeout_ms,
    });

    enqueue_task(task, queue_timeout_ms)
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn submit_wasm_batch(
    module_name: String,
    func_name: String,
    payloads: Py<pyo3::types::PyList>,
    batch_len: usize,
    isolated: bool,
    wasm_memory_limit_bytes: Option<usize>,
    wasm_timeout_ms: Option<u64>,
    queue_timeout_ms: Option<u64>,
) -> PyResult<Vec<usize>> {
    enqueue_batch(batch_len, isolated, queue_timeout_ms, move || {
        Python::attach(|py| {
            payloads
                .bind(py)
                .iter()
                .map(|payload| {
                    Ok(Arc::new(Task {
                        status: AtomicU8::new(TaskStatus::Pending as u8),
                        callable: None,
                        payload: payload.unbind(),
                        result: Mutex::new(None),
                        completed_cvar: Condvar::new(),
                        completed_mutex: Mutex::new(false),
                        cancelled: AtomicBool::new(false),
                        autofree: AtomicBool::new(false),
                        wasm_module: Some(module_name.clone()),
                        wasm_func: Some(func_name.clone()),
                        dylib: None,
                        dylib_symbol: None,
                        ffi_sig: None,
                        isolated,
                        wasm_memory_limit_bytes,
                        wasm_timeout_ms,
                    }))
                })
                .collect()
        })
    })
}

pub(crate) fn submit_dylib_task(
    plugin_name: String,
    symbol_name: String,
    payload: Py<PyAny>,
    ffi_sig: Option<(Vec<String>, String)>,
    isolated: bool,
    queue_timeout_ms: Option<u64>,
) -> PyResult<usize> {
    let task = Arc::new(Task {
        status: AtomicU8::new(TaskStatus::Pending as u8),
        callable: None,
        payload,
        result: Mutex::new(None),
        completed_cvar: Condvar::new(),
        completed_mutex: Mutex::new(false),
        cancelled: AtomicBool::new(false),
        autofree: AtomicBool::new(false),
        wasm_module: None,
        wasm_func: None,
        dylib: Some(plugin_name),
        dylib_symbol: Some(symbol_name),
        ffi_sig,
        isolated,
        wasm_memory_limit_bytes: None,
        wasm_timeout_ms: None,
    });

    enqueue_task(task, queue_timeout_ms)
}

#[allow(clippy::too_many_arguments)]
pub(crate) fn submit_dylib_batch(
    plugin_name: String,
    symbol_name: String,
    payloads: Py<pyo3::types::PyList>,
    batch_len: usize,
    ffi_sig: Option<(Vec<String>, String)>,
    isolated: bool,
    queue_timeout_ms: Option<u64>,
    payload_builder: Option<Py<PyAny>>,
) -> PyResult<Vec<usize>> {
    enqueue_batch(batch_len, isolated, queue_timeout_ms, move || {
        Python::attach(|py| {
            payloads
                .bind(py)
                .iter()
                .map(|payload| {
                    let payload = if let Some(builder) = &payload_builder {
                        builder.bind(py).call1((payload,))?.unbind()
                    } else {
                        payload.unbind()
                    };
                    Ok(Arc::new(Task {
                        status: AtomicU8::new(TaskStatus::Pending as u8),
                        callable: None,
                        payload,
                        result: Mutex::new(None),
                        completed_cvar: Condvar::new(),
                        completed_mutex: Mutex::new(false),
                        cancelled: AtomicBool::new(false),
                        autofree: AtomicBool::new(false),
                        wasm_module: None,
                        wasm_func: None,
                        dylib: Some(plugin_name.clone()),
                        dylib_symbol: Some(symbol_name.clone()),
                        ffi_sig: ffi_sig.clone(),
                        isolated,
                        wasm_memory_limit_bytes: None,
                        wasm_timeout_ms: None,
                    }))
                })
                .collect()
        })
    })
}

pub(crate) fn cancel_task(task_id: usize) -> bool {
    let engine = get_engine();
    let task = engine.broker.tasks.get(task_id).map(|e| Arc::clone(&*e));

    if let Some(task) = task {
        let mut current = task.status.load(Ordering::Acquire);
        loop {
            if current == TaskStatus::Running as u8 && !task.isolated {
                return false;
            }
            if current == TaskStatus::Completed as u8
                || current == TaskStatus::Failed as u8
                || current == TaskStatus::Cancelled as u8
            {
                return false;
            }
            match task.status.compare_exchange_weak(
                current,
                TaskStatus::Cancelled as u8,
                Ordering::Release,
                Ordering::Acquire,
            ) {
                Ok(_) => {
                    let wait_for_isolated_cleanup =
                        current == TaskStatus::Running as u8 && task.isolated;
                    engine
                        .broker
                        .cancelled_count
                        .fetch_add(1, Ordering::Relaxed);
                    task.cancelled.store(true, Ordering::Release);
                    {
                        let mut res_guard = task.result.lock().unwrap_or_else(|e| e.into_inner());
                        *res_guard = Some(Err("Task cancelled".to_string()));
                    }
                    if !wait_for_isolated_cleanup {
                        {
                            let mut completed = task
                                .completed_mutex
                                .lock()
                                .unwrap_or_else(|e| e.into_inner());
                            *completed = true;
                        }
                        task.completed_cvar.notify_all();
                        #[cfg(unix)]
                        crate::notify_waker(task_id);
                    }
                    return true;
                }
                Err(actual) => current = actual,
            }
        }
    }
    false
}

pub(crate) fn get_task_status(task_id: usize) -> Option<String> {
    let engine = get_engine();

    engine.broker.tasks.get(task_id).map(|task| {
        let status_val = task.status.load(Ordering::Acquire);
        TaskStatus::to_status_string(status_val)
    })
}

pub(crate) fn wait_task(task_id: usize, timeout_ms: Option<u64>) -> Option<String> {
    let engine = get_engine();

    let task = engine.broker.tasks.get(task_id).map(|e| Arc::clone(&*e));

    if let Some(task) = task {
        let mut completed = task
            .completed_mutex
            .lock()
            .unwrap_or_else(|e| e.into_inner());

        match timeout_ms {
            None => {
                while !*completed {
                    completed = task
                        .completed_cvar
                        .wait(completed)
                        .unwrap_or_else(|e| e.into_inner());
                }
            }
            Some(ms) => {
                let timeout = Duration::from_millis(ms);
                let start = Instant::now();
                while !*completed {
                    let elapsed = start.elapsed();
                    if elapsed >= timeout {
                        break;
                    }
                    let remaining = timeout - elapsed;
                    let (new_completed, result) = task
                        .completed_cvar
                        .wait_timeout(completed, remaining)
                        .unwrap_or_else(|e| e.into_inner());
                    completed = new_completed;
                    if result.timed_out() {
                        break;
                    }
                }
            }
        }

        let status_val = task.status.load(Ordering::Acquire);
        Some(TaskStatus::to_status_string(status_val))
    } else {
        None
    }
}

pub(crate) fn get_task_result(py: Python<'_>, task_id: usize) -> Option<Result<Py<PyAny>, String>> {
    let engine = get_engine();

    let task = engine.broker.tasks.get(task_id).map(|e| Arc::clone(&*e));

    task.and_then(|t| {
        let res = t.result.lock().unwrap_or_else(|e| e.into_inner());
        res.as_ref().map(|r| match r {
            Ok(val) => Ok(val.clone_ref(py)),
            Err(err) => Err(err.clone()),
        })
    })
}

pub(crate) fn free_task(task_id: usize) {
    let engine = get_engine();
    Python::attach(|_| {
        if engine.broker.tasks.remove(task_id) {
            engine.broker.task_count.fetch_sub(1, Ordering::Relaxed);
        }
    });
}

pub(crate) fn get_slab_size() -> usize {
    let engine = get_engine();
    engine.broker.task_count.load(Ordering::Relaxed)
}

pub(crate) fn set_autofree(task_id: usize) {
    let engine = get_engine();
    if let Some(task) = engine.broker.tasks.get(task_id) {
        task.autofree.store(true, Ordering::Release);
        let current_status = task.status.load(Ordering::Acquire);
        if current_status == TaskStatus::Completed as u8
            || current_status == TaskStatus::Failed as u8
            || current_status == TaskStatus::Cancelled as u8
        {
            free_task(task_id);
        }
    }
}

#[pyfunction]
pub(crate) fn get_engine_stats(py: Python<'_>) -> PyResult<Py<PyAny>> {
    check_engine_process()?;
    let engine = get_engine();
    let dict = pyo3::types::PyDict::new(py);
    dict.set_item("worker_count", get_worker_count())?;
    dict.set_item("max_processes", get_max_processes())?;
    dict.set_item("queue_capacity", get_queue_capacity())?;
    dict.set_item("queued_tasks", engine.admission.queued())?;
    dict.set_item(
        "running_tasks",
        engine.broker.running_count.load(Ordering::Relaxed),
    )?;
    dict.set_item(
        "rejected_tasks",
        engine.broker.rejected_count.load(Ordering::Relaxed),
    )?;
    dict.set_item(
        "active_tasks",
        engine.broker.task_count.load(Ordering::Relaxed),
    )?;
    dict.set_item(
        "submitted_tasks",
        engine.broker.submitted_count.load(Ordering::Relaxed),
    )?;
    dict.set_item(
        "completed_tasks",
        engine.broker.completed_count.load(Ordering::Relaxed),
    )?;
    dict.set_item(
        "failed_tasks",
        engine.broker.failed_count.load(Ordering::Relaxed),
    )?;
    dict.set_item(
        "cancelled_tasks",
        engine.broker.cancelled_count.load(Ordering::Relaxed),
    )?;
    Ok(dict.into_any().unbind())
}

#[cfg(test)]
mod tests {
    use super::{QueueAdmission, QueueAdmissionWaitHook, reserve_batch_and_build};
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::{Arc, mpsc};
    use std::time::Duration;

    #[test]
    fn close_wakes_a_capacity_waiter_without_waiting_for_its_timeout() {
        let admission = Arc::new(QueueAdmission::new(1));
        assert!(admission.reserve(1, Duration::ZERO));

        let hook = Arc::new(QueueAdmissionWaitHook::new());
        admission.install_before_wait_hook(Arc::clone(&hook));

        let (result_tx, result_rx) = mpsc::channel();
        let waiting_admission = Arc::clone(&admission);
        let waiter = std::thread::spawn(move || {
            let reserved = waiting_admission.reserve(1, Duration::from_secs(30));
            result_tx.send(reserved).unwrap();
        });

        if !hook.wait_until_reached(Duration::from_secs(1)) {
            hook.resume();
            admission.close();
            let _ = waiter.join();
            panic!("capacity waiter did not reach the wait boundary");
        }

        let (close_started_tx, close_started_rx) = mpsc::channel();
        let (close_done_tx, close_done_rx) = mpsc::channel();
        let closing_admission = Arc::clone(&admission);
        let closer = std::thread::spawn(move || {
            close_started_tx.send(()).unwrap();
            closing_admission.close();
            close_done_tx.send(()).unwrap();
        });
        close_started_rx
            .recv_timeout(Duration::from_secs(1))
            .unwrap();

        // On the buggy implementation close() finishes here, so its notification
        // is deterministically sent before reserve() enrolls in the condvar wait.
        // Once close() locks the predicate mutex, it instead completes after the
        // hook releases reserve() into wait_timeout().
        let _ = close_done_rx.recv_timeout(Duration::from_millis(250));
        hook.resume();

        let prompt_result = result_rx.recv_timeout(Duration::from_millis(250)).ok();
        if prompt_result.is_none() {
            admission.release(1);
            let _ = result_rx.recv_timeout(Duration::from_secs(1));
        }

        closer.join().unwrap();
        waiter.join().unwrap();
        assert_eq!(
            prompt_result,
            Some(false),
            "close() did not promptly wake the capacity-blocked reservation"
        );
    }

    #[test]
    fn huge_timeout_does_not_overflow_and_close_wakes_waiter() {
        let admission = Arc::new(QueueAdmission::new(1));
        assert!(admission.reserve(1, Duration::ZERO));

        let hook = Arc::new(QueueAdmissionWaitHook::new());
        admission.install_before_wait_hook(Arc::clone(&hook));

        let (result_tx, result_rx) = mpsc::channel();
        let waiting_admission = Arc::clone(&admission);
        let waiter = std::thread::spawn(move || {
            let reserved = waiting_admission.reserve(1, Duration::MAX);
            result_tx.send(reserved).unwrap();
        });

        if !hook.wait_until_reached(Duration::from_secs(1)) {
            hook.resume();
            admission.close();
            let _ = waiter.join();
            panic!("extreme-timeout reservation did not reach the wait boundary");
        }

        let (close_started_tx, close_started_rx) = mpsc::channel();
        let (close_done_tx, close_done_rx) = mpsc::channel();
        let closing_admission = Arc::clone(&admission);
        let closer = std::thread::spawn(move || {
            close_started_tx.send(()).unwrap();
            closing_admission.close();
            close_done_tx.send(()).unwrap();
        });
        close_started_rx
            .recv_timeout(Duration::from_secs(1))
            .unwrap();

        let _ = close_done_rx.recv_timeout(Duration::from_millis(250));
        hook.resume();

        let prompt_result = result_rx.recv_timeout(Duration::from_millis(250)).ok();
        if prompt_result.is_none() {
            admission.release(1);
            let _ = result_rx.recv_timeout(Duration::from_secs(1));
        }

        closer.join().unwrap();
        waiter.join().unwrap();
        assert_eq!(
            prompt_result,
            Some(false),
            "close() did not promptly wake the extreme-timeout reservation"
        );
    }

    #[test]
    fn rejected_batch_does_not_build_task_records_before_admission() {
        let admission = QueueAdmission::new(1);
        assert!(admission.reserve(1, Duration::ZERO));
        let task_factory_called = Arc::new(AtomicBool::new(false));
        let factory_observation = Arc::clone(&task_factory_called);

        let result = reserve_batch_and_build(&admission, 1, Duration::ZERO, move || {
            factory_observation.store(true, Ordering::Release);
            Ok(Vec::<()>::new())
        })
        .unwrap();

        assert!(result.is_none());
        assert!(!task_factory_called.load(Ordering::Acquire));
    }
}
