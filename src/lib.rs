pub mod broker;
pub mod process_pool;
pub mod worker;
pub mod worker_process;

use crate::broker::{get_task_result, get_task_status, wait_task};
use object::Object;
use pyo3::prelude::*;
use std::collections::HashMap;
use std::sync::OnceLock;
use std::sync::RwLock;
use wasmtime::{Engine, Module};

pub(crate) struct DylibPlugin {
    pub(crate) lib: libloading::Library,
    pub(crate) free_fn: unsafe extern "C" fn(ptr: *mut u8, len: usize),
    pub(crate) symbol_cache: RwLock<HashMap<String, PluginRunFn>>,
}

static DYLIB_PLUGINS: OnceLock<RwLock<HashMap<String, DylibPlugin>>> = OnceLock::new();

static DYLIB_PATHS: OnceLock<RwLock<HashMap<String, String>>> = OnceLock::new();
static WASM_BYTES: OnceLock<RwLock<HashMap<String, Vec<u8>>>> = OnceLock::new();

pub(crate) fn get_dylib_paths() -> HashMap<String, String> {
    DYLIB_PATHS
        .get_or_init(|| RwLock::new(HashMap::new()))
        .read()
        .unwrap_or_else(|e| e.into_inner())
        .clone()
}

pub(crate) fn get_wasm_bytes() -> HashMap<String, Vec<u8>> {
    WASM_BYTES
        .get_or_init(|| RwLock::new(HashMap::new()))
        .read()
        .unwrap_or_else(|e| e.into_inner())
        .clone()
}

pub(crate) fn get_shm_threshold() -> usize {
    static SHM_THRESHOLD: OnceLock<usize> = OnceLock::new();
    *SHM_THRESHOLD.get_or_init(|| {
        std::env::var("PYROXIDE_SHM_THRESHOLD")
            .ok()
            .and_then(|v| v.parse().ok())
            .unwrap_or(1024 * 1024)
    })
}

pub type PluginRunFn =
    unsafe extern "C" fn(ptr: *const u8, len: usize, out_len: *mut usize) -> *mut u8;
pub type PluginFreeFn = unsafe extern "C" fn(ptr: *mut u8, len: usize);

pub(crate) fn register_dylib_internal(name: String, library_path: String) -> Result<(), String> {
    // Safety: the library comes from a user-compiled source, the symbols are checked
    // by `get()` and return Err on mismatch, and the plugin never escapes the process.
    unsafe {
        let lib = libloading::Library::new(&library_path)
            .map_err(|e| format!("Failed to load dynamic library: {e}"))?;

        let free_fn = *lib
            .get::<PluginFreeFn>(b"pyroxide_plugin_free")
            .map_err(|e| format!("Missing symbol 'pyroxide_plugin_free': {e}"))?;

        let plugin = DylibPlugin {
            lib,
            free_fn,
            symbol_cache: RwLock::new(HashMap::new()),
        };

        let registry = DYLIB_PLUGINS.get_or_init(|| RwLock::new(HashMap::new()));
        let mut map = registry
            .write()
            .map_err(|e| format!("Registry poisoned: {e}"))?;
        map.insert(name, plugin);
        Ok(())
    }
}

/// Registers a dynamic shared library (.so / .dylib / .dll) with the Pyroxide engine.
#[pyfunction]
fn register_dylib(name: String, library_path: String) -> PyResult<()> {
    register_dylib_internal(name.clone(), library_path.clone())
        .map_err(pyo3::exceptions::PyValueError::new_err)?;
    let paths = DYLIB_PATHS.get_or_init(|| RwLock::new(HashMap::new()));
    paths
        .write()
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?
        .insert(name, library_path);
    Ok(())
}

