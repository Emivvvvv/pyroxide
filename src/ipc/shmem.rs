use shared_memory::{Shmem, ShmemConf};

pub(crate) struct ShmemGuard {
    shm: Option<Shmem>,
}

impl ShmemGuard {
    pub(crate) fn create(size: usize, os_id: &str) -> Result<Self, String> {
        let shm = ShmemConf::new()
            .size(size)
            .os_id(os_id)
            .create()
            .map_err(|e| format!("Failed to create shared memory: {e}"))?;
        Ok(Self { shm: Some(shm) })
    }

    pub(crate) fn open(os_id: &str) -> Result<Self, String> {
        let shm = ShmemConf::new()
            .os_id(os_id)
            .open()
            .map_err(|e| format!("Failed to open shared memory segment '{os_id}': {e}"))?;
        unlink_name(os_id);
        Ok(Self { shm: Some(shm) })
    }

    pub(crate) fn len(&self) -> usize {
        self.shm.as_ref().map_or(0, Shmem::len)
    }

    pub(crate) fn as_slice(&self) -> &[u8] {
        if let Some(ref shm) = self.shm {
            // SAFETY: `shm` owns a mapping valid for exactly `shm.len()` bytes.
            unsafe { std::slice::from_raw_parts(shm.as_ptr(), shm.len()) }
        } else {
            &[]
        }
    }

    pub(crate) fn as_ptr(&self) -> *mut u8 {
        if let Some(ref shm) = self.shm {
            shm.as_ptr()
        } else {
            std::ptr::null_mut()
        }
    }

    pub(crate) fn copy_from_slice(&self, data: &[u8]) -> Result<(), String> {
        if self.len() != data.len() {
            return Err(format!(
                "Shared memory size {} does not match payload size {}",
                self.len(),
                data.len()
            ));
        }
        // SAFETY: the shared-memory mapping is valid for `self.len()` bytes,
        // and the source slice is non-overlapping and has the same length.
        unsafe {
            std::ptr::copy_nonoverlapping(data.as_ptr(), self.as_ptr(), data.len());
        }
        Ok(())
    }
}

impl Drop for ShmemGuard {
    fn drop(&mut self) {
        if let Some(shm) = self.shm.take() {
            #[cfg(unix)]
            unlink_name(shm.get_os_id());
        }
    }
}

#[cfg(unix)]
fn unlink_name(os_id: &str) {
    if let Ok(c_name) = std::ffi::CString::new(os_id) {
        // SAFETY: `c_name` is a valid null-terminated name for `shm_unlink`.
        unsafe {
            libc::shm_unlink(c_name.as_ptr());
        }
    }
}
