use std::sync::RwLock;
use slab::Slab;

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