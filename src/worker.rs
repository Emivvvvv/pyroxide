use crate::broker::{Broker, TaskStatus};
use std::sync::Arc;

fn worker_loop(broker: Arc<Broker>, receiver: crossbeam_channel::Receiver<usize>) {
    while let Ok(task_id) = receiver.recv() {
        // Update task status
        {
            let mut slab = broker.tasks.write().unwrap();
            if let Some(task) = slab.get_mut(task_id) {
                task.status = TaskStatus::Running
            }
        } // Release lock

        // Simulate work
        std::thread::sleep(std::time::Duration::from_millis(500));

        // Update task status
        {
            let mut slab = broker.tasks.write().unwrap();
            if let Some(task) = slab.get_mut(task_id) {
                task.status = TaskStatus::Completed
            }
        } // Release lock
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
