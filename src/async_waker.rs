#[cfg(unix)]
use std::os::fd::{AsRawFd, BorrowedFd, OwnedFd, RawFd};
#[cfg(unix)]
use std::sync::Mutex;

#[cfg(unix)]
struct RegisteredWaker {
    source_fd: RawFd,
    owned_fd: OwnedFd,
}

#[cfg(unix)]
static ASYNC_WAKER_FD: Mutex<Option<RegisteredWaker>> = Mutex::new(None);

#[cfg(unix)]
pub(crate) fn set_async_waker_fd(fd: RawFd) -> Result<(), String> {
    // SAFETY: Python passes an open pipe descriptor. The borrow ends after
    // cloning, and the registered waker owns the cloned descriptor.
    let owned = unsafe { BorrowedFd::borrow_raw(fd) }
        .try_clone_to_owned()
        .map_err(|e| format!("Failed to clone waker FD: {e}"))?;
    let mut guard = ASYNC_WAKER_FD
        .lock()
        .map_err(|e| format!("Waker lock poisoned: {e}"))?;
    *guard = Some(RegisteredWaker {
        source_fd: fd,
        owned_fd: owned,
    });
    Ok(())
}

#[cfg(unix)]
pub(crate) fn clear_async_waker_fd(fd: RawFd) -> bool {
    if let Ok(mut guard) = ASYNC_WAKER_FD.lock() {
        if guard
            .as_ref()
            .is_some_and(|registered| registered.source_fd == fd)
        {
            guard.take();
            true
        } else {
            false
        }
    } else {
        false
    }
}

#[cfg(unix)]
pub(crate) fn notify_waker(_task_id: usize) {
    let Ok(guard) = ASYNC_WAKER_FD.lock() else {
        return;
    };
    let Some(registered) = guard.as_ref() else {
        return;
    };

    let raw = registered.owned_fd.as_raw_fd();
    let byte = [1u8];
    loop {
        // SAFETY: `raw` is owned by `registered`, and `byte` remains valid for
        // the duration of this one-byte write.
        let result = unsafe { libc::write(raw, byte.as_ptr().cast::<libc::c_void>(), byte.len()) };
        if result >= 0 {
            break;
        }

        let error = std::io::Error::last_os_error();
        if error.kind() != std::io::ErrorKind::Interrupted {
            break;
        }
    }
}

#[cfg(not(unix))]
pub(crate) fn notify_waker(_task_id: usize) {
    // Windows uses the executor-backed async wait path.
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    use std::io::Read;
    use std::os::unix::net::UnixStream;
    use std::time::Duration;

    #[test]
    fn stale_clear_preserves_replacement() {
        let (_first_reader, first_writer) = UnixStream::pair().unwrap();
        let (mut replacement_reader, replacement_writer) = UnixStream::pair().unwrap();
        replacement_reader
            .set_read_timeout(Some(Duration::from_secs(1)))
            .unwrap();

        set_async_waker_fd(first_writer.as_raw_fd()).unwrap();
        set_async_waker_fd(replacement_writer.as_raw_fd()).unwrap();

        assert!(!clear_async_waker_fd(first_writer.as_raw_fd()));
        notify_waker(1);

        let mut byte = [0u8; 1];
        replacement_reader.read_exact(&mut byte).unwrap();
        assert_eq!(byte, [1]);
        assert!(clear_async_waker_fd(replacement_writer.as_raw_fd()));
    }
}