pub(crate) fn execute_dylib(
    name: &str,
    symbol_name: &str,
    payload: &[u8],
) -> Result<Vec<u8>, String> {
    let registry = DYLIB_PLUGINS
        .get()
        .ok_or_else(|| "Dylib registry not initialized".to_string())?;
    let map = registry
        .read()
        .map_err(|e| format!("Registry lock poisoned: {e}"))?;
    let plugin = map
        .get(name)
        .ok_or_else(|| format!("Dynamic library '{name}' not registered"))?;

    // 1. Check if the symbol is already in the cache using a read lock
    let cached_fn = {
        let cache = plugin
            .symbol_cache
            .read()
            .map_err(|e| format!("Symbol cache read lock poisoned: {e}"))?;
        cache.get(symbol_name).cloned()
    };

    let run_fn = match cached_fn {
        Some(f) => f,
        None => {
            // 2. Not cached. Acquire a write lock, resolve the symbol from the library, and insert it
            let mut cache = plugin
                .symbol_cache
                .write()
                .map_err(|e| format!("Symbol cache write lock poisoned: {e}"))?;

            // Double check inside the write lock to prevent race conditions
            if let Some(&f) = cache.get(symbol_name) {
                f
            } else {
                unsafe {
                    let symbol: libloading::Symbol<PluginRunFn> = plugin
                        .lib
                        .get(symbol_name.as_bytes())
                        .map_err(|e| format!("Failed to find symbol '{symbol_name}': {e}"))?;
                    let f = *symbol;
                    cache.insert(symbol_name.to_string(), f);
                    f
                }
            }
        }
    };

    // Safety: run_fn/free_fn come from a trusted library, out_ptr is null-checked,
    // and free_fn is called exactly once with the pointer run_fn gave us.
    unsafe {
        let mut out_len: usize = 0;
        let out_ptr = (run_fn)(payload.as_ptr(), payload.len(), &mut out_len);
        if out_ptr.is_null() {
            return Err("Execution returned NULL pointer".to_string());
        }
        let output = std::slice::from_raw_parts(out_ptr, out_len).to_vec();
        (plugin.free_fn)(out_ptr, out_len);
        Ok(output)
    }
}

fn read_i32(payload: &[u8], offset: &mut usize) -> Result<i32, String> {
    if *offset + 4 > payload.len() {
        return Err("Payload too short for i32 argument".to_string());
    }
    let val = i32::from_ne_bytes(payload[*offset..*offset + 4].try_into().unwrap());
    *offset += 4;
    Ok(val)
}

fn read_i64(payload: &[u8], offset: &mut usize) -> Result<i64, String> {
    if *offset + 8 > payload.len() {
        return Err("Payload too short for i64 argument".to_string());
    }
    let val = i64::from_ne_bytes(payload[*offset..*offset + 8].try_into().unwrap());
    *offset += 8;
    Ok(val)
}

fn read_f32(payload: &[u8], offset: &mut usize) -> Result<f32, String> {
    if *offset + 4 > payload.len() {
        return Err("Payload too short for f32 argument".to_string());
    }
    let val = f32::from_ne_bytes(payload[*offset..*offset + 4].try_into().unwrap());
    *offset += 4;
    Ok(val)
}

fn read_f64(payload: &[u8], offset: &mut usize) -> Result<f64, String> {
    if *offset + 8 > payload.len() {
        return Err("Payload too short for f64 argument".to_string());
    }
    let val = f64::from_ne_bytes(payload[*offset..*offset + 8].try_into().unwrap());
    *offset += 8;
    Ok(val)
}

trait FfiRead {
    fn ffi_read(payload: &[u8], offset: &mut usize) -> Result<Self, String>
    where
        Self: Sized;
}

impl FfiRead for i32 {
    fn ffi_read(payload: &[u8], offset: &mut usize) -> Result<Self, String> {
        read_i32(payload, offset)
    }
}
impl FfiRead for i64 {
    fn ffi_read(payload: &[u8], offset: &mut usize) -> Result<Self, String> {
        read_i64(payload, offset)
    }
}
impl FfiRead for f32 {
    fn ffi_read(payload: &[u8], offset: &mut usize) -> Result<Self, String> {
        read_f32(payload, offset)
    }
}
impl FfiRead for f64 {
    fn ffi_read(payload: &[u8], offset: &mut usize) -> Result<Self, String> {
        read_f64(payload, offset)
    }
}

