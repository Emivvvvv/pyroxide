use crate::async_waker;
use crate::backends::dylib::{register_dylib_internal, unregister_dylib_internal};
use crate::backends::wasm::{get_wasm_module, register_wasm_module_internal};
use crate::broker::{self, get_engine_stats, get_task_result, get_task_status, wait_task};
use crate::config::{
    set_global_queue_timeout_ms_internal, set_global_wasm_memory_limit_bytes_internal,
    set_global_wasm_timeout_ms_internal,
};
use crate::registry::{
    DYLIB_PATHS, DylibRegistration, RegistryEntry, WASM_BYTES, get_dylib_paths,
    next_registry_generation,
};

use object::Object;
use pyo3::prelude::*;

pyo3::create_exception!(_pyroxide, ForkSafetyError, pyo3::exceptions::PyRuntimeError);

#[pyfunction]
pub(crate) fn set_global_wasm_memory_limit_bytes(bytes: usize) {
    set_global_wasm_memory_limit_bytes_internal(bytes);
}

#[pyfunction]
pub(crate) fn set_global_wasm_timeout_ms(ms: u64) {
    set_global_wasm_timeout_ms_internal(ms);
}

#[pyfunction]
pub(crate) fn set_global_queue_timeout_ms(ms: u64) {
    set_global_queue_timeout_ms_internal(ms);
}

#[pyfunction]
#[pyo3(signature = (name, library_path, free_fn_name=None))]
pub(crate) fn register_dylib(
    name: String,
    library_path: String,
    free_fn_name: Option<String>,
) -> PyResult<()> {
    broker::check_engine_process()?;
    register_dylib_internal(name.clone(), library_path.clone(), free_fn_name.clone())
        .map_err(pyo3::exceptions::PyValueError::new_err)?;
    let paths =
        DYLIB_PATHS.get_or_init(|| std::sync::RwLock::new(std::collections::HashMap::new()));
    let val_to_store = DylibRegistration {
        library_path,
        free_fn_name,
    };
    paths
        .write()
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?
        .insert(
            name,
            RegistryEntry {
                value: val_to_store,
                generation: next_registry_generation(),
            },
        );
    Ok(())
}

#[pyfunction]
pub(crate) fn unregister_dylib(name: String) -> PyResult<()> {
    broker::check_engine_process()?;
    if let Some(Ok(mut paths_guard)) = DYLIB_PATHS.get().map(|p| p.write()) {
        paths_guard.remove(&name);
    }
    unregister_dylib_internal(&name).map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
    Ok(())
}

#[pyfunction]
#[pyo3(signature = (plugin_name, symbol_name, payload, ffi_sig=None, isolated=false, queue_timeout_ms=None))]
pub(crate) fn submit_dylib_task(
    py: Python<'_>,
    plugin_name: String,
    symbol_name: String,
    payload: Bound<'_, PyAny>,
    ffi_sig: Option<(Vec<String>, String)>,
    isolated: bool,
    queue_timeout_ms: Option<u64>,
) -> PyResult<usize> {
    broker::check_engine_process()?;
    let py_payload = payload.into_any().unbind();
    py.detach(move || {
        broker::submit_dylib_task(
            plugin_name,
            symbol_name,
            py_payload,
            ffi_sig,
            isolated,
            queue_timeout_ms,
        )
    })
}

#[pyfunction]
#[pyo3(signature = (plugin_name, symbol_name, payloads, ffi_sig=None, isolated=false, queue_timeout_ms=None, payload_builder=None))]
#[allow(clippy::too_many_arguments)]
pub(crate) fn submit_dylib_batch(
    py: Python<'_>,
    plugin_name: String,
    symbol_name: String,
    payloads: Bound<'_, pyo3::types::PyList>,
    ffi_sig: Option<(Vec<String>, String)>,
    isolated: bool,
    queue_timeout_ms: Option<u64>,
    payload_builder: Option<Bound<'_, PyAny>>,
) -> PyResult<Vec<usize>> {
    broker::check_engine_process()?;
    let batch_len = payloads.len();
    let py_payloads = payloads.unbind();
    let py_payload_builder = payload_builder.map(Bound::unbind);
    py.detach(move || {
        broker::submit_dylib_batch(
            plugin_name,
            symbol_name,
            py_payloads,
            batch_len,
            ffi_sig,
            isolated,
            queue_timeout_ms,
            py_payload_builder,
        )
    })
}

