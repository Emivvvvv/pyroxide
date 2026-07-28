pub mod broker;
pub mod process_pool;
pub mod worker;
pub mod worker_process;

use crate::broker::{get_engine_stats, get_task_result, get_task_status, wait_task};
use object::Object;
use pyo3::prelude::*;
use std::collections::HashMap;
use std::sync::Arc;
use std::sync::OnceLock;
use std::sync::RwLock;
use std::sync::atomic::{AtomicBool, AtomicI32, AtomicU64, AtomicUsize, Ordering};
use wasmtime::{Engine, Module};

pyo3::create_exception!(_pyroxide, ForkSafetyError, pyo3::exceptions::PyRuntimeError);

pub(crate) struct GlobalConfig {
    pub(crate) wasm_memory_limit_bytes: AtomicUsize,
    pub(crate) wasm_timeout_ms: AtomicU64,
    pub(crate) queue_timeout_ms: AtomicU64,
}

pub(crate) static CONFIG: GlobalConfig = GlobalConfig {
    wasm_memory_limit_bytes: AtomicUsize::new(100 * 1024 * 1024), // 100 MB default
    wasm_timeout_ms: AtomicU64::new(1000),                        // 1 second default
    queue_timeout_ms: AtomicU64::new(1000),                       // 1 second default
};
static GLOBAL_WASM_MEMORY_SET: AtomicBool = AtomicBool::new(false);
static GLOBAL_WASM_TIMEOUT_SET: AtomicBool = AtomicBool::new(false);
static GLOBAL_QUEUE_TIMEOUT_SET: AtomicBool = AtomicBool::new(false);

pub(crate) fn get_wasm_memory_limit_bytes() -> usize {
    if GLOBAL_WASM_MEMORY_SET.load(Ordering::Acquire) {
        return CONFIG.wasm_memory_limit_bytes.load(Ordering::Relaxed);
    }
    std::env::var("PYROXIDE_WASM_MEMORY_LIMIT_BYTES")
        .ok()
        .and_then(|value| value.parse::<usize>().ok())
        .filter(|value| *value > 0 && *value <= i32::MAX as usize)
        .unwrap_or(100 * 1024 * 1024)
}

pub(crate) fn get_wasm_timeout_ms() -> u64 {
    if GLOBAL_WASM_TIMEOUT_SET.load(Ordering::Acquire) {
        return CONFIG.wasm_timeout_ms.load(Ordering::Relaxed);
    }
    std::env::var("PYROXIDE_WASM_TIMEOUT_MS")
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(1000)
}

pub(crate) fn get_queue_timeout_ms() -> u64 {
    if GLOBAL_QUEUE_TIMEOUT_SET.load(Ordering::Acquire) {
        return CONFIG.queue_timeout_ms.load(Ordering::Relaxed);
    }
    std::env::var("PYROXIDE_QUEUE_TIMEOUT_MS")
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .unwrap_or(1000)
}

pub(crate) const MAX_IPC_METADATA_BYTES: usize = 1024 * 1024;

pub(crate) fn get_max_ipc_frame_bytes() -> usize {
    static VALUE: OnceLock<usize> = OnceLock::new();
    *VALUE.get_or_init(|| {
        std::env::var("PYROXIDE_MAX_IPC_FRAME_BYTES")
            .ok()
            .and_then(|value| value.parse::<usize>().ok())
            .filter(|value| *value > 0)
            .unwrap_or(64 * 1024 * 1024)
    })
}

pub(crate) fn get_max_native_output_bytes() -> usize {
    static VALUE: OnceLock<usize> = OnceLock::new();
    *VALUE.get_or_init(|| {
        std::env::var("PYROXIDE_MAX_NATIVE_OUTPUT_BYTES")
            .ok()
            .and_then(|value| value.parse::<usize>().ok())
            .filter(|value| *value > 0)
            .unwrap_or(64 * 1024 * 1024)
    })
}

pub(crate) fn validate_native_output_len(len: usize, max: usize) -> Result<(), String> {
    if len > max {
        return Err(format!(
            "Native plugin output length {len} exceeds limit {max}"
        ));
    }
    Ok(())
}

pub(crate) fn checked_ipc_len(len: u64, max: usize, label: &str) -> Result<usize, String> {
    let len = usize::try_from(len).map_err(|_| format!("IPC {label} length is too large"))?;
    if len > max {
        return Err(format!("IPC {label} length {len} exceeds limit {max}"));
    }
    Ok(len)
}

pub(crate) fn get_wasm_tick_ms() -> u64 {
    std::env::var("PYROXIDE_WASM_TICK_MS")
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .filter(|value| *value > 0)
        .unwrap_or(10)
}

pub(crate) fn validate_wasm_input_len(len: usize, limit: usize) -> Result<i32, String> {
    if len > limit {
        return Err(format!(
            "WASM input length {len} exceeds memory limit {limit}"
        ));
    }
    i32::try_from(len).map_err(|_| "WASM input is too large for guest memory".to_string())
}

pub(crate) fn validate_wasm_output_range(
    ptr: i32,
    len: i32,
    limit: usize,
    memory_size: usize,
) -> Result<(usize, usize), String> {
    let ptr = usize::try_from(ptr).map_err(|_| "WASM returned a negative output pointer")?;
    let len = usize::try_from(len).map_err(|_| "WASM returned a negative output length")?;
    if len > limit {
        return Err(format!(
            "WASM output length {len} exceeds memory limit {limit}"
        ));
    }
    let end = ptr
        .checked_add(len)
        .ok_or_else(|| "WASM output range overflowed".to_string())?;
    if end > memory_size {
        return Err(format!(
            "WASM output range {ptr}..{end} exceeds guest memory size {memory_size}"
        ));
    }
    Ok((ptr, len))
}

#[pyfunction]
fn set_global_wasm_memory_limit_bytes(bytes: usize) {
    CONFIG
        .wasm_memory_limit_bytes
        .store(bytes, Ordering::Relaxed);
    GLOBAL_WASM_MEMORY_SET.store(true, Ordering::Release);
}

#[pyfunction]
fn set_global_wasm_timeout_ms(ms: u64) {
    CONFIG.wasm_timeout_ms.store(ms, Ordering::Relaxed);
    GLOBAL_WASM_TIMEOUT_SET.store(true, Ordering::Release);
}

#[pyfunction]
fn set_global_queue_timeout_ms(ms: u64) {
    CONFIG.queue_timeout_ms.store(ms, Ordering::Relaxed);
    GLOBAL_QUEUE_TIMEOUT_SET.store(true, Ordering::Release);
}

#[derive(Hash, Eq, PartialEq, Clone)]
pub(crate) struct SymbolKey {
    pub(crate) symbol_name: String,
    pub(crate) signature: Option<(Vec<String>, String)>,
}

pub(crate) struct DylibPlugin {
    pub(crate) lib: libloading::Library,
    pub(crate) free_fn: Option<PluginFreeFn>,
    pub(crate) symbol_cache: RwLock<HashMap<SymbolKey, usize>>,
    pub(crate) ffi_call_cache: RwLock<HashMap<String, HashMap<SignatureCode, PreparedFfiCall>>>,
}

static DYLIB_PLUGINS: OnceLock<RwLock<HashMap<String, Arc<DylibPlugin>>>> = OnceLock::new();

#[derive(Clone)]
pub(crate) struct RegistryEntry<T> {
    pub(crate) value: T,
    pub(crate) generation: u64,
}

pub(crate) enum RegistrySync<T> {
    Missing,
    Current,
    Changed(RegistryEntry<T>),
}

fn registry_sync<T: Clone>(
    registrations: &HashMap<String, RegistryEntry<T>>,
    name: &str,
    known_generation: Option<u64>,
) -> RegistrySync<T> {
    match registrations.get(name) {
        None => RegistrySync::Missing,
        Some(entry) if known_generation == Some(entry.generation) => RegistrySync::Current,
        Some(entry) => RegistrySync::Changed(entry.clone()),
    }
}

static NEXT_REGISTRY_GENERATION: AtomicU64 = AtomicU64::new(1);
static DYLIB_PATHS: OnceLock<RwLock<HashMap<String, RegistryEntry<String>>>> = OnceLock::new();
static WASM_BYTES: OnceLock<RwLock<HashMap<String, RegistryEntry<Vec<u8>>>>> = OnceLock::new();

fn next_registry_generation() -> u64 {
    NEXT_REGISTRY_GENERATION.fetch_add(1, Ordering::Relaxed)
}

pub(crate) fn get_dylib_paths() -> HashMap<String, String> {
    DYLIB_PATHS
        .get_or_init(|| RwLock::new(HashMap::new()))
        .read()
        .unwrap_or_else(|e| e.into_inner())
        .iter()
        .map(|(name, entry)| (name.clone(), entry.value.clone()))
        .collect()
}

pub(crate) fn get_dylib_registrations() -> HashMap<String, RegistryEntry<String>> {
    DYLIB_PATHS
        .get_or_init(|| RwLock::new(HashMap::new()))
        .read()
        .unwrap_or_else(|e| e.into_inner())
        .clone()
}

