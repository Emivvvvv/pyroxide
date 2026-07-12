pub mod broker;
pub mod worker;

use crate::broker::get_task_status;
use pyo3::prelude::*;

/// This function submits a task to the broker and returns the task ID.
#[pyfunction]
fn submit_task(py: Python<'_>, payload: String) -> PyResult<usize> {
    let task_id = py.detach(move || broker::submit_task(payload));

    Ok(task_id)
}

/// This function returns the status of the task with the given ID.
#[pyfunction]
fn get_status(task_id: usize) -> PyResult<String> {
    match get_task_status(task_id) {
        Some(status) => Ok(status),
        None => Err(pyo3::exceptions::PyValueError::new_err(format!(
            "Task ID {task_id} not found"
        ))),
    }
}

/// PyO3 entry point
#[pymodule]
fn _pyroxide(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(submit_task, m)?)?;
    m.add_function(wrap_pyfunction!(get_status, m)?)?;

    Ok(())
}
