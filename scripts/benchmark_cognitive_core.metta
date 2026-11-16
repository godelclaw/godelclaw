!(import! &self ../tests/cognitive_core_benchmark.metta)
!(nt:require complete-small-flow-budget (core-bench:workload 0 21))
!(nt:require complete-large-flow-budget (core-bench:workload 524288 9))
!(println! NATIVE_CORE_BENCHMARK_PASSED)
