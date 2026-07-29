mod async_waker;
pub(crate) mod backends;
pub(crate) mod broker;
pub(crate) mod config;
pub(crate) mod ipc;
pub(crate) mod process_pool;
pub(crate) mod py_api;
pub(crate) mod registry;
pub(crate) mod task;
pub(crate) mod worker;
pub(crate) mod worker_process;

use pyo3::prelude::*;

#[pymodule]
fn _pyroxide(py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    py_api::register_py_module(py, m)
}
