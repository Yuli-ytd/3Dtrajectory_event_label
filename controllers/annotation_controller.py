import json
import os
import bisect
from typing import List, Dict, Optional

class AnnotationController:
    def __init__(self, output_dir: str, start_timestamp: float):
        self.segments: List[Dict] = []
        self.current: Optional[Dict] = None
        self.by_frame: Dict[int, List[Dict]] = {}
        self.output_path = os.path.join(output_dir, "3D_event_labeled.json")
        self.start_timestamp = start_timestamp
        self.phase_map = {"serve": "rally", "hit": "rally", "touch": "rally", "dead": "rally",
                          "rest-hit": "rest", "rest-dead": "rest"}

        # Initialize the annotation 
        self._init_annotations()

    def _init_annotations(self):
        # Load existing annotations if available
        if os.path.exists(self.output_path):
            with open(self.output_path, 'r', encoding='utf-8') as f:
                self.segments = json.load(f)
            for segment in self.segments:
                for event in segment.get("description", []):
                    fid = event["fid"]
                    self.by_frame.setdefault(fid, []).append(event)
            print(f"Segments: {self.segments}")
            self.current =  None
        
        else:
            # Start the first segment as phase "rest", the time range starts at the first frame's timestamp
            self.current = {
                "phase": "rest",
                "time_range": [self.start_timestamp, None],
                "description": []
            }
    
    def _sync_segment(self, segment: Dict):
        seg = segment["description"]
        seg.sort(key=lambda e: e["fid"])
        ts = [e["timestamp"] for e in seg]
        if segment["phase"] == "rally":
            segment["time_range"] = [min(ts), max(ts)]

    def _sync_adjacent_rest(self, rally_idx: int):
        
        segments = self.segments
        ts_start, ts_end = segments[rally_idx]["time_range"]

        # adjust previous rest segment's time range
        if rally_idx - 1 >= 0 and segments[rally_idx - 1]["phase"] == "rest":
            segments[rally_idx - 1]["time_range"][1] = ts_start
        
        # adjust next rest segment's time range
        if rally_idx + 1 < len(segments) and segments[rally_idx + 1]["phase"] == "rest":
            segments[rally_idx + 1]["time_range"][0] = ts_end


        
    def on_phase_switch(self, new_phase: Optional[str], timestamp: float):
        
        # Close the current segment if it exists
        if self.current:
            self.current["time_range"][1] = timestamp
            self.segments.append(self.current)
            if self.current["phase"] == "rally":
                idx = len(self.segments) - 1
                self._sync_adjacent_rest(idx)
            self.current = None
        
        # if the current segment is none, and the time range of the last segment is not closed, close it
        elif new_phase and self.segments:
            last = self.segments[-1]
            if last["time_range"][1] is None:
                last["time_range"][1] = timestamp

        # Start a new segment if a new phase is provided
        if new_phase:
            self.current = {
                "phase": new_phase,
                "time_range": [timestamp, None],
                "description": []
            }
    
    # Toggle an event for a specific frame
    def toggle_event(self, event_type: str, frame_idx: int, timestamp: float, position: List[float]):
        
        event_list = self.by_frame.setdefault(frame_idx, [])
        print(f"Current events for frame {frame_idx}: {event_list}")
        exists = next((e for e in event_list if e["event_type"] == event_type), None)

        # If the event already exists, remove it
        if exists:
            # remove the existing event
            self.remove_existing_event(event_list, exists, frame_idx, timestamp)
        
        # If the event does not exist, create a new one
        else:
            event = {
                "type": "event",
                "event_type": event_type,
                "fid": frame_idx,
                "timestamp": timestamp,
                "position": position
            }
            event_list.append(event)
            # insert the new event into the specific segment
            self.insert_event(event)
    
    def insert_event(self, new_event: Dict):

        ts = new_event["timestamp"]
        et = new_event["event_type"]
        phase = self.phase_map[et]

        # Check if there's an open segment for the current phase
        if self.current and self.current["phase"] == phase:
            # If there's an open segment for the current phase, add the event to it
            self.current["description"].append(new_event)
            self._sync_segment(self.current)
            print(f"Added event {new_event} to current segment {self.current}")
            if et == "dead":
                self.on_phase_switch("rest", ts)
            return
        
        # If the event type is dead and the current segment is not open, check for existing segments
        if et == "dead":
            cands = [s for s in self.segments if s["phase"] == phase and s["time_range"][1] <= ts]
            if cands:
                # if there are candidates, find the one with the latest time range
                seg = max(cands, key=lambda s: s["time_range"][1])
                seg["description"].append(new_event)
                self._sync_segment(seg)
                idx = self.segments.index(seg)
                self._sync_adjacent_rest(idx)
                print(f"Added dead event {new_event} to existing segment {seg}")
                return
            
        # if the event type is serve and the current segment is not open, check for existing segments
        if et == "serve":
            cands = [s for s in self.segments if s["phase"] == phase and s["time_range"][0] >= ts]
            if cands:
                # if there are candidates, find the one with the earliest time range
                seg = min(cands, key=lambda s: s["time_range"][0])
                seg["description"].append(new_event)
                self._sync_segment(seg)
                idx = self.segments.index(seg)
                self._sync_adjacent_rest(idx)
                print(f"Added serve event {new_event} to existing segment {seg}")
                return
        
        # check if the new event should be added to the previous segment by timestamp
        for seg in self.segments:
            if seg["phase"] == phase and phase == "rest" and \
                ((seg["time_range"][1] is None and seg["time_range"][0] <= ts) or 
                 (seg["time_range"][0] <= ts <= seg["time_range"][1])):
                # If the segment is found, add the event to it
                seg["description"].append(new_event)
                self._sync_segment(seg)
                print(f"Added event {new_event} to existing segment {seg}")
                return
            if seg["phase"] == phase and phase == "rally" and \
                ((seg["time_range"][0] <= ts <= seg["time_range"][1]) or
                 (seg["description"][-1]["event_type"] != "dead")):
                # If the segment is found, add the event to it
                seg["description"].append(new_event)
                self._sync_segment(seg)
                idx = self.segments.index(seg)
                self._sync_adjacent_rest(idx)
                print(f"Added event {new_event} to existing segment {seg}")
                return
                        
        # If no existing segment is found, create a new segment
        self.on_phase_switch(phase, new_event["timestamp"])
        self.current["description"].append(new_event)
        print(f"Created new segment for phase {phase} and added event {new_event}")

    def remove_existing_event(self, event_list: List[Dict], exists: Dict, frame_idx: int, timestamp: float):
        
        # remove the existing event from the by_frame dictionary
        event_list.remove(exists)
        if not event_list:
            del self.by_frame[frame_idx]
            print(f"Removed all events for frame {frame_idx}, deleting from by_frame")

        # find the segment that contains this event
        for segment in self.segments:
            if exists in segment.get("description", []):
                segment["description"].remove(exists)
                print(f"Removed event {exists} from segment {segment}")
                # fix the time range of the segment if necessary
                if len(segment["description"]) > 0 and segment["phase"] != "rest":
                    if segment["time_range"][0] == timestamp:
                        # If the removed event was the first event in the segment, update the 
                        segment["time_range"][0] = segment["description"][0]["timestamp"]
                            
                    if segment["time_range"][1] == timestamp:
                        # If the removed event was the last event in the segment, update the 
                        segment["time_range"][1] = segment["description"][-1]["timestamp"]
                    
                    idx = self.segments.index(segment)
                    self._sync_adjacent_rest(idx)

                elif len(segment["description"]) == 0:
                    # If the segment is now empty, remove it
                    self.segments.remove(segment)
                    print(f"Removed empty segment {segment}")
                break
        
        # If the current segment contains this event, remove it
        if self.current and exists in self.current.get("description", []):
            self.current["description"].remove(exists)
            print(f"Removed event {exists} from current segment {self.current}")
    
    def get_events_for_frame(self, frame_idx: int) -> List[Dict]:
        
        sorted_idxs = sorted(self.by_frame.keys())

        # Use bisect to find the position of the frame index
        if not sorted_idxs:
            return -1
        pos = bisect.bisect_right(sorted_idxs, frame_idx)

        if pos == 0:
            return -1
        nearest_idx = sorted_idxs[pos - 1]
        # print(f"Nearest index for frame {frame_idx} is {nearest_idx}")

        return nearest_idx
    
    def save_annotations(self, end_timestamp: float):
        print("Saving annotations...")
        print(f"Current segments: {self.current}")
        if self.current and (self.current["description"] or self.current["phase"] == "rest"):
            try:
                # If there's an open segment, close it
                self.current["time_range"][1] = self.current["description"][-1]["timestamp"]
            # If the current segment is rest and has no events, set the end timestamp
            except IndexError:
                self.current["time_range"][1] = end_timestamp
            self.segments.append(self.current)
            self.current = None

        elif self.segments and self.segments[-1]["time_range"][1] is None:
            # If the last segment is open, close it with the end timestamp
            self.segments[-1]["time_range"][1] = end_timestamp
        
        elif self.segments and self.segments[-1]["phase"] == "rally":
            new_segment = {
                "phase": "rest",
                "time_range": [self.segments[-1]["time_range"][1], end_timestamp],
                "description": []
            }
            self.segments.append(new_segment)

        with open(self.output_path, 'w', encoding='utf-8') as f:
            json.dump(self.segments, f, ensure_ascii=False, indent=2)