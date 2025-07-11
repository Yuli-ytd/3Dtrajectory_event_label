import cv2
from collections import OrderedDict

class FrameCache:
    """
    LRU Cache for video frames. On cache miss, reads frame from disk.
    """
    def __init__(self, video_path: str, capacity: int = 360):
        self.video_path = video_path
        self.cap = cv2.VideoCapture(video_path)
        self.capacity = capacity
        self.cache = OrderedDict()  # idx -> frame (ndarray)
        self.last_idx = -1 # Last accessed frame index

    def _random_access(self, idx: int):
        
        start_hint = max(0, idx - self.capacity)
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, start_hint)

        self._fill_cache_from_current_pos(idx)
        self.last_idx = idx

        return self.cache[idx]
    
    def _fill_cache_from_current_pos(self, stop_idx: int):
        """ Fill cache with frames from current position to stop_idx.
        This is used to pre-load frames when accessing a range of frames.
        """
        while True:
            cur_pos = int(self.cap.get(cv2.CAP_PROP_POS_FRAMES)) # Get current frame index
            if cur_pos > stop_idx:
                break

            ret, frame = self.cap.read()
            if not ret:
                raise IndexError(f"Frame {stop_idx} not in {self.video_path}")

            self.cache[cur_pos] = frame
            if len(self.cache) > self.capacity:
                self.cache.popitem(last=False)

    def __getitem__(self, idx: int):
        # Return frame from cache or load if missing
        if idx in self.cache:
            # Move to end to mark as recently used
            frame = self.cache.pop(idx)
            self.cache[idx] = frame
            self.last_idx = idx
            return frame
        
        # Cache miss: read frame and insert
        cur_pos = int(self.cap.get(cv2.CAP_PROP_POS_FRAMES))
        if idx == cur_pos:
            # Sequential access, read next frame
            ret, frame = self.cap.read()
            if not ret:
                raise IndexError(f"Frame {idx} not available in {self.video_path}")
            real_idx = int(self.cap.get(cv2.CAP_PROP_POS_FRAMES)) - 1
            self.cache[real_idx] = frame
            self.last_idx = real_idx
            return frame
        
        else:
            return self._random_access(idx)
    
    def __del__(self):
        """Release video capture when the cache is deleted."""
        self.cap.release()

    def clear(self):
        """Clear all cached frames."""
        self.cache.clear()
