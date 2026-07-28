use std::sync::atomic::{AtomicBool, AtomicU64, AtomicUsize, Ordering};
use std::sync::OnceLock;

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

pub(crate) fn get_shm_threshold() -> usize {
    static SHM_THRESHOLD: OnceLock<usize> = OnceLock::new();
    *SHM_THRESHOLD.get_or_init(|| {
        std::env::var("PYROXIDE_SHM_THRESHOLD")
            .ok()
            .and_then(|v| v.parse().ok())
            .unwrap_or(1024 * 1024)
    })
}

pub(crate) fn set_global_wasm_memory_limit_bytes_internal(bytes: usize) {
    CONFIG
        .wasm_memory_limit_bytes
        .store(bytes, Ordering::Relaxed);
    GLOBAL_WASM_MEMORY_SET.store(true, Ordering::Release);
}

pub(crate) fn set_global_wasm_timeout_ms_internal(ms: u64) {
    CONFIG.wasm_timeout_ms.store(ms, Ordering::Relaxed);
    GLOBAL_WASM_TIMEOUT_SET.store(true, Ordering::Release);
}

pub(crate) fn set_global_queue_timeout_ms_internal(ms: u64) {
    CONFIG.queue_timeout_ms.store(ms, Ordering::Relaxed);
    GLOBAL_QUEUE_TIMEOUT_SET.store(true, Ordering::Release);
}
