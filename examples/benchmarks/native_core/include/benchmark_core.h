#ifndef BENCHMARK_CORE_H
#define BENCHMARK_CORE_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

#define BENCHMARK_FRAME_VERSION 1u
#define BENCHMARK_FRAME_BYTES 52u

enum benchmark_error {
    BENCHMARK_OK = 0,
    BENCHMARK_ERROR_NULL_OUTPUT_LENGTH = 1,
    BENCHMARK_ERROR_NULL_INPUT = 2,
    BENCHMARK_ERROR_LENGTH_OVERFLOW = 3,
    BENCHMARK_ERROR_INVALID_OUTPUT_LENGTH = 4,
    BENCHMARK_ERROR_ALLOCATION = 5,
};

/*
 * The returned buffer is exactly BENCHMARK_FRAME_BYTES bytes:
 * version (u32 LE), input length (u64 LE), mix value (u64 LE), SHA-256 digest.
 * The caller owns it and must pass the exact reported length to benchmark_free.
 * On failure this returns NULL, stores zero through output_len when non-NULL,
 * and exposes an enum benchmark_error through benchmark_last_error().
 */
uint8_t *benchmark_run(const uint8_t *input, size_t input_len, size_t *output_len);
void benchmark_free(uint8_t *output, size_t output_len);
int benchmark_last_error(void);

/* Pyroxide requires these names; they have the same semantics as the neutral ABI. */
uint8_t *pyroxide_plugin_run(
    const uint8_t *input,
    size_t input_len,
    size_t *output_len
);
void pyroxide_plugin_free(uint8_t *output, size_t output_len);

#ifdef __cplusplus
}
#endif

#endif