#[pyfunction]
pub(crate) fn register_wasm_module(module_name: String, wasm_bytes: Vec<u8>) -> PyResult<()> {
    broker::check_engine_process()?;
    register_wasm_module_internal(module_name.clone(), wasm_bytes.clone())
        .map_err(pyo3::exceptions::PyValueError::new_err)?;
    let bytes = WASM_BYTES.get_or_init(|| std::sync::RwLock::new(std::collections::HashMap::new()));
    bytes
        .write()
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?
        .insert(
            module_name,
            RegistryEntry {
                value: wasm_bytes,
                generation: next_registry_generation(),
            },
        );
    Ok(())
}

#[pyfunction]
pub(crate) fn register_wasm_wat(module_name: String, wat_str: String) -> PyResult<()> {
    broker::check_engine_process()?;
    let wasm_bytes = wat::parse_str(&wat_str)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
    register_wasm_module_internal(module_name.clone(), wasm_bytes.clone())
        .map_err(pyo3::exceptions::PyValueError::new_err)?;
    let bytes = WASM_BYTES.get_or_init(|| std::sync::RwLock::new(std::collections::HashMap::new()));
    bytes
        .write()
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?
        .insert(
            module_name,
            RegistryEntry {
                value: wasm_bytes,
                generation: next_registry_generation(),
            },
        );
    Ok(())
}

#[pyfunction]
#[pyo3(signature = (module_name, func_name, payload, isolated=false, wasm_memory_limit_bytes=None, wasm_timeout_ms=None, queue_timeout_ms=None))]
#[allow(clippy::too_many_arguments)]
pub(crate) fn submit_wasm_task(
    py: Python<'_>,
    module_name: String,
    func_name: String,
    payload: Bound<'_, PyAny>,
    isolated: bool,
    wasm_memory_limit_bytes: Option<usize>,
    wasm_timeout_ms: Option<u64>,
    queue_timeout_ms: Option<u64>,
) -> PyResult<usize> {
    broker::check_engine_process()?;
    let py_payload = payload.into_any().unbind();
    py.detach(move || {
        broker::submit_wasm_task(
            module_name,
            func_name,
            py_payload,
            isolated,
            wasm_memory_limit_bytes,
            wasm_timeout_ms,
            queue_timeout_ms,
        )
    })
}

#[pyfunction]
#[pyo3(signature = (module_name, func_name, payloads, isolated=false, wasm_memory_limit_bytes=None, wasm_timeout_ms=None, queue_timeout_ms=None))]
#[allow(clippy::too_many_arguments)]
pub(crate) fn submit_wasm_batch(
    py: Python<'_>,
    module_name: String,
    func_name: String,
    payloads: Bound<'_, pyo3::types::PyList>,
    isolated: bool,
    wasm_memory_limit_bytes: Option<usize>,
    wasm_timeout_ms: Option<u64>,
    queue_timeout_ms: Option<u64>,
) -> PyResult<Vec<usize>> {
    broker::check_engine_process()?;
    let batch_len = payloads.len();
    let py_payloads = payloads.unbind();
    py.detach(move || {
        broker::submit_wasm_batch(
            module_name,
            func_name,
            py_payloads,
            batch_len,
            isolated,
            wasm_memory_limit_bytes,
            wasm_timeout_ms,
            queue_timeout_ms,
        )
    })
}

#[pyfunction]
#[pyo3(signature = (callable, payload, isolated=false, queue_timeout_ms=None))]
pub(crate) fn submit_task(
    py: Python<'_>,
    callable: Option<Bound<'_, PyAny>>,
    payload: Bound<'_, PyAny>,
    isolated: bool,
    queue_timeout_ms: Option<u64>,
) -> PyResult<usize> {
    broker::check_engine_process()?;
    let py_callable = callable.map(|c| c.into_any().unbind());
    let py_payload = payload.into_any().unbind();

    py.detach(move || broker::submit_task(py_callable, py_payload, isolated, queue_timeout_ms))
}

#[pyfunction]
#[pyo3(signature = (callable, payloads, isolated=false, queue_timeout_ms=None))]
pub(crate) fn submit_batch(
    py: Python<'_>,
    callable: Option<Bound<'_, PyAny>>,
    payloads: Bound<'_, pyo3::types::PyList>,
    isolated: bool,
    queue_timeout_ms: Option<u64>,
) -> PyResult<Vec<usize>> {
    broker::check_engine_process()?;
    let py_callable = callable.map(|c| c.into_any().unbind());
    let batch_len = payloads.len();
    let py_payloads = payloads.unbind();

    py.detach(move || {
        broker::submit_batch(
            py_callable,
            py_payloads,
            batch_len,
            isolated,
            queue_timeout_ms,
        )
    })
}

#[pyfunction]
pub(crate) fn cancel_task(task_id: usize) -> PyResult<bool> {
    broker::check_engine_process()?;
    Ok(broker::cancel_task(task_id))
}

