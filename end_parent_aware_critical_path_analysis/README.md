# End parent aware critical path analysis

Our analysis code first applies the modifications as described in the paper:
- **Synthesizing dropped spans**: If we identify that a span was dropped (i.e., a span is tracking parent that is not present in the trace data), we synthesize the dropped span to cover the duration of its children spans. We then attach these synthesized spans into the trace as a child of a span that covers its duration. This approach has known llimitations (i.e., not all dropped spans can be identified this way, critical path time may still be missed in identifiable dropped spans), but we still find that it reduces critical path error.  Please see our paper for more details.
- **Clock skew adjustments**: We use an end parent aware version of Jaeger's clock skew adjustment, which centers spans within their parent when clock skew is detected. Here, we must consider both a span's start and end parents when centering the span and when deciding if the parent's adjustment can be used for the child.
- **Inferring end parents (optional)**: Infers end parents for traces that were collected without end parent support as described in our paper. This approach has known limitations and thus using our OpenTelemetry changes/threading wrappers to track end parents is recommended, but we provide this heuristic to use if that is not possible.
- **Filling gaps/extending parents**: There can sometimes be gaps between a span and its start/end parents (e.g., asynchronous operation that starts a bit after it was launched).  CRISP and Jaeger eliminate these spans from consideration when calculating the critical path; this is a different approach where we extend the child to cover these gaps and also ensure that the start/end parents cover the start/end of a child span, preventing issues with calculating the critical path.

After these adjustments, the code calculates the end parent aware critical path of the trace. Below is a description and psuedocode representation of the algorithm.

- The algorithm calculates the critical path by tracking time in a variable *t* and moving *t* backwards in time throughout the algorithm.
- We initialize *t* to the root span's end time (line 4).
- In addition to tracking the current time in *t*, the algorithm also tracks the current span that is on the critical path in a variable *curr_span*, which is initialized to the root span (line 5).
- To determine how much time *curr_span* spends on the critical path, we consider the children of *curr_span* that end before *t* and pick the one that ends latest (lines 10-14). Note that only children that have *curr_span* as their end parent are considered.
- CPA2 treats all spans as conceptually having both a start and end parent, where if no end parent is specified in the trace, the span's start parent is treated as its end parent.
- If a child is found (line 16):
    - The time between *t* and the child's end time is added to the current span's critical path time (line 17).
    - The algorithm then switches to calculating the critical path time of the child by updating *t* to the child's end time and updating *curr_span* to the child (lines 19-20).
- If no child is found (line 21):
    - The remaining time of *curr_span* (i.e., time between *t* and the span's start time) is on the critical path (line 23).
    - The algorithm then switches to calculating the critical path time of the span's start parent by updating *t* to the span's start time and updating *curr_span* to the start parent (lines 25-26).
- Note that *t* always progresses backwards in time and never skips any time, so the total critical path time is conserved and matches the request latency.
This includes correctly handling any gaps in time when transitioning between parents/children.

```python
1 # Calculates the critical path starting from the root span of a trace
2 # Assumes each span has both a start parent and end parent
3 def calculateCriticalPath(root_span):
4     t = root_span.end_time
5     curr_span = root_span  
6     
7     while t > root_span.start_time:
8         # Finds the latest ending child that completes before t (if any)
9         # Only considers children that have curr_span as their end parent
10        child = None
11        for c in curr_span.children:
12            if c.end_time < t and c.end_parent == curr_span:
13                if child is None or c.end_time > child.end_time:
14                    child = c
15        
16        if child is not None:
17            curr_span.crit_path_time += t - child.end_time
18            # Move to child
19            t = child.end_time
20            curr_span = child
21        else:
22            # curr_span doesn't have any (more) children that are on the critical path
23            curr_span.crit_path_time += t - curr_span.start_time
24            # Move to start parent 
25            t = curr_span.start_time                
26            curr_span = curr_span.start_parent
```

The output of this code is another JSON file where the timestamps of the spans are adjusted according to the modifications described above. If inferring end parents is enabled, spans will have an end_parent attribute added where applicable. Each span will also contain an attribute indicating its critical path time.

## To use:
Run: `python3 analyze.py --input {input_trace} --output {output_trace}`

Optional flags:
- `--find_end_parents`: enabled inferring end parents
- `--end_parent {edges threshold}`: specify integer threshold to limit end parent candidates to those that are at least as related as this threshold indicates