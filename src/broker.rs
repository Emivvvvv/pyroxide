use std::sync::{RwLock, OnceLock, Arc};
use std::thread;
use std::thread::JoinHandle;

use slab::Slab;
use crate::worker::spawn_workers;

#[derive(Debug, Clone, PartialEq)]
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
    sender: crossbeam_channel::Sender<usize>,
}

impl Broker {
    fn new(sender: crossbeam_channel::Sender<usize>) -> Self {
        Self {
            tasks: RwLock::new(Slab::new()),
            sender
        }
    }
}

struct Engine {
    broker: Arc<Broker>,
    workers: Vec<JoinHandle<()>>,
}

static ENGINE: OnceLock<Engine> = OnceLock::new();

fn get_engine() -> &'static Engine {
    ENGINE.get_or_init(|| {
        let (sender, receiver) = crossbeam_channel::unbounded::<usize>();
        let broker = Arc::new(Broker::new(sender));

        let num_workers = thread::available_parallelism().unwrap().get();
        let workers = spawn_workers(num_workers, broker.clone(), receiver);

        Engine { broker, workers }
    })
}

pub(crate) fn submit_task(payload: String) -> usize {
    let engine = get_engine();

    let task_id = {
        let mut slab = engine.broker.tasks.write().expect("Lock poisoned");
        slab.insert(Task { status: TaskStatus::Pending, payload })
    }; // The guard is dropped here automatically

    engine.broker.sender.send(task_id).expect("Failed to send task ID");

    task_id
}