macro_rules! match_ffi {
    ($args_sig:expr, $ret_sig:expr, $run_ptr:expr, $payload:expr, $offset:expr, [
        $( ([$($arg_ty:ident),*] -> $ret_ty:ident) ),* $(,)?
    ]) => {
        match ($args_sig, $ret_sig) {
            $(
                (args, ret) if args == &[ $(stringify!($arg_ty).to_string()),* ] && ret == stringify!($ret_ty) => {
                    unsafe {
                        let f: unsafe extern "C" fn($($arg_ty),*) -> $ret_ty = std::mem::transmute($run_ptr);
                        let res = f(
                            $(
                                <$arg_ty as FfiRead>::ffi_read($payload, $offset)?
                            ),*
                        );
                        Ok(res.to_ne_bytes().to_vec())
                    }
                }
            )*
            _ => Err(format!(
                "Unsupported FFI signature mapping: {:?} -> {}",
                $args_sig, $ret_sig
            )),
        }
    }
}

pub(crate) fn execute_dylib_ffi(
    name: &str,
    symbol_name: &str,
    args_sig: &[String],
    ret_sig: &str,
    payload: &[u8],
) -> Result<Vec<u8>, String> {
    let registry = DYLIB_PLUGINS
        .get()
        .ok_or_else(|| "Dylib registry not initialized".to_string())?;
    let map = registry
        .read()
        .map_err(|e| format!("Registry lock poisoned: {e}"))?;
    let plugin = map
        .get(name)
        .ok_or_else(|| format!("Dynamic library '{name}' not registered"))?;

    let cached_ptr = {
        let cache = plugin
            .symbol_cache
            .read()
            .map_err(|e| format!("Symbol cache read lock poisoned: {e}"))?;
        cache.get(symbol_name).cloned()
    };

    let run_ptr = match cached_ptr {
        Some(f) => f as *const std::ffi::c_void,
        None => {
            let mut cache = plugin
                .symbol_cache
                .write()
                .map_err(|e| format!("Symbol cache write lock poisoned: {e}"))?;

            if let Some(&f) = cache.get(symbol_name) {
                f as *const std::ffi::c_void
            } else {
                unsafe {
                    let symbol: libloading::Symbol<*const std::ffi::c_void> = plugin
                        .lib
                        .get(symbol_name.as_bytes())
                        .map_err(|e| format!("Failed to find symbol '{symbol_name}': {e}"))?;
                    let ptr = *symbol;
                    let f: PluginRunFn = std::mem::transmute(ptr);
                    cache.insert(symbol_name.to_string(), f);
                    ptr
                }
            }
        }
    };

    let mut offset = 0;

    match_ffi!(args_sig, ret_sig, run_ptr, payload, &mut offset, [
        // 1 arg
        ([i32] -> i32), ([i32] -> i64), ([i32] -> f32), ([i32] -> f64),
        ([i64] -> i32), ([i64] -> i64), ([i64] -> f32), ([i64] -> f64),
        ([f32] -> i32), ([f32] -> i64), ([f32] -> f32), ([f32] -> f64),
        ([f64] -> i32), ([f64] -> i64), ([f64] -> f32), ([f64] -> f64),

        // 2 args
        ([i32, i32] -> i32), ([i32, i32] -> i64), ([i32, i32] -> f32), ([i32, i32] -> f64),
        ([i32, f64] -> i32), ([i32, f64] -> i64), ([i32, f64] -> f32), ([i32, f64] -> f64),
        ([f64, i32] -> i32), ([f64, i32] -> i64), ([f64, i32] -> f32), ([f64, i32] -> f64),
        ([f64, f64] -> i32), ([f64, f64] -> i64), ([f64, f64] -> f32), ([f64, f64] -> f64),
        ([i64, i64] -> i64), ([i64, i64] -> i32), ([i64, i64] -> f64),
        ([f32, f32] -> f32), ([f32, f32] -> f64),

        // 3 args
        ([i32, i32, i32] -> i32), ([i32, i32, i32] -> i64), ([i32, i32, i32] -> f64),
        ([f64, f64, f64] -> f64), ([f64, f64, f64] -> i32), ([f64, f64, f64] -> i64),
        ([i32, i32, f64] -> i32), ([i32, i32, f64] -> f64),
        ([f64, f64, i32] -> f64), ([f64, f64, i32] -> i32),
        ([i64, i64, i64] -> i64),

        // 4 args
        ([i32, i32, i32, i32] -> i32), ([i32, i32, i32, i32] -> i64), ([i32, i32, i32, i32] -> f64),
        ([f64, f64, f64, f64] -> f64), ([f64, f64, f64, f64] -> i32), ([f64, f64, f64, f64] -> i64),
        ([i32, i32, f64, f64] -> i32), ([i32, i32, f64, f64] -> f64),
        ([i64, i64, i64, i64] -> i64),

        // 5 args
        ([i32, i32, i32, i32, i32] -> i32), ([i32, i32, i32, i32, i32] -> i64), ([i32, i32, i32, i32, i32] -> f64),
        ([f64, f64, f64, f64, f64] -> f64), ([f64, f64, f64, f64, f64] -> i32), ([f64, f64, f64, f64, f64] -> i64),
        ([i64, i64, i64, i64, i64] -> i64),

        // 6 args
        ([i32, i32, i32, i32, i32, i32] -> i32), ([i32, i32, i32, i32, i32, i32] -> i64), ([i32, i32, i32, i32, i32, i32] -> f64),
        ([f64, f64, f64, f64, f64, f64] -> f64), ([f64, f64, f64, f64, f64, f64] -> i32), ([f64, f64, f64, f64, f64, f64] -> i64),
        ([i64, i64, i64, i64, i64, i64] -> i64),

        // 7 args
        ([i32, i32, i32, i32, i32, i32, i32] -> i32), ([i32, i32, i32, i32, i32, i32, i32] -> i64), ([i32, i32, i32, i32, i32, i32, i32] -> f64),
        ([f64, f64, f64, f64, f64, f64, f64] -> f64), ([f64, f64, f64, f64, f64, f64, f64] -> i32), ([f64, f64, f64, f64, f64, f64, f64] -> i64),
        ([i64, i64, i64, i64, i64, i64, i64] -> i64),

        // 8 args
        ([i32, i32, i32, i32, i32, i32, i32, i32] -> i32), ([i32, i32, i32, i32, i32, i32, i32, i32] -> i64), ([i32, i32, i32, i32, i32, i32, i32, i32] -> f64),
        ([f64, f64, f64, f64, f64, f64, f64, f64] -> f64), ([f64, f64, f64, f64, f64, f64, f64, f64] -> i32), ([f64, f64, f64, f64, f64, f64, f64, f64] -> i64),
        ([i64, i64, i64, i64, i64, i64, i64, i64] -> i64),
    ])
}

