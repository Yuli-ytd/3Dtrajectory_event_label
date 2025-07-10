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
        
        start = max(0, idx - self.capacity)
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, start)

        frame = None

        for i in range(start, idx + 1):
            ret, frame = self.cap.read()
            if not ret:
                raise IndexError(f"Frame {idx} not available in {self.video_path}")
            
            self.cache[i] = frame  # Cache the frame

            if len(self.cache) > self.capacity:
                # Evict oldest frame if cache exceeds capacity
                self.cache.popitem(last=False)
            
        self.last_idx = idx
        return frame

    def __getitem__(self, idx: int):
        # Return frame from cache or load if missing
        if idx in self.cache:
            # Move to end to mark as recently used
            frame = self.cache.pop(idx)
            self.cache[idx] = frame
            self.last_idx = idx
            return frame
        
        # Cache miss: read frame and insert
        if idx == self.last_idx + 1:
            # Sequential access, read next frame
            ret, frame = self.cap.read()
            if not ret:
                raise IndexError(f"Frame {idx} not available in {self.video_path}")
            self.last_idx = idx
        else:
            frame = self._random_access(idx)
        
        # Insert into cache
        self.cache[idx] = frame
        # Evict oldest if over capacity
        if len(self.cache) > self.capacity:
            self.cache.popitem(last=False)

        return frame
    
    def __del__(self):
        """Release video capture when the cache is deleted."""
        self.cap.release()

    def clear(self):
        """Clear all cached frames."""
        self.cache.clear()
