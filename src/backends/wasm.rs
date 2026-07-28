use crate::config::get_wasm_tick_ms;
use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::OnceLock;
use std::sync::RwLock;
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
