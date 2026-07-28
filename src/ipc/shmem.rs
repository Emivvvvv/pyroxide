use shared_memory::{Shmem, ShmemConf};

pub(crate) struct ShmemGuard {
    pub(crate) shm: Shmem,
}

impl ShmemGuard {
    pub(crate) fn new(size: usize) -> Result<(Self, String), String> {
        let shm = ShmemConf::new()
            .size(size)
            .os_id("")
            .create()
            .map_err(|e| format!("Failed to create shared memory: {e}"))?;
        let os_id = shm.get_os_id().to_string();
        Ok((Self { shm }, os_id))
    }

    pub(crate) fn open(os_id: &str) -> Result<Self, String> {
        let shm = ShmemConf::new()
            .os_id(os_id)
            .open()
            .map_err(|e| format!("Failed to open shared memory segment '{os_id}': {e}"))?;
        Ok(Self { shm })
    }

    pub(crate) fn as_slice(&self) -> &[u8] {
        unsafe { std::slice::from_raw_parts(self.shm.as_ptr(), self.shm.len()) }
    }

    #[allow(clippy::mut_from_ref)]
    pub(crate) unsafe fn as_mut_slice(&self) -> &mut [u8] {
        unsafe { std::slice::from_raw_parts_mut(self.shm.as_ptr(), self.shm.len()) }
    }
}
