use pyo3::prelude::*;
use std::sync::atomic::{AtomicBool, AtomicU8};
use std::sync::{Condvar, Mutex};

#[derive(Debug, Clone, Copy, PartialEq)]
#[repr(u8)]
pub(crate) enum TaskStatus {
    Pending = 0,
    Running = 1,
    Completed = 2,
    Failed = 3,
    Cancelled = 4,
}

impl TaskStatus {
    pub fn to_status_string(val: u8) -> String {
        match val {
            0 => "Pending".to_string(),
            1 => "Running".to_string(),
            2 => "Completed".to_string(),
            3 => "Failed".to_string(),
            4 => "Cancelled".to_string(),
            _ => "Unknown".to_string(),
        }
    }
}

pub(crate) struct Task {
    pub(crate) status: AtomicU8,
    pub(crate) callable: Option<Py<PyAny>>,
    pub(crate) payload: Py<PyAny>,
    pub(crate) result: Mutex<Option<Result<Py<PyAny>, String>>>,
    pub(crate) completed_cvar: Condvar,
    pub(crate) completed_mutex: Mutex<bool>,
    pub(crate) cancelled: AtomicBool,
    pub(crate) autofree: AtomicBool,
    pub(crate) wasm_module: Option<String>,
    pub(crate) wasm_func: Option<String>,
    pub(crate) dylib: Option<String>,
    pub(crate) dylib_symbol: Option<String>,
    pub(crate) ffi_sig: Option<(Vec<String>, String)>,
    pub(crate) isolated: bool,
    pub(crate) wasm_memory_limit_bytes: Option<usize>,
    pub(crate) wasm_timeout_ms: Option<u64>,
}
