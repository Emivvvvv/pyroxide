mod async_waker;
pub mod backends;
pub mod broker;
pub mod config;
pub mod ipc;
pub mod process_pool;
pub mod py_api;
pub mod registry;
pub mod task;
pub mod worker;
pub mod worker_process;

use pyo3::prelude::*;

#[pymodule]
fn _pyroxide(py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    py_api::register_py_module(py, m)
}
