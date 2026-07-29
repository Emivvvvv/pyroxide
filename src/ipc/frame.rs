use crate::ipc::protocol::{
    FrameFlags, REQUEST_HEADER_LEN, RESPONSE_HEADER_LEN, RequestHeader, RequestMetadata,
    ResponseHeader,
};
use std::io::{Read, Write};

pub(crate) fn read_request(
    stream: &mut impl Read,
) -> Result<Option<(RequestMetadata, FrameFlags, Vec<u8>)>, String> {
    let mut header_bytes = [0u8; REQUEST_HEADER_LEN];
    match stream.read(&mut header_bytes[..1]) {
        Ok(0) => return Ok(None),
        Ok(1) => {}
        Ok(_) => unreachable!("one-byte read returned more than one byte"),
        Err(error) => return Err(format!("Failed to read request kind: {error}")),
    }
    stream
        .read_exact(&mut header_bytes[1..])
        .map_err(|error| format!("Failed to read request header: {error}"))?;
    let header = RequestHeader::decode(header_bytes)?;

    let mut metadata_bytes = vec![0u8; header.metadata_len];
    stream
        .read_exact(&mut metadata_bytes)
        .map_err(|error| format!("Failed to read metadata: {error}"))?;
    let metadata = RequestMetadata::decode(&metadata_bytes)
        .map_err(|error| format!("Failed to decode metadata: {error}"))?;
    if header.kind != metadata.kind_byte() {
        return Err(format!(
            "Request kind mismatch: frame={}, metadata={}",
            header.kind,
            metadata.kind_byte()
        ));
    }

    let mut payload = vec![0u8; header.payload_len];
    stream
        .read_exact(&mut payload)
        .map_err(|error| format!("Failed to read payload: {error}"))?;
    Ok(Some((metadata, header.flags, payload)))
}

pub(crate) fn write_request(
    stream: &mut impl Write,
    metadata: &RequestMetadata,
    flags: FrameFlags,
    payload: &[u8],
) -> Result<(), String> {
    let metadata_bytes = metadata.encode();
    let header = RequestHeader::new(
        metadata.kind_byte(),
        flags,
        metadata_bytes.len(),
        payload.len(),
    )?;

    stream
        .write_all(&header.encode())
        .map_err(|error| format!("Failed to write request header: {error}"))?;
    stream
        .write_all(&metadata_bytes)
        .map_err(|error| format!("Failed to write request metadata: {error}"))?;
    stream
        .write_all(payload)
        .map_err(|error| format!("Failed to write request payload: {error}"))?;
    stream
        .flush()
        .map_err(|error| format!("Failed to flush request: {error}"))?;
    Ok(())
}

pub(crate) fn read_response(stream: &mut impl Read) -> Result<(ResponseHeader, Vec<u8>), String> {
    let mut header_bytes = [0u8; RESPONSE_HEADER_LEN];
    stream
        .read_exact(&mut header_bytes)
        .map_err(|error| format!("Failed to read response header: {error}"))?;
    let header = ResponseHeader::decode(header_bytes)?;

    let mut payload = vec![0u8; header.payload_len];
    stream
        .read_exact(&mut payload)
        .map_err(|error| format!("Failed to read response payload: {error}"))?;
    Ok((header, payload))
}

pub(crate) fn write_response(
    stream: &mut impl Write,
    success: bool,
    flags: FrameFlags,
    payload: &[u8],
) -> Result<(), String> {
    let header = ResponseHeader::new(success, flags, payload.len())?;
    stream
        .write_all(&header.encode())
        .map_err(|error| format!("Failed to write response header: {error}"))?;
    stream
        .write_all(payload)
        .map_err(|error| format!("Failed to write response payload: {error}"))?;
    stream
        .flush()
        .map_err(|error| format!("Failed to flush response: {error}"))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Cursor;

    #[test]
    fn request_frame_roundtrip_preserves_wire_bytes() {
        let metadata = RequestMetadata::Python;
        let mut stream = Cursor::new(Vec::new());

        write_request(&mut stream, &metadata, FrameFlags::inline(), b"abc").unwrap();

        assert_eq!(
            stream.get_ref(),
            &[
                0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 3, 0, b'a', b'b', b'c'
            ]
        );

        stream.set_position(0);
        let (decoded, flags, payload) = read_request(&mut stream).unwrap().unwrap();
        assert_eq!(decoded, metadata);
        assert_eq!(flags, FrameFlags::inline());
        assert_eq!(payload, b"abc");
    }

    #[test]
    fn request_frame_returns_none_for_clean_eof() {
        assert!(
            read_request(&mut Cursor::new(Vec::new()))
                .unwrap()
                .is_none()
        );
    }

    #[test]
    fn request_frame_rejects_unsupported_flags_before_payload() {
        let bytes = vec![0, 2, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0];
        let error = read_request(&mut Cursor::new(bytes)).unwrap_err();
        assert!(error.contains("Unsupported IPC frame flags"));
    }

    #[test]
    fn response_frame_roundtrip_preserves_wire_bytes() {
        let mut stream = Cursor::new(Vec::new());
        write_response(&mut stream, true, FrameFlags::inline(), b"ok").unwrap();
        assert_eq!(
            stream.get_ref(),
            &[1, 0, 0, 0, 0, 0, 0, 0, 0, 2, b'o', b'k']
        );

        stream.set_position(0);
        let (header, payload) = read_response(&mut stream).unwrap();
        assert!(header.success);
        assert_eq!(header.flags, FrameFlags::inline());
        assert_eq!(payload, b"ok");
    }
}
