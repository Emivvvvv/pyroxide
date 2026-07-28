#[cfg(unix)]
use std::sync::atomic::{AtomicI32, Ordering};

#[cfg(unix)]
static ASYNC_WAKER_FD: AtomicI32 = AtomicI32::new(-1);

#[cfg(unix)]
pub(crate) fn set_async_waker_fd(fd: std::os::fd::RawFd) {
    ASYNC_WAKER_FD.store(fd, Ordering::Release);
}

#[cfg(unix)]
pub(crate) fn clear_async_waker_fd(fd: std::os::fd::RawFd) -> bool {
    ASYNC_WAKER_FD
        .compare_exchange(fd, -1, Ordering::AcqRel, Ordering::Acquire)
        .is_ok()
}

#[cfg(unix)]
pub(crate) fn notify_waker(_task_id: usize) {
    let fd = ASYNC_WAKER_FD.load(Ordering::Acquire);
    if fd < 0 {
        return;
    }

    let byte = [1u8];
    loop {
        let result = unsafe { libc::write(fd, byte.as_ptr().cast::<libc::c_void>(), byte.len()) };
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
