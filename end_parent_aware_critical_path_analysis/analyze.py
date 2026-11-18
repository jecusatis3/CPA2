import pprint
import statistics
import numpy as np
import time
import json
import sys
import argparse
from enum import Enum
from anytree import Node
from bitarray import bitarray #version 1.2.1


class GenericReprStrBase:
    def __repr__(self):
        return "{}({})".format(type(self).__name__, ", ".join(["{}={!r}".format(k, v) for k, v in vars(self).items()]))
    def __str__(self):
        return "{}({})".format(type(self).__name__, ", ".join(["{}={!r}".format(k, v) for k, v in vars(self).items()]))

class ClockSkew:
    def __init__(self, delta, host_key):
        self.delta = delta
        self.host_key = host_key
        
    def get_delta(self):
        return self.delta
    
    def get_host_key(self):
        return self.host_key

# Trace class tracks important information about the Jaeger trace.
# Contains multiple functions to modify the trace to account for issues such as dropped spans, clock skew, and inferring end parents in traces that did not track them.
class Trace(GenericReprStrBase):
    def __init__(self, trace_id):
        self.spans = {}
        self.trace_id = trace_id
        self.T = None
        # add a root span that encompasses all spans
        self.root_id = "0000000000000001"
        self.root_name = "ROOT"
        self.dropped_span_count = 0
        self.spans_needing_end_parent = {}
        self.overlap_threshold = 0.01
        self.invalid_span_id = "0000000000000000"
        self.spans_in_JSON = []
        self.min_start = float('inf')
        self.max_end = 0
        self.children_of_root = []
        
    def add_span(self, span_id, span):
        self.spans[span_id] = span
        # track min start and max end to adjust root span to cover all of this (which is added after all other spans are added)
        if span.start_time < self.min_start:
            self.min_start = span.start_time
        if span.end_time > self.max_end:
            self.max_end = span.end_time

    def increment_span_id_str(self, span_id_str):
        # interpret string as hex and convert to int and add 1, then convert back to string
        # in hex with 8 bytes (16 characters)
        return format((int(span_id_str, 16) + 1), "016x")

    def add_root_span(self):
        # check if root span id is in the trace; if it is, increment until that is not the case
        while True:
            if self.root_id not in self.spans:
                break
            else:
                self.root_id = self.increment_span_id_str(self.root_id)
        
        self.add_span(self.root_id, Span("pROOT", self.root_name, self.root_name, self.min_start, self.max_end, "", "", {}, {}, False))

        for child in self.children_of_root:
            self.spans[child].start_parent = self.root_id
            if self.spans[child].end_parent == "":
                self.spans[child].end_parent = self.root_id
            
    ###################CRITICAL PATH FUNCTIONS###################            
    # Find the next child to visit when calculating the critical path
    # In general, this should be before the current time t.  However, similar to CRISP we allow some overlap to account for clock skew issues.
    # The permitted overlap delta by default is 1% of the parent span's duration.  Our top choice is a span that ends between the current time t and t - delta
    # Our second choice is the true overlapping case, where we find spans that have an end time that ie between t and t + delta.
    # Our third choice is a span that ends before t - delta.
    # This way, we favor choosing spans that end close to t if possible.
    # Note that even though some overlap may occur, t (the current time in the critical path calculation) can only move backwards in time (i.e., later to earlier).
    # There are checks when calculating the critical path to prevent tracking negative critical path time intervals.
    def find_recent_completion(self, span_children_dict, t, visited_children, parent_span_id):
        second_choice = []
        third_choice = None
        delta = self.overlap_threshold * (self.spans[parent_span_id].end_time - self.spans[parent_span_id].start_time)
        for child_id, child_stop in span_children_dict.items():
            if parent_span_id in visited_children and child_id in visited_children[parent_span_id]:
                continue
            # only consider end children
            if self.spans[child_id].end_parent != parent_span_id:
                continue

            if t - delta <= child_stop and child_stop <= t:
                # this is our top choice, and the first one we find will have the max end time of this type since the children dictionary is sorted by descending end time.
                return child_id
            elif t < child_stop and child_stop <= t + delta:
                second_choice.append(child_id)
            elif child_stop < t - delta:
                third_choice = child_id
                break
        if len(second_choice) > 0:
            # in this case we want the earliest end
            # the dictionary is sorted by end time so get the last item in this list
            return second_choice[-1]
        
        return third_choice

    # sort by end time(later to earlier), then duration (largest to smallest)
    def sort_children_descending_by_end_time(self, spanid):
        child_to_end_dict = {}
        for child in self.spans[spanid].children:
            child_to_end_dict[child] = self.spans[child].end_time
        sorted_child_to_end_dict = dict(sorted(child_to_end_dict.items(), key = lambda child: (child[1], (self.spans[child[0]].end_time - self.spans[child[0]].start_time)), reverse=True))
        return sorted_child_to_end_dict
            
    # end parent aware crtical path analysis
    def calculate_critical_path_time(self, root_span_id):
        # t is the current time in the critical path calculation
        t = self.spans[root_span_id].end_time
        curr_span_id = root_span_id

        # track all the children that have been visited from this span to prevent infinite looping
        visited_children = {}
        
        # keep moving back in time until we've reached the start time of the root span (which is the beginning of the trace)
        while t > self.spans[root_span_id].start_time:
            if curr_span_id not in visited_children:
                visited_children[curr_span_id] = set()
            # get sorted dictionary of children to use when finding the latest finishing child (LFC) 
            sorted_children_dict = self.sort_children_descending_by_end_time(curr_span_id)
            # pass curr_span_id so we can find the duration of the parent (current) span, to use in threshold calculation.
            LFC_id = self.find_recent_completion(sorted_children_dict, t, visited_children, curr_span_id)
            assert(LFC_id not in visited_children[curr_span_id])
            if LFC_id is not None:
                LFC = self.spans[LFC_id]
                visited_children[curr_span_id].add(LFC_id)
                # need to check because we allow an overlap threshold for happens-before
                # don't track negative critical path time intervals
                if LFC.end_time < t:
                    self.spans[curr_span_id].crit_path_time += t - LFC.end_time
                    self.spans[curr_span_id].crit_path_interval.append((LFC.end_time,t))
                    t = LFC.end_time
                curr_span_id = LFC_id
            else:
                # similar above, don't track negative critical path time intervals
                if self.spans[curr_span_id].start_time < t:
                    self.spans[curr_span_id].crit_path_time += t - self.spans[curr_span_id].start_time
                    self.spans[curr_span_id].crit_path_interval.append((self.spans[curr_span_id].start_time, t))
                    t = self.spans[curr_span_id].start_time
                
                # go back to start parent
                curr_span_id = self.spans[curr_span_id].start_parent


    ###################CLOCK SKEW FUNCTIONS###################
    def host_key(self, span_id):
        if self.spans[span_id].host_id is not None:
            return self.spans[span_id].host_id
        # assuming that host_ips are stored in a list, following the otel semantic convention.
        if len(self.spans[span_id].host_ips) > 0:
            return self.spans[span_id].host_ips[0]
        if self.spans[span_id].host_name is not None:
            return self.spans[span_id].host_name
        return ""        

    def calculate_skew(self, child_span_id):
        start_parent_span_id = self.spans[child_span_id].start_parent
        end_parent_span_id = self.spans[child_span_id].end_parent
        if start_parent_span_id == "" or end_parent_span_id == "":
            # root span
            return 0
        # if the end parent is the invalid span, the async span finished before it was joined.
        if end_parent_span_id == self.invalid_span_id:
            # make the end time "infinitely" long (as long as it can be in this trace), because in this case, we are only concerned with fixing skew about the start parent.
            end_parent_span_id = self.root_id
            
        parent_start_time = self.spans[start_parent_span_id].start_time
        child_start_time = self.spans[child_span_id].start_time

        parent_end_time = self.spans[end_parent_span_id].end_time
        child_end_time = self.spans[child_span_id].end_time

        parent_duration = parent_end_time - parent_start_time
        child_duration = child_end_time - child_start_time
        
        if child_duration > parent_duration:
            # if child is longer than parent (because of async call/parent timed out),
            # adjust child (if needed) to not start before parent
            # can shift opposite way if we want the child to be on the critical path
            if child_start_time < parent_start_time:
                return parent_start_time - child_start_time
            return 0
        
        elif child_start_time >= parent_start_time and child_end_time <= parent_end_time:
            # child fits within parent
            return 0

        latency = (parent_duration - child_duration) / 2
        return parent_start_time + latency - child_start_time

    def adjust_timestamp(self, span_id, skew):
        if skew.get_delta() == 0:
            return
        self.spans[span_id].start_time += skew.get_delta()
        self.spans[span_id].end_time += skew.get_delta()

    def parents_not_adjusted(self, span_id):
        if span_id == self.root_id:
            return False # root doesn't have any parents, so they cannot be adjusted/unadjusted
        start_parent_id = self.spans[span_id].start_parent
        end_parent_id = self.spans[span_id].end_parent
        if end_parent_id == self.invalid_span_id:
            end_parent_id = start_parent_id
        
        if self.spans[start_parent_id].clock_skew_adjustment is None:
            return True
        if self.spans[end_parent_id].clock_skew_adjustment is None:
            return True
        return False

    # since spans have a start and end parent, need to take care when considering if the parent's adjustment should be used for the child or not
    # specifically, if a span has different start and end parents, only use adjustment from parents if they have the same host key and adjustment.
    def use_parent_adjustment(self, span_id):
        if span_id == self.root_id:
            return False
        start_parent_id = self.spans[span_id].start_parent
        end_parent_id = self.spans[span_id].end_parent
        if end_parent_id == self.invalid_span_id:
            end_parent_id = start_parent_id

        start_parent_host_key = self.host_key(start_parent_id)
        end_parent_host_key = self.host_key(end_parent_id)

        start_parent_adjustment = self.spans[start_parent_id].clock_skew_adjustment
        end_parent_adjustment = self.spans[end_parent_id].clock_skew_adjustment
        
        if start_parent_host_key == "" or end_parent_host_key == "":
            return False
        if start_parent_host_key == end_parent_host_key:
            if start_parent_host_key == self.host_key(span_id):
                if start_parent_adjustment == end_parent_adjustment:
                    return True
        return False
        
    def apply_clock_skew_adjustments(self, curr_span_id, clock_skew):
        curr_host_key = self.host_key(curr_span_id) # if not set, this defaults to empty string-- in which case, we always calculate the adjustment
        if self.parents_not_adjusted(curr_span_id) or self.spans[curr_span_id].clock_skew_adjustment is not None:
            # if one of the span's parents hasn't been adjusted yet, stop in this path-- will be found through the other parent eventually
            # also stop if this span has already been adjusted.
            return 
        if curr_host_key == "" or curr_host_key != clock_skew.get_host_key() or ((self.spans[curr_span_id].start_parent != self.spans[curr_span_id].end_parent) and (self.use_parent_adjustment(curr_span_id) == False)):
            clock_skew = ClockSkew(self.calculate_skew(curr_span_id), curr_host_key)
        self.adjust_timestamp(curr_span_id, clock_skew)
        self.spans[curr_span_id].clock_skew_adjustment = clock_skew.delta
        
        for child_span_id in self.spans[curr_span_id].children:
            self.apply_clock_skew_adjustments(child_span_id, clock_skew)


    ###################FILLING GAPS FUNCTIONS###################
    # CRISP and Jaeger sanitize overflowing children; this is an alternate approach that does not eliminate spans from the critical path that have gaps between their start/end time and parent.         
    def ext_start(self, span_id, orig_parent_end, mod_start_tracker):
        span = self.spans[span_id]
        # first, handle gaps
        mod_start = min(span.start_time, orig_parent_end)
        for c_id in span.children:
            # then, recurse through start children.  On way back up, we adjust this span's start to cover their start, if needed.
            if self.spans[c_id].start_parent == span_id:
                mod_start = min(mod_start, self.ext_start(c_id, span.end_time, mod_start_tracker))
        mod_start_tracker[span_id] = mod_start
        return mod_start
        
    def ext_end(self, span_id, orig_parent_start, mod_end_tracker):
        span = self.spans[span_id]
        # handle gaps
        mod_end = max(span.end_time, orig_parent_start)
        for c_id in span.children:
            if self.spans[c_id].end_parent == span_id:
                # recurse through end children and on way back up adjust this span's end to cover their start, if needed.
                mod_end = max(mod_end, self.ext_end(c_id, span.start_time, mod_end_tracker))
            elif self.spans[c_id].end_parent == self.invalid_span_id:
                # this is a start child of this span that has NULL as its end parent
                # in this case, don't worry about this span covering the end of the child because it won't be on the critical path.   
                self.ext_end(c_id, span.start_time, mod_end_tracker)
        mod_end_tracker[span_id] = mod_end
        return mod_end
                
    def fill_gaps_and_extend_parents(self):
        mod_start_tracker = {}
        # recurse through start parents
        self.ext_start(self.root_id, float("inf"), mod_start_tracker)
        
        mod_end_tracker = {}
        # recurse through end parents
        self.ext_end(self.root_id, 0, mod_end_tracker)

        # pass through all spans to modify their start/end times, if needed
        for span_id, span_obj in self.spans.items():
            if span_obj.start_time != mod_start_tracker[span_id]:
                span_obj.start_time = mod_start_tracker[span_id]
            if span_obj.end_time != mod_end_tracker[span_id]:
                span_obj.end_time = mod_end_tracker[span_id]

    # modify original JSON to have the modifications from our adjustments
    # includes: adding synthesized dropped spans, adjusting span start/end times for clock skew and filling gaps, and adding end parent attributes (if enabled)
    # we also add an attribute to indicate critical path time calculated by our algorithm
    def get_new_JSON(self, in_file, out_file):
        with open(in_file, 'r') as orig_JSON:
            traces_json = json.load(orig_JSON)

        # add root process to trace
        traces_json["data"][0]["processes"]["pROOT"] = {"serviceName": self.root_name,
                                                            "tags": []}
        # add root & dropped spans to trace
        for span_id, span_obj in self.spans.items():
            if span_id not in self.spans_in_JSON:
                traces_json["data"][0]["spans"].append({"traceID": self.trace_id,
                                                    "spanID": span_id,
                                                    "operationName": span_obj.operation_name,
                                                    "references":[
                                                        {"refType": "CHILD_OF",
                                                        "traceID": self.trace_id,
                                                        "spanID": span_obj.start_parent}],
                                                    "startTime": span_obj.start_time,
                                                    "duration": span_obj.end_time - span_obj.start_time,
                                                    "tags": [],
                                                    "logs": [],
                                                    "processID": span_obj.process_id})   
        # assuming 1 trace per file
        main_data = traces_json["data"][0]
        for span in main_data["spans"]:
            span_obj = self.spans[span["spanID"]]

            # update span time: could have been adjusted for clock skew or filling in gaps/covering children
            span["startTime"] = span_obj.start_time
            span["duration"] = span_obj.end_time - span_obj.start_time
            
            if "tags" not in span:
                span["tags"] = []

            # if end parent is different from start parent, add end parent attribute (if doesn't already exist)
            if span_obj.end_parent != span_obj.start_parent:
                end_parent_found = False
                for attr in span["tags"]:
                    if attr["key"] == "end_parent":
                        end_parent_found = True
                        break
                if end_parent_found == False:
                    span["tags"].append(
                        {"key": "end_parent",
                        "type": "string",
                        "value": span_obj.end_parent
                        })

            # add critical path time as attribute
            span["tags"].append(
                {"key": "critical-path-time",
                "type": "float",
                "value": span_obj.crit_path_time
                })

            # attach all spans without a start parent to the root span
            if span["spanID"] != self.root_id:
                start_parent_span_id = ""
                for ref in span["references"]:
                    if ref["refType"] == "CHILD_OF":
                        start_parent_span_id = ref["spanID"]
                if start_parent_span_id == "":
                    # if this is the case, span will have ROOT as its parent
                    # CHILD_OF references should be the first reference of the span
                    span["references"].insert(0, {"refType": "CHILD_OF",
                                                "traceID": self.trace_id,
                                                  "spanID": self.root_id})
        with open(out_file, "w") as f:
            json.dump(traces_json, f, indent=4)



    ###################END PARENT HEURISTIC FUNCTIONS###################
    # backtrack through parent candidate's start parents
    # once we find the connection to the ancestor_to_earliest_join_time dictionary, the candidate is:
    # valid if the connection to the ancestor is after the earliest join time
    # invalid otherwise
    def is_valid_candidate(self, span_end_time, parent_candidate, ancestor_to_earliest_join_time):
        current_span = parent_candidate
        # if the parent candidate itself is in the ancestor_to_earliest_join_time dictionary, should be fine as long as we are trying to join after its earliest join time
        if current_span in ancestor_to_earliest_join_time:
            if span_end_time <= ancestor_to_earliest_join_time[current_span]:
                return False, None
            else:
                return True, current_span

        # if parent candidate is not in the ancestor_to_earliest_join_time dictionary, backtrack through the candidate's start parent ancestors to find the connecting point to that hierarchy
        current_parent = self.spans[current_span].start_parent
        while current_parent not in ancestor_to_earliest_join_time and current_parent != "":
            current_span = current_parent
            current_parent = self.spans[current_parent].start_parent

        if current_parent == "":
            return False, None
            
        # at this point, the current parent should be in the ancestor_to_earliest_join_time dictionary, so check if child starts late enough for candidate to be valid
        if self.spans[current_span].start_time <= ancestor_to_earliest_join_time[current_parent]:
            return False, None
        else:
            return True, current_parent

       
    def find_end_parent(self, curr_span_id, spans_needing_end_parents, found_end_parents, threshold):
        curr_span_obj = self.spans[curr_span_id]
        
        # backtrack through ancestors to populate span to earliest join time dictionary
        ancestor_to_earliest_join_time = {}

        # first, set up start parent of the span of interest
        # since this is the launcher, it could try to join the child any time after the child is started
        if curr_span_obj.start_parent in spans_needing_end_parents and curr_span_obj.start_parent not in found_end_parents:
            self.find_end_parent(curr_span_obj.start_parent, spans_needing_end_parents, found_end_parents, threshold)
        curr_parent = curr_span_obj.start_parent
        ancestor_to_earliest_join_time[curr_parent] = curr_span_obj.start_time
            
        # now, work back through end parent ancestors
        # if one of these also needs an end parent, we recurse
        while self.spans[curr_parent].end_parent != "" and self.spans[curr_parent].end_parent != self.invalid_span_id:
            ancestor_to_earliest_join_time[self.spans[curr_parent].end_parent] = self.spans[curr_parent].end_time
            
            if self.spans[curr_parent].end_parent in spans_needing_end_parents and self.spans[curr_parent].end_parent not in found_end_parents:
                self.find_end_parent(self.spans[curr_parent].end_parent, spans_needing_end_parents, found_end_parents, threshold)
            
            curr_parent = self.spans[curr_parent].end_parent
            
        # find candidates-- these are all spans that overlap the end time of the span of interest
        # we also exclude the ancestors of these candidates to find the lowest span in the hierarchy
        # we also start by excluding all descendants of the span of interest
        excluded_candidates = set()
        descendants = set()
        for child_span in curr_span_obj.children:
            descendants.add(child_span)
        while len(descendants) > 0:
            curr_descendant = descendants.pop()
            excluded_candidates.add(curr_descendant)
            # should be able to assume that all spans are in the trace at this point, since we already handled dropped spans
            for curr_descendant_child in self.spans[curr_descendant].children:
                descendants.add(curr_descendant_child)

        parent_candidates = set()
        for span_id, span_object in self.spans.items():
            # already excluded descendants, so skip those here so we don't exclude valid spans unnecessarily
            # also don't consider self
            if span_id not in excluded_candidates and span_id != curr_span_id: 
                if span_object.start_time <= curr_span_obj.end_time and curr_span_obj.end_time <= span_object.end_time:
                    parent_candidates.add(span_id)
                    
                    # exclude each candidate's ancestors-- that is, find the "lowest" candidate span within the hierarchy
                    ancestors = set()
                    ancestors.add(span_object.start_parent)
                    ancestors.add(span_object.end_parent)

                    while len(ancestors) > 0:
                        #get an ancestor, order shouldn't matter
                        curr_ancestor = ancestors.pop()
                        #root span has empty string as its parent
                        if curr_ancestor == "":
                            continue
                        #when we mix assigning valid and invalid end parents, makes it possible for invalid spans ids to show up here
                        elif curr_ancestor == self.invalid_span_id:
                            continue
                        excluded_candidates.add(curr_ancestor)
                        ancestors.add(self.spans[curr_ancestor].start_parent)
                        ancestors.add(self.spans[curr_ancestor].end_parent)

        parent_to_edges = {}
        # once we have our candidates, eliminate the ones that do not make sense
        # that is, eliminate any that connect to the span of interest's ancestry too early (before the earliest join time at that point in the hierarchy)
        for parent_candidate in parent_candidates:
            if parent_candidate not in excluded_candidates:
                success, ancestor = self.is_valid_candidate(curr_span_obj.end_time, parent_candidate, ancestor_to_earliest_join_time)
                if not success:
                    excluded_candidates.add(parent_candidate)
                else: 
                    # count the number of edges between the common ancestor and the start/end parent
                    # used to determine how "related" the candidate end parent is to the span
                    edges = 0
                    curr_start_parent = curr_span_obj.start_parent
                    while curr_start_parent != ancestor:
                        edges += 1
                        curr_start_parent = self.spans[curr_start_parent].end_parent #use end parents here
                        
                    curr_end_parent = parent_candidate
                    while curr_end_parent != ancestor:
                        edges += 1
                        curr_end_parent = self.spans[curr_end_parent].start_parent #use start parents here
                    parent_to_edges[parent_candidate] = edges
        # if there are still multiple candidates, choose the one with the earliest end time
        # if nothing was found, default to invalid span (NULL) for end parent
        earliest_ending_parent = self.invalid_span_id
        earliest_ending_time = self.spans[self.root_id].end_time
        for parent_candidate in parent_candidates:
            if parent_candidate not in excluded_candidates:
                if self.spans[parent_candidate].end_time <= earliest_ending_time:
                    earliest_ending_parent = parent_candidate
                    earliest_ending_time = self.spans[earliest_ending_parent].end_time

        if earliest_ending_parent == self.invalid_span_id:
            self.spans[curr_span_id].end_parent = self.invalid_span_id
        # finally, modify the span of interest's end parent
        elif parent_to_edges[earliest_ending_parent] > threshold:
            self.spans[curr_span_id].end_parent = self.invalid_span_id
        else:
            self.spans[curr_span_id].end_parent = earliest_ending_parent
            self.spans[earliest_ending_parent].children.add(curr_span_id)
        found_end_parents.add(curr_span_id)

        
    # applies all adjustments to trace: synthesizing dropped spans, adjusting for clock skew, inferring end parents, and filling in gaps
    def finalize(self, is_finding_end_parents = False, end_parent_threshold = float('inf')):
        root_span = self.spans[self.root_id]
        self.T = root_span.end_time - root_span.start_time
        # add children to trace while looking for dropped spans
        all_calls={}
        dropped_spans = {}
        for span_id, span_object in self.spans.items():
            if span_id == self.root_id:
                continue
            start_parent = span_object.start_parent
            end_parent = span_object.end_parent
            parents = [(start_parent,span_object.start_time),
                       (end_parent,span_object.end_time)]
            for parent, object_time in parents:
                if parent == self.invalid_span_id:
                    continue
                if parent not in self.spans:
                    # don't modify the dictionary here since we are iterating through it currently
                    if parent not in dropped_spans:
                        # give the synthesized dropped span the same host identification as the first child we identified
                        # goal is to shift this synthesized parent span along with the child (if there are clock skew effects)
                        dropped_spans[parent] = Span(span_object.process_id, span_object.service_name, "dropped", object_time, object_time, parent, parent, {}, {}, dropped=True, host_id=span_object.host_id, host_ips=span_object.host_ips, host_name=span_object.host_name)                
                        self.dropped_span_count += 1

                    else:
                        # extend to include the relevant time (if necessary)
                        dropped_spans[parent].start_time = min(dropped_spans[parent].start_time, object_time)
                        dropped_spans[parent].end_time = max(dropped_spans[parent].end_time, object_time)

                    # add span to children of the dropped span
                    dropped_spans[parent].children.add(span_id)
                    
                else:
                    # add span to children
                    self.spans[parent].children.add(span_id)

        # find parent for dropped spans
        # iterate from latest end time to earliest to add dropped spans higher in the hierarchy earlier, to handle cases where multiple spans are dropped in the same ancestry line
        for dropped_span_id, dropped_span_object in sorted(dropped_spans.items(), key=lambda item: item[1].end_time, reverse=True):
            excluded_candidates = set()
            # exclude all descendants of the dropped span from being parent candidates
            descendants = set()
            for child_span in dropped_span_object.children:
                descendants.add(child_span)
            while len(descendants) > 0:
                curr_descendant = descendants.pop()
                excluded_candidates.add(curr_descendant)
                if curr_descendant in self.spans:
                    for curr_descendant_child in self.spans[curr_descendant].children:
                        descendants.add(curr_descendant_child)
                elif curr_descendant in dropped_spans:
                    for curr_descendant_child in dropped_spans[curr_descendant].children:
                        descendants.add(curr_descendant_child)
                else:
                    print("Could not find span object for span id: {}".format(curr_descendant))
            
            parent_candidates = set()
            for span_id, span_object in self.spans.items():
                if span_id not in excluded_candidates:
                    # look for spans that fully cover the dropped span's duration
                    if span_object.start_time <= dropped_span_object.start_time and dropped_span_object.end_time <= span_object.end_time:
                        parent_candidates.add(span_id)
                        # exclude each candidate's ancestors-- that is, find the "lowest" candidate span within the hiearchy
                        ancestors = set()
                        ancestors.add(span_object.start_parent)
                        if span_object.end_parent != self.invalid_span_id:
                            ancestors.add(span_object.end_parent)

                        while len(ancestors) > 0:
                            # get an ancestor, order shouldn't matter
                            curr_ancestor = ancestors.pop()
                            # root span has empty string as its parent
                            if curr_ancestor == "":
                                continue
                            if curr_ancestor in self.spans:
                                excluded_candidates.add(curr_ancestor)
                                ancestors.add(self.spans[curr_ancestor].start_parent)
                                if self.spans[curr_ancestor].end_parent != self.invalid_span_id:
                                    ancestors.add(self.spans[curr_ancestor].end_parent)
                            else:
                                # exclude spans that do not have an ancestry line to the root
                                # this could cause issues with gaps in the ancestry line, which is why we add dropped spans from largest end_time to smallest to fill the gaps first
                                excluded_candidates.add(span_id)
                                break

            shortest_parent = self.root_id
            shortest_duration = self.spans[self.root_id].get_span_duration()
            for parent_candidate in parent_candidates:
                if parent_candidate not in excluded_candidates:
                    # at least 1, could be multiple. to break ties, we choose the shortest duration to find the closest fit
                    if self.spans[parent_candidate].get_span_duration() <= shortest_duration:
                        shortest_duration = self.spans[parent_candidate].get_span_duration()
                        shortest_parent = parent_candidate
            dropped_span_object.start_parent = shortest_parent
            dropped_span_object.end_parent = shortest_parent
            self.spans[dropped_span_id] = dropped_span_object
            self.spans[shortest_parent].children.add(dropped_span_id)

        # adjust span times to handle clock skew
        all_adjustments = {}
        self.apply_clock_skew_adjustments(self.root_id, ClockSkew(0,""))
        
        # if any spans are longer than their parent, give them a second parent
        # this is included to infer the possible second parent in traces where this is not explicitly recorded.
        spans_needing_end_parent = set()
        
        if is_finding_end_parents:
            for span_id, span_obj in self.spans.items():
                if span_obj.start_parent == span_obj.end_parent:
                    if span_obj.start_parent != "": #don't consider root span
                        if span_obj.end_time > self.spans[span_obj.start_parent].end_time:
                            if end_parent_threshold > 0:
                                spans_needing_end_parent.add(span_id)
                            else: #set end parent to NULL
                                span_obj.end_parent = self.invalid_span_id
        
        found_end_parents = set()
        
        # iterate from latest end time to earliest to avoid cases of different end parents being chosen if spans have the same end times
        for span_needing_end_parent in sorted(list(spans_needing_end_parent), key=lambda span_id: self.spans[span_id].end_time, reverse=True):
            if span_needing_end_parent in found_end_parents:
                continue #already found end parent when recursing for another span
            else:
                self.find_end_parent(span_needing_end_parent, spans_needing_end_parent, found_end_parents, end_parent_threshold)


        # fill gaps between (likely async) spans and their parents. Also, ensure that span starts/ends are entirely covered by their start/end parents.
        self.fill_gaps_and_extend_parents()

