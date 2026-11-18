#ifndef ASYNC_WRAPPER_H
#define ASYNC_WRAPPER_H

#include <atomic>
#include <future>

#include "opentelemetry/context/runtime_context.h"
#include "opentelemetry/trace/provider.h"
#include "opentelemetry/trace/scope.h"
#include "opentelemetry/trace/span.h"
#include "opentelemetry/trace/span_id.h"
#include "opentelemetry/trace/tracer.h"
#include "opentelemetry/trace/tracer_provider.h"

/* 
 * AsyncSpanScope creates a span around the asynchronous operation and sets this as the active span.
 * This is the span that will potentially have the joining operation as its end parent. 
 * We determine if the end parent should be set by checking if the end_parent_span_id_ptr_ is set (indicating that the joining has started) 
 * when the async operation completes.
 * If the joining span is different from the launching span (start parent), then the id of the joining span is tracked as the end parent.
 */
class AsyncSpanScope {
public:
    AsyncSpanScope(std::atomic<opentelemetry::trace::SpanId*>* end_parent_span_id_ptr) :
        span_(opentelemetry::trace::Provider::GetTracerProvider()->GetTracer("CPA2-tracer", OPENTELEMETRY_SDK_VERSION)->StartSpan("async")),
        scope_(opentelemetry::trace::Scope(span_)),
        end_parent_span_id_ptr_(end_parent_span_id_ptr)
        {}
    ~AsyncSpanScope() {
        // set the end_parent attribute if necessary
        opentelemetry::trace::SpanId* curr_span_id_ptr = end_parent_span_id_ptr_->load();
        if (curr_span_id_ptr) {
            // only add end parent if the current (joining) span is different from our original parent
            if (*curr_span_id_ptr != span_->GetParentSpanId()){
                span_->SetAttribute("end_parent", curr_span_id_ptr->getSpanIdString());
            }
        } else {
            // the joining thread is not waiting on this async operation; the async span should not be on the critical path
            span_->SetAttribute("end_parent", opentelemetry::trace::SpanId().getSpanIdString());
        }
    }
    
private:
    nostd::shared_ptr<opentelemetry::trace::Span> span_;
    opentelemetry::trace::Scope scope_;
    std::atomic<opentelemetry::trace::SpanId*>* end_parent_span_id_ptr_;

};

/*
 * The EndParent class sets the end_parent_span_id_ptr_ to indicate that the joining operation is waiting for the asynchronous operation to complete.
 */

class EndParent {
public:
    EndParent(std::atomic<opentelemetry::trace::SpanId*>* end_parent_span_id_ptr)
        : span_id_(opentelemetry::trace::Provider::GetTracerProvider()->GetTracer("CPA2-tracer", OPENTELEMETRY_SDK_VERSION)->GetCurrentSpan()->GetContext().span_id()),
          end_parent_span_id_ptr_(end_parent_span_id_ptr)
        {
            // store span id of joining operation so async span can use this as its end parent
            end_parent_span_id_ptr_->store(&span_id_);
        }
    ~EndParent() {
        delete end_parent_span_id_ptr_;
    }
  
private:
    opentelemetry::trace::SpanId span_id_;
    std::atomic<opentelemetry::trace::SpanId*>* end_parent_span_id_ptr_;
};

// Put it in std namespace to be in the same namespace as async
namespace std {

    /*
     * Wrapper around std::async where the launch policy is specified.
     */
    template<class F, class... Args>
    __attribute__ ((noinline))
    std::future<typename std::result_of<typename std::decay<F>::type(typename std::decay<Args>::type...)>::type>
    async_wrapper(std::launch policy, F&& f, Args&&... args)
    {
        auto ctx = opentelemetry::context::RuntimeContext::GetCurrent();
        // allocate space for the end parent span id ptr
        std::atomic<opentelemetry::trace::SpanId*>* end_parent_span_id_ptr = new std::atomic<opentelemetry::trace::SpanId*>(); 
        auto fut = std::async(policy, [=]() {
            // start parent is the span that was active at the launch
            // conditionally attach to current context: in deferred mode, the joining span should be our start (and end) parent
            nostd::unique_ptr<opentelemetry::context::Token> token;
            auto span_id = opentelemetry::trace::Provider::GetTracerProvider()->GetTracer("CPA2-tracer", OPENTELEMETRY_SDK_VERSION)->GetCurrentSpan()->GetContext().span_id();
            if (!span_id.IsValid()) {
                // new thread, so attach to the passed in context
                token = opentelemetry::context::RuntimeContext::Attach(ctx);	    
            }
            AsyncSpanScope scope(end_parent_span_id_ptr);
            return f(args...);
        });
        
        auto ret = std::async(std::launch::deferred, [fut = move(fut), end_parent_span_id_ptr] () mutable {
            // end parent will modify the atomic pointer to indicate it is waiting for the async operation to complete
            EndParent end_parent(end_parent_span_id_ptr);
            return fut.get();
        });

        return ret;
    }

    /*
     * Wrapper around std::async where the launch policy is NOT specified.
     */
    template<class F, class... Args>
    __attribute__ ((noinline))
    std::future<typename std::result_of<typename std::decay<F>::type(typename std::decay<Args>::type...)>::type>
    async_wrapper(F&& f, Args&&... args)
    {
        auto ctx = opentelemetry::context::RuntimeContext::GetCurrent();
        std::atomic<opentelemetry::trace::SpanId*>* end_parent_span_id_ptr = new std::atomic<opentelemetry::trace::SpanId*>(); //malloc space for ptr to end parent span id
        auto fut = std::async([=]() {
            nostd::unique_ptr<opentelemetry::context::Token> token;
            auto span_id = opentelemetry::trace::Provider::GetTracerProvider()->GetTracer("CPA2-tracer", OPENTELEMETRY_SDK_VERSION)->GetCurrentSpan()->GetContext().span_id();
            if (!span_id.IsValid()) {
                token = opentelemetry::context::RuntimeContext::Attach(ctx);	    
            }
            AsyncSpanScope scope(end_parent_span_id_ptr);
            return f(args...);
        });
        
        auto ret = std::async(std::launch::deferred, [fut = move(fut), end_parent_span_id_ptr] () mutable {
            EndParent end_parent(end_parent_span_id_ptr);
            return fut.get();
        });

        return ret;
    }
};

// Macro to replace async calls with async_wrapper calls
#define async(...) async_wrapper(__VA_ARGS__)

#endif // ASYNC_WRAPPER_H
