pub mod broker;
pub mod worker;

use pyo3::prelude::*;

/// This function submits a task to the broker and returns the task ID.
#[pyfunction]
fn submit_task(payload: String) -> PyResult<usize> {
    let task_id = broker::submit_task(payload);
    Ok(task_id)
}

/// PyO3 entry point
#[pymodule]
fn pyroxide(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Register the `ping` function so Python can see it
    m.add_function(wrap_pyfunction!(submit_task, m)?)?;
    Ok(())
}