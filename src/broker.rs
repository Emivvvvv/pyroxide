use std::sync::{Arc, OnceLock, RwLock};
use std::thread;
use std::thread::JoinHandle;

use crate::worker::spawn_workers;
use slab::Slab;
use strum::Display;

#[derive(Debug, Clone, PartialEq, Display)]
pub(crate) enum TaskStatus {
    Pending,
    Running,
    Completed,
    Failed,
}

pub(crate) struct Task {
    pub(crate) status: TaskStatus,
    payload: String,
}

pub(crate) struct Broker {
    pub(crate) tasks: RwLock<Slab<Task>>,
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
        let (sender, receiver) = crossbeam_channel::unbounded::<usize>();
        let broker = Arc::new(Broker::new());

        let num_workers = thread::available_parallelism().unwrap().get();
        let workers = spawn_workers(num_workers, broker.clone(), receiver);

        Engine {
            broker,
            sender,
            workers,
        }
    })
}

pub(crate) fn submit_task(payload: String) -> usize {
    let engine = get_engine();

    let task_id = {
        let mut slab = engine.broker.tasks.write().expect("Lock poisoned");
        slab.insert(Task {
            status: TaskStatus::Pending,
            payload,
        })
    }; // The guard is dropped here automatically

    engine.sender.send(task_id).expect("Failed to send task ID");

    task_id
}

pub(crate) fn get_task_status(task_id: usize) -> Option<String> {
    let engine = get_engine();

    let slab = engine.broker.tasks.read().expect("Lock poisoned");
    slab.get(task_id).map(|task| task.status.to_string())
}
