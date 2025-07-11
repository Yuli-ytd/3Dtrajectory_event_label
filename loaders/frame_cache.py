import cv2
from collections import OrderedDict
import time
import threading

# class FrameCache:
#     """
#     LRU Cache for video frames. On cache miss, reads frame from disk.
#     """
#     def __init__(self, video_path: str, capacity: int = 360, jump_size: int = 30):
#         self.video_path = video_path
#         self.cap = cv2.VideoCapture(video_path)
#         self.capacity = capacity
#         self.cache = OrderedDict()  # idx -> frame (ndarray)
#         self.lock = threading.Lock()
#         self.decode_ptr = -1
#         self.stopped = False
#         self.last_idx = -1 # Last accessed frame index

#         # assert capacity >= jump_size
#         self.prefetch_threshold = capacity // 2

#         for _ in range(capacity):
#             ret, frame = self.cap.read()
#             if not ret:
#                 break
#             idx = int(self.cap.get(cv2.CAP_PROP_POS_FRAMES)) - 1
#             self.cache[idx] = frame
#             self.decode_ptr = idx

#         self.worker = threading.Thread(target=self._decode_loop, daemon=True)
#         self.worker.start()

#     # def _random_access(self, idx: int):
        
#     #     start_hint = max(0, idx - self.capacity)
#     #     self.cap.set(cv2.CAP_PROP_POS_FRAMES, start_hint)

#     #     self._fill_cache_from_current_pos(idx)
#     #     self.last_idx = idx

#     #     return self.cache[idx]

#     def _decode_loop(self):
#         """
#         持續從頭開始解碼，直到 capacity 幀滿，之後若發生目前的idx位於超過cache一半的位置，則啟動sliding window:
#         pop 最舊、推入最新。
#         """

#         while not self.stopped:
#             with self.lock:
#                 pos = list(self.cache.keys()).index(self.last_idx) if self.last_idx in self.cache else -1
#                 # 如果還沒 decode 到 capacity，就繼續填
#                 if len(self.cache) < self.capacity:
#                     do_pop = False
#                 else:
#                     # cache 已滿，只有當使用者讀到中點之後才 pop
#                     do_pop = (self.last_idx > self.prefetch_threshold)

#             # 如果要停一下，避免空轉
#             if len(self.cache) >= self.capacity and not do_pop:
#                 time.sleep(0.01)
#                 continue

#             # 真正 decode 一張
#             ret, frame = self.cap.read()
#             if not ret:
#                 break
#             idx = int(self.cap.get(cv2.CAP_PROP_POS_FRAMES)) - 1

#             with self.lock:
#                 # pop 最舊
#                 if do_pop and pos >= self.prefetch_threshold:
#                     self.cache.popitem(last=False)
#                 # push 新幀
#                 self.cache[idx] = frame
#                 self.decode_ptr = idx

#         self.stopped = True
    
#     def _fill_cache_from_current_pos(self, stop_idx: int):
#         """ Fill cache with frames from current position to stop_idx.
#         This is used to pre-load frames when accessing a range of frames.
#         """
#         while True:
#             cur_pos = int(self.cap.get(cv2.CAP_PROP_POS_FRAMES)) # Get current frame index
#             if cur_pos > stop_idx:
#                 break

#             ret, frame = self.cap.read()
#             if not ret:
#                 raise IndexError(f"Frame {stop_idx} not in {self.video_path}")

#             self.cache[cur_pos] = frame
#             if len(self.cache) > self.capacity:
#                 self.cache.popitem(last=False)

#     def __getitem__(self, idx: int):
#         """
#         只在 cache 範圍內取資料；超出就 IndexError。
#         """
#         with self.lock:
#             if idx in self.cache:
#                 self.last_idx = idx   # 使用者真的讀到這裡
#                 return self.cache[idx]
#             min_idx = next(iter(self.cache))
#             max_idx = self.decode_ptr
#             raise IndexError(f"Frame {idx} not in cache window [{min_idx}…{max_idx}].")

        # # Return frame from cache or load if missing
        # if idx in self.cache:
        #     # Move to end to mark as recently used
        #     frame = self.cache.pop(idx)
        #     self.cache[idx] = frame
        #     self.last_idx = idx
        #     return frame
        
        # # Cache miss: read frame and insert
        # cur_pos = int(self.cap.get(cv2.CAP_PROP_POS_FRAMES))
        # if idx == cur_pos:
        #     # Sequential access, read next frame
        #     ret, frame = self.cap.read()
        #     if not ret:
        #         raise IndexError(f"Frame {idx} not available in {self.video_path}")
        #     real_idx = int(self.cap.get(cv2.CAP_PROP_POS_FRAMES)) - 1
        #     self.cache[real_idx] = frame
        #     self.last_idx = real_idx
        #     return frame
        
        # else:
        #     return self._random_access(idx)
        
class FrameCache:
    def __init__(self, video_path, capacity=360, jump_size=30):
        self.cap = cv2.VideoCapture(video_path)
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)        # ← 保證從 0 開始
        self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.capacity = capacity
        self.cache = OrderedDict()                      # idx -> frame
        self.lock = threading.Lock()
        self.decode_ptr = -1
        self.last_idx   = -1
        self.stopped    = False
        self.jump_size  = jump_size                     # 預取的幀數
        self.threshold  = capacity // 2                 # 播到一半才開始 pop

        # ---------- Prefill ----------
        for _ in range(capacity):
            ret, frame = self.cap.read()
            if not ret:
                break
            idx = int(self.cap.get(cv2.CAP_PROP_POS_FRAMES)) - 1
            self.cache[idx] = frame
            self.decode_ptr = idx

        # ---------- Background decode ----------
        self.worker = threading.Thread(target=self._decode_loop, daemon=True)
        self.worker.start()

    # ------------ Background thread ------------
    def _decode_loop(self):
        while True:
            ret, frame = self.cap.read()
            if not ret:
                self.stopped = True
                break

            idx = int(self.cap.get(cv2.CAP_PROP_POS_FRAMES)) - 1
            with self.lock:
                # 永不删，缓存一路长到视频末尾
                self.cache[idx] = frame
                self.decode_ptr = idx

    # ------------ Public read ------------
    def __getitem__(self, idx):

        # clamp idx 到文件末尾
        idx = min(idx, self.total_frames - 1)

        # 不用 cap.set()。如果缓存里没有，就等它补齐
        while True:
            with self.lock:
                if idx in self.cache:
                    self.last_idx = idx
                    return self.cache[idx]
                # 如果已经解到尾了，还是没这帧，就抛错
                if self.stopped and self.decode_ptr < idx:
                    mins, maxs = next(iter(self.cache)), self.decode_ptr
                    raise IndexError(f"Frame {idx} not in window [{mins}…{maxs}]")
            # 小歇一下，让后台线程跑跑
            time.sleep(0.005)

    # ------------ Cleanup ------------
    def stop(self):
        self.stopped = True
        self.worker.join()
        self.cap.release()    


    def __del__(self):
        """Release video capture when the cache is deleted."""
        self.cap.release()

    def clear(self):
        """Clear all cached frames."""
        self.cache.clear()