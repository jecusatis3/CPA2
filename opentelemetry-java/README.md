# OpenTelemetry Java

Modifications to OpenTelemetry Java v1.24.0 to automatically track end parents in spans when necessary to improve critical path analysis for distributed tracing.

Includes modifications similar to the OpenTelemetry C++ patch which modifies ending a span to add an end parent if the active span is different from the ending span's start parent. Also handles asynchronous operations in a similar manner as the async_wrapper.h/pthread_wrapper.h header files.

## To use:
- Download OpenTelemetry Java v1.24.0 (base commit: https://github.com/open-telemetry/opentelemetry-java-instrumentation/commit/ce8c7fa08a37d29bef64ec98c5da6c93baf1ebd9)
- Apply changes: `git apply opentelemetry-java_end_parents.patch`