#[pyfunction]
pub(crate) fn get_status(task_id: usize) -> PyResult<String> {
    broker::check_engine_process()?;
    match get_task_status(task_id) {
        Some(status) => Ok(status),
        None => Err(pyo3::exceptions::PyValueError::new_err(format!(
            "Task ID {task_id} not found"
        ))),
    }
}

#[pyfunction]
#[pyo3(signature = (task_id, timeout_ms=None))]
pub(crate) fn wait_status(
    py: Python<'_>,
    task_id: usize,
    timeout_ms: Option<u64>,
) -> PyResult<String> {
    broker::check_engine_process()?;
    let res = py.detach(move || wait_task(task_id, timeout_ms));
    match res {
        Some(status) => Ok(status),
        None => Err(pyo3::exceptions::PyValueError::new_err(format!(
            "Task ID {task_id} not found"
        ))),
    }
}

#[pyfunction]
pub(crate) fn get_result(py: Python<'_>, task_id: usize) -> PyResult<Bound<'_, PyAny>> {
    broker::check_engine_process()?;
    match get_task_result(py, task_id) {
        Some(Ok(val)) => Ok(val.into_bound(py)),
        Some(Err(err)) => Err(pyo3::exceptions::PyRuntimeError::new_err(err)),
        None => Err(pyo3::exceptions::PyValueError::new_err(format!(
            "Task ID {task_id} result not found or task is still running/failed without details"
        ))),
    }
}

#[pyfunction]
pub(crate) fn free_task(task_id: usize) -> PyResult<()> {
    broker::check_engine_process()?;
    broker::free_task(task_id);
    Ok(())
}

#[pyfunction]
pub(crate) fn get_slab_size() -> PyResult<usize> {
    broker::check_engine_process()?;
    Ok(broker::get_slab_size())
}

#[pyfunction]
pub(crate) fn get_wasm_exports(module_name: String) -> PyResult<Vec<String>> {
    broker::check_engine_process()?;
    let module = get_wasm_module(&module_name).ok_or_else(|| {
        pyo3::exceptions::PyValueError::new_err(format!(
            "WASM module '{module_name}' not registered"
        ))
    })?;

    let mut exports = Vec::new();
    for export in module.exports() {
        if export.ty().func().is_some() {
            exports.push(export.name().to_string());
        }
    }
    Ok(exports)
}

#[pyfunction]
pub(crate) fn get_dylib_exports(plugin_name: String) -> PyResult<Vec<String>> {
    broker::check_engine_process()?;
    let paths = get_dylib_paths();
    let raw_path = paths.get(&plugin_name).ok_or_else(|| {
        pyo3::exceptions::PyValueError::new_err(format!(
            "Dynamic library '{plugin_name}' not registered"
        ))
    })?;

    let parts: Vec<&str> = raw_path.split(';').collect();
    let library_path = parts[0];

    let file_data = std::fs::read(library_path).map_err(|e| {
        pyo3::exceptions::PyIOError::new_err(format!(
            "Failed to read dylib file {library_path}: {e}"
        ))
    })?;

    let file = object::File::parse(&*file_data).map_err(|e| {
        pyo3::exceptions::PyValueError::new_err(format!("Failed to parse dylib binary: {e}"))
    })?;

    let mut exports = Vec::new();
    if let Ok(file_exports) = file.exports() {
        for export in file_exports {
            if let Ok(name) = std::str::from_utf8(export.name()) {
                let mut s = name.to_string();
                if cfg!(target_os = "macos") && s.starts_with('_') {
                    s = s[1..].to_string();
                }
                if !s.starts_with('_')
                    && s != "pyroxide_plugin_free"
                    && s != "rust_eh_personality"
                    && s != "pyroxide_metadata"
                {
                    exports.push(s);
                }
            }
        }
    }

    Ok(exports)
}

#[pyfunction]
pub(crate) fn get_dylib_metadata(name: &str) -> PyResult<Option<String>> {
    broker::check_engine_process()?;
    crate::backends::dylib::get_dylib_metadata_internal(name)
        .map_err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>)
}

#[pyfunction]
pub(crate) fn get_dylib_path(name: String) -> PyResult<Option<String>> {
    broker::check_engine_process()?;
    let paths =
        DYLIB_PATHS.get_or_init(|| std::sync::RwLock::new(std::collections::HashMap::new()));
    let map = paths
        .read()
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
    Ok(map.get(&name).map(|entry| entry.value.library_path.clone()))
}

#[pyfunction]
pub(crate) fn start_worker_loop(socket_path: String) -> PyResult<()> {
    crate::worker_process::start_worker_loop(&socket_path)
        .map_err(pyo3::exceptions::PyRuntimeError::new_err)
}

