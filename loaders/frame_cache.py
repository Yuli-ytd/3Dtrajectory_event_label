import cv2
from collections import OrderedDict, deque
import time
import threading
import queue

class FrameCache:
    """
    精確的影片快取系統
    - 使用順序解碼確保幀級精確度
    - 智能預取策略減少延遲
    - 支援向前向後瀏覽
    - 限制記憶體使用
    """
    def __init__(self, video_path: str, initial_cache_seconds: int = 3, 
                 cache_window_seconds: int = 6, max_cache_size: int = 1000):
        self.video_path = video_path
        self.cap = cv2.VideoCapture(video_path)
        self.fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        
        # 快取參數
        self.initial_cache_frames = int(initial_cache_seconds * self.fps)
        self.cache_window_frames = int(cache_window_seconds * self.fps)
        self.max_cache_size = max_cache_size
        
        # 快取狀態
        self.cache = OrderedDict()  # frame_idx -> frame
        self.current_frame = 0      # 目前播放的幀
        
        # 順序解碼追蹤器
        self.decode_position = 0    # 目前解碼位置
        self.frame_queue = deque()  # 解碼幀佇列
        self.max_queue_size = 100   # 佇列最大大小
        
        # 執行緒控制
        self.lock = threading.Lock()
        self.stopped = False
        
        # 預取執行緒
        self.prefetch_thread = None
        self.prefetch_queue = queue.Queue(maxsize=50)  # 限制佇列大小
        self.start_prefetch_thread()
        
        # 防抖機制
        self.last_request_time = 0
        self.request_debounce_ms = 50  # 50ms 防抖
        
        # 載入狀態追蹤
        self.loading_frames = set()  # 正在載入的幀
        self.loading_lock = threading.Lock()
        
        # 初始化快取
        self._load_initial_cache()
    
    def start_prefetch_thread(self):
        """啟動預取執行緒"""
        self.prefetch_thread = threading.Thread(target=self._prefetch_worker, daemon=True)
        self.prefetch_thread.start()
    
    def _prefetch_worker(self):
        """預取工作執行緒"""
        while not self.stopped:
            try:
                # 從佇列取得預取請求
                target_frame = self.prefetch_queue.get(timeout=0.1)
                if target_frame is None:  # 停止信號
                    break
                
                # 檢查是否已經在快取中
                if target_frame in self.cache:
                    continue
                
                # 檢查是否正在載入
                with self.loading_lock:
                    if target_frame in self.loading_frames:
                        continue
                    self.loading_frames.add(target_frame)
                
                try:
                    # 預取目標幀附近的幀
                    self._prefetch_frames_around(target_frame)
                finally:
                    # 清理載入狀態
                    with self.loading_lock:
                        self.loading_frames.discard(target_frame)
                
            except queue.Empty:
                continue
            except Exception as e:
                print(f"預取執行緒錯誤：{e}")
                # 清理載入狀態
                with self.loading_lock:
                    if 'target_frame' in locals():
                        self.loading_frames.discard(target_frame)
    
    def _prefetch_frames_around(self, center_frame: int, batch_size: int = 30):
        """分批預取中心幀附近的幀，優先載入目標幀，降低單次 I/O 負擔"""
        window_start = max(0, center_frame - self.cache_window_frames // 2)
        window_end = min(self.total_frames, center_frame + self.cache_window_frames // 2)
        frames_to_load = [i for i in range(window_start, window_end) if i not in self.cache]
        if not frames_to_load:
            return
        # 目標幀優先
        if center_frame in frames_to_load:
            frames_to_load.remove(center_frame)
            frames_to_load = [center_frame] + frames_to_load
        # 分批載入
        for i in range(0, len(frames_to_load), batch_size):
            batch = frames_to_load[i:i+batch_size]
            self._sequential_load_frames(batch)
    
    def _sequential_load_frames(self, frame_indices: list):
        """順序載入指定的幀"""
        if not frame_indices:
            return
        
        # 排序幀索引以優化載入
        frame_indices.sort()
        
        # 檢查是否需要重新開始解碼（向後跳轉的情況）
        min_target = min(frame_indices)
        if min_target < self.decode_position:
            # 向後跳轉，需要重新開始
            new_cap = cv2.VideoCapture(self.video_path)
            if not new_cap.isOpened():
                raise RuntimeError("無法重新開啟影片檔案")
            
            # 釋放舊的並替換
            self.cap.release()
            self.cap = new_cap
            self.decode_position = 0
            self.frame_queue.clear()
        
        # 找到最接近目前解碼位置的起始點
        start_idx = min(frame_indices, key=lambda x: abs(x - self.decode_position))
        
        # 從起始點開始順序解碼
        self._seek_to_frame_sequential(start_idx)
        
        frames_loaded = 0
        for target_idx in frame_indices:
            if self.stopped:
                break
            
            # 如果目標幀已經在快取中，跳過
            if target_idx in self.cache:
                continue
            
            # 順序解碼直到達到目標幀
            while self.decode_position <= target_idx and not self.stopped:
                ret, frame = self.cap.read()
                if not ret:
                    break
                
                # 將幀加入快取
                self.cache[self.decode_position] = frame.copy()
                
                # 同時加入佇列（用於預取）
                self.frame_queue.append((self.decode_position, frame.copy()))
                self.decode_position += 1
                
                # 限制佇列大小
                if len(self.frame_queue) > self.max_queue_size:
                    self.frame_queue.popleft()
            
            if target_idx in self.cache:
                frames_loaded += 1
        
        # 清理過期的快取
        self._cleanup_cache()
        
        if frames_loaded > 0:
            print(f"預取載入 {frames_loaded} 幀")
    
    def _seek_to_frame_sequential(self, target_frame: int):
        """順序定位到指定幀（不使用set）"""
        if target_frame < self.decode_position:
            # 需要重新開始
            new_cap = cv2.VideoCapture(self.video_path)
            if not new_cap.isOpened():
                raise RuntimeError("無法重新開啟影片檔案")
            
            # 釋放舊的並替換
            self.cap.release()
            self.cap = new_cap
            self.decode_position = 0
            self.frame_queue.clear()
        
        # 順序解碼到目標幀
        while self.decode_position < target_frame:
            ret, frame = self.cap.read()
            if not ret:
                break
            self.decode_position += 1
    
    def _load_initial_cache(self):
        """載入初始快取（前幾秒的影片）"""
        print("開始載入初始快取...")
        
        # 直接順序載入前幾幀
        frames_to_load = min(self.initial_cache_frames, self.total_frames)
        
        for i in range(frames_to_load):
            ret, frame = self.cap.read()
            if not ret:
                break
            self.cache[i] = frame.copy()
            self.decode_position = i + 1
        
        print(f"初始快取載入完成：{len(self.cache)} 幀")
    
    def _ensure_frame_in_cache(self, frame_idx: int):
        """確保指定幀在快取中"""
        if frame_idx in self.cache:
            return
        
        # 統一使用順序載入邏輯，不再區分前向和後向
        # 將預取請求加入佇列
        try:
            self.prefetch_queue.put_nowait(frame_idx)
        except queue.Full:
            # 佇列滿了，直接載入
            self._sequential_load_frames([frame_idx])
    
    def _ensure_frame_in_cache_nonblocking(self, frame_idx: int):
        """非阻塞地確保指定幀在快取中"""
        if frame_idx in self.cache:
            return
        
        # 將預取請求加入佇列
        try:
            self.prefetch_queue.put_nowait(frame_idx)
        except queue.Full:
            # 佇列滿了，直接載入
            self._sequential_load_frames([frame_idx])
    
    def _cleanup_cache(self):
        """清理過期的快取，保持記憶體使用在合理範圍"""
        if len(self.cache) <= self.max_cache_size:
            return
        
        # 保留以目前幀為中心的視窗
        center = self.current_frame
        window_start = max(0, center - self.cache_window_frames // 2)
        window_end = min(self.total_frames, center + self.cache_window_frames // 2)
        
        # 移除視窗外的幀，但保留一些緩衝
        buffer_size = 50  # 保留額外的緩衝幀
        keys_to_remove = []
        for frame_idx in self.cache.keys():
            # 保留視窗內的幀和一些緩衝幀
            if (frame_idx < window_start - buffer_size or 
                frame_idx >= window_end + buffer_size):
                keys_to_remove.append(frame_idx)
        
        # 限制每次清理的數量，避免過度清理
        if len(keys_to_remove) > 100:
            keys_to_remove = keys_to_remove[:100]
        
        for key in keys_to_remove:
            del self.cache[key]
        
        if keys_to_remove:
            print(f"清理了 {len(keys_to_remove)} 個過期幀")
    
    def __getitem__(self, frame_idx: int):
        """取得指定幀"""
        if frame_idx < 0 or frame_idx >= self.total_frames:
            raise IndexError(f"幀索引 {frame_idx} 超出範圍 [0, {self.total_frames})")
        
        # 防抖機制：避免過於頻繁的請求
        current_time = time.time() * 1000
        if current_time - self.last_request_time < self.request_debounce_ms:
            # 如果請求太頻繁，直接返回快取中的幀（如果存在）
            if frame_idx in self.cache:
                return self.cache[frame_idx]
            # 否則等待一小段時間
            time.sleep(0.01)
        
        self.last_request_time = current_time
        
        with self.lock:
            self.current_frame = frame_idx
            
            # 如果幀已經在快取中，直接返回
            if frame_idx in self.cache:
                return self.cache[frame_idx]
            
            # 檢查是否正在載入
            with self.loading_lock:
                if frame_idx in self.loading_frames:
                    # 等待載入完成
                    max_wait = 1.0  # 增加等待時間到1秒
                    start_time = time.time()
                    while frame_idx in self.loading_frames:
                        if time.time() - start_time > max_wait:
                            # 超時，嘗試直接載入
                            break
                        time.sleep(0.01)
                    
                    # 再次檢查快取
                    if frame_idx in self.cache:
                        return self.cache[frame_idx]
                
                # 標記為正在載入
                self.loading_frames.add(frame_idx)
            
            # 確保幀在快取中（非阻塞）
            self._ensure_frame_in_cache_nonblocking(frame_idx)
            
            # 等待幀載入完成
            max_wait = 1.0  # 增加等待時間
            start_time = time.time()
            while frame_idx not in self.cache:
                if time.time() - start_time > max_wait:
                    # 超時處理：返回空白幀或拋出異常
                    with self.loading_lock:
                        self.loading_frames.discard(frame_idx)
                    raise IndexError(f"載入幀 {frame_idx} 超時")
                time.sleep(0.01)
            
            # 清理載入狀態
            with self.loading_lock:
                self.loading_frames.discard(frame_idx)
            
            # 返回幀
            return self.cache[frame_idx]
    
    def prefetch_around_current(self):
        """預取目前幀附近的幀"""
        if self.prefetch_queue.empty():
            try:
                # 預取目前幀附近的幀，包括前後的幀
                center_frame = self.current_frame
                window_start = max(0, center_frame - self.cache_window_frames // 2)
                window_end = min(self.total_frames, center_frame + self.cache_window_frames // 2)
                
                # 將預取請求加入佇列
                self.prefetch_queue.put_nowait(center_frame)
            except queue.Full:
                pass
    
    def get_cache_info(self):
        """取得快取資訊"""
        with self.lock:
            return {
                'cache_size': len(self.cache),
                'current_frame': self.current_frame,
                'total_frames': self.total_frames,
                'decode_position': self.decode_position,
                'queue_size': len(self.frame_queue),
                'loading_frames': len(self.loading_frames),
                'prefetch_queue_size': self.prefetch_queue.qsize()
            }
    
    def optimize_cache(self):
        """優化快取性能"""
        with self.lock:
            # 檢查快取大小
            if len(self.cache) > self.max_cache_size * 0.8:
                # 如果快取接近滿載，進行清理
                self._cleanup_cache()
            
            # 檢查預取佇列大小
            if self.prefetch_queue.qsize() > 40:
                # 如果預取佇列太滿，清空一些請求
                try:
                    for _ in range(20):
                        self.prefetch_queue.get_nowait()
                except queue.Empty:
                    pass
    
    def stop(self):
        """停止快取系統"""
        self.stopped = True
        if self.prefetch_thread:
            try:
                self.prefetch_queue.put_nowait(None)  # 停止信號
                self.prefetch_thread.join(timeout=1.0)
            except:
                pass
        self.cap.release()
    
    def __del__(self):
        """釋放資源"""
        self.stop()
    
    def clear(self):
        """清空快取"""
        with self.lock:
            self.cache.clear()
            self.frame_queue.clear()
            self.decode_position = 0