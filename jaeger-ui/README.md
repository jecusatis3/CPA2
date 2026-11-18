# Jaeger UI

Modifications to Jaeger UI v2.22.0 to use CPA2's end parent aware critical path analysis algorithm. This will display the correct critical path in the Jaeger UI for traces that contain end parent information.

In addition to the updated critical path algorithm, other changes include:
- **Updated find last finishing span**: Similar to CRISP, allow some overlap when finding the last finising child span to account for clock skew. Permitted overlap is set to 1% of the parent span's duration.
- **Updated sanitize overflowing children**: Modified to be end parent aware.

## To use:
- Download Jaeger UI v2.22.0 (base commit: https://github.com/jaegertracing/jaeger-ui/commit/f538c58b20fab5ba3973269dcb3f458e84605da3)
- Apply changes: `git apply jaeger-ui_cpa2.patch`