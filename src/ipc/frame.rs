use crate::config::checked_ipc_len;

pub(crate) fn read_exact_frame(
    stream: &mut impl std::io::Read,
    max_len: usize,
    label: &str,
) -> Result<Vec<u8>, String> {
    let mut len_buf = [0u8; 8];
    stream
        .read_exact(&mut len_buf)
        .map_err(|e| format!("Failed to read {label} length: {e}"))?;
    let raw_len = u64::from_be_bytes(len_buf);
    let len = checked_ipc_len(raw_len, max_len, label)?;
    let mut buf = vec![0u8; len];
    stream
        .read_exact(&mut buf)
        .map_err(|e| format!("Failed to read {label} payload of size {len}: {e}"))?;
    Ok(buf)
}

pub(crate) fn write_exact_frame(
    stream: &mut impl std::io::Write,
    payload: &[u8],
    label: &str,
) -> Result<(), String> {
    let len_buf = (payload.len() as u64).to_be_bytes();
    stream
        .write_all(&len_buf)
        .map_err(|e| format!("Failed to write {label} length: {e}"))?;
    stream
        .write_all(payload)
        .map_err(|e| format!("Failed to write {label} payload: {e}"))?;
    stream
        .flush()
        .map_err(|e| format!("Failed to flush {label}: {e}"))?;
    Ok(())
}
