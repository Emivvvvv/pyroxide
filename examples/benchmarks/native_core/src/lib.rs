//! Deterministic byte ABI shared by direct bindings and the Pyroxide plugin path.

use std::alloc::{Layout, alloc, dealloc};
use std::cell::Cell;

use sha2::{Digest, Sha256};

pub const FRAME_VERSION: u32 = 1;
pub const FRAME_BYTES: usize = 52;
pub const ERROR_OK: i32 = 0;
pub const ERROR_NULL_OUTPUT_LENGTH: i32 = 1;
pub const ERROR_NULL_INPUT: i32 = 2;
pub const ERROR_LENGTH_OVERFLOW: i32 = 3;
pub const ERROR_INVALID_OUTPUT_LENGTH: i32 = 4;
pub const ERROR_ALLOCATION: i32 = 5;

const MIX_MULTIPLIER_1: u64 = 0xBF58_476D_1CE4_E5B9;
const MIX_MULTIPLIER_2: u64 = 0x94D0_49BB_1331_11EB;
const MIX_SEED: u64 = 0x9E37_79B9_7F4A_7C15;

thread_local! {
    static LAST_ERROR: Cell<i32> = const { Cell::new(ERROR_OK) };
}

/// Build the fixed-width little-endian result frame used by every binding.
pub fn run_frame(input: &[u8]) -> Result<[u8; FRAME_BYTES], i32> {
    let input_length = u64::try_from(input.len()).map_err(|_| ERROR_LENGTH_OVERFLOW)?;
    let mut frame = [0_u8; FRAME_BYTES];
    frame[..4].copy_from_slice(&FRAME_VERSION.to_le_bytes());
    frame[4..12].copy_from_slice(&input_length.to_le_bytes());
    frame[12..20].copy_from_slice(&mix(input).to_le_bytes());
    frame[20..].copy_from_slice(&Sha256::digest(input));
    Ok(frame)
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

fn set_error(error: i32) {
    LAST_ERROR.with(|last_error| last_error.set(error));
}

fn allocate_frame(frame: [u8; FRAME_BYTES]) -> Result<*mut u8, i32> {
    let layout = Layout::array::<u8>(FRAME_BYTES).map_err(|_| ERROR_ALLOCATION)?;
    let pointer = unsafe { alloc(layout) };
    if pointer.is_null() {
        return Err(ERROR_ALLOCATION);
    }
    unsafe { std::ptr::copy_nonoverlapping(frame.as_ptr(), pointer, FRAME_BYTES) };
    Ok(pointer)
}

unsafe fn run_abi(input: *const u8, input_len: usize, output_len: *mut usize) -> *mut u8 {
    if output_len.is_null() {
        set_error(ERROR_NULL_OUTPUT_LENGTH);
        return std::ptr::null_mut();
    }
    unsafe { *output_len = 0 };
    if input.is_null() && input_len != 0 {
        set_error(ERROR_NULL_INPUT);
        return std::ptr::null_mut();
    }

    let input = if input_len == 0 {
        &[]
    } else {
        unsafe { std::slice::from_raw_parts(input, input_len) }
    };
    let frame = match run_frame(input) {
        Ok(frame) => frame,
        Err(error) => {
            set_error(error);
            return std::ptr::null_mut();
        }
    };
    match allocate_frame(frame) {
        Ok(pointer) => {
            unsafe { *output_len = FRAME_BYTES };
            set_error(ERROR_OK);
            pointer
        }
        Err(error) => {
            set_error(error);
            std::ptr::null_mut()
        }
    }
}

unsafe fn free_abi(output: *mut u8, output_len: usize) {
    if output.is_null() {
        return;
    }
    if output_len != FRAME_BYTES {
        set_error(ERROR_INVALID_OUTPUT_LENGTH);
        return;
    }
    let layout = match Layout::array::<u8>(FRAME_BYTES) {
        Ok(layout) => layout,
        Err(_) => {
            set_error(ERROR_ALLOCATION);
            return;
        }
    };
    unsafe { dealloc(output, layout) };
}

/// Return a newly allocated frame. The caller must use [`benchmark_free`].
///
/// `input_len` is encoded as an unsigned 64-bit little-endian integer. If a
/// future architecture exposes a larger `usize`, values that cannot fit in the
/// frame return null and `benchmark_last_error()` reports length overflow.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn benchmark_run(
    input: *const u8,
    input_len: usize,
    output_len: *mut usize,
) -> *mut u8 {
    unsafe { run_abi(input, input_len, output_len) }
}

/// Pyroxide's byte-plugin entry point forwards to the exact same ABI core.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn pyroxide_plugin_run(
    input: *const u8,
    input_len: usize,
    output_len: *mut usize,
) -> *mut u8 {
    unsafe { run_abi(input, input_len, output_len) }
}

/// Free a frame returned by [`benchmark_run`] or [`pyroxide_plugin_run`].
#[unsafe(no_mangle)]
pub unsafe extern "C" fn benchmark_free(output: *mut u8, output_len: usize) {
    unsafe { free_abi(output, output_len) }
}

/// Pyroxide's byte-plugin deallocator forwards to the exact same ownership path.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn pyroxide_plugin_free(output: *mut u8, output_len: usize) {
    unsafe { free_abi(output, output_len) }
}

#[unsafe(no_mangle)]
pub extern "C" fn benchmark_last_error() -> i32 {
    LAST_ERROR.with(Cell::get)
}
