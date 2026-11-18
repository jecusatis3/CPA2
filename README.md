# CPA2

CPA2 introduces the idea using of two parents for distributed trace spans instead of only one; the first parent (i.e., start parent) is the standard parent used in OpenTelemetry spans, indicating the span that caused the child span to occur. The added second parent (i.e., end parent) indicates the span that waits for the child span to complete. End parents thus represent dependency information that was missing in distributed traces, and we find that tracking these dependencies improves critical path analysis for distributed tracing. Please see our paper for more details.

## Tracking end parents automatically
The OpenTelemetry extensions and threading wrappers can be used to track second parents automatically. To use these, please see the corresponding READMEs in each directory for more information.  

## End parent aware critical path analysis
End parents represent dependencies between spans and thus should be considered when calculating a trace's critical path. Our analysis does this and also applies the modifications mentioned in our paper (i.e., synthesizing dropped spans, clock skew adjustments, inferring end parents, and filling gaps).

## Paper
CPA2 is published in the proceedings of SoCC 2026 and can be accessed here: https://doi.org/10.1145/3828161.3857895