/// Submits a task to be executed by a registered dynamic shared library (dylib).
#[pyfunction]
#[pyo3(signature = (plugin_name, symbol_name, payload, ffi_sig=None, isolated=false))]
fn submit_dylib_task(
    py: Python<'_>,
    plugin_name: String,
    symbol_name: String,
    payload: Bound<'_, PyAny>,
    ffi_sig: Option<(Vec<String>, String)>,
    isolated: bool,
) -> PyResult<usize> {
    let py_payload = payload.into_any().unbind();
    let task_id = py.detach(move || {
        broker::submit_dylib_task(plugin_name, symbol_name, py_payload, ffi_sig, isolated)
    });
    Ok(task_id)
}

static WASM_ENGINE: OnceLock<Engine> = OnceLock::new();
static WASM_REGISTRY: OnceLock<RwLock<HashMap<String, Module>>> = OnceLock::new();

pub(crate) fn get_wasm_engine() -> &'static Engine {
    WASM_ENGINE.get_or_init(Engine::default)
}

pub(crate) fn get_wasm_module(module_name: &str) -> Option<Module> {
    let registry = WASM_REGISTRY.get()?;
    let map = registry.read().ok()?;
    map.get(module_name).cloned()
}

pub(crate) fn register_wasm_module_internal(
    module_name: String,
    wasm_bytes: Vec<u8>,
) -> Result<(), String> {
    let engine = get_wasm_engine();
    let module = Module::new(engine, &wasm_bytes)
        .map_err(|e| format!("Failed to compile WASM module: {e}"))?;

    let registry = WASM_REGISTRY.get_or_init(|| RwLock::new(HashMap::new()));
    let mut map = registry
        .write()
        .map_err(|e| format!("Registry lock poisoned: {e}"))?;
    map.insert(module_name, module);
    Ok(())
}

/// This function registers a WebAssembly module binary under a name in the global registry.
#[pyfunction]
fn register_wasm_module(module_name: String, wasm_bytes: Vec<u8>) -> PyResult<()> {
    register_wasm_module_internal(module_name.clone(), wasm_bytes.clone())
        .map_err(pyo3::exceptions::PyValueError::new_err)?;
    let bytes = WASM_BYTES.get_or_init(|| RwLock::new(HashMap::new()));
    bytes
        .write()
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?
        .insert(module_name, wasm_bytes);
    Ok(())
}

