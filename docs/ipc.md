# Isolated Worker IPC

The isolated worker protocol is private to one Pyroxide release, but its byte
layout is centralized and tested so refactors do not change it accidentally.
All integer fields use big-endian byte order.

## Request frame

Each request is:

```text
[kind: u8]
[flags: u8]
[metadata length: u32]
[payload length: u64]
[metadata bytes]
[payload bytes]
```

The fixed header is 14 bytes. `RequestHeader` validates the kind, flags, and
lengths before allocating metadata or payload buffers. `RequestMetadata`
encodes and decodes backend-specific fields.

Supported request kinds are:

| Value | Meaning |
| ---: | --- |
| 0 | Python call |
| 1 | WebAssembly call |
| 2 | Native library call |
| 10 | Register WebAssembly |
| 11 | Register native library |
| 12 | Unregister native library |

## Response frame

Each response is:

```text
[success: u8]
[flags: u8]
[payload length: u64]
[payload bytes]
```

The fixed header is 10 bytes. Success must be exactly zero or one.

## Flags

Only bit zero is defined:

| Bit | Meaning |
| ---: | --- |
| 0 | Payload bytes contain a shared-memory name |

All other bits are rejected. Inline frames use zero. This validation prevents
two processes from interpreting the same bytes differently.

## Metadata

Metadata strings are encoded as a big-endian `u32` byte length followed by
UTF-8 bytes. The decoder rejects invalid UTF-8, truncation, unknown kinds,
invalid presence flags, excess FFI arguments, and trailing bytes.

The kind stored in the request header must match the kind decoded from
metadata.

## Length limits

Metadata is limited by `MAX_IPC_METADATA_BYTES`, currently 1 MiB. Payload and
response lengths use `PYROXIDE_MAX_IPC_FRAME_BYTES`, with a default of 64 MiB.
Every received length is checked for integer conversion and configured bounds
before allocation.

## Shared memory

Payloads at or above `PYROXIDE_SHM_THRESHOLD`, default 1 MiB, may use shared
memory.

1. The sender creates a uniquely named mapping and copies the payload.
2. The request or response carries the mapping name.
3. The receiver opens the mapping and unlinks its name immediately on Unix.
4. The mapping stays valid through its open handles.
5. `ShmemGuard` closes the mapping and attempts final unlink during drop.

For response mappings, the isolated worker retains its creator guard until the
parent acknowledges that it opened and consumed the mapping.

## I/O ownership

- `ipc::protocol` owns typed headers, flags, and metadata codecs.
- `ipc::frame` owns exact blocking request and response reads and writes.
- `worker.rs` retains cancellation-aware nonblocking response reads, then
  decodes the completed header with `ResponseHeader`.
- `ipc::shmem` owns mapping creation, opening, copying, reading, and cleanup.

Protocol tests pin exact bytes, clean EOF, hostile flags, truncated values,
metadata round trips, and kind mismatches.
