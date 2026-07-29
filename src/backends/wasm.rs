use crate::config::get_wasm_tick_ms;
use std::collections::HashMap;
use std::sync::OnceLock;
use std::sync::RwLock;
use std::sync::atomic::{AtomicBool, Ordering};
use wasmtime::{Engine, Module};

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

pub(crate) fn execute_wasm_guest(
    module_name: &str,
    func_name: &str,
    input_bytes: &[u8],
    limit_bytes: usize,
    timeout_ms: u64,
    cancel_check: Option<&dyn Fn() -> bool>,
) -> Result<Vec<u8>, String> {
    let module = get_wasm_module(module_name)
        .ok_or_else(|| format!("WASM module '{module_name}' not registered"))?;

    let engine = get_wasm_engine();
    let state = WasmState {
        limits: wasmtime::StoreLimitsBuilder::new()
            .memory_size(limit_bytes)
            .build(),
    };
    let mut store = wasmtime::Store::new(engine, state);
    store.limiter(|s| &mut s.limits);

    let tick_ms = get_wasm_tick_ms();
    let ticks = timeout_ms.div_ceil(tick_ms).max(1);
    store.set_epoch_deadline(ticks);

    let linker = wasmtime::Linker::new(engine);
    let instance = linker
        .instantiate(&mut store, &module)
        .map_err(|e| format!("Failed to instantiate WASM: {e}"))?;

    let alloc_fn = instance
        .get_typed_func::<i32, i32>(&mut store, "alloc")
        .map_err(|e| format!("WASM missing export 'alloc': {e}"))?;
    let dealloc_fn = instance
        .get_typed_func::<(i32, i32), ()>(&mut store, "dealloc")
        .map_err(|e| format!("WASM missing export 'dealloc': {e}"))?;
    let run_fn = instance
        .get_typed_func::<(i32, i32), i64>(&mut store, func_name)
        .map_err(|e| format!("WASM missing export '{func_name}': {e}"))?;

    let memory = instance
        .get_memory(&mut store, "memory")
        .ok_or_else(|| "WASM missing export 'memory'".to_string())?;

    let input_len = crate::config::validate_wasm_input_len(input_bytes.len(), limit_bytes)?;

    if let Some(check) = cancel_check {
        if check() {
            return Err("Task cancelled".to_string());
        }
    }

    let guest_ptr = alloc_fn
        .call(&mut store, input_len)
        .map_err(|e| format!("WASM alloc failed: {e}"))?;

    memory
        .write(&mut store, guest_ptr as usize, input_bytes)
        .map_err(|e| format!("Failed to write to WASM memory: {e}"))?;

    if let Some(check) = cancel_check {
        if check() {
            let _ = dealloc_fn.call(&mut store, (guest_ptr, input_len));
            return Err("Task cancelled".to_string());
        }
    }

    let packed_result = run_fn
        .call(&mut store, (guest_ptr, input_len))
        .map_err(|e| format!("WASM execution failed: {e}"))?;

    let out_ptr = (packed_result >> 32) as i32;
    let out_len = (packed_result & 0xFFFFFFFF) as i32;
    let (out_start, out_size) = crate::config::validate_wasm_output_range(
        out_ptr,
        out_len,
        limit_bytes,
        memory.data_size(&store),
    )?;

    if let Some(check) = cancel_check {
        if check() {
            let _ = dealloc_fn.call(&mut store, (guest_ptr, input_len));
            if !ranges_overlap(guest_ptr, input_len, out_ptr, out_len) {
                let _ = dealloc_fn.call(&mut store, (out_ptr, out_len));
            }
            return Err("Task cancelled".to_string());
        }
    }

    let mut output_bytes = vec![0u8; out_size];
    memory
        .read(&store, out_start, &mut output_bytes)
        .map_err(|e| format!("Failed to read from WASM memory: {e}"))?;

    let _ = dealloc_fn.call(&mut store, (guest_ptr, input_len));
    if !ranges_overlap(guest_ptr, input_len, out_ptr, out_len) {
        let _ = dealloc_fn.call(&mut store, (out_ptr, out_len));
    }

    Ok(output_bytes)
}

fn ranges_overlap(left_ptr: i32, left_len: i32, right_ptr: i32, right_len: i32) -> bool {
    let left_start = i64::from(left_ptr);
    let left_end = left_start + i64::from(left_len);
    let right_start = i64::from(right_ptr);
    let right_end = right_start + i64::from(right_len);

    left_start < right_end && right_start < left_end
}
