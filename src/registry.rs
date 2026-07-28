use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::OnceLock;
use std::sync::RwLock;

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

pub(crate) fn registry_sync<T: Clone>(
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
pub(crate) static DYLIB_PATHS: OnceLock<RwLock<HashMap<String, RegistryEntry<String>>>> = OnceLock::new();
pub(crate) static WASM_BYTES: OnceLock<RwLock<HashMap<String, RegistryEntry<Vec<u8>>>>> = OnceLock::new();

pub(crate) fn next_registry_generation() -> u64 {
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
