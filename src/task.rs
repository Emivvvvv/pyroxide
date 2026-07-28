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

pub(crate) enum TaskKind {
    PythonCall {
        callable: Py<PyAny>,
    },
    Wasm {
        module: String,
        function: String,
        memory_limit_bytes: Option<usize>,
        timeout_ms: Option<u64>,
    },
    Dylib {
        plugin: String,
        symbol: String,
        ffi_sig: Option<(Vec<String>, String)>,
    },
}

pub(crate) struct Task {
    pub(crate) status: AtomicU8,
    pub(crate) kind: TaskKind,
    pub(crate) payload: Py<PyAny>,
    pub(crate) result: Mutex<Option<Result<Py<PyAny>, String>>>,
    pub(crate) completed_cvar: Condvar,
    pub(crate) completed_mutex: Mutex<bool>,
    pub(crate) cancelled: AtomicBool,
    pub(crate) autofree: AtomicBool,
    pub(crate) isolated: bool,
}

impl Task {
    pub(crate) fn new(kind: TaskKind, payload: Py<PyAny>, isolated: bool) -> Self {
        Self {
            status: AtomicU8::new(TaskStatus::Pending as u8),
            kind,
            payload,
            result: Mutex::new(None),
            completed_cvar: Condvar::new(),
            completed_mutex: Mutex::new(false),
            cancelled: AtomicBool::new(false),
            autofree: AtomicBool::new(false),
            isolated,
        }
    }

    pub(crate) fn python(callable: Py<PyAny>, payload: Py<PyAny>, isolated: bool) -> Self {
        Self::new(TaskKind::PythonCall { callable }, payload, isolated)
    }

    pub(crate) fn wasm(
        module: String,
        function: String,
        payload: Py<PyAny>,
        memory_limit_bytes: Option<usize>,
        timeout_ms: Option<u64>,
        isolated: bool,
    ) -> Self {
        Self::new(
            TaskKind::Wasm {
                module,
                function,
                memory_limit_bytes,
                timeout_ms,
            },
            payload,
            isolated,
        )
    }

    pub(crate) fn dylib(
        plugin: String,
        symbol: String,
        payload: Py<PyAny>,
        ffi_sig: Option<(Vec<String>, String)>,
        isolated: bool,
    ) -> Self {
        Self::new(
            TaskKind::Dylib {
                plugin,
                symbol,
                ffi_sig,
            },
            payload,
            isolated,
        )
    }
}
