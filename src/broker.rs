use std::sync::atomic::{AtomicBool, AtomicU8, Ordering};
use std::sync::{Arc, Condvar, Mutex, OnceLock, RwLock};
use std::thread;
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use crate::worker::spawn_workers;
use pyo3::prelude::*;
use slab::Slab;

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

pub(crate) struct Task {
    pub(crate) status: AtomicU8,
    pub(crate) callable: Option<Py<PyAny>>,
    pub(crate) payload: Py<PyAny>,
    pub(crate) result: Mutex<Option<Result<Py<PyAny>, String>>>,
    pub(crate) completed_cvar: Condvar,
    pub(crate) completed_mutex: Mutex<bool>,
    pub(crate) cancelled: AtomicBool,
    pub(crate) wasm_module: Option<String>,
    pub(crate) wasm_func: Option<String>,
    pub(crate) dylib: Option<String>,
}

pub(crate) struct Broker {
    pub(crate) tasks: RwLock<Slab<Arc<Task>>>,
}

impl Broker {
    fn new() -> Self {
        Self {
            tasks: RwLock::new(Slab::new()),
        }
    }
}

struct Engine {
    broker: Arc<Broker>,
    sender: crossbeam_channel::Sender<usize>,
    workers: Vec<JoinHandle<()>>,
}

impl Drop for Engine {
    fn drop(&mut self) {
        for worker in self.workers.drain(..) {
            worker.join().expect("Worker thread panicked");
        }
    }
}

static ENGINE: OnceLock<Engine> = OnceLock::new();

fn get_engine() -> &'static Engine {
    ENGINE.get_or_init(|| {
        let (sender, receiver) = crossbeam_channel::bounded::<usize>(10000);
        let broker = Arc::new(Broker::new());

        let num_workers = std::env::var("PYROXIDE_WORKERS")
            .ok()
            .and_then(|val| val.parse::<usize>().ok())
            .unwrap_or_else(|| thread::available_parallelism().unwrap().get());

        let workers = spawn_workers(num_workers, broker.clone(), receiver);

        Engine {
            broker,
            sender,
            workers,
        }
    })
}

pub(crate) fn submit_task(callable: Option<Py<PyAny>>, payload: Py<PyAny>) -> usize {
    let engine = get_engine();

    let task = Arc::new(Task {
        status: AtomicU8::new(TaskStatus::Pending as u8),
        callable,
        payload,
        result: Mutex::new(None),
        completed_cvar: Condvar::new(),
        completed_mutex: Mutex::new(false),
        cancelled: AtomicBool::new(false),
        wasm_module: None,
        wasm_func: None,
        dylib: None,
    });

    let task_id = {
        let mut slab = engine.broker.tasks.write().expect("Lock poisoned");
        slab.insert(task)
    };

    engine.sender.send(task_id).expect("Failed to send task ID");

    task_id
}

pub(crate) fn submit_batch(
    callables: Vec<Option<Py<PyAny>>>,
    payloads: Vec<Py<PyAny>>,
) -> Vec<usize> {
    let engine = get_engine();
    let mut ids = Vec::with_capacity(payloads.len());

    let mut slab = engine.broker.tasks.write().expect("Lock poisoned");

    for (callable, payload) in callables.into_iter().zip(payloads) {
        let task = Arc::new(Task {
            status: AtomicU8::new(TaskStatus::Pending as u8),
            callable,
            payload,
            result: Mutex::new(None),
            completed_cvar: Condvar::new(),
            completed_mutex: Mutex::new(false),
            cancelled: AtomicBool::new(false),
            wasm_module: None,
            wasm_func: None,
            dylib: None,
        });
        let task_id = slab.insert(task);
        ids.push(task_id);
        engine.sender.send(task_id).expect("Failed to send task ID");
    }

    ids
}

pub(crate) fn submit_wasm_task(
    module_name: String,
    func_name: String,
    payload: Py<PyAny>,
) -> usize {
    let engine = get_engine();

    let task = Arc::new(Task {
        status: AtomicU8::new(TaskStatus::Pending as u8),
        callable: None,
        payload,
        result: Mutex::new(None),
        completed_cvar: Condvar::new(),
        completed_mutex: Mutex::new(false),
        cancelled: AtomicBool::new(false),
        wasm_module: Some(module_name),
        wasm_func: Some(func_name),
        dylib: None,
    });

    let task_id = {
        let mut slab = engine.broker.tasks.write().expect("Lock poisoned");
        slab.insert(task)
    };

    engine.sender.send(task_id).expect("Failed to send task ID");

    task_id
}

pub(crate) fn submit_dylib_task(plugin_name: String, payload: Py<PyAny>) -> usize {
    let engine = get_engine();

    let task = Arc::new(Task {
        status: AtomicU8::new(TaskStatus::Pending as u8),
        callable: None,
        payload,
        result: Mutex::new(None),
        completed_cvar: Condvar::new(),
        completed_mutex: Mutex::new(false),
        cancelled: AtomicBool::new(false),
        wasm_module: None,
        wasm_func: None,
        dylib: Some(plugin_name),
    });

    let task_id = {
        let mut slab = engine.broker.tasks.write().expect("Lock poisoned");
        slab.insert(task)
    };

    engine.sender.send(task_id).expect("Failed to send task ID");

    task_id
}

pub(crate) fn cancel_task(task_id: usize) -> bool {
    let engine = get_engine();
    let task = {
        let slab = engine.broker.tasks.read().expect("Lock poisoned");
        slab.get(task_id).cloned()
    };

    if let Some(task) = task {
        let mut current = task.status.load(Ordering::Acquire);
        loop {
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
                    task.cancelled.store(true, Ordering::Release);
                    {
                        let mut res_guard = task.result.lock().unwrap();
                        *res_guard = Some(Err("Task cancelled".to_string()));
                    }
                    {
                        let mut completed = task.completed_mutex.lock().unwrap();
                        *completed = true;
                    }
                    task.completed_cvar.notify_all();
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

    let slab = engine.broker.tasks.read().expect("Lock poisoned");
    slab.get(task_id).map(|task| {
        let status_val = task.status.load(Ordering::Acquire);
        TaskStatus::to_status_string(status_val)
    })
}

pub(crate) fn wait_task(task_id: usize, timeout_ms: Option<u64>) -> Option<String> {
    let engine = get_engine();

    let task = {
        let slab = engine.broker.tasks.read().expect("Lock poisoned");
        slab.get(task_id).cloned()
    };

    if let Some(task) = task {
        let mut completed = task.completed_mutex.lock().unwrap();

        match timeout_ms {
            None => {
                while !*completed {
                    completed = task.completed_cvar.wait(completed).unwrap();
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
                        .unwrap();
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

    let task = {
        let slab = engine.broker.tasks.read().expect("Lock poisoned");
        slab.get(task_id).cloned()
    };

    task.and_then(|t| {
        let res = t.result.lock().unwrap();
        res.as_ref().map(|r| match r {
            Ok(val) => Ok(val.clone_ref(py)),
            Err(err) => Err(err.clone()),
        })
    })
}

pub(crate) fn free_task(task_id: usize) {
    let engine = get_engine();
    let mut slab = engine.broker.tasks.write().expect("Lock poisoned");
    if slab.contains(task_id) {
        slab.remove(task_id);
    }
}

pub(crate) fn get_slab_size() -> usize {
    let engine = get_engine();
    let slab = engine.broker.tasks.read().expect("Lock poisoned");
    slab.len()
}
