# -*- coding: utf-8 -*-
from pyroxide import compile_c, dylib_task

C_SRC = """
#include <stdint.h>
#include <stdlib.h>

uint8_t* pyroxide_plugin_run(const uint8_t* ptr, size_t len, size_t* out_len) {
    uint8_t* res = (uint8_t*)malloc(len);
    for (size_t i = 0; i < len; i++) {
        res[i] = ptr[i] + 1; // Basic caesar-shift
    }
    *out_len = len;
    return res;
}

void pyroxide_plugin_free(uint8_t* ptr, size_t len) {
    free(ptr);
}
"""

if __name__ == "__main__":
    print("--- 8. Dynamic Native Compilers Example ---")
    
    # Compile and register C code on-the-fly!
    compile_c("caesar_shift", C_SRC)
    
    @dylib_task("caesar_shift")
    def apply_caesar(payload: bytes) -> bytes:
        pass
        
    # Execute native task GIL-free
    result = apply_caesar(b"abc").result()
    print(f"Input:  b'abc'")
    print(f"Output: {result}")
