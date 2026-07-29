pub(crate) const REQUEST_KIND_PYTHON: u8 = 0;
pub(crate) const REQUEST_KIND_WASM: u8 = 1;
pub(crate) const REQUEST_KIND_DYLIB: u8 = 2;
pub(crate) const REQUEST_KIND_REGISTER_WASM: u8 = 10;
pub(crate) const REQUEST_KIND_REGISTER_DYLIB: u8 = 11;
pub(crate) const REQUEST_KIND_UNREGISTER_DYLIB: u8 = 12;

pub(crate) const REQUEST_HEADER_LEN: usize = 14;
pub(crate) const RESPONSE_HEADER_LEN: usize = 10;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct FrameFlags(u8);

impl FrameFlags {
    const SHARED_MEMORY: u8 = 1;

    pub(crate) const fn inline() -> Self {
        Self(0)
    }

    pub(crate) const fn shared_memory() -> Self {
        Self(Self::SHARED_MEMORY)
    }

    pub(crate) fn decode(raw: u8) -> Result<Self, String> {
        if raw & !Self::SHARED_MEMORY != 0 {
            return Err(format!("Unsupported IPC frame flags: {raw:#04x}"));
        }
        Ok(Self(raw))
    }

    pub(crate) const fn encode(self) -> u8 {
        self.0
    }