#[pyfunction]
pub(crate) fn set_autofree(task_id: usize) -> PyResult<()> {
    broker::check_engine_process()?;
    broker::set_autofree(task_id);
    Ok(())
}

#[cfg(debug_assertions)]
#[pyfunction]
pub(crate) fn _arm_start_claim_test_hook() -> PyResult<()> {
    crate::worker::arm_start_claim_test_hook().map_err(pyo3::exceptions::PyRuntimeError::new_err)
}

#[cfg(debug_assertions)]
#[pyfunction]
pub(crate) fn _wait_start_claim_test_hook() -> PyResult<bool> {
    crate::worker::wait_start_claim_test_hook().map_err(pyo3::exceptions::PyRuntimeError::new_err)
}

#[cfg(debug_assertions)]
#[pyfunction]
pub(crate) fn _resume_start_claim_test_hook() -> PyResult<()> {
    crate::worker::resume_start_claim_test_hook().map_err(pyo3::exceptions::PyRuntimeError::new_err)
}

#[pyfunction]
#[pyo3(signature = (wait=true, cancel_pending=false))]
pub(crate) fn shutdown_engine(py: Python<'_>, wait: bool, cancel_pending: bool) -> PyResult<()> {
    py.detach(move || broker::shutdown_engine(wait, cancel_pending))
}

#[cfg(unix)]
#[pyfunction]
pub(crate) fn register_async_waker(fd: std::os::fd::RawFd) -> PyResult<()> {
    async_waker::set_async_waker_fd(fd).map_err(pyo3::exceptions::PyValueError::new_err)
}

#[cfg(unix)]
#[pyfunction]
pub(crate) fn unregister_async_waker(fd: std::os::fd::RawFd) -> bool {
    async_waker::clear_async_waker_fd(fd)
}

pub(crate) fn register_py_module(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add("ForkSafetyError", _py.get_type::<ForkSafetyError>())?;
    m.add_function(wrap_pyfunction!(set_global_wasm_memory_limit_bytes, m)?)?;
    m.add_function(wrap_pyfunction!(set_global_wasm_timeout_ms, m)?)?;
    m.add_function(wrap_pyfunction!(set_global_queue_timeout_ms, m)?)?;
    m.add_function(wrap_pyfunction!(submit_task, m)?)?;
    m.add_function(wrap_pyfunction!(submit_batch, m)?)?;
    m.add_function(wrap_pyfunction!(get_status, m)?)?;
    m.add_function(wrap_pyfunction!(wait_status, m)?)?;
    m.add_function(wrap_pyfunction!(get_result, m)?)?;
    m.add_function(wrap_pyfunction!(free_task, m)?)?;
    m.add_function(wrap_pyfunction!(get_slab_size, m)?)?;
    m.add_function(wrap_pyfunction!(cancel_task, m)?)?;
    m.add_function(wrap_pyfunction!(register_wasm_module, m)?)?;
    m.add_function(wrap_pyfunction!(register_wasm_wat, m)?)?;
    m.add_function(wrap_pyfunction!(submit_wasm_task, m)?)?;
    m.add_function(wrap_pyfunction!(submit_wasm_batch, m)?)?;
    m.add_function(wrap_pyfunction!(register_dylib, m)?)?;
    m.add_function(wrap_pyfunction!(unregister_dylib, m)?)?;
    m.add_function(wrap_pyfunction!(submit_dylib_task, m)?)?;
    m.add_function(wrap_pyfunction!(submit_dylib_batch, m)?)?;
    m.add_function(wrap_pyfunction!(get_wasm_exports, m)?)?;
    m.add_function(wrap_pyfunction!(get_dylib_exports, m)?)?;
    m.add_function(wrap_pyfunction!(get_dylib_metadata, m)?)?;
    m.add_function(wrap_pyfunction!(get_dylib_path, m)?)?;
    m.add_function(wrap_pyfunction!(start_worker_loop, m)?)?;
    m.add_function(wrap_pyfunction!(set_autofree, m)?)?;
    m.add_function(wrap_pyfunction!(shutdown_engine, m)?)?;
    m.add_function(wrap_pyfunction!(get_engine_stats, m)?)?;
    #[cfg(debug_assertions)]
    {
        m.add_function(wrap_pyfunction!(_arm_start_claim_test_hook, m)?)?;
        m.add_function(wrap_pyfunction!(_wait_start_claim_test_hook, m)?)?;
        m.add_function(wrap_pyfunction!(_resume_start_claim_test_hook, m)?)?;
    }
    #[cfg(unix)]
    m.add_function(wrap_pyfunction!(register_async_waker, m)?)?;
    #[cfg(unix)]
    m.add_function(wrap_pyfunction!(unregister_async_waker, m)?)?;

    Ok(())
}