/// This function submits a WebAssembly task to the broker.
#[pyfunction]
#[pyo3(signature = (module_name, func_name, payload, isolated=false))]
fn submit_wasm_task(
    py: Python<'_>,
    module_name: String,
    func_name: String,
    payload: Bound<'_, PyAny>,
    isolated: bool,
) -> PyResult<usize> {
    let py_payload = payload.into_any().unbind();
    let task_id =
        py.detach(move || broker::submit_wasm_task(module_name, func_name, py_payload, isolated));
    Ok(task_id)
}

/// This function submits a task to the broker and returns the task ID.
#[pyfunction]
#[pyo3(signature = (callable, payload, isolated=false))]
fn submit_task(
    py: Python<'_>,
    callable: Option<Bound<'_, PyAny>>,
    payload: Bound<'_, PyAny>,
    isolated: bool,
) -> PyResult<usize> {
    let py_callable = callable.map(|c| c.into_any().unbind());
    let py_payload = payload.into_any().unbind();

    let task_id = py.detach(move || broker::submit_task(py_callable, py_payload, isolated));

    Ok(task_id)
}

/// This function submits a batch of tasks to the broker under a single write lock.
#[pyfunction]
#[pyo3(signature = (callable, payloads, isolated=false))]
fn submit_batch(
    py: Python<'_>,
    callable: Option<Bound<'_, PyAny>>,
    payloads: Bound<'_, pyo3::types::PyList>,
    isolated: bool,
) -> PyResult<Vec<usize>> {
    let py_callable = callable.map(|c| c.into_any().unbind());
    let mut py_payloads = Vec::with_capacity(payloads.len());
    let mut py_callables = Vec::with_capacity(payloads.len());

    for item in payloads.iter() {
        py_payloads.push(item.into_any().unbind());
        py_callables.push(py_callable.as_ref().map(|c| c.clone_ref(py)));
    }

    let task_ids = py.detach(move || broker::submit_batch(py_callables, py_payloads, isolated));

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

#[pyfunction]
fn get_wasm_exports(module_name: String) -> PyResult<Vec<String>> {
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
fn get_dylib_exports(plugin_name: String) -> PyResult<Vec<String>> {
    let paths = get_dylib_paths();
    let library_path = paths.get(&plugin_name).ok_or_else(|| {
        pyo3::exceptions::PyValueError::new_err(format!(
            "Dynamic library '{plugin_name}' not registered"
        ))
    })?;

    let file_data = std::fs::read(library_path).map_err(|e| {
        pyo3::exceptions::PyIOError::new_err(format!("Failed to read dylib file: {e}"))
    })?;

    let file = object::File::parse(&*file_data).map_err(|e| {
        pyo3::exceptions::PyValueError::new_err(format!("Failed to parse dylib binary: {e}"))
    })?;

    let mut exports = Vec::new();
    if let Ok(file_exports) = file.exports() {
        for export in file_exports {
            if let Ok(name) = std::str::from_utf8(export.name()) {
                let s = name.to_string();
                if !s.starts_with('_') && s != "pyroxide_plugin_free" && s != "rust_eh_personality"
                {
                    exports.push(s);
                }
            }
        }
    }

    Ok(exports)
}

#[pyfunction]
fn start_worker_loop(socket_path: String) -> PyResult<()> {
    worker_process::start_worker_loop(&socket_path)
        .map_err(pyo3::exceptions::PyRuntimeError::new_err)
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
    m.add_function(wrap_pyfunction!(register_wasm_module, m)?)?;
    m.add_function(wrap_pyfunction!(submit_wasm_task, m)?)?;
    m.add_function(wrap_pyfunction!(register_dylib, m)?)?;
    m.add_function(wrap_pyfunction!(submit_dylib_task, m)?)?;
    m.add_function(wrap_pyfunction!(get_wasm_exports, m)?)?;
    m.add_function(wrap_pyfunction!(get_dylib_exports, m)?)?;
    m.add_function(wrap_pyfunction!(start_worker_loop, m)?)?;

    Ok(())
}