    pub(crate) const fn uses_shared_memory(self) -> bool {
        self.0 & Self::SHARED_MEMORY != 0
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct RequestHeader {
    pub(crate) kind: u8,
    pub(crate) flags: FrameFlags,
    pub(crate) metadata_len: usize,
    pub(crate) payload_len: usize,
}

impl RequestHeader {
    pub(crate) fn new(
        kind: u8,
        flags: FrameFlags,
        metadata_len: usize,
        payload_len: usize,
    ) -> Result<Self, String> {
        validate_request_kind(kind)?;
        let metadata_len = crate::config::checked_ipc_len(
            metadata_len as u64,
            crate::config::MAX_IPC_METADATA_BYTES,
            "metadata",
        )?;
        let payload_len = crate::config::checked_ipc_len(
            payload_len as u64,
            crate::config::get_max_ipc_frame_bytes(),
            "payload",
        )?;
        Ok(Self {
            kind,
            flags,
            metadata_len,
            payload_len,
        })
    }

    pub(crate) fn decode(bytes: [u8; REQUEST_HEADER_LEN]) -> Result<Self, String> {
        let kind = bytes[0];
        let flags = FrameFlags::decode(bytes[1])?;
        let metadata_len = u32::from_be_bytes(
            bytes[2..6]
                .try_into()
                .map_err(|_| "Invalid request metadata length bytes".to_string())?,
        ) as usize;
        let payload_len = u64::from_be_bytes(
            bytes[6..14]
                .try_into()
                .map_err(|_| "Invalid request payload length bytes".to_string())?,
        );
        let payload_len = usize::try_from(payload_len)
            .map_err(|_| "Request payload length does not fit usize".to_string())?;
        Self::new(kind, flags, metadata_len, payload_len)
    }

    pub(crate) fn encode(self) -> [u8; REQUEST_HEADER_LEN] {
        let mut bytes = [0u8; REQUEST_HEADER_LEN];
        bytes[0] = self.kind;
        bytes[1] = self.flags.encode();
        bytes[2..6].copy_from_slice(&(self.metadata_len as u32).to_be_bytes());
        bytes[6..14].copy_from_slice(&(self.payload_len as u64).to_be_bytes());
        bytes
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) struct ResponseHeader {
    pub(crate) success: bool,
    pub(crate) flags: FrameFlags,
    pub(crate) payload_len: usize,
}

impl ResponseHeader {
    pub(crate) fn new(
        success: bool,
        flags: FrameFlags,
        payload_len: usize,
    ) -> Result<Self, String> {
        let payload_len = crate::config::checked_ipc_len(
            payload_len as u64,
            crate::config::get_max_ipc_frame_bytes(),
            "response",
        )?;
        Ok(Self {
            success,
            flags,
            payload_len,
        })
    }

    pub(crate) fn decode(bytes: [u8; RESPONSE_HEADER_LEN]) -> Result<Self, String> {
        let success = match bytes[0] {
            0 => false,
            1 => true,
            value => return Err(format!("Invalid IPC response status: {value}")),
        };
        let flags = FrameFlags::decode(bytes[1])?;
        let payload_len = u64::from_be_bytes(
            bytes[2..10]
                .try_into()
                .map_err(|_| "Invalid response payload length bytes".to_string())?,
        );
        let payload_len = usize::try_from(payload_len)
            .map_err(|_| "Response payload length does not fit usize".to_string())?;
        Self::new(success, flags, payload_len)
    }

    pub(crate) fn encode(self) -> [u8; RESPONSE_HEADER_LEN] {
        let mut bytes = [0u8; RESPONSE_HEADER_LEN];
        bytes[0] = u8::from(self.success);
        bytes[1] = self.flags.encode();
        bytes[2..10].copy_from_slice(&(self.payload_len as u64).to_be_bytes());
        bytes
    }
}

fn validate_request_kind(kind: u8) -> Result<(), String> {
    match kind {
        REQUEST_KIND_PYTHON
        | REQUEST_KIND_WASM
        | REQUEST_KIND_DYLIB
        | REQUEST_KIND_REGISTER_WASM
        | REQUEST_KIND_REGISTER_DYLIB
        | REQUEST_KIND_UNREGISTER_DYLIB => Ok(()),
        _ => Err(format!("Unknown request kind: {kind}")),
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum RequestMetadata {
    Python,
    Wasm {
        module: String,
        function: String,
        memory_limit: usize,
        timeout_ms: u64,
    },
    Dylib {
        plugin: String,
        symbol: String,
        signature: Option<(Vec<String>, String)>,
    },
    RegisterWasm {
        module: String,
    },
    RegisterDylib {
        plugin: String,
        library_path: String,
        free_fn_name: Option<String>,
    },
    UnregisterDylib {
        plugin: String,
    },
}

impl RequestMetadata {
    pub(crate) fn kind_byte(&self) -> u8 {
        match self {
            Self::Python => REQUEST_KIND_PYTHON,
            Self::Wasm { .. } => REQUEST_KIND_WASM,
            Self::Dylib { .. } => REQUEST_KIND_DYLIB,
            Self::RegisterWasm { .. } => REQUEST_KIND_REGISTER_WASM,
            Self::RegisterDylib { .. } => REQUEST_KIND_REGISTER_DYLIB,
            Self::UnregisterDylib { .. } => REQUEST_KIND_UNREGISTER_DYLIB,
        }
    }

    pub(crate) fn encode(&self) -> Vec<u8> {
        let mut buf = Vec::new();
        buf.push(self.kind_byte());
        match self {
            Self::Python => {}
            Self::Wasm {
                module,
                function,
                memory_limit,
                timeout_ms,
            } => {
                encode_str(&mut buf, module);
                encode_str(&mut buf, function);
                buf.extend_from_slice(&(*memory_limit as u64).to_be_bytes());
                buf.extend_from_slice(&timeout_ms.to_be_bytes());
            }
            Self::Dylib {
                plugin,
                symbol,
                signature,
            } => {
                encode_str(&mut buf, plugin);
                encode_str(&mut buf, symbol);
                if let Some((args, ret)) = signature {
                    buf.push(1);
                    buf.extend_from_slice(&(args.len() as u32).to_be_bytes());
                    for arg in args {
                        encode_str(&mut buf, arg);
                    }
                    encode_str(&mut buf, ret);
                } else {
                    buf.push(0);
                }
            }
            Self::RegisterWasm { module } => {
                encode_str(&mut buf, module);
            }
            Self::RegisterDylib {
                plugin,
                library_path,
                free_fn_name,
            } => {
                encode_str(&mut buf, plugin);
                encode_str(&mut buf, library_path);
                if let Some(free_fn) = free_fn_name {
                    buf.push(1);
                    encode_str(&mut buf, free_fn);
                } else {
                    buf.push(0);
                }
            }
            Self::UnregisterDylib { plugin } => {
                encode_str(&mut buf, plugin);
            }
        }
        buf
    }

    pub(crate) fn decode(buf: &[u8]) -> Result<Self, String> {
        if buf.is_empty() {
            return Err("Empty metadata buffer".to_string());
        }
        let kind = buf[0];
        let mut cursor = &buf[1..];
        let res = match kind {
            REQUEST_KIND_PYTHON => Ok(Self::Python),
            REQUEST_KIND_WASM => {
                let module = decode_str(&mut cursor)?;
                let function = decode_str(&mut cursor)?;
                let raw_limit = decode_u64(&mut cursor)?;
                let memory_limit = usize::try_from(raw_limit)
                    .map_err(|_| "WASM memory limit does not fit usize".to_string())?;
                let timeout_ms = decode_u64(&mut cursor)?;
                Ok(Self::Wasm {
                    module,
                    function,
                    memory_limit,
                    timeout_ms,
                })
            }
            REQUEST_KIND_DYLIB => {
                let plugin = decode_str(&mut cursor)?;
                let symbol = decode_str(&mut cursor)?;
                if cursor.is_empty() {
                    return Err("Truncated dylib metadata".to_string());
                }
                let has_sig = cursor[0];
                cursor = &cursor[1..];
                let signature = match has_sig {
                    0 => None,
                    1 => {
                        let count = usize::try_from(decode_u32(&mut cursor)?)
                            .map_err(|_| "FFI argument count overflow".to_string())?;
                        if count > 8 {
                            return Err(format!(
                                "FFI metadata supports at most 8 arguments; received {count}"
                            ));
                        }
                        let mut args = Vec::with_capacity(count);
                        for _ in 0..count {
                            args.push(decode_str(&mut cursor)?);
                        }
                        let ret = decode_str(&mut cursor)?;
                        Some((args, ret))
                    }
                    val => return Err(format!("Invalid signature-present flag: {val}")),
                };
                Ok(Self::Dylib {
                    plugin,
                    symbol,
                    signature,
                })
            }
            REQUEST_KIND_REGISTER_WASM => {
                let module = decode_str(&mut cursor)?;
                Ok(Self::RegisterWasm { module })
            }
            REQUEST_KIND_REGISTER_DYLIB => {
                let plugin = decode_str(&mut cursor)?;
                let library_path = decode_str(&mut cursor)?;
                if cursor.is_empty() {
                    return Err("Truncated register dylib metadata".to_string());
                }
                let has_free = cursor[0];
                cursor = &cursor[1..];
                let free_fn_name = match has_free {
                    0 => None,
                    1 => Some(decode_str(&mut cursor)?),
                    val => return Err(format!("Invalid free_fn-present flag: {val}")),
                };
                Ok(Self::RegisterDylib {
                    plugin,
                    library_path,
                    free_fn_name,
                })
            }
            REQUEST_KIND_UNREGISTER_DYLIB => {
                let plugin = decode_str(&mut cursor)?;
                Ok(Self::UnregisterDylib { plugin })
            }
            _ => Err(format!("Unknown request kind: {kind}")),
        }?;

        if !cursor.is_empty() {
            return Err(format!(
                "Metadata contains {} unexpected trailing bytes",
                cursor.len()
            ));
        }

        Ok(res)
    }
}

fn encode_str(buf: &mut Vec<u8>, s: &str) {
    let bytes = s.as_bytes();
    buf.extend_from_slice(&(bytes.len() as u32).to_be_bytes());
    buf.extend_from_slice(bytes);
}

fn decode_str(cursor: &mut &[u8]) -> Result<String, String> {
    let len = decode_u32(cursor)? as usize;
    if cursor.len() < len {
        return Err(format!("Buffer too short for string of len {len}"));
    }
    let (str_bytes, rest) = cursor.split_at(len);
    *cursor = rest;
    String::from_utf8(str_bytes.to_vec()).map_err(|e| format!("Invalid UTF-8 in metadata: {e}"))
}

fn decode_u32(cursor: &mut &[u8]) -> Result<u32, String> {
    if cursor.len() < 4 {
        return Err("Buffer too short for u32".to_string());
    }
    let (bytes, rest) = cursor.split_at(4);
    *cursor = rest;
    let array: [u8; 4] = bytes.try_into().unwrap();
    Ok(u32::from_be_bytes(array))
}

fn decode_u64(cursor: &mut &[u8]) -> Result<u64, String> {
    if cursor.len() < 8 {
        return Err("Buffer too short for u64".to_string());
    }
    let (bytes, rest) = cursor.split_at(8);
    *cursor = rest;
    let array: [u8; 8] = bytes.try_into().unwrap();
    Ok(u64::from_be_bytes(array))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_request_metadata_roundtrip_complex_delimiters() {
        let meta = RequestMetadata::Wasm {
            module: "my:module:with:colons".to_string(),
            function: "func|with;semicolons,and,commas".to_string(),
            memory_limit: 1048576,
            timeout_ms: 500,
        };
        let encoded = meta.encode();
        let decoded = RequestMetadata::decode(&encoded).unwrap();
        assert_eq!(meta, decoded);

        let dylib_meta = RequestMetadata::Dylib {
            plugin: "plugin:1;2|3".to_string(),
            symbol: "run:fn;".to_string(),
            signature: Some((
                vec!["i32".to_string(), "u64:special".to_string()],
                "f64|type".to_string(),
            )),
        };
        let encoded_dylib = dylib_meta.encode();
        let decoded_dylib = RequestMetadata::decode(&encoded_dylib).unwrap();
        assert_eq!(dylib_meta, decoded_dylib);
    }

    #[test]
    fn test_decode_hostile_trailing_bytes() {
        let meta = RequestMetadata::Python;
        let mut encoded = meta.encode();
        encoded.push(0xFF); // Trailing unexpected byte
        let err = RequestMetadata::decode(&encoded).unwrap_err();
        assert!(err.contains("unexpected trailing bytes"));
    }

    #[test]
    fn test_decode_hostile_invalid_flag() {
        let mut buf = vec![REQUEST_KIND_DYLIB];
        encode_str(&mut buf, "plugin");
        encode_str(&mut buf, "symbol");
        buf.push(2); // Invalid flag (must be 0 or 1)
        let err = RequestMetadata::decode(&buf).unwrap_err();
        assert!(err.contains("Invalid signature-present flag"));
    }

    #[test]
    fn test_decode_hostile_excessive_arg_count() {
        let mut buf = vec![REQUEST_KIND_DYLIB];
        encode_str(&mut buf, "plugin");
        encode_str(&mut buf, "symbol");
        buf.push(1); // Has signature
        buf.extend_from_slice(&100u32.to_be_bytes()); // 100 args (exceeds max 8)
        let err = RequestMetadata::decode(&buf).unwrap_err();
        assert!(err.contains("supports at most 8 arguments"));
    }

    #[test]
    fn frame_flags_reject_unsupported_bits() {
        assert_eq!(FrameFlags::decode(0).unwrap(), FrameFlags::inline());
        assert_eq!(FrameFlags::decode(1).unwrap(), FrameFlags::shared_memory());
        assert!(FrameFlags::decode(2).is_err());
        assert!(FrameFlags::decode(u8::MAX).is_err());
    }

    #[test]
    fn request_header_preserves_wire_layout() {
        let header =
            RequestHeader::new(REQUEST_KIND_PYTHON, FrameFlags::inline(), 1, 0x0102_0304).unwrap();

        assert_eq!(
            header.encode(),
            [REQUEST_KIND_PYTHON, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 2, 3, 4,]
        );
        assert_eq!(RequestHeader::decode(header.encode()).unwrap(), header);
    }

    #[test]
    fn response_header_preserves_wire_layout_and_validates_status() {
        let header = ResponseHeader::new(true, FrameFlags::shared_memory(), 3).unwrap();
        assert_eq!(header.encode(), [1, 1, 0, 0, 0, 0, 0, 0, 0, 3]);
        assert_eq!(ResponseHeader::decode(header.encode()).unwrap(), header);

        assert!(ResponseHeader::decode([2, 0, 0, 0, 0, 0, 0, 0, 0, 0]).is_err());
        assert!(ResponseHeader::decode([1, 2, 0, 0, 0, 0, 0, 0, 0, 0]).is_err());
    }
}
