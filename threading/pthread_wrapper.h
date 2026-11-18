#ifndef PTHREAD_WRAPPER_H
#define PTHREAD_WRAPPER_H

#include <atomic>
#include <mutex>
#include <pthread.h>

#include "opentelemetry/context/runtime_context.h"
#include "opentelemetry/trace/provider.h"
#include "opentelemetry/trace/scope.h"
#include "opentelemetry/trace/span.h"
#include "opentelemetry/trace/span_id.h"
#include "opentelemetry/trace/tracer.h"
#include "opentelemetry/trace/tracer_provider.h"

std::unordered_map<pthread_t, opentelemetry::trace::SpanId> thread_id_to_span_id{};
std::mutex span_id_mutex;

typedef void* (*StartRoutine)(void*);

/* 
 * ContextAndThreadInfo contains the information needed to run the start routine of the new thread.
 * If the start routine finishes "fast" (i.e., before pthread_create_wrapper has returned), set start_routine_done to indicate this
 * This will indicate to pthread_create_wrapper to not create a map entry for the thread so memory is not leaked.
 */
class ContextAndThreadInfo {
public:
    ContextAndThreadInfo(StartRoutine start_routine, void* arg, std::atomic<bool>* start_routine_done) :
        ctx_(opentelemetry::context::Context(opentelemetry::context::RuntimeContext::GetCurrent())),
        start_routine_(start_routine),
        arg_(arg),
        start_routine_done_(start_routine_done)
        {}
    ~ContextAndThreadInfo() {}
    void* start() {
        auto token = opentelemetry::context::RuntimeContext::Attach(ctx_);
        auto span = opentelemetry::trace::Provider::GetTracerProvider()->GetTracer("CPA2-tracer", OPENTELEMETRY_SDK_VERSION)->StartSpan("pthread_wrapper");
        auto scope = opentelemetry::trace::Scope(span);
        // run the start routine
        void* retval = start_routine_(arg_);

        // set the end_parent attribute, if needed
        opentelemetry::trace::SpanId span_id;
        pthread_t tid = pthread_self();
        span_id_mutex.lock();
        auto it = thread_id_to_span_id.find(tid);
        if (it != thread_id_to_span_id.end()) {
            span_id = it->second;
            // remove from map because
            thread_id_to_span_id.erase(tid);
            span_id_mutex.unlock();
            // value will either be OpenTelemetry invalid span or actual end parent; either way, set the end parent (if not equal to parent)
            if (span_id != span->GetParentSpanId()) {
                span->SetAttribute("end_parent", span_id.getSpanIdString());
            }
        } else {
            // start_routine was fast, pthread_create_wrapper hasn't created an entry in the map yet for this thread
            // lack of existence of the thread id in the map indicates that the pthread_create_wrapper hasn't returned yet
            (*start_routine_done_).store(true);            
            span_id_mutex.unlock();

            // add invalid span as this span's end parent to indicate that the span should not be on the critical path because nothing waited for it
            span->SetAttribute("end_parent", opentelemetry::trace::SpanId().getSpanIdString());
        }
        return retval;
    }


private:
    opentelemetry::context::Context ctx_;
    StartRoutine start_routine_;
    void* arg_;
    std::atomic<bool>* start_routine_done_;
};

/*
 * @param args - ptr to ContextAndThreadInfo that was created in pthread_create_wrapper
 *
 * @return - retur value of the thread's start routine
 */
void* start_routine_wrapper(void* args) {
    ContextAndThreadInfo* ctx_and_thread_info = static_cast<ContextAndThreadInfo*>(args);
    void* retval = ctx_and_thread_info->start();
    delete ctx_and_thread_info;
    return retval;
}

/*
 * @param thread - new thread
 * @param attr - new thread's attributes
 * @param start_routine - new thread's start routine
 * @param arg - new threads's arg for start routine
 */
void pthread_create_wrapper(pthread_t* thread, const pthread_attr_t* attr, StartRoutine start_routine, void* arg) {
    std::atomic<bool> start_routine_done(false);
    ContextAndThreadInfo* ctx_and_thread_info = new ContextAndThreadInfo(start_routine, arg, &start_routine_done);
    pthread_create(thread, attr, start_routine_wrapper, ctx_and_thread_info);
    span_id_mutex.lock();
    // only add an entry to the map for this thread if the start routine has not already completed, so there isn't a leaked entry
    // also, if an entry is added here, it is the invalid span id; can be updated to joining operation span id IF it waits for the new thread
    if (start_routine_done.load() == false) {
        thread_id_to_span_id[*thread] = opentelemetry::trace::SpanId();
    }
    span_id_mutex.unlock();
}

/*
 * @param tid - id of thread to join
 * @param value_ptr - pointer to exit status of thread being joined
 */
void pthread_join_wrapper(pthread_t tid, void** value_ptr) {
    // if the thread id is in the map, set its value so it can be used as the end parent (if it is not the same as the start parent)
    span_id_mutex.lock();
    auto it = thread_id_to_span_id.find(tid);
    if (it != thread_id_to_span_id.end()) {
        it->second = opentelemetry::trace::Provider::GetTracerProvider()->GetTracer("CPA2-tracer", OPENTELEMETRY_SDK_VERSION)->GetCurrentSpan()->GetContext().span_id();
    }
    span_id_mutex.unlock();

    pthread_join(tid, value_ptr);
}

// Macros to replace pthread_create/pthread_join calls with pthread_create_wrapper/pthread_join_wrapper calls
#define pthread_create(...) pthread_create_wrapper(__VA_ARGS__)
#define pthread_join(...) pthread_join_wrapper(__VA_ARGS__)

#endif // PTHREAD_WRAPPER_H
