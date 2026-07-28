use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;

const GIL_POLICY: &str = "The Rust core runs under Python::detach and is not a scheduler.";

#[pyfunction]
fn run(py: Python<'_>, input: &[u8]) -> PyResult<Vec<u8>> {
    py.detach(|| benchmark_core::run_frame(input).map(|frame| frame.to_vec()))
        .map_err(|error| PyRuntimeError::new_err(format!("native ABI error {error}")))
}

#[pyfunction]
fn gil_policy() -> &'static str {
    GIL_POLICY
}

#[pymodule]
fn benchmark_pyo3(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(run, module)?)?;
    module.add_function(wrap_pyfunction!(gil_policy, module)?)?;
    Ok(())
}
