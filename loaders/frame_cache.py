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
        self.prefetch_queue = queue.Queue()
        self.start_prefetch_thread()
        
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
                
                # 預取目標幀附近的幀
                self._prefetch_frames_around(target_frame)
                
            except queue.Empty:
                continue
            except Exception as e:
                print(f"預取執行緒錯誤：{e}")
    
    def _prefetch_frames_around(self, center_frame: int):
        """預取中心幀附近的幀"""
        window_start = max(0, center_frame - self.cache_window_frames // 2)
        window_end = min(self.total_frames, center_frame + self.cache_window_frames // 2)
        
        # 檢查哪些幀需要載入
        frames_to_load = []
        for frame_idx in range(window_start, window_end):
            if frame_idx not in self.cache:
                frames_to_load.append(frame_idx)
        
        if not frames_to_load:
            return
        
        # 順序載入幀
        self._sequential_load_frames(frames_to_load)
    
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
    
    def _cleanup_cache(self):
        """清理過期的快取，保持記憶體使用在合理範圍"""
        if len(self.cache) <= self.max_cache_size:
            return
        
        # 保留以目前幀為中心的視窗
        center = self.current_frame
        window_start = max(0, center - self.cache_window_frames // 2)
        window_end = min(self.total_frames, center + self.cache_window_frames // 2)
        
        # 移除視窗外的幀
        keys_to_remove = []
        for frame_idx in self.cache.keys():
            if frame_idx < window_start or frame_idx >= window_end:
                keys_to_remove.append(frame_idx)
        
        for key in keys_to_remove:
            del self.cache[key]
        
        if keys_to_remove:
            print(f"清理了 {len(keys_to_remove)} 個過期幀")
    
    def __getitem__(self, frame_idx: int):
        """取得指定幀"""
        if frame_idx < 0 or frame_idx >= self.total_frames:
            raise IndexError(f"幀索引 {frame_idx} 超出範圍 [0, {self.total_frames})")
        
        with self.lock:
            self.current_frame = frame_idx
            
            # 確保幀在快取中
            self._ensure_frame_in_cache(frame_idx)
            
            # 等待幀載入完成
            max_wait = 0.5  # 最大等待時間
            start_time = time.time()
            while frame_idx not in self.cache:
                if time.time() - start_time > max_wait:
                    raise IndexError(f"載入幀 {frame_idx} 超時")
                time.sleep(0.01)
            
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
                'queue_size': len(self.frame_queue)
            }
    
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