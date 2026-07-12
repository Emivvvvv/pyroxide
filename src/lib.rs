pub mod broker;
pub mod worker;

use pyo3::prelude::*;

/// A simple Rust function that we will call from Python.
/// The #[pyfunction] macro generates the C-API bindings automatically.
#[pyfunction]
fn ping(message: String) -> PyResult<String> {
    let response = format!("Pyroxide Engine received: {}", message);
    Ok(response)
}

/// A Python module implemented in Rust.
/// The name of this function MUST match the `lib.name` in Cargo.toml.
#[pymodule]
fn pyroxide(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    // Register the `ping` function so Python can see it
    m.add_function(wrap_pyfunction!(ping, m)?)?;
    Ok(())
}