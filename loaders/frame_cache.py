import queue
import threading
from collections import OrderedDict, deque

import cv2


class FrameNotReady(IndexError):
    """Raised immediately when a frame is still decoding in the background."""


class FrameCache:
    """Frame-accurate, non-blocking cache backed by one decoder thread."""

    def __init__(self, video_path: str, initial_cache_seconds: int = 3,
                 cache_window_seconds: int = 6, max_cache_size: int = 1000):
        self.video_path = video_path
        self.cap = cv2.VideoCapture(video_path)
        if not self.cap.isOpened():
            raise RuntimeError(f"無法開啟影片檔案：{video_path}")

        self.fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.initial_cache_frames = int(initial_cache_seconds * self.fps)
        self.cache_window_frames = max(30, int(cache_window_seconds * self.fps))
        self.max_cache_size = max_cache_size

        # UI cache lookups and decoder ownership use different locks. The UI
        # therefore never waits for a long VideoCapture read batch.
        self.cache_lock = threading.RLock()
        self.decoder_lock = threading.Lock()
        self.lock = self.cache_lock  # compatibility with existing callers

        self.cache = OrderedDict()  # frame_idx -> ndarray; None = bad frame
        self.current_frame = 0
        self.decode_position = 0
        self.frame_queue = deque(maxlen=100)

        self.stopped = False
        self.prefetch_thread = None
        self.prefetch_queue = queue.PriorityQueue(maxsize=50)
        self._request_lock = threading.Lock()
        self._request_generation = 0
        self._request_sequence = 0
        self._pending_frames = set()
        self.loading_frames = set()

        # Initial loading finishes before the worker starts, preserving a single
        # owner for the OpenCV/FFmpeg decoder.
        self._load_initial_cache()
        self.start_prefetch_thread()

    def start_prefetch_thread(self):
        self.prefetch_thread = threading.Thread(
            target=self._prefetch_worker,
            name=f"FrameCache:{self.video_path}",
            daemon=True,
        )
        self.prefetch_thread.start()

    def request_frame(self, frame_idx: int, urgent: bool = False) -> bool:
        """Queue a decode request without waiting.

        True means that the index (including a known-bad black frame) is already
        cached. An urgent request cancels work from an older slider position.
        """
        if frame_idx < 0 or frame_idx >= self.total_frames or self.stopped:
            return False

        with self.cache_lock:
            self.current_frame = frame_idx
            cached = frame_idx in self.cache
            if cached:
                self.cache.move_to_end(frame_idx)
            if cached and not urgent:
                return True

        with self._request_lock:
            if urgent:
                self._request_generation += 1
                self._drain_request_queue_locked()
                self._pending_frames.clear()

            generation = self._request_generation
            pending_key = (generation, frame_idx)
            if pending_key in self._pending_frames:
                return False

            self._request_sequence += 1
            item = (0 if urgent else 10, self._request_sequence,
                    generation, frame_idx)
            try:
                self.prefetch_queue.put_nowait(item)
                self._pending_frames.add(pending_key)
            except queue.Full:
                # Never fall back to synchronous decoding on the UI thread.
                if urgent:
                    self._drain_request_queue_locked()
                    self._pending_frames.clear()
                    self.prefetch_queue.put_nowait(item)
                    self._pending_frames.add(pending_key)
        return cached

    def _drain_request_queue_locked(self):
        try:
            while True:
                self.prefetch_queue.get_nowait()
        except queue.Empty:
            pass

    def _prefetch_worker(self):
        while not self.stopped:
            target_frame = None
            generation = None
            try:
                _, _, generation, target_frame = self.prefetch_queue.get(
                    timeout=0.1
                )
                if target_frame is None:
                    break

                with self._request_lock:
                    if generation != self._request_generation:
                        continue

                with self.cache_lock:
                    self.loading_frames.add(target_frame)
                self._load_target_and_prefetch(target_frame, generation)
            except queue.Empty:
                continue
            except Exception as exc:
                print(f"預取執行緒錯誤：{exc}")
            finally:
                if target_frame is not None:
                    with self._request_lock:
                        self._pending_frames.discard((generation, target_frame))
                    with self.cache_lock:
                        self.loading_frames.discard(target_frame)

    def _request_is_current(self, generation: int) -> bool:
        with self._request_lock:
            return (not self.stopped and
                    generation == self._request_generation)

    def _load_target_and_prefetch(self, target_frame: int,
                                  generation: int):
        # Exact positioning: backward jumps reopen at frame zero and all movement
        # to the requested index is sequential. CAP_PROP_POS_FRAMES is not used.
        if not self._decode_through(target_frame, generation):
            return

        prefetch_end = min(
            self.total_frames - 1,
            target_frame + self.cache_window_frames // 2,
        )
        chunk_size = 10
        chunk_end = target_frame + chunk_size
        while chunk_end <= prefetch_end and self._request_is_current(generation):
            if not self._decode_through(min(chunk_end, prefetch_end), generation):
                break
            chunk_end += chunk_size

        with self.cache_lock:
            self._cleanup_cache_locked()

    def _decode_through(self, target_frame: int, generation: int) -> bool:
        """Sequentially decode through target_frame with exact frame indexing."""
        if target_frame < 0 or target_frame >= self.total_frames:
            return False

        with self.decoder_lock:
            if target_frame < self.decode_position:
                if not self._restart_decoder():
                    self._store_frame(target_frame, None)
                    return False

            while self.decode_position <= target_frame and not self.stopped:
                if not self._request_is_current(generation):
                    return False

                frame_idx = self.decode_position
                ret, frame = self.cap.read()
                decoded = frame.copy() if ret and frame is not None else None
                self.decode_position += 1

                with self.cache_lock:
                    if self._should_cache(frame_idx, target_frame):
                        self.cache[frame_idx] = decoded
                        self.cache.move_to_end(frame_idx)
                    if decoded is not None:
                        self.frame_queue.append((frame_idx, decoded))

            return self.is_cached(target_frame)

    def _restart_decoder(self) -> bool:
        new_cap = cv2.VideoCapture(self.video_path)
        if not new_cap.isOpened():
            return False
        self.cap.release()
        self.cap = new_cap
        self.decode_position = 0
        self.frame_queue.clear()
        return True

    def _should_cache(self, frame_idx: int, target_frame: int) -> bool:
        half_window = self.cache_window_frames // 2
        return (abs(frame_idx - target_frame) <= half_window or
                abs(frame_idx - self.current_frame) <= half_window)

    def _store_frame(self, frame_idx: int, frame):
        with self.cache_lock:
            self.cache[frame_idx] = frame
            self.cache.move_to_end(frame_idx)

    def _load_initial_cache(self):
        print("開始載入初始快取...")
        frames_to_load = min(self.initial_cache_frames, self.total_frames)
        with self.decoder_lock:
            for frame_idx in range(frames_to_load):
                ret, frame = self.cap.read()
                self.cache[frame_idx] = (
                    frame.copy() if ret and frame is not None else None
                )
                self.decode_position = frame_idx + 1
        print(f"初始快取載入完成：{len(self.cache)} 幀")

    def __getitem__(self, frame_idx: int):
        """Return immediately and queue a missing frame for background work."""
        if frame_idx < 0 or frame_idx >= self.total_frames:
            raise IndexError(
                f"Frame {frame_idx} is outside [0, {self.total_frames})"
            )

        with self.cache_lock:
            self.current_frame = frame_idx
            if frame_idx in self.cache:
                self.cache.move_to_end(frame_idx)
                return self.cache[frame_idx]

        self.request_frame(frame_idx)
        raise FrameNotReady(f"Frame {frame_idx} is loading")

    def is_cached(self, frame_idx: int) -> bool:
        with self.cache_lock:
            return frame_idx in self.cache

    def prefetch_around_current(self):
        with self.cache_lock:
            next_frame = min(self.current_frame + 1, self.total_frames - 1)
        self.request_frame(next_frame)

    def _cleanup_cache_locked(self):
        if len(self.cache) <= self.max_cache_size:
            return

        half_window = self.cache_window_frames // 2
        keep_start = max(0, self.current_frame - half_window - 50)
        keep_end = min(
            self.total_frames,
            self.current_frame + half_window + 50,
        )
        removable = [
            idx for idx in self.cache
            if idx < keep_start or idx >= keep_end
        ]
        for idx in removable[:100]:
            del self.cache[idx]
        if removable:
            print(f"清理了 {min(100, len(removable))} 個過期幀")

    def get_cache_info(self):
        with self.cache_lock:
            return {
                "cache_size": len(self.cache),
                "current_frame": self.current_frame,
                "total_frames": self.total_frames,
                "decode_position": self.decode_position,
                "queue_size": len(self.frame_queue),
                "loading_frames": len(self.loading_frames),
                "prefetch_queue_size": self.prefetch_queue.qsize(),
            }

    def optimize_cache(self):
        with self.cache_lock:
            self._cleanup_cache_locked()

    def stop(self):
        if self.stopped:
            return
        self.stopped = True
        with self._request_lock:
            self._request_generation += 1
            self._drain_request_queue_locked()
            self._request_sequence += 1
            try:
                self.prefetch_queue.put_nowait(
                    (100, self._request_sequence,
                     self._request_generation, None)
                )
            except queue.Full:
                pass

        if self.prefetch_thread:
            self.prefetch_thread.join(timeout=2.0)
        with self.decoder_lock:
            self.cap.release()

    def __del__(self):
        try:
            self.stop()
        except Exception:
            pass

    def clear(self):
        with self.cache_lock:
            self.cache.clear()
            self.frame_queue.clear()
