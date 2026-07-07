from PyQt5.QtCore import QThread, pyqtSignal
from loaders.sync_manager import AllCamerasInfo
from loaders.frame_cache import FrameCache
import time

class PrefetchThread(QThread):
    """
    輕量級預載入執行緒
    - 只負責觸發快取系統的預載入機制
    - 不直接操作幀資料，避免與 FrameCache 衝突
    """
    def __init__(self, info: AllCamerasInfo, parent=None):
        super().__init__(parent)
        self.info = info
        self._running = False

    def run(self):
        self._running = True
        while self._running and self.info.playing:
            # 觸發快取系統的預載入機制
            # FrameCache 內部會處理預載入邏輯
            current_frame = self.info.frame_idx
            
            # 檢查是否需要觸發預載入
            for frame_cache in self.info.frames:
                # 觸發預載入檢查（如果需要的話）
                if hasattr(frame_cache, 'prefetch_event'):
                    frame_cache.prefetch_event.set()
            
            # 短暫休眠避免過度消耗 CPU
            time.sleep(0.1)
    
    def stop(self):
        self._running = False
        self.wait()