pub(crate) fn get_wasm_registrations() -> HashMap<String, RegistryEntry<Vec<u8>>> {
    WASM_BYTES
        .get_or_init(|| RwLock::new(HashMap::new()))
        .read()
        .unwrap_or_else(|e| e.into_inner())
        .clone()
}

pub(crate) fn get_dylib_registration_sync(
    name: &str,
    known_generation: Option<u64>,
) -> RegistrySync<String> {
    let registrations = DYLIB_PATHS
        .get_or_init(|| RwLock::new(HashMap::new()))
        .read()
        .unwrap_or_else(|e| e.into_inner());
    registry_sync(&registrations, name, known_generation)
}

pub(crate) fn get_wasm_registration_sync(
    name: &str,
    known_generation: Option<u64>,
) -> RegistrySync<Vec<u8>> {
    let registrations = WASM_BYTES
        .get_or_init(|| RwLock::new(HashMap::new()))
        .read()
        .unwrap_or_else(|e| e.into_inner());
    registry_sync(&registrations, name, known_generation)
}

pub(crate) fn has_dylib_registration(name: &str) -> bool {
    DYLIB_PATHS
        .get_or_init(|| RwLock::new(HashMap::new()))
        .read()
        .unwrap_or_else(|e| e.into_inner())
        .contains_key(name)
}

