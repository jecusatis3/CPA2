# OpenTelemetry C++

Modifications to OpenTelemetry C++ v1.29.0 to automatically track end parents in spans when necessary to improve critical path analysis for distributed tracing.

When a span is ending, check if the active span is different from the ending span's start parent; if it is, that span is tracked as the ending span's end parent, as it is the operation that waits for the span to complete.

## To use:
- Download OpenTelemetry C++ v1.29.0 (base commit: https://github.com/open-telemetry/opentelemetry-cpp/commit/eb92442e9cacc3ecd20b481cb256471f03b70cf6)
- Apply changes: `git apply opentelemetry-cpp_end_parents.patch`