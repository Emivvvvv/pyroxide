//! Import-free guest implementing Pyroxide's `memory/alloc/dealloc/run` byte ABI.

use std::alloc::{Layout, alloc as allocate, dealloc as deallocate};

use sha2::{Digest, Sha256};

const FRAME_VERSION: u32 = 1;
const FRAME_BYTES: usize = 52;
const MIX_MULTIPLIER_1: u64 = 0xBF58_476D_1CE4_E5B9;
const MIX_MULTIPLIER_2: u64 = 0x94D0_49BB_1331_11EB;
const MIX_SEED: u64 = 0x9E37_79B9_7F4A_7C15;

fn layout(size: i32) -> Option<Layout> {
    usize::try_from(size)
        .ok()
        .filter(|size| *size != 0)
        .and_then(|size| Layout::from_size_align(size, 1).ok())
}

#[unsafe(no_mangle)]
pub extern "C" fn alloc(size: i32) -> i32 {
    let Some(layout) = layout(size) else {
        return 0;
    };
    let pointer = unsafe { allocate(layout) };
    let Ok(pointer) = i32::try_from(pointer as usize) else {
        return 0;
    };
    pointer
}

#[unsafe(no_mangle)]
pub extern "C" fn dealloc(pointer: i32, size: i32) {
    let Some(layout) = layout(size) else {
        return;
    };
    if pointer <= 0 {
        return;
    }
    unsafe { deallocate(pointer as usize as *mut u8, layout) };
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn run(input_pointer: i32, input_len: i32) -> i64 {
    if input_pointer < 0 || input_len < 0 {
        return 0;
    }
    let input = if input_len == 0 {
        &[]
    } else {
        unsafe {
            std::slice::from_raw_parts(input_pointer as usize as *const u8, input_len as usize)
        }
    };
    let output_pointer = alloc(FRAME_BYTES as i32);
    if output_pointer == 0 {
        return 0;
    }
    let output =
        unsafe { std::slice::from_raw_parts_mut(output_pointer as usize as *mut u8, FRAME_BYTES) };
    output[..4].copy_from_slice(&FRAME_VERSION.to_le_bytes());
    output[4..12].copy_from_slice(&(input.len() as u64).to_le_bytes());
    output[12..20].copy_from_slice(&mix(input).to_le_bytes());
    output[20..].copy_from_slice(&Sha256::digest(input));
    ((i64::from(output_pointer)) << 32) | i64::try_from(FRAME_BYTES).unwrap()
}

#[unsafe(no_mangle)]
pub extern "C" fn trap(_input_pointer: i32, _input_len: i32) -> i64 {
    core::arch::wasm32::unreachable()
}

fn mix(input: &[u8]) -> u64 {
    let mut state = MIX_SEED;
    for value in input {
        state ^= u64::from(*value);
        state = state.wrapping_mul(MIX_MULTIPLIER_1);
        state ^= state >> 31;
        state = state.wrapping_mul(MIX_MULTIPLIER_2);
        state ^= state >> 27;
    }
    state
}
