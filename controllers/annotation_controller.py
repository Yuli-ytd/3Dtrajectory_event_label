import json
import os
from typing import List, Dict, Optional

class AnnotationController:
    def __init__(self, output_dir: str):
        self.segments: List[Dict] = []
        self.current: Optional[Dict] = None
        self.by_frame: Dict[int, List[Dict]] = {}
        self.output_path = os.path.join(output_dir, "event_labeled.json")

        # load existing annotations if available
        self._load_existing_annotations()

    def _load_existing_annotations(self):
        if os.path.exists(self.output_path):
            with open(self.output_path, 'r', encoding='utf-8') as f:
                self.segments = json.load(f)
            for segment in self.segments:
                for event in segment.get("description", []):
                    fid = event["fid"]
                    self.by_frame.setdefault(fid, []).append(event)

    def on_phase_switch(self, new_phase: Optional[str], frame_idx: int, timestamp: float):
        # Close the current segment if it exists
        if self.current:
            self.current["time_range"][1] = timestamp
            self.segments.append(self.current)
            self.current = None

        # Start a new segment if a new phase is provided
        if new_phase:
            self.current = {
                "phase": new_phase,
                "time_range": [timestamp, None],
                "description": []
            }

    def toggle_event(self, event_type: str, frame_idx: int, timestamp: float, position: List[float]):
        
        event_list = self.by_frame.setdefault(frame_idx, [])

        exists = next((e for e in self.by_frame[frame_idx] 
                       if e["event_type"] == event_type), None)

        if exists:
            event_list.remove(exists)
            if self.current:
                self.current["description"].remove(exists)
        
        else:
            event = {
                "type": "event",
                "event_type": event_type,
                "fid": frame_idx,
                "timestamp": timestamp,
                "position": position
            }
            event_list.append(event)
            if self.current:
                self.current["description"].append(event)

    def get_events_for_frame(self, frame_idx: int) -> List[Dict]:
        return self.by_frame.get(frame_idx, [])
    
    def save_annotations(self):
        
        if self.current:
            # If there's an open segment, close it
            self.current["time_range"][1] = self.current["time_range"][0]
            self.segments.append(self.current)
            self.current = None

        with open(self.output_path, 'w', encoding='utf-8') as f:
            json.dump(self.segments, f, ensure_ascii=False, indent=2)