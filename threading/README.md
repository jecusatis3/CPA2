# Threading wrappers

Automatically handle context propagation between threads and track dependencies between asynchronous operations and their joining operation to improve critical path analysis for distributed tracing. 

## std::async wrapper
Wrapper around std::async calls. Creates a span around the asynchronous operation and uses an atomic pointer to indicate if the joining operation is waiting for the asynchronous operation; if this is the case, the joining operation span is tracked as the end parent of the asynchronous operation span. Otherwise, the asynchronous operation span should not be on the critical path as it does not cause any other operations to wait; to denote this, the asynchronous operation span is assigned a NULL end parent (i.e., OpenTelemetry invalid span id). 

### To use:
Include header in files where std::async is called.  Note that OpenTelemetry C++ must be installed for this wrapper to work.


## pthread wrapper
Wrappers around pthread_create and pthread_join calls. Creates a span around the asynchronous operation performed by the new thread and uses a global map to determine if the joining operation is waiting for the asynchronous operation; if this is the case, the joining operation span is tracked as the end parent of the asynchronous operation span. Otherwise, the asynchronous operation span should not be on the critical path as it does not cause any other operations to wait; to denote this, the asynchronous operation span is assigned a NULL end parent (i.e., OpenTelemetry invalid span id). 

### To use:
Include header in files where pthread_create and pthread_join are called.  Note that OpenTelemetry C++ must be installed for this wrapper to work.