# Span class tracks important information about spans from Jaeger traces.
class Span(GenericReprStrBase):
    def __init__(self, process_id, service_name, operation_name, start_time, end_time, start_parent_span_id, end_parent_span_id, attrs, events, dropped, host_id = None, host_ips = [], host_name = None):
        self.process_id = process_id        
        self.service_name = service_name #string indetifiier
        self.operation_name = operation_name #string identifier
        self.start_time = start_time # in unix time in nano secs
        self.end_time = end_time # same as above
        self.start_parent = start_parent_span_id
        self.end_parent = end_parent_span_id
        self.attrs = attrs
        self.events = events
        self.children = set()
        self.crit_path_time = 0 # in nano secs
        self.dropped = dropped
        self.crit_path_interval = []
        self.host_id = host_id
        self.host_ips = host_ips
        self.host_name = host_name
        self.clock_skew_adjustment = None

    def get_span_duration(self):
        return self.end_time - self.start_time


# sets up our representation of a Trace and Spans based on the input Jaeger JSON trace file
class JaegerFile():
    def __init__(self, input_file):
        self.cache = {}
        self.input_file = input_file

    def get_trace(self, is_finding_end_parents=False, end_parent_threshold=float('inf')):
        with open(self.input_file, 'r') as input_file:
            traces_json = json.load(input_file)
        trace_id_str = traces_json["data"][0]["traceID"]
        self.process_traces_json(traces_json)

        trace_obj = self.cache[trace_id_str]

        # add root span after adding all other spans in trace
        trace_obj.add_root_span()

        # finalize synthesizes dropped spans, applied clock skew adjustments, finds end parents, and fills in gaps 
        trace_obj.finalize(is_finding_end_parents=is_finding_end_parents, end_parent_threshold=end_parent_threshold)
        return trace_obj        

    def process_traces_json(self, traces_json):
        for main_data in traces_json["data"]:
            for span in main_data["spans"]:
                # each span is a {} object
                assert(span != ""), "weird! trace_id = ''"
                if span["traceID"] not in self.cache:
                    self.cache[span["traceID"]] = Trace(span["traceID"])

                trace_obj = self.cache[span["traceID"]]
                span_id = span["spanID"]
                trace_obj.spans_in_JSON.append(span_id)
                # get attributes
                attrs = {}
                if "tags" in span:
                    for tag in span["tags"]:
                        attrs[tag["key"]] = tag["value"]
                
                # all spans have start and end parents (except for ROOT span)
                start_parent_span_id = ""
                for ref in span["references"]:
                    if ref["refType"] == "CHILD_OF":
                        start_parent_span_id = ref["spanID"]
                if start_parent_span_id == "":
                    trace_obj.children_of_root.append(span_id)

                # if indicated within span's attributes, track end parent
                if "end_parent" in attrs:
                    end_parent_span_id = attrs["end_parent"]
                else:
                    end_parent_span_id = start_parent_span_id

                # get events
                events = []
                if "logs" in span:
                    for log in span["logs"]:
                        curr_item = {}
                        curr_item["time"] = log["timestamp"]
                        for field in log["fields"]:
                            if field["key"] == "event":
                                curr_item["name"] = field["value"]
                            else:
                                if "attrs" not in curr_item:
                                    curr_item["attrs"] = {}
                                curr_item["attrs"][field["key"]] = field["value"]

                        events.append(curr_item)

                span_process_id = span["processID"]
                # lookup the service name in the processes section
                span_service_name = main_data["processes"][span_process_id]["serviceName"]

                # also check if any of the host.id, host.ip, or host.name are set
                # following the OpenTelemetry Semantic Convention for Host: https://opentelemetry.io/docs/specs/semconv/registry/attributes/host/
                host_id = None
                host_ips = []
                host_name = None
                if "tags" in main_data["processes"][span_process_id]:
                    for tag in main_data["processes"][span_process_id]["tags"]:
                        if tag["key"] == "host.id":
                            host_id = tag["value"]
                        elif tag["key"] == "host.ip":
                            host_ips = literal_eval(tag["value"])
                        elif tag["key"] == "host.name":
                            host_name = tag["value"]
                
                span_start_time = span["startTime"]
                span_end_time = span_start_time + span["duration"]
                
                new_span = Span(span_process_id, span_service_name, span["operationName"], span_start_time, span_end_time, start_parent_span_id, end_parent_span_id, attrs, events, False, host_id, host_ips, host_name)
                trace_obj.add_span(span_id, new_span)

def main():
    # get arguments, indicating input and output JSON files and enabling/disabling inferring finding end parents.
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=str, required=True)
    parser.add_argument("--output", type=str, required=True)
    parser.add_argument("--find_end_parents", action="store_true")
    parser.add_argument("--end_parent_threshold", type=int, default=None)
    
    args = parser.parse_args()

    # parse the JSON into our Trace/Span representations
    jaeger_file = JaegerFile(args.input)

    if args.end_parent_threshold is None:
        end_parent_threshold = float('inf')
    else:
        end_parent_threshold = args.end_parent_threshold

    trace_obj = jaeger_file.get_trace(is_finding_end_parents=args.find_end_parents, end_parent_threshold=end_parent_threshold)

    # calculate the critical path of the trace
    trace_obj.calculate_critical_path_time(trace_obj.root_id)

    # adjust span timestamps, add end parents (if applicable), and add critical path stats to the JSON
    trace_obj.get_new_JSON(args.input, args.output)

if __name__ == '__main__':    
    main()
