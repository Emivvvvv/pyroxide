#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
#[repr(u8)]
pub(crate) enum FfiType {
    I32 = 0,
    U32 = 1,
    I64 = 2,
    U64 = 3,
    Isize = 4,
    Usize = 5,
    F32 = 6,
    F64 = 7,
}

impl FfiType {
    pub(crate) fn parse(name: &str) -> Result<Self, String> {
        match name {
            "i32" => Ok(FfiType::I32),
            "u32" => Ok(FfiType::U32),
            "i64" => Ok(FfiType::I64),
            "u64" => Ok(FfiType::U64),
            "isize" => Ok(FfiType::Isize),
            "usize" => Ok(FfiType::Usize),
            "f32" => Ok(FfiType::F32),
            "f64" => Ok(FfiType::F64),
            _ => Err(format!(
                "Unsupported FFI type '{name}'. Supported types: i32, u32, i64, u64, isize, usize, f32, f64."
            )),
        }
    }

    #[allow(dead_code)]
    pub(crate) fn name(self) -> &'static str {
        match self {
            FfiType::I32 => "i32",
            FfiType::U32 => "u32",
            FfiType::I64 => "i64",
            FfiType::U64 => "u64",
            FfiType::Isize => "isize",
            FfiType::Usize => "usize",
            FfiType::F32 => "f32",
            FfiType::F64 => "f64",
        }
    }

    pub(crate) fn byte_width(self) -> usize {
        match self {
            FfiType::I32 | FfiType::U32 | FfiType::F32 => 4,
            FfiType::I64 | FfiType::U64 | FfiType::F64 => 8,
            FfiType::Isize | FfiType::Usize => std::mem::size_of::<usize>(),
        }
    }

    pub(crate) fn code(self) -> u8 {
        self as u8
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub(crate) struct SignatureCode(pub(crate) u32);

impl SignatureCode {
    pub(crate) fn new(args: &[FfiType], ret: FfiType) -> Result<Self, String> {
        if args.len() > 8 {
            return Err(format!(
                "FFI signatures support at most 8 arguments; received {}.",
                args.len()
            ));
        }
        let mut code = (args.len() as u32) & 0x0F;
        code |= ((ret.code() as u32) & 0x07) << 4;
        for (i, &arg) in args.iter().enumerate() {
            let shift = 7 + i * 3;
            code |= ((arg.code() as u32) & 0x07) << shift;
        }
        Ok(SignatureCode(code))
    }

    pub(crate) fn from_str_sigs(args_sig: &[String], ret_sig: &str) -> Result<Self, String> {
        if args_sig.len() > 8 {
            return Err(format!(
                "FFI signatures support at most 8 arguments; received {}.",
                args_sig.len()
            ));
        }
        let ret = FfiType::parse(ret_sig)?;
        let mut code = (args_sig.len() as u32) & 0x0F;
        code |= ((ret.code() as u32) & 0x07) << 4;
        for (i, arg_str) in args_sig.iter().enumerate() {
            let arg = FfiType::parse(arg_str)?;
            let shift = 7 + i * 3;
            code |= ((arg.code() as u32) & 0x07) << shift;
        }
        Ok(SignatureCode(code))
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) struct ParsedFfiSignature {
    pub(crate) args: Vec<FfiType>,
    pub(crate) ret: FfiType,
    pub(crate) encoded: SignatureCode,
    pub(crate) expected_payload_len: usize,
}

impl ParsedFfiSignature {
    pub(crate) fn parse(args_sig: &[String], ret_sig: &str) -> Result<Self, String> {
        if args_sig.len() > 8 {
            return Err(format!(
                "FFI signatures support at most 8 arguments; received {}.",
                args_sig.len()
            ));
        }
        let mut args = Vec::with_capacity(args_sig.len());
        let mut expected_payload_len = 0;
        for arg_str in args_sig {
            let t = FfiType::parse(arg_str)?;
            expected_payload_len += t.byte_width();
            args.push(t);
        }
        let ret = FfiType::parse(ret_sig)?;
        let encoded = SignatureCode::new(&args, ret)?;
        Ok(ParsedFfiSignature {
            args,
            ret,
            encoded,
            expected_payload_len,
        })
    }
}

pub(crate) trait FfiArg: Sized {
    fn read(payload: &[u8], offset: &mut usize) -> Result<Self, String>;
}

impl FfiArg for i32 {
    fn read(payload: &[u8], offset: &mut usize) -> Result<Self, String> {
        if *offset + 4 > payload.len() {
            return Err("Payload too short for i32".to_string());
        }
        let val = i32::from_ne_bytes(payload[*offset..*offset + 4].try_into().unwrap());
        *offset += 4;
        Ok(val)
    }
}

impl FfiArg for u32 {
    fn read(payload: &[u8], offset: &mut usize) -> Result<Self, String> {
        if *offset + 4 > payload.len() {
            return Err("Payload too short for u32".to_string());
        }
        let val = u32::from_ne_bytes(payload[*offset..*offset + 4].try_into().unwrap());
        *offset += 4;
        Ok(val)
    }
}

impl FfiArg for i64 {
    fn read(payload: &[u8], offset: &mut usize) -> Result<Self, String> {
        if *offset + 8 > payload.len() {
            return Err("Payload too short for i64".to_string());
        }
        let val = i64::from_ne_bytes(payload[*offset..*offset + 8].try_into().unwrap());
        *offset += 8;
        Ok(val)
    }
}

impl FfiArg for u64 {
    fn read(payload: &[u8], offset: &mut usize) -> Result<Self, String> {
        if *offset + 8 > payload.len() {
            return Err("Payload too short for u64".to_string());
        }
        let val = u64::from_ne_bytes(payload[*offset..*offset + 8].try_into().unwrap());
        *offset += 8;
        Ok(val)
    }
}

impl FfiArg for isize {
    fn read(payload: &[u8], offset: &mut usize) -> Result<Self, String> {
        let size = std::mem::size_of::<isize>();
        if *offset + size > payload.len() {
            return Err("Payload too short for isize".to_string());
        }
        let val = isize::from_ne_bytes(payload[*offset..*offset + size].try_into().unwrap());
        *offset += size;
        Ok(val)
    }
}

impl FfiArg for usize {
    fn read(payload: &[u8], offset: &mut usize) -> Result<Self, String> {
        let size = std::mem::size_of::<usize>();
        if *offset + size > payload.len() {
            return Err("Payload too short for usize".to_string());
        }
        let val = usize::from_ne_bytes(payload[*offset..*offset + size].try_into().unwrap());
        *offset += size;
        Ok(val)
    }
}

impl FfiArg for f32 {
    fn read(payload: &[u8], offset: &mut usize) -> Result<Self, String> {
        if *offset + 4 > payload.len() {
            return Err("Payload too short for f32".to_string());
        }
        let val = f32::from_ne_bytes(payload[*offset..*offset + 4].try_into().unwrap());
        *offset += 4;
        Ok(val)
    }
}

impl FfiArg for f64 {
    fn read(payload: &[u8], offset: &mut usize) -> Result<Self, String> {
        if *offset + 8 > payload.len() {
            return Err("Payload too short for f64".to_string());
        }
        let val = f64::from_ne_bytes(payload[*offset..*offset + 8].try_into().unwrap());
        *offset += 8;
        Ok(val)
    }
}

pub(crate) trait FfiReturn: Sized {
    fn into_ffi_bytes(self) -> Vec<u8>;
}

impl FfiReturn for i32 {
    fn into_ffi_bytes(self) -> Vec<u8> {
        self.to_ne_bytes().to_vec()
    }
}
impl FfiReturn for u32 {
    fn into_ffi_bytes(self) -> Vec<u8> {
        self.to_ne_bytes().to_vec()
    }
}
impl FfiReturn for i64 {
    fn into_ffi_bytes(self) -> Vec<u8> {
        self.to_ne_bytes().to_vec()
    }
}
impl FfiReturn for u64 {
    fn into_ffi_bytes(self) -> Vec<u8> {
        self.to_ne_bytes().to_vec()
    }
}
impl FfiReturn for isize {
    fn into_ffi_bytes(self) -> Vec<u8> {
        self.to_ne_bytes().to_vec()
    }
}
impl FfiReturn for usize {
    fn into_ffi_bytes(self) -> Vec<u8> {
        self.to_ne_bytes().to_vec()
    }
}
impl FfiReturn for f32 {
    fn into_ffi_bytes(self) -> Vec<u8> {
        self.to_ne_bytes().to_vec()
    }
}
impl FfiReturn for f64 {
    fn into_ffi_bytes(self) -> Vec<u8> {
        self.to_ne_bytes().to_vec()
    }
}
