# Jaeger

Modifications to Jaeger v2.22.0 to apply CPA2's trace adjustments to Jaeger traces, which can then be viewed in the Jaeger UI. Specifically, adds new passes (explained below) and modifies others to be end parent aware.

Changes include: 
- **Adding a root span**: Add a span that covers the duration of all other spans in the trace.
- **Synthesizing dropped spans**: Synthesize dropped spans to cover the duration of their children spans in the trace.
- **Filling gaps and extending parents**: Alternate approach to eliminating spans where the parent does not overlap the child; instead extends a span to eliminate the gap(s) between it and its parent(s) and also ensures that start/end parents cover the start/end of the span.

We also modify the Clock Skew and Span ID Uniquifier passes to be end parent aware.

Note that the additional passes can be enabled/disabled by using flags included in your Jaeger config file:
- `add_root_span={true/false}`: enables/disables Add Root Span pass
- `dropped_span_support={true/false}`: enables/disables Synthesize Dropped Spans pass; depends on Add Root Span pass being enabled.
- `fill_gaps_extend_parents={true/false}`: enables/disables Fill Gaps and Extend End Parents pass: depends on Add Root Span pass being enabled.

## To use:
- Download Jaeger v2.22.0 (base commit: https://github.com/jaegertracing/jaeger/commit/462bb49fcff9af4be9556bf0206d07f5171bd8d7)
- Apply changes: `git apply jaeger_cpa2.patch`