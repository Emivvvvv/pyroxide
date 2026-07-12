pub mod broker;
pub mod worker;

use crate::broker::{get_task_result, get_task_status, wait_task};
use pyo3::prelude::*;

/// This function submits a task to the broker and returns the task ID.
#[pyfunction]
#[pyo3(signature = (callable, payload))]
fn submit_task(
    py: Python<'_>,
    callable: Option<Bound<'_, PyAny>>,
    payload: Bound<'_, PyAny>,
) -> PyResult<usize> {
    let py_callable = callable.map(|c| c.into_any().unbind());
    let py_payload = payload.into_any().unbind();

    let task_id = py.detach(move || broker::submit_task(py_callable, py_payload));

    Ok(task_id)
}

/// This function submits a batch of tasks to the broker under a single write lock.
#[pyfunction]
#[pyo3(signature = (callable, payloads))]
fn submit_batch(
    py: Python<'_>,
    callable: Option<Bound<'_, PyAny>>,
    payloads: Bound<'_, pyo3::types::PyList>,
) -> PyResult<Vec<usize>> {
    let py_callable = callable.map(|c| c.into_any().unbind());
    let mut py_payloads = Vec::with_capacity(payloads.len());
    let mut py_callables = Vec::with_capacity(payloads.len());

    for item in payloads.iter() {
        py_payloads.push(item.into_any().unbind());
        py_callables.push(py_callable.as_ref().map(|c| c.clone_ref(py)));
    }

    let task_ids = py.detach(move || broker::submit_batch(py_callables, py_payloads));

    Ok(task_ids)
}

/// This function cancels a task with the given ID.
#[pyfunction]
fn cancel_task(task_id: usize) -> PyResult<bool> {
    Ok(broker::cancel_task(task_id))
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

/// This function blocks the Python thread (releasing the GIL) until the task is complete or timeout.
#[pyfunction]
#[pyo3(signature = (task_id, timeout_ms=None))]
fn wait_status(py: Python<'_>, task_id: usize, timeout_ms: Option<u64>) -> PyResult<String> {
    let res = py.detach(move || wait_task(task_id, timeout_ms));
    match res {
        Some(status) => Ok(status),
        None => Err(pyo3::exceptions::PyValueError::new_err(format!(
            "Task ID {task_id} not found"
        ))),
    }
}

/// This function retrieves the result of a completed task.
#[pyfunction]
fn get_result<'py>(py: Python<'py>, task_id: usize) -> PyResult<Bound<'py, PyAny>> {
    match get_task_result(py, task_id) {
        Some(Ok(val)) => Ok(val.into_bound(py)),
        Some(Err(err)) => Err(pyo3::exceptions::PyRuntimeError::new_err(err)),
        None => Err(pyo3::exceptions::PyValueError::new_err(format!(
            "Task ID {task_id} result not found or task is still running/failed without details"
        ))),
    }
}

/// This function removes a task from the Slab to reclaim memory.
#[pyfunction]
fn free_task(task_id: usize) {
    broker::free_task(task_id);
}

/// This function returns the current number of tasks allocated in the Slab (useful for debugging leaks).
#[pyfunction]
fn get_slab_size() -> usize {
    broker::get_slab_size()
}

/// PyO3 entry point
#[pymodule]
fn _pyroxide(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(submit_task, m)?)?;
    m.add_function(wrap_pyfunction!(submit_batch, m)?)?;
    m.add_function(wrap_pyfunction!(get_status, m)?)?;
    m.add_function(wrap_pyfunction!(wait_status, m)?)?;
    m.add_function(wrap_pyfunction!(get_result, m)?)?;
    m.add_function(wrap_pyfunction!(free_task, m)?)?;
    m.add_function(wrap_pyfunction!(get_slab_size, m)?)?;
    m.add_function(wrap_pyfunction!(cancel_task, m)?)?;

    Ok(())
}
