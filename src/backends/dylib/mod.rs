pub(crate) mod dispatch;
pub(crate) mod ffi;

use crate::config::{get_max_native_output_bytes, validate_native_output_len};
use dispatch::{FfiThunk, resolve_ffi_thunk};
use ffi::{ParsedFfiSignature, SignatureCode};

use std::collections::HashMap;
use std::sync::Arc;
use std::sync::OnceLock;
use std::sync::RwLock;

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

pub(crate) static DYLIB_PLUGINS: OnceLock<RwLock<HashMap<String, Arc<DylibPlugin>>>> =
    OnceLock::new();

pub type PluginRunFn =
    unsafe extern "C" fn(ptr: *const u8, len: usize, out_len: *mut usize) -> *mut u8;
pub type PluginFreeFn = unsafe extern "C" fn(ptr: *mut u8, len: usize);

#[derive(Clone, Copy)]
pub(crate) struct PreparedFfiCall {
    pub(crate) function_ptr: usize,
    pub(crate) thunk: FfiThunk,
    pub(crate) expected_payload_len: usize,
}

pub(crate) fn register_dylib_internal(
    name: String,
    library_path: String,
    free_fn_name: Option<String>,
) -> Result<(), String> {
    // SAFETY: loading native code is an explicit trusted-host operation. Any
    // resolved function pointer is copied while `lib` remains owned by the
    // stored plugin for at least as long as that pointer can be used.
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

/// Retrieve the `pyroxide_metadata` string from a loaded dylib plugin.
/// Returns `Ok(None)` if the plugin has no metadata symbol or the symbol returns NULL.
pub(crate) fn get_dylib_metadata_internal(name: &str) -> Result<Option<String>, String> {
    let registry = DYLIB_PLUGINS
        .get()
        .ok_or_else(|| "Dylib registry not initialized".to_string())?;
    let plugin = {
        let map = registry
            .read()
            .map_err(|e| format!("Registry lock poisoned: {e}"))?;
        match map.get(name) {
            Some(p) => Arc::clone(p),
            None => return Ok(None),
        }
    };

    // SAFETY: `pyroxide_metadata` is an optional native ABI symbol documented
    // to return either null or a valid, static, NUL-terminated UTF-8 string.
    unsafe {
        type MetadataFn = unsafe extern "C" fn() -> *const std::ffi::c_char;
        let symbol = match plugin.lib.get::<MetadataFn>(b"pyroxide_metadata") {
            Ok(sym) => sym,
            Err(_) => return Ok(None),
        };

        let ptr = symbol();
        if ptr.is_null() {
            return Err("pyroxide_metadata returned NULL".to_string());
        }

        let metadata = std::ffi::CStr::from_ptr(ptr)
            .to_str()
            .map_err(|_| "pyroxide_metadata returned invalid UTF-8".to_string())?;

        Ok(Some(metadata.to_owned()))
    }
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
            let mut cache = plugin
                .symbol_cache
                .write()
                .map_err(|e| format!("Symbol cache write lock poisoned: {e}"))?;

            if let Some(&v) = cache.get(&key) {
                v
            } else {
                // SAFETY: the plugin library remains owned by `plugin`; the
                // requested raw-task symbol must implement `PluginRunFn`.
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
    // SAFETY: `run_ptr_val` was produced from a `PluginRunFn` resolved from
    // the still-live plugin library immediately above.
    let run_fn: PluginRunFn =
        unsafe { std::mem::transmute(run_ptr_val as *const std::ffi::c_void) };

    let free_fn = plugin.free_fn.ok_or_else(|| {
        "Raw binary tasks require the symbol 'pyroxide_plugin_free' to prevent memory leaks."
            .to_string()
    })?;

    // SAFETY: `run_fn` and `free_fn` come from the same live plugin. The input
    // slice is valid for the call, output length is bounded before reading,
    // and every non-null returned allocation is released exactly once.
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
        // SAFETY: the cached thunk and pointer were resolved for the exact
        // validated signature and expected payload length.
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

    if let Some(c_str) = get_dylib_metadata_internal(name)? {
        let expected_sig = format!("{}:{}|{}", symbol_name, args_sig.join(","), ret_sig);
        let entries: Vec<&str> = c_str.split(';').collect();
        if !entries.contains(&expected_sig.as_str()) {
            return Err(format!(
                "FFI signature mismatch for symbol '{symbol_name}': expected '{expected_sig}', metadata contains: {c_str}"
            ));
        }
    }

    // SAFETY: the library remains owned by `plugin`; the untyped pointer is
    // called only through a thunk resolved from validated metadata below.
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

    // SAFETY: `thunk` was resolved from `parsed_sig`, `raw_ptr` belongs to the
    // live plugin, and the payload length was validated above.
    unsafe { (thunk)(raw_ptr, payload) }
}
