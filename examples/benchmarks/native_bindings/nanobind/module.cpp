#include <nanobind/nanobind.h>
#include <nanobind/stl/string.h>

#include <stdexcept>
#include <string>
#include <vector>

#include "benchmark_core.h"

namespace nb = nanobind;

NB_MODULE(benchmark_nanobind, module) {
    module.def("run", [](nb::bytes input) {
        std::string copied(input.c_str(), input.size());
        std::vector<uint8_t> output;
        {
            nb::gil_scoped_release release;
            size_t output_len = 0;
            auto *output_ptr = benchmark_run(
                reinterpret_cast<const uint8_t *>(copied.data()), copied.size(), &output_len
            );
            if (output_ptr == nullptr) {
                throw std::runtime_error("native ABI error " + std::to_string(benchmark_last_error()));
            }
            output.assign(output_ptr, output_ptr + output_len);
            benchmark_free(output_ptr, output_len);
        }
        return nb::bytes(reinterpret_cast<const char *>(output.data()), output.size());
    });
    module.attr("gil_policy") = "The native C ABI runs under gil_scoped_release and is not a scheduler.";
}
