use std::sync::RwLock;
use slab::Slab;

#[derive(Debug, Clone, PartialEq)]
enum TaskStatus {
    Pending,
    Running,
    Completed,
    Failed,
}

struct Task {
    status: TaskStatus,
    payload: String,
}

struct Broker {
    tasks: RwLock<Slab<Task>>,
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