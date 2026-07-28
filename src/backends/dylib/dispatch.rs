use super::ffi::{FfiArg, FfiReturn, FfiType, ParsedFfiSignature};

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