pub(crate) fn has_wasm_registration(name: &str) -> bool {
    WASM_BYTES
        .get_or_init(|| RwLock::new(HashMap::new()))
        .read()
        .unwrap_or_else(|e| e.into_inner())
        .contains_key(name)
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

pub(crate) fn register_dylib_internal(
    name: String,
    library_path: String,
    free_fn_name: Option<String>,
) -> Result<(), String> {
    // Safety: the library comes from a user-compiled source, the symbols are checked
    // by `get()` and return Err on mismatch, and the plugin never escapes the process.
    unsafe {
        let lib = libloading::Library::new(&library_path)
            .map_err(|e| format!("Failed to load dynamic library: {e}"))?;

        let free_symbol_name = free_fn_name.unwrap_or_else(|| "pyroxide_plugin_free".to_string());
        let free_fn = lib
            .get::<PluginFreeFn>(free_symbol_name.as_bytes())
            .map(|sym| *sym)
            .ok();

        let plugin = Arc::new(DylibPlugin {
            lib,
            free_fn,
            symbol_cache: RwLock::new(HashMap::new()),
            ffi_call_cache: RwLock::new(HashMap::new()),
        });

        let registry = DYLIB_PLUGINS.get_or_init(|| RwLock::new(HashMap::new()));
        let mut map = registry
            .write()
            .map_err(|e| format!("Registry poisoned: {e}"))?;
        map.insert(name, plugin);
        Ok(())
    }
}

pub(crate) fn unregister_dylib_internal(name: &str) -> Result<(), String> {
    if let Some(registry) = DYLIB_PLUGINS.get() {
        registry
            .write()
            .map_err(|e| format!("Registry poisoned: {e}"))?
            .remove(name);
    }
    Ok(())
}

/// Registers a dynamic shared library (.so / .dylib / .dll) with the Pyroxide engine.
#[pyfunction]
#[pyo3(signature = (name, library_path, free_fn_name=None))]
fn register_dylib(
    name: String,
    library_path: String,
    free_fn_name: Option<String>,
) -> PyResult<()> {
    broker::check_engine_process()?;
    register_dylib_internal(name.clone(), library_path.clone(), free_fn_name.clone())
        .map_err(pyo3::exceptions::PyValueError::new_err)?;
    let paths = DYLIB_PATHS.get_or_init(|| RwLock::new(HashMap::new()));
    let val_to_store = if let Some(ref free_name) = free_fn_name {
        format!("{library_path};{free_name}")
    } else {
        library_path
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

/// Unregisters a dynamic shared library from the registries.
#[pyfunction]
fn unregister_dylib(name: String) -> PyResult<()> {
    broker::check_engine_process()?;
    if let Some(Ok(mut paths_guard)) = DYLIB_PATHS.get().map(|p| p.write()) {
        paths_guard.remove(&name);
    }
    unregister_dylib_internal(&name).map_err(pyo3::exceptions::PyRuntimeError::new_err)?;
    Ok(())
}

pub(crate) fn execute_dylib(
    name: &str,
    symbol_name: &str,
    payload: &[u8],
) -> Result<Vec<u8>, String> {
    if payload.is_empty() {
        return Err("Payload cannot be empty for raw binary tasks".to_string());
    }

    let registry = DYLIB_PLUGINS
        .get()
        .ok_or_else(|| "Dylib registry not initialized".to_string())?;
    let plugin = {
        let map = registry
            .read()
            .map_err(|e| format!("Registry lock poisoned: {e}"))?;
        map.get(name)
            .cloned()
            .ok_or_else(|| format!("Dynamic library '{name}' not registered"))?
    };

    let key = SymbolKey {
        symbol_name: symbol_name.to_string(),
        signature: None,
    };

    // 1. Check if the symbol is already in the cache using a read lock
    let cached_val = {
        let cache = plugin
            .symbol_cache
            .read()
            .map_err(|e| format!("Symbol cache read lock poisoned: {e}"))?;
        cache.get(&key).cloned()
    };

    let run_ptr_val = match cached_val {
        Some(v) => v,
        None => {
            // 2. Not cached. Acquire a write lock, resolve the symbol from the library, and insert it
            let mut cache = plugin
                .symbol_cache
                .write()
                .map_err(|e| format!("Symbol cache write lock poisoned: {e}"))?;

            // Double check inside the write lock to prevent race conditions
            if let Some(&v) = cache.get(&key) {
                v
            } else {
                unsafe {
                    let symbol: libloading::Symbol<PluginRunFn> = plugin
                        .lib
                        .get(symbol_name.as_bytes())
                        .map_err(|e| format!("Failed to find symbol '{symbol_name}': {e}"))?;
                    let f = *symbol;
                    let val = f as *const std::ffi::c_void as usize;
                    cache.insert(key, val);
                    val
                }
            }
        }
    };
    // Safety: `run_ptr_val` was resolved from this live library as a
    // `PluginRunFn` and the cache key separates raw and typed FFI signatures.
    let run_fn: PluginRunFn =
        unsafe { std::mem::transmute(run_ptr_val as *const std::ffi::c_void) };

    let free_fn = plugin.free_fn.ok_or_else(|| {
        "Raw binary tasks require the symbol 'pyroxide_plugin_free' to prevent memory leaks."
            .to_string()
    })?;

    let res = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| unsafe {
        let mut out_len: usize = 0;
        let out_ptr = (run_fn)(payload.as_ptr(), payload.len(), &mut out_len);
        if out_ptr.is_null() {
            return Err("Execution returned NULL pointer".to_string());
        }
        if let Err(error) = validate_native_output_len(out_len, get_max_native_output_bytes()) {
            (free_fn)(out_ptr, out_len);
            return Err(error);
        }
        let output = std::slice::from_raw_parts(out_ptr, out_len).to_vec();
        (free_fn)(out_ptr, out_len);
        Ok(output)
    }));
    match res {
        Ok(inner_res) => inner_res,
        Err(_) => Err("Dynamic library execution panicked".to_string()),
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[repr(u8)]
pub(crate) enum FfiType {
    I32 = 0,
    U32 = 1,
    I64 = 2,
    U64 = 3,
    Isize = 4,
    Usize = 5,
    F32 = 6,
    F64 = 7,
}

impl FfiType {
    pub(crate) fn parse(name: &str) -> Result<Self, String> {
        match name {
            "i32" => Ok(FfiType::I32),
            "u32" => Ok(FfiType::U32),
            "i64" => Ok(FfiType::I64),
            "u64" => Ok(FfiType::U64),
            "isize" => Ok(FfiType::Isize),
            "usize" => Ok(FfiType::Usize),
            "f32" => Ok(FfiType::F32),
            "f64" => Ok(FfiType::F64),
            _ => Err(format!(
                "Unsupported FFI type '{name}'. Supported types: i32, u32, i64, u64, isize, usize, f32, f64."
            )),
        }
    }

    #[allow(dead_code)]
    pub(crate) fn name(self) -> &'static str {
        match self {
            FfiType::I32 => "i32",
            FfiType::U32 => "u32",
            FfiType::I64 => "i64",
            FfiType::U64 => "u64",
            FfiType::Isize => "isize",
            FfiType::Usize => "usize",
            FfiType::F32 => "f32",
            FfiType::F64 => "f64",
        }
    }

    pub(crate) fn byte_width(self) -> usize {
        match self {
            FfiType::I32 | FfiType::U32 | FfiType::F32 => 4,
            FfiType::I64 | FfiType::U64 | FfiType::F64 => 8,
            FfiType::Isize | FfiType::Usize => std::mem::size_of::<usize>(),
        }
    }

    pub(crate) fn code(self) -> u8 {
        self as u8
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub(crate) struct SignatureCode(pub(crate) u32);

impl SignatureCode {
    pub(crate) fn new(args: &[FfiType], ret: FfiType) -> Result<Self, String> {
        if args.len() > 8 {
            return Err(format!(
                "FFI signatures support at most 8 arguments; received {}.",
                args.len()
            ));
        }
        let mut code = (args.len() as u32) & 0x0F;
        code |= ((ret.code() as u32) & 0x07) << 4;
        for (i, &arg) in args.iter().enumerate() {
            let shift = 7 + i * 3;
            code |= ((arg.code() as u32) & 0x07) << shift;
        }
        Ok(SignatureCode(code))
    }

    pub(crate) fn from_str_sigs(args_sig: &[String], ret_sig: &str) -> Result<Self, String> {
        if args_sig.len() > 8 {
            return Err(format!(
                "FFI signatures support at most 8 arguments; received {}.",
                args_sig.len()
            ));
        }
        let ret = FfiType::parse(ret_sig)?;
        let mut code = (args_sig.len() as u32) & 0x0F;
        code |= ((ret.code() as u32) & 0x07) << 4;
        for (i, arg_str) in args_sig.iter().enumerate() {
            let arg = FfiType::parse(arg_str)?;
            let shift = 7 + i * 3;
            code |= ((arg.code() as u32) & 0x07) << shift;
        }
        Ok(SignatureCode(code))
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ParsedFfiSignature {
    pub(crate) args: Vec<FfiType>,
    pub(crate) ret: FfiType,
    pub(crate) encoded: SignatureCode,
    pub(crate) expected_payload_len: usize,
}

impl ParsedFfiSignature {
    pub(crate) fn parse(args_sig: &[String], ret_sig: &str) -> Result<Self, String> {
        if args_sig.len() > 8 {
            return Err(format!(
                "FFI signatures support at most 8 arguments; received {}.",
                args_sig.len()
            ));
        }
        let mut args = Vec::with_capacity(args_sig.len());
        let mut expected_payload_len = 0;
        for arg_str in args_sig {
            let t = FfiType::parse(arg_str)?;
            expected_payload_len += t.byte_width();
            args.push(t);
        }
        let ret = FfiType::parse(ret_sig)?;
        let encoded = SignatureCode::new(&args, ret)?;
        Ok(ParsedFfiSignature {
            args,
            ret,
            encoded,
            expected_payload_len,
        })
    }
}

pub(crate) trait FfiArg: Sized {
    fn read(payload: &[u8], offset: &mut usize) -> Result<Self, String>;
}

impl FfiArg for i32 {
    fn read(payload: &[u8], offset: &mut usize) -> Result<Self, String> {
        if *offset + 4 > payload.len() {
            return Err("Payload too short for i32".to_string());
        }
        let val = i32::from_ne_bytes(payload[*offset..*offset + 4].try_into().unwrap());
        *offset += 4;
        Ok(val)
    }
}

impl FfiArg for u32 {
    fn read(payload: &[u8], offset: &mut usize) -> Result<Self, String> {
        if *offset + 4 > payload.len() {
            return Err("Payload too short for u32".to_string());
        }
        let val = u32::from_ne_bytes(payload[*offset..*offset + 4].try_into().unwrap());
        *offset += 4;
        Ok(val)
    }
}

impl FfiArg for i64 {
    fn read(payload: &[u8], offset: &mut usize) -> Result<Self, String> {
        if *offset + 8 > payload.len() {
            return Err("Payload too short for i64".to_string());
        }
        let val = i64::from_ne_bytes(payload[*offset..*offset + 8].try_into().unwrap());
        *offset += 8;
        Ok(val)
    }
}

impl FfiArg for u64 {
    fn read(payload: &[u8], offset: &mut usize) -> Result<Self, String> {
        if *offset + 8 > payload.len() {
            return Err("Payload too short for u64".to_string());
        }
        let val = u64::from_ne_bytes(payload[*offset..*offset + 8].try_into().unwrap());
        *offset += 8;
        Ok(val)
    }
}

impl FfiArg for isize {
    fn read(payload: &[u8], offset: &mut usize) -> Result<Self, String> {
        let size = std::mem::size_of::<isize>();
        if *offset + size > payload.len() {
            return Err("Payload too short for isize".to_string());
        }
        let val = isize::from_ne_bytes(payload[*offset..*offset + size].try_into().unwrap());
        *offset += size;
        Ok(val)
    }
}

impl FfiArg for usize {
    fn read(payload: &[u8], offset: &mut usize) -> Result<Self, String> {
        let size = std::mem::size_of::<usize>();
        if *offset + size > payload.len() {
            return Err("Payload too short for usize".to_string());
        }
        let val = usize::from_ne_bytes(payload[*offset..*offset + size].try_into().unwrap());
        *offset += size;
        Ok(val)
    }
}

impl FfiArg for f32 {
    fn read(payload: &[u8], offset: &mut usize) -> Result<Self, String> {
        if *offset + 4 > payload.len() {
            return Err("Payload too short for f32".to_string());
        }
        let val = f32::from_ne_bytes(payload[*offset..*offset + 4].try_into().unwrap());
        *offset += 4;
        Ok(val)
    }
}

impl FfiArg for f64 {
    fn read(payload: &[u8], offset: &mut usize) -> Result<Self, String> {
        if *offset + 8 > payload.len() {
            return Err("Payload too short for f64".to_string());
        }
        let val = f64::from_ne_bytes(payload[*offset..*offset + 8].try_into().unwrap());
        *offset += 8;
        Ok(val)
    }
}

pub(crate) trait FfiReturn: Sized {
    fn into_ffi_bytes(self) -> Vec<u8>;
}

impl FfiReturn for i32 {
    fn into_ffi_bytes(self) -> Vec<u8> {
        self.to_ne_bytes().to_vec()
    }
}
impl FfiReturn for u32 {
    fn into_ffi_bytes(self) -> Vec<u8> {
        self.to_ne_bytes().to_vec()
    }
}
impl FfiReturn for i64 {
    fn into_ffi_bytes(self) -> Vec<u8> {
        self.to_ne_bytes().to_vec()
    }
}
impl FfiReturn for u64 {
    fn into_ffi_bytes(self) -> Vec<u8> {
        self.to_ne_bytes().to_vec()
    }
}
impl FfiReturn for isize {
    fn into_ffi_bytes(self) -> Vec<u8> {
        self.to_ne_bytes().to_vec()
    }
}
impl FfiReturn for usize {
    fn into_ffi_bytes(self) -> Vec<u8> {
        self.to_ne_bytes().to_vec()
    }
}
impl FfiReturn for f32 {
    fn into_ffi_bytes(self) -> Vec<u8> {
        self.to_ne_bytes().to_vec()
    }
}
impl FfiReturn for f64 {
    fn into_ffi_bytes(self) -> Vec<u8> {
        self.to_ne_bytes().to_vec()
    }
}

pub(crate) type FfiThunk =
    unsafe fn(function_ptr: *const std::ffi::c_void, payload: &[u8]) -> Result<Vec<u8>, String>;

pub(crate) unsafe fn invoke0<R: FfiReturn>(
    function_ptr: *const std::ffi::c_void,
    payload: &[u8],
) -> Result<Vec<u8>, String> {
    if !payload.is_empty() {
        return Err(format!(
            "Payload length mismatch for 0-arg call: expected 0, got {}",
            payload.len()
        ));
    }
    let res = unsafe {
        let f: unsafe extern "C" fn() -> R = std::mem::transmute(function_ptr);
        f()
    };
    Ok(res.into_ffi_bytes())
}

pub(crate) unsafe fn invoke1<A: FfiArg, R: FfiReturn>(
    function_ptr: *const std::ffi::c_void,
    payload: &[u8],
) -> Result<Vec<u8>, String> {
    let mut offset = 0;
    let a = A::read(payload, &mut offset)?;
    if offset != payload.len() {
        return Err(format!(
            "Payload length mismatch: consumed {offset} bytes, payload had {}",
            payload.len()
        ));
    }
    let res = unsafe {
        let f: unsafe extern "C" fn(A) -> R = std::mem::transmute(function_ptr);
        f(a)
    };
    Ok(res.into_ffi_bytes())
}

pub(crate) unsafe fn invoke2<A: FfiArg, B: FfiArg, R: FfiReturn>(
    function_ptr: *const std::ffi::c_void,
    payload: &[u8],
) -> Result<Vec<u8>, String> {
    let mut offset = 0;
    let a = A::read(payload, &mut offset)?;
    let b = B::read(payload, &mut offset)?;
    if offset != payload.len() {
        return Err(format!(
            "Payload length mismatch: consumed {offset} bytes, payload had {}",
            payload.len()
        ));
    }
    let res = unsafe {
        let f: unsafe extern "C" fn(A, B) -> R = std::mem::transmute(function_ptr);
        f(a, b)
    };
    Ok(res.into_ffi_bytes())
}

pub(crate) unsafe fn invoke3<A: FfiArg, B: FfiArg, C: FfiArg, R: FfiReturn>(
    function_ptr: *const std::ffi::c_void,
    payload: &[u8],
) -> Result<Vec<u8>, String> {
    let mut offset = 0;
    let a = A::read(payload, &mut offset)?;
    let b = B::read(payload, &mut offset)?;
    let c = C::read(payload, &mut offset)?;
    if offset != payload.len() {
        return Err(format!(
            "Payload length mismatch: consumed {offset} bytes, payload had {}",
            payload.len()
        ));
    }
    let res = unsafe {
        let f: unsafe extern "C" fn(A, B, C) -> R = std::mem::transmute(function_ptr);
        f(a, b, c)
    };
    Ok(res.into_ffi_bytes())
}

pub(crate) unsafe fn invoke4<A: FfiArg, B: FfiArg, C: FfiArg, D: FfiArg, R: FfiReturn>(
    function_ptr: *const std::ffi::c_void,
    payload: &[u8],
) -> Result<Vec<u8>, String> {
    let mut offset = 0;
    let a = A::read(payload, &mut offset)?;
    let b = B::read(payload, &mut offset)?;
    let c = C::read(payload, &mut offset)?;
    let d = D::read(payload, &mut offset)?;
    if offset != payload.len() {
        return Err(format!(
            "Payload length mismatch: consumed {offset} bytes, payload had {}",
            payload.len()
        ));
    }
    let res = unsafe {
        let f: unsafe extern "C" fn(A, B, C, D) -> R = std::mem::transmute(function_ptr);
        f(a, b, c, d)
    };
    Ok(res.into_ffi_bytes())
}

pub(crate) unsafe fn invoke5<
    A: FfiArg,
    B: FfiArg,
    C: FfiArg,
    D: FfiArg,
    E: FfiArg,
    R: FfiReturn,
>(
    function_ptr: *const std::ffi::c_void,
    payload: &[u8],
) -> Result<Vec<u8>, String> {
    let mut offset = 0;
    let a = A::read(payload, &mut offset)?;
    let b = B::read(payload, &mut offset)?;
    let c = C::read(payload, &mut offset)?;
    let d = D::read(payload, &mut offset)?;
    let e = E::read(payload, &mut offset)?;
    if offset != payload.len() {
        return Err(format!(
            "Payload length mismatch: consumed {offset} bytes, payload had {}",
            payload.len()
        ));
    }
    let res = unsafe {
        let f: unsafe extern "C" fn(A, B, C, D, E) -> R = std::mem::transmute(function_ptr);
        f(a, b, c, d, e)
    };
    Ok(res.into_ffi_bytes())
}

pub(crate) unsafe fn invoke6<
    A: FfiArg,
    B: FfiArg,
    C: FfiArg,
    D: FfiArg,
    E: FfiArg,
    F: FfiArg,
    R: FfiReturn,
>(
    function_ptr: *const std::ffi::c_void,
    payload: &[u8],
) -> Result<Vec<u8>, String> {
    let mut offset = 0;
    let a = A::read(payload, &mut offset)?;
    let b = B::read(payload, &mut offset)?;
    let c = C::read(payload, &mut offset)?;
    let d = D::read(payload, &mut offset)?;
    let e = E::read(payload, &mut offset)?;
    let f_arg = F::read(payload, &mut offset)?;
    if offset != payload.len() {
        return Err(format!(
            "Payload length mismatch: consumed {offset} bytes, payload had {}",
            payload.len()
        ));
    }
    let res = unsafe {
        let func: unsafe extern "C" fn(A, B, C, D, E, F) -> R = std::mem::transmute(function_ptr);
        func(a, b, c, d, e, f_arg)
    };
    Ok(res.into_ffi_bytes())
}

pub(crate) unsafe fn invoke7<
    A: FfiArg,
    B: FfiArg,
    C: FfiArg,
    D: FfiArg,
    E: FfiArg,
    F: FfiArg,
    G: FfiArg,
    R: FfiReturn,
>(
    function_ptr: *const std::ffi::c_void,
    payload: &[u8],
) -> Result<Vec<u8>, String> {
    let mut offset = 0;
    let a = A::read(payload, &mut offset)?;
    let b = B::read(payload, &mut offset)?;
    let c = C::read(payload, &mut offset)?;
    let d = D::read(payload, &mut offset)?;
    let e = E::read(payload, &mut offset)?;
    let f_arg = F::read(payload, &mut offset)?;
    let g = G::read(payload, &mut offset)?;
    if offset != payload.len() {
        return Err(format!(
            "Payload length mismatch: consumed {offset} bytes, payload had {}",
            payload.len()
        ));
    }
    let res = unsafe {
        let func: unsafe extern "C" fn(A, B, C, D, E, F, G) -> R =
            std::mem::transmute(function_ptr);
        func(a, b, c, d, e, f_arg, g)
    };
    Ok(res.into_ffi_bytes())
}

pub(crate) unsafe fn invoke8<
    A: FfiArg,
    B: FfiArg,
    C: FfiArg,
    D: FfiArg,
    E: FfiArg,
    F: FfiArg,
    G: FfiArg,
    H: FfiArg,
    R: FfiReturn,
>(
    function_ptr: *const std::ffi::c_void,
    payload: &[u8],
) -> Result<Vec<u8>, String> {
    let mut offset = 0;
    let a = A::read(payload, &mut offset)?;
    let b = B::read(payload, &mut offset)?;
    let c = C::read(payload, &mut offset)?;
    let d = D::read(payload, &mut offset)?;
    let e = E::read(payload, &mut offset)?;
    let f_arg = F::read(payload, &mut offset)?;
    let g = G::read(payload, &mut offset)?;
    let h = H::read(payload, &mut offset)?;
    if offset != payload.len() {
        return Err(format!(
            "Payload length mismatch: consumed {offset} bytes, payload had {}",
            payload.len()
        ));
    }
    let res = unsafe {
        let func: unsafe extern "C" fn(A, B, C, D, E, F, G, H) -> R =
            std::mem::transmute(function_ptr);
        func(a, b, c, d, e, f_arg, g, h)
    };
    Ok(res.into_ffi_bytes())
}

macro_rules! match_ret_0 {
    ($ret:expr) => {
        match $ret {
            FfiType::I32 => Some(invoke0::<i32>),
            FfiType::U32 => Some(invoke0::<u32>),
            FfiType::I64 => Some(invoke0::<i64>),
            FfiType::U64 => Some(invoke0::<u64>),
            FfiType::Isize => Some(invoke0::<isize>),
            FfiType::Usize => Some(invoke0::<usize>),
            FfiType::F32 => Some(invoke0::<f32>),
            FfiType::F64 => Some(invoke0::<f64>),
        }
    };
}

macro_rules! match_ret_1 {
    ($A:ty, $ret:expr) => {
        match $ret {
            FfiType::I32 => Some(invoke1::<$A, i32>),
            FfiType::U32 => Some(invoke1::<$A, u32>),
            FfiType::I64 => Some(invoke1::<$A, i64>),
            FfiType::U64 => Some(invoke1::<$A, u64>),
            FfiType::Isize => Some(invoke1::<$A, isize>),
            FfiType::Usize => Some(invoke1::<$A, usize>),
            FfiType::F32 => Some(invoke1::<$A, f32>),
            FfiType::F64 => Some(invoke1::<$A, f64>),
        }
    };
}

macro_rules! match_arg_1 {
    ($arg0:expr, $ret:expr) => {
        match $arg0 {
            FfiType::I32 => match_ret_1!(i32, $ret),
            FfiType::U32 => match_ret_1!(u32, $ret),
            FfiType::I64 => match_ret_1!(i64, $ret),
            FfiType::U64 => match_ret_1!(u64, $ret),
            FfiType::Isize => match_ret_1!(isize, $ret),
            FfiType::Usize => match_ret_1!(usize, $ret),
            FfiType::F32 => match_ret_1!(f32, $ret),
            FfiType::F64 => match_ret_1!(f64, $ret),
        }
    };
}

macro_rules! match_ret_2 {
    ($A:ty, $B:ty, $ret:expr) => {
        match $ret {
            FfiType::I32 => Some(invoke2::<$A, $B, i32>),
            FfiType::U32 => Some(invoke2::<$A, $B, u32>),
            FfiType::I64 => Some(invoke2::<$A, $B, i64>),
            FfiType::U64 => Some(invoke2::<$A, $B, u64>),
            FfiType::Isize => Some(invoke2::<$A, $B, isize>),
            FfiType::Usize => Some(invoke2::<$A, $B, usize>),
            FfiType::F32 => Some(invoke2::<$A, $B, f32>),
            FfiType::F64 => Some(invoke2::<$A, $B, f64>),
        }
    };
}

macro_rules! match_arg1_2 {
    ($A:ty, $arg1:expr, $ret:expr) => {
        match $arg1 {
            FfiType::I32 => match_ret_2!($A, i32, $ret),
            FfiType::U32 => match_ret_2!($A, u32, $ret),
            FfiType::I64 => match_ret_2!($A, i64, $ret),
            FfiType::U64 => match_ret_2!($A, u64, $ret),
            FfiType::Isize => match_ret_2!($A, isize, $ret),
            FfiType::Usize => match_ret_2!($A, usize, $ret),
            FfiType::F32 => match_ret_2!($A, f32, $ret),
            FfiType::F64 => match_ret_2!($A, f64, $ret),
        }
    };
}

macro_rules! match_arg0_2 {
    ($arg0:expr, $arg1:expr, $ret:expr) => {
        match $arg0 {
            FfiType::I32 => match_arg1_2!(i32, $arg1, $ret),
            FfiType::U32 => match_arg1_2!(u32, $arg1, $ret),
            FfiType::I64 => match_arg1_2!(i64, $arg1, $ret),
            FfiType::U64 => match_arg1_2!(u64, $arg1, $ret),
            FfiType::Isize => match_arg1_2!(isize, $arg1, $ret),
            FfiType::Usize => match_arg1_2!(usize, $arg1, $ret),
            FfiType::F32 => match_arg1_2!(f32, $arg1, $ret),
            FfiType::F64 => match_arg1_2!(f64, $arg1, $ret),
        }
    };
}

macro_rules! match_ret_3_homo {
    ($A:ty, $ret:expr) => {
        match $ret {
            FfiType::I32 => Some(invoke3::<$A, $A, $A, i32>),
            FfiType::U32 => Some(invoke3::<$A, $A, $A, u32>),
            FfiType::I64 => Some(invoke3::<$A, $A, $A, i64>),
            FfiType::U64 => Some(invoke3::<$A, $A, $A, u64>),
            FfiType::Isize => Some(invoke3::<$A, $A, $A, isize>),
            FfiType::Usize => Some(invoke3::<$A, $A, $A, usize>),
            FfiType::F32 => Some(invoke3::<$A, $A, $A, f32>),
            FfiType::F64 => Some(invoke3::<$A, $A, $A, f64>),
        }
    };
}

macro_rules! match_arg_3_homo {
    ($arg0:expr, $ret:expr) => {
        match $arg0 {
            FfiType::I32 => match_ret_3_homo!(i32, $ret),
            FfiType::U32 => match_ret_3_homo!(u32, $ret),
            FfiType::I64 => match_ret_3_homo!(i64, $ret),
            FfiType::U64 => match_ret_3_homo!(u64, $ret),
            FfiType::Isize => match_ret_3_homo!(isize, $ret),
            FfiType::Usize => match_ret_3_homo!(usize, $ret),
            FfiType::F32 => match_ret_3_homo!(f32, $ret),
            FfiType::F64 => match_ret_3_homo!(f64, $ret),
        }
    };
}

macro_rules! match_ret_3_mixed1 {
    ($ret:expr) => {
        match $ret {
            FfiType::I32 => Some(invoke3::<i32, i32, f64, i32>),
            FfiType::U32 => Some(invoke3::<i32, i32, f64, u32>),
            FfiType::I64 => Some(invoke3::<i32, i32, f64, i64>),
            FfiType::U64 => Some(invoke3::<i32, i32, f64, u64>),
            FfiType::Isize => Some(invoke3::<i32, i32, f64, isize>),
            FfiType::Usize => Some(invoke3::<i32, i32, f64, usize>),
            FfiType::F32 => Some(invoke3::<i32, i32, f64, f32>),
            FfiType::F64 => Some(invoke3::<i32, i32, f64, f64>),
        }
    };
}

macro_rules! match_ret_3_mixed2 {
    ($ret:expr) => {
        match $ret {
            FfiType::I32 => Some(invoke3::<f64, f64, i32, i32>),
            FfiType::U32 => Some(invoke3::<f64, f64, i32, u32>),
            FfiType::I64 => Some(invoke3::<f64, f64, i32, i64>),
            FfiType::U64 => Some(invoke3::<f64, f64, i32, u64>),
            FfiType::Isize => Some(invoke3::<f64, f64, i32, isize>),
            FfiType::Usize => Some(invoke3::<f64, f64, i32, usize>),
            FfiType::F32 => Some(invoke3::<f64, f64, i32, f32>),
            FfiType::F64 => Some(invoke3::<f64, f64, i32, f64>),
        }
    };
}

macro_rules! match_ret_4_homo {
    ($A:ty, $ret:expr) => {
        match $ret {
            FfiType::I32 => Some(invoke4::<$A, $A, $A, $A, i32>),
            FfiType::U32 => Some(invoke4::<$A, $A, $A, $A, u32>),
            FfiType::I64 => Some(invoke4::<$A, $A, $A, $A, i64>),
            FfiType::U64 => Some(invoke4::<$A, $A, $A, $A, u64>),
            FfiType::Isize => Some(invoke4::<$A, $A, $A, $A, isize>),
            FfiType::Usize => Some(invoke4::<$A, $A, $A, $A, usize>),
            FfiType::F32 => Some(invoke4::<$A, $A, $A, $A, f32>),
            FfiType::F64 => Some(invoke4::<$A, $A, $A, $A, f64>),
        }
    };
}

macro_rules! match_arg_4_homo {
    ($arg0:expr, $ret:expr) => {
        match $arg0 {
            FfiType::I32 => match_ret_4_homo!(i32, $ret),
            FfiType::U32 => match_ret_4_homo!(u32, $ret),
            FfiType::I64 => match_ret_4_homo!(i64, $ret),
            FfiType::U64 => match_ret_4_homo!(u64, $ret),
            FfiType::Isize => match_ret_4_homo!(isize, $ret),
            FfiType::Usize => match_ret_4_homo!(usize, $ret),
            FfiType::F32 => match_ret_4_homo!(f32, $ret),
            FfiType::F64 => match_ret_4_homo!(f64, $ret),
        }
    };
}

macro_rules! match_ret_4_mixed {
    ($ret:expr) => {
        match $ret {
            FfiType::I32 => Some(invoke4::<i32, i32, f64, f64, i32>),
            FfiType::U32 => Some(invoke4::<i32, i32, f64, f64, u32>),
            FfiType::I64 => Some(invoke4::<i32, i32, f64, f64, i64>),
            FfiType::U64 => Some(invoke4::<i32, i32, f64, f64, u64>),
            FfiType::Isize => Some(invoke4::<i32, i32, f64, f64, isize>),
            FfiType::Usize => Some(invoke4::<i32, i32, f64, f64, usize>),
            FfiType::F32 => Some(invoke4::<i32, i32, f64, f64, f32>),
            FfiType::F64 => Some(invoke4::<i32, i32, f64, f64, f64>),
        }
    };
}

macro_rules! match_ret_5_homo {
    ($A:ty, $ret:expr) => {
        match $ret {
            FfiType::I32 => Some(invoke5::<$A, $A, $A, $A, $A, i32>),
            FfiType::U32 => Some(invoke5::<$A, $A, $A, $A, $A, u32>),
            FfiType::I64 => Some(invoke5::<$A, $A, $A, $A, $A, i64>),
            FfiType::U64 => Some(invoke5::<$A, $A, $A, $A, $A, u64>),
            FfiType::Isize => Some(invoke5::<$A, $A, $A, $A, $A, isize>),
            FfiType::Usize => Some(invoke5::<$A, $A, $A, $A, $A, usize>),
            FfiType::F32 => Some(invoke5::<$A, $A, $A, $A, $A, f32>),
            FfiType::F64 => Some(invoke5::<$A, $A, $A, $A, $A, f64>),
        }
    };
}

macro_rules! match_arg_5_homo {
    ($arg0:expr, $ret:expr) => {
        match $arg0 {
            FfiType::I32 => match_ret_5_homo!(i32, $ret),
            FfiType::U32 => match_ret_5_homo!(u32, $ret),
            FfiType::I64 => match_ret_5_homo!(i64, $ret),
            FfiType::U64 => match_ret_5_homo!(u64, $ret),
            FfiType::Isize => match_ret_5_homo!(isize, $ret),
            FfiType::Usize => match_ret_5_homo!(usize, $ret),
            FfiType::F32 => match_ret_5_homo!(f32, $ret),
            FfiType::F64 => match_ret_5_homo!(f64, $ret),
        }
    };
}

macro_rules! match_ret_6_homo {
    ($A:ty, $ret:expr) => {
        match $ret {
            FfiType::I32 => Some(invoke6::<$A, $A, $A, $A, $A, $A, i32>),
            FfiType::U32 => Some(invoke6::<$A, $A, $A, $A, $A, $A, u32>),
            FfiType::I64 => Some(invoke6::<$A, $A, $A, $A, $A, $A, i64>),
            FfiType::U64 => Some(invoke6::<$A, $A, $A, $A, $A, $A, u64>),
            FfiType::Isize => Some(invoke6::<$A, $A, $A, $A, $A, $A, isize>),
            FfiType::Usize => Some(invoke6::<$A, $A, $A, $A, $A, $A, usize>),
            FfiType::F32 => Some(invoke6::<$A, $A, $A, $A, $A, $A, f32>),
            FfiType::F64 => Some(invoke6::<$A, $A, $A, $A, $A, $A, f64>),
        }
    };
}

macro_rules! match_arg_6_homo {
    ($arg0:expr, $ret:expr) => {
        match $arg0 {
            FfiType::I32 => match_ret_6_homo!(i32, $ret),
            FfiType::U32 => match_ret_6_homo!(u32, $ret),
            FfiType::I64 => match_ret_6_homo!(i64, $ret),
            FfiType::U64 => match_ret_6_homo!(u64, $ret),
            FfiType::Isize => match_ret_6_homo!(isize, $ret),
            FfiType::Usize => match_ret_6_homo!(usize, $ret),
            FfiType::F32 => match_ret_6_homo!(f32, $ret),
            FfiType::F64 => match_ret_6_homo!(f64, $ret),
        }
    };
}

macro_rules! match_ret_7_homo {
    ($A:ty, $ret:expr) => {
        match $ret {
            FfiType::I32 => Some(invoke7::<$A, $A, $A, $A, $A, $A, $A, i32>),
            FfiType::U32 => Some(invoke7::<$A, $A, $A, $A, $A, $A, $A, u32>),
            FfiType::I64 => Some(invoke7::<$A, $A, $A, $A, $A, $A, $A, i64>),
            FfiType::U64 => Some(invoke7::<$A, $A, $A, $A, $A, $A, $A, u64>),
            FfiType::Isize => Some(invoke7::<$A, $A, $A, $A, $A, $A, $A, isize>),
            FfiType::Usize => Some(invoke7::<$A, $A, $A, $A, $A, $A, $A, usize>),
            FfiType::F32 => Some(invoke7::<$A, $A, $A, $A, $A, $A, $A, f32>),
            FfiType::F64 => Some(invoke7::<$A, $A, $A, $A, $A, $A, $A, f64>),
        }
    };
}

macro_rules! match_arg_7_homo {
    ($arg0:expr, $ret:expr) => {
        match $arg0 {
            FfiType::I32 => match_ret_7_homo!(i32, $ret),
            FfiType::U32 => match_ret_7_homo!(u32, $ret),
            FfiType::I64 => match_ret_7_homo!(i64, $ret),
            FfiType::U64 => match_ret_7_homo!(u64, $ret),
            FfiType::Isize => match_ret_7_homo!(isize, $ret),
            FfiType::Usize => match_ret_7_homo!(usize, $ret),
            FfiType::F32 => match_ret_7_homo!(f32, $ret),
            FfiType::F64 => match_ret_7_homo!(f64, $ret),
        }
    };
}

macro_rules! match_ret_8_homo {
    ($A:ty, $ret:expr) => {
        match $ret {
            FfiType::I32 => Some(invoke8::<$A, $A, $A, $A, $A, $A, $A, $A, i32>),
            FfiType::U32 => Some(invoke8::<$A, $A, $A, $A, $A, $A, $A, $A, u32>),
            FfiType::I64 => Some(invoke8::<$A, $A, $A, $A, $A, $A, $A, $A, i64>),
            FfiType::U64 => Some(invoke8::<$A, $A, $A, $A, $A, $A, $A, $A, u64>),
            FfiType::Isize => Some(invoke8::<$A, $A, $A, $A, $A, $A, $A, $A, isize>),
            FfiType::Usize => Some(invoke8::<$A, $A, $A, $A, $A, $A, $A, $A, usize>),
            FfiType::F32 => Some(invoke8::<$A, $A, $A, $A, $A, $A, $A, $A, f32>),
            FfiType::F64 => Some(invoke8::<$A, $A, $A, $A, $A, $A, $A, $A, f64>),
        }
    };
}

macro_rules! match_arg_8_homo {
    ($arg0:expr, $ret:expr) => {
        match $arg0 {
            FfiType::I32 => match_ret_8_homo!(i32, $ret),
            FfiType::U32 => match_ret_8_homo!(u32, $ret),
            FfiType::I64 => match_ret_8_homo!(i64, $ret),
            FfiType::U64 => match_ret_8_homo!(u64, $ret),
            FfiType::Isize => match_ret_8_homo!(isize, $ret),
            FfiType::Usize => match_ret_8_homo!(usize, $ret),
            FfiType::F32 => match_ret_8_homo!(f32, $ret),
            FfiType::F64 => match_ret_8_homo!(f64, $ret),
        }
    };
}

pub(crate) fn resolve_ffi_thunk(signature: &ParsedFfiSignature) -> Option<FfiThunk> {
    match signature.args.as_slice() {
        [] => match_ret_0!(signature.ret),
        [a0] => match_arg_1!(*a0, signature.ret),
        [a0, a1] => match_arg0_2!(*a0, *a1, signature.ret),
        [a0, a1, a2] => {
            if a0 == a1 && a1 == a2 {
                match_arg_3_homo!(*a0, signature.ret)
            } else if *a0 == FfiType::I32 && *a1 == FfiType::I32 && *a2 == FfiType::F64 {
                match_ret_3_mixed1!(signature.ret)
            } else if *a0 == FfiType::F64 && *a1 == FfiType::F64 && *a2 == FfiType::I32 {
                match_ret_3_mixed2!(signature.ret)
            } else {
                None
            }
        }
        [a0, a1, a2, a3] => {
            if a0 == a1 && a1 == a2 && a2 == a3 {
                match_arg_4_homo!(*a0, signature.ret)
            } else if *a0 == FfiType::I32
                && *a1 == FfiType::I32
                && *a2 == FfiType::F64
                && *a3 == FfiType::F64
            {
                match_ret_4_mixed!(signature.ret)
            } else {
                None
            }
        }
        [a0, a1, a2, a3, a4] => {
            if a0 == a1 && a1 == a2 && a2 == a3 && a3 == a4 {
                match_arg_5_homo!(*a0, signature.ret)
            } else {
                None
            }
        }
        [a0, a1, a2, a3, a4, a5] => {
            if a0 == a1 && a1 == a2 && a2 == a3 && a3 == a4 && a4 == a5 {
                match_arg_6_homo!(*a0, signature.ret)
            } else {
                None
            }
        }
        [a0, a1, a2, a3, a4, a5, a6] => {
            if a0 == a1 && a1 == a2 && a2 == a3 && a3 == a4 && a4 == a5 && a5 == a6 {
                match_arg_7_homo!(*a0, signature.ret)
            } else {
                None
            }
        }
        [a0, a1, a2, a3, a4, a5, a6, a7] => {
            if a0 == a1 && a1 == a2 && a2 == a3 && a3 == a4 && a4 == a5 && a5 == a6 && a6 == a7 {
                match_arg_8_homo!(*a0, signature.ret)
            } else {
                None
            }
        }
        _ => None,
    }
}

#[derive(Clone, Copy)]
pub(crate) struct PreparedFfiCall {
    pub(crate) function_ptr: usize,
    pub(crate) thunk: FfiThunk,
    pub(crate) expected_payload_len: usize,
}

pub(crate) fn execute_dylib_ffi(
    name: &str,
    symbol_name: &str,
    args_sig: &[String],
    ret_sig: &str,
    payload: &[u8],
) -> Result<Vec<u8>, String> {
    let sig_code = SignatureCode::from_str_sigs(args_sig, ret_sig)?;

    let registry = DYLIB_PLUGINS
        .get()
        .ok_or_else(|| "Dylib registry not initialized".to_string())?;
    let plugin = {
        let map = registry
            .read()
            .map_err(|e| format!("Registry lock poisoned: {e}"))?;
        map.get(name)
            .cloned()
            .ok_or_else(|| format!("Dynamic library '{name}' not registered"))?
    };

    let cached_call = {
        let cache_read = plugin
            .ffi_call_cache
            .read()
            .map_err(|e| format!("FFI call cache read lock poisoned: {e}"))?;
        cache_read
            .get(symbol_name)
            .and_then(|sym_map| sym_map.get(&sig_code).copied())
    };

    if let Some(prepared) = cached_call {
        if payload.len() != prepared.expected_payload_len {
            return Err(format!(
                "Payload length mismatch for FFI call '{symbol_name}': expected {} bytes, received {}.",
                prepared.expected_payload_len,
                payload.len()
            ));
        }
        return unsafe {
            (prepared.thunk)(prepared.function_ptr as *const std::ffi::c_void, payload)
        };
    }

    let parsed_sig = ParsedFfiSignature::parse(args_sig, ret_sig)?;
    if payload.len() != parsed_sig.expected_payload_len {
        return Err(format!(
            "Payload length mismatch for FFI call '{symbol_name}': expected {} bytes, received {}.",
            parsed_sig.expected_payload_len,
            payload.len()
        ));
    }

    unsafe {
        let symbol: Result<
            libloading::Symbol<unsafe extern "C" fn() -> *const std::ffi::c_char>,
            _,
        > = plugin.lib.get(b"pyroxide_metadata");
        if let Ok(sym) = symbol {
            let ptr = sym();
            let c_str_opt = if ptr.is_null() {
                None
            } else {
                std::ffi::CStr::from_ptr(ptr).to_str().ok()
            };
            if let Some(c_str) = c_str_opt {
                let expected_sig = format!("{}:{}|{}", symbol_name, args_sig.join(","), ret_sig);
                let entries: Vec<&str> = c_str.split(';').collect();
                if !entries.contains(&expected_sig.as_str()) {
                    return Err(format!(
                        "FFI signature mismatch for symbol '{symbol_name}': expected '{expected_sig}', metadata contains: {c_str}"
                    ));
                }
            }
        }
    }

    let raw_ptr = unsafe {
        let symbol: libloading::Symbol<*const std::ffi::c_void> = plugin
            .lib
            .get(symbol_name.as_bytes())
            .map_err(|e| format!("Failed to find symbol '{symbol_name}': {e}"))?;
        *symbol
    };

    let thunk = resolve_ffi_thunk(&parsed_sig).ok_or_else(|| {
        format!(
            "Unsupported FFI signature mapping: ({}) -> {}",
            args_sig.join(", "),
            ret_sig
        )
    })?;

    let prepared = PreparedFfiCall {
        function_ptr: raw_ptr as usize,
        thunk,
        expected_payload_len: parsed_sig.expected_payload_len,
    };

    {
        let mut cache_write = plugin
            .ffi_call_cache
            .write()
            .map_err(|e| format!("FFI call cache write lock poisoned: {e}"))?;
        cache_write
            .entry(symbol_name.to_string())
            .or_default()
            .insert(parsed_sig.encoded, prepared);
    }

    unsafe { (thunk)(raw_ptr, payload) }
}

/// Submits a task to be executed by a registered dynamic shared library (dylib).
#[pyfunction]
#[pyo3(signature = (plugin_name, symbol_name, payload, ffi_sig=None, isolated=false, queue_timeout_ms=None))]
fn submit_dylib_task(
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
fn submit_dylib_batch(
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

pub(crate) struct WasmState {
    pub(crate) limits: wasmtime::StoreLimits,
}

static WASM_ENGINE: OnceLock<Engine> = OnceLock::new();
static WASM_REGISTRY: OnceLock<RwLock<HashMap<String, Module>>> = OnceLock::new();
static WASM_TICKER_SHUTDOWN: AtomicBool = AtomicBool::new(false);

pub(crate) fn stop_wasm_ticker() {
    WASM_TICKER_SHUTDOWN.store(true, Ordering::Release);
}

pub(crate) fn get_wasm_engine() -> &'static Engine {
    WASM_ENGINE.get_or_init(|| {
        let mut config = wasmtime::Config::new();
        config.epoch_interruption(true);
        let engine = Engine::new(&config).expect("Failed to initialize WASM engine");

        let engine_clone = engine.clone();
        std::thread::spawn(move || {
            let tick_ms = get_wasm_tick_ms();
            while !WASM_TICKER_SHUTDOWN.load(Ordering::Acquire) {
                engine_clone.increment_epoch();
                std::thread::sleep(std::time::Duration::from_millis(tick_ms));
            }
        });

        engine
    })
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
    broker::check_engine_process()?;
    register_wasm_module_internal(module_name.clone(), wasm_bytes.clone())
        .map_err(pyo3::exceptions::PyValueError::new_err)?;
    let bytes = WASM_BYTES.get_or_init(|| RwLock::new(HashMap::new()));
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
fn register_wasm_wat(module_name: String, wat_str: String) -> PyResult<()> {
    broker::check_engine_process()?;
    let wasm_bytes = wat::parse_str(&wat_str)
        .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
    register_wasm_module_internal(module_name.clone(), wasm_bytes.clone())
        .map_err(pyo3::exceptions::PyValueError::new_err)?;
    let bytes = WASM_BYTES.get_or_init(|| RwLock::new(HashMap::new()));
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

/// This function submits a WebAssembly task to the broker.
#[pyfunction]
#[pyo3(signature = (module_name, func_name, payload, isolated=false, wasm_memory_limit_bytes=None, wasm_timeout_ms=None, queue_timeout_ms=None))]
#[allow(clippy::too_many_arguments)]
fn submit_wasm_task(
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
fn submit_wasm_batch(
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

/// This function submits a task to the broker and returns the task ID.
#[pyfunction]
#[pyo3(signature = (callable, payload, isolated=false, queue_timeout_ms=None))]
fn submit_task(
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

/// Submit a batch after atomically reserving capacity for every item.
#[pyfunction]
#[pyo3(signature = (callable, payloads, isolated=false, queue_timeout_ms=None))]
fn submit_batch(
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

/// This function cancels a task with the given ID.
#[pyfunction]
fn cancel_task(task_id: usize) -> PyResult<bool> {
    broker::check_engine_process()?;
    Ok(broker::cancel_task(task_id))
}

/// This function returns the status of the task with the given ID.
#[pyfunction]
fn get_status(task_id: usize) -> PyResult<String> {
    broker::check_engine_process()?;
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
    broker::check_engine_process()?;
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
fn get_result(py: Python<'_>, task_id: usize) -> PyResult<Bound<'_, PyAny>> {
    broker::check_engine_process()?;
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
fn free_task(task_id: usize) -> PyResult<()> {
    broker::check_engine_process()?;
    broker::free_task(task_id);
    Ok(())
}

/// This function returns the current number of tasks allocated in the Slab (useful for debugging leaks).
#[pyfunction]
fn get_slab_size() -> PyResult<usize> {
    broker::check_engine_process()?;
    Ok(broker::get_slab_size())
}

#[pyfunction]
fn get_wasm_exports(module_name: String) -> PyResult<Vec<String>> {
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
fn get_dylib_exports(plugin_name: String) -> PyResult<Vec<String>> {
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
fn get_dylib_metadata(name: &str) -> PyResult<Option<String>> {
    broker::check_engine_process()?;
    let registry = DYLIB_PLUGINS.get().ok_or_else(|| {
        PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("Dylib registry not initialized")
    })?;
    let plugin = {
        let map = registry.read().map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(format!(
                "Registry lock poisoned: {e}"
            ))
        })?;
        match map.get(name) {
            Some(p) => Arc::clone(p),
            None => return Ok(None),
        }
    };

    unsafe {
        let symbol: Result<
            libloading::Symbol<unsafe extern "C" fn() -> *const std::ffi::c_char>,
            _,
        > = plugin.lib.get(b"pyroxide_metadata");

        match symbol {
            Ok(sym) => {
                let ptr = sym();
                if ptr.is_null() {
                    Ok(None)
                } else {
                    let c_str = std::ffi::CStr::from_ptr(ptr);
                    let s = c_str.to_string_lossy().into_owned();
                    Ok(Some(s))
                }
            }
            Err(_) => Ok(None),
        }
    }
}

#[pyfunction]
fn get_dylib_path(name: String) -> PyResult<Option<String>> {
    broker::check_engine_process()?;
    let paths = DYLIB_PATHS.get_or_init(|| RwLock::new(HashMap::new()));
    let map = paths
        .read()
        .map_err(|e| pyo3::exceptions::PyRuntimeError::new_err(e.to_string()))?;
    Ok(map.get(&name).map(|entry| entry.value.clone()))
}

#[pyfunction]
fn start_worker_loop(socket_path: String) -> PyResult<()> {
    worker_process::start_worker_loop(&socket_path)
        .map_err(pyo3::exceptions::PyRuntimeError::new_err)
}

#[pyfunction]
fn set_autofree(task_id: usize) -> PyResult<()> {
    broker::check_engine_process()?;
    broker::set_autofree(task_id);
    Ok(())
}

#[cfg(debug_assertions)]
#[pyfunction]
fn _arm_start_claim_test_hook() -> PyResult<()> {
    worker::arm_start_claim_test_hook().map_err(pyo3::exceptions::PyRuntimeError::new_err)
}

#[cfg(debug_assertions)]
#[pyfunction]
fn _wait_start_claim_test_hook() -> PyResult<bool> {
    worker::wait_start_claim_test_hook().map_err(pyo3::exceptions::PyRuntimeError::new_err)
}

#[cfg(debug_assertions)]
#[pyfunction]
fn _resume_start_claim_test_hook() -> PyResult<()> {
    worker::resume_start_claim_test_hook().map_err(pyo3::exceptions::PyRuntimeError::new_err)
}

#[pyfunction]
#[pyo3(signature = (wait=true, cancel_pending=false))]
fn shutdown_engine(py: Python<'_>, wait: bool, cancel_pending: bool) -> PyResult<()> {
    py.detach(move || broker::shutdown_engine(wait, cancel_pending))
}

#[cfg(unix)]
static ASYNC_WAKER_FD: AtomicI32 = AtomicI32::new(-1);

#[cfg(unix)]
#[pyfunction]
fn register_async_waker(fd: std::os::fd::RawFd) {
    ASYNC_WAKER_FD.store(fd, Ordering::Release);
}

#[cfg(unix)]
#[pyfunction]
fn unregister_async_waker(fd: std::os::fd::RawFd) -> bool {
    ASYNC_WAKER_FD
        .compare_exchange(fd, -1, Ordering::AcqRel, Ordering::Acquire)
        .is_ok()
}

#[cfg(unix)]
pub(crate) fn notify_waker(_task_id: usize) {
    let fd = ASYNC_WAKER_FD.load(Ordering::Acquire);
    if fd < 0 {
        return;
    }

    let byte = [1u8];
    loop {
        let result = unsafe { libc::write(fd, byte.as_ptr().cast::<libc::c_void>(), byte.len()) };
        if result >= 0 {
            break;
        }

        let error = std::io::Error::last_os_error();
        if error.kind() != std::io::ErrorKind::Interrupted {
            break;
        }
    }
}

#[cfg(not(unix))]
pub(crate) fn notify_waker(_task_id: usize) {
    // Windows uses the executor-backed async wait path.
}

/// PyO3 entry point
#[pymodule]
fn _pyroxide(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
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

#[cfg(test)]
mod boundary_tests {
    use super::*;
    use std::collections::HashMap;
    use std::sync::Arc;
    use std::sync::atomic::{AtomicUsize, Ordering};

    #[test]
    fn wasm_input_must_fit_guest_i32_and_configured_limit() {
        assert!(validate_wasm_input_len(8, 16).is_ok());
        assert!(validate_wasm_input_len(17, 16).is_err());
        assert!(validate_wasm_input_len(i32::MAX as usize + 1, usize::MAX).is_err());
    }

    #[test]
    fn wasm_output_range_rejects_negative_or_out_of_bounds_values() {
        assert!(validate_wasm_output_range(-1, 4, 16, 16).is_err());
        assert!(validate_wasm_output_range(0, -1, 16, 16).is_err());
        assert!(validate_wasm_output_range(8, 9, 16, 16).is_err());
        assert!(validate_wasm_output_range(8, 8, 16, 16).is_ok());
    }

    #[test]
    fn ipc_length_is_checked_before_conversion_or_allocation() {
        assert_eq!(checked_ipc_len(8, 16, "payload").unwrap(), 8);
        assert!(checked_ipc_len(17, 16, "payload").is_err());
        assert!(checked_ipc_len(u64::MAX, 16, "payload").is_err());
    }

    #[test]
    fn native_output_length_is_bounded_before_copying() {
        assert!(validate_native_output_len(16, 16).is_ok());
        assert!(validate_native_output_len(17, 16).is_err());
    }

    #[test]
    fn current_registry_lookup_does_not_clone_its_payload() {
        struct CloneCounter(Arc<AtomicUsize>);

        impl Clone for CloneCounter {
            fn clone(&self) -> Self {
                self.0.fetch_add(1, Ordering::Relaxed);
                Self(Arc::clone(&self.0))
            }
        }

        let clones = Arc::new(AtomicUsize::new(0));
        let registrations = HashMap::from([(
            "module".to_string(),
            RegistryEntry {
                value: CloneCounter(Arc::clone(&clones)),
                generation: 7,
            },
        )]);

        assert!(matches!(
            registry_sync(&registrations, "module", Some(7)),
            RegistrySync::Current
        ));
        assert_eq!(clones.load(Ordering::Relaxed), 0);

        assert!(matches!(
            registry_sync(&registrations, "module", Some(6)),
            RegistrySync::Changed(_)
        ));
        assert_eq!(clones.load(Ordering::Relaxed), 1);
    }

    #[test]
    fn test_ffi_type_parsing_and_properties() {
        let types = ["i32", "u32", "i64", "u64", "isize", "usize", "f32", "f64"];
        for t in &types {
            let parsed = FfiType::parse(t).unwrap();
            assert_eq!(parsed.name(), *t);
        }
        assert!(FfiType::parse("invalid").is_err());
    }

    #[test]
    fn test_ffi_resolver_matrix_count_and_uniqueness() {
        let all_types = [
            FfiType::I32,
            FfiType::U32,
            FfiType::I64,
            FfiType::U64,
            FfiType::Isize,
            FfiType::Usize,
            FfiType::F32,
            FfiType::F64,
        ];

        let mut unique_codes = std::collections::HashSet::new();
        let mut count = 0;

        // 0-arg
        for &ret in &all_types {
            let sig = ParsedFfiSignature {
                args: vec![],
                ret,
                encoded: SignatureCode::new(&[], ret).unwrap(),
                expected_payload_len: 0,
            };
            assert!(
                resolve_ffi_thunk(&sig).is_some(),
                "0-arg failed for {ret:?}"
            );
            assert!(unique_codes.insert(sig.encoded));
            count += 1;
        }

        // 1-arg
        for &a0 in &all_types {
            for &ret in &all_types {
                let sig = ParsedFfiSignature {
                    args: vec![a0],
                    ret,
                    encoded: SignatureCode::new(&[a0], ret).unwrap(),
                    expected_payload_len: a0.byte_width(),
                };
                assert!(
                    resolve_ffi_thunk(&sig).is_some(),
                    "1-arg failed for {a0:?} -> {ret:?}"
                );
                assert!(unique_codes.insert(sig.encoded));
                count += 1;
            }
        }

        // 2-arg
        for &a0 in &all_types {
            for &a1 in &all_types {
                for &ret in &all_types {
                    let sig = ParsedFfiSignature {
                        args: vec![a0, a1],
                        ret,
                        encoded: SignatureCode::new(&[a0, a1], ret).unwrap(),
                        expected_payload_len: a0.byte_width() + a1.byte_width(),
                    };
                    assert!(
                        resolve_ffi_thunk(&sig).is_some(),
                        "2-arg failed for {a0:?},{a1:?} -> {ret:?}"
                    );
                    assert!(unique_codes.insert(sig.encoded));
                    count += 1;
                }
            }
        }

        // 3-arg (homogeneous + mixed)
        for &t in &all_types {
            for &ret in &all_types {
                let sig = ParsedFfiSignature {
                    args: vec![t, t, t],
                    ret,
                    encoded: SignatureCode::new(&[t, t, t], ret).unwrap(),
                    expected_payload_len: t.byte_width() * 3,
                };
                assert!(resolve_ffi_thunk(&sig).is_some());
                assert!(unique_codes.insert(sig.encoded));
                count += 1;
            }
        }
        for &ret in &all_types {
            let sig1 = ParsedFfiSignature {
                args: vec![FfiType::I32, FfiType::I32, FfiType::F64],
                ret,
                encoded: SignatureCode::new(&[FfiType::I32, FfiType::I32, FfiType::F64], ret)
                    .unwrap(),
                expected_payload_len: 16,
            };
            assert!(resolve_ffi_thunk(&sig1).is_some());
            assert!(unique_codes.insert(sig1.encoded));
            count += 1;

            let sig2 = ParsedFfiSignature {
                args: vec![FfiType::F64, FfiType::F64, FfiType::I32],
                ret,
                encoded: SignatureCode::new(&[FfiType::F64, FfiType::F64, FfiType::I32], ret)
                    .unwrap(),
                expected_payload_len: 20,
            };
            assert!(resolve_ffi_thunk(&sig2).is_some());
            assert!(unique_codes.insert(sig2.encoded));
            count += 1;
        }

        // 4-arg (homogeneous + mixed)
        for &t in &all_types {
            for &ret in &all_types {
                let sig = ParsedFfiSignature {
                    args: vec![t, t, t, t],
                    ret,
                    encoded: SignatureCode::new(&[t, t, t, t], ret).unwrap(),
                    expected_payload_len: t.byte_width() * 4,
                };
                assert!(resolve_ffi_thunk(&sig).is_some());
                assert!(unique_codes.insert(sig.encoded));
                count += 1;
            }
        }
        for &ret in &all_types {
            let sig = ParsedFfiSignature {
                args: vec![FfiType::I32, FfiType::I32, FfiType::F64, FfiType::F64],
                ret,
                encoded: SignatureCode::new(
                    &[FfiType::I32, FfiType::I32, FfiType::F64, FfiType::F64],
                    ret,
                )
                .unwrap(),
                expected_payload_len: 24,
            };
            assert!(resolve_ffi_thunk(&sig).is_some());
            assert!(unique_codes.insert(sig.encoded));
            count += 1;
        }

        // 5-8 homo
        for arity in 5..=8 {
            for &t in &all_types {
                for &ret in &all_types {
                    let args = vec![t; arity];
                    let sig = ParsedFfiSignature {
                        args: args.clone(),
                        ret,
                        encoded: SignatureCode::new(&args, ret).unwrap(),
                        expected_payload_len: t.byte_width() * arity,
                    };
                    assert!(
                        resolve_ffi_thunk(&sig).is_some(),
                        "{arity}-arg homo failed for {t:?}"
                    );
                    assert!(unique_codes.insert(sig.encoded));
                    count += 1;
                }
            }
        }

        assert_eq!(count, 992);
        assert_eq!(unique_codes.len(), 992);
    }
}
