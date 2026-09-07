from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtWidgets import QLabel, QSlider, QPushButton
from PyQt5.QtGui import QPixmap, QImage
from loaders.sync_manager import AllCamerasInfo
from .annotation_controller import AnnotationController
from ui.camera_panel import CameraPanel
# from .prefetch_thread import PrefetchThread
import cv2
from typing import Callable, Optional

class PlaybackController:
    def __init__(self, 
                 cameras_info: AllCamerasInfo,
                 panels: list[CameraPanel],
                 status_label: QLabel,
                 slider: QSlider,
                 play_button: QPushButton,
                 output_dir: str,
                 frame_changed_callback: Optional[Callable[[int], None]] = None):
        
        self.info = cameras_info
        for fc in self.info.frames:
            if not fc:
                raise ValueError("Frame cache is empty. Ensure the video files are loaded correctly.")
            # fc.clear()  # Clear the frame cache to free memory
        # self.prefetch = PrefetchThread(self.info)
        self.status_label = status_label
        self.panels = panels
        self.slider = slider
        self.play_button = play_button
        self.is_updating = False
        self.show_tracknet = True
        self.output_dir = output_dir
        self.frame_changed_callback = frame_changed_callback

        self.timer = QTimer()
        self.timer.timeout.connect(self.play_next_frame)

        self.cache_refresh_timer = QTimer()
        self.cache_refresh_timer.setSingleShot(True)
        self.cache_refresh_timer.timeout.connect(self._refresh_pending_frame)
        
        # 快取優化計時器
        self.cache_optimization_timer = QTimer()
        self.cache_optimization_timer.timeout.connect(self._optimize_caches)
        self.cache_optimization_timer.start(5000)  # 每5秒優化一次

        # create annotation controller
        self.annot = AnnotationController(output_dir, self.info.synced_groups[0][0])
        
        # ui event connections
        self.slider.valueChanged.connect(self.on_slider_changed)
        self.play_button.clicked.connect(self.toggle_playback)

    def _render_frame(self, cam_id: int, idx: int) -> QPixmap:
        """渲染幀，包含錯誤處理"""
        try:
            # Get the frame and apply brightness adjustment
            frame = self.info.frames[cam_id][idx]
            if frame is None:
                raise ValueError(f"Camera {cam_id} frame {idx} could not be decoded")
            frame = cv2.convertScaleAbs(frame, alpha = self.info.brightness_factor, beta = 0)
            
            # Rotate the frame if necessary
            ang = self.info.rotation_angles[cam_id]
            if ang != 0:
                rotate_control = {90: cv2.ROTATE_90_CLOCKWISE,
                                  180: cv2.ROTATE_180,
                                  270: cv2.ROTATE_90_COUNTERCLOCKWISE}
                frame = cv2.rotate(frame, rotate_control[ang])

            if not self.info.playing and not self.info.sliding:
                
                # Show TrackNet points if enabled    
                if self.show_tracknet:
                    df = self.info.tracknets[cam_id]
                    if (df is not None) and (idx in df.index) and (df.loc[idx, "Visibility"] == 1):
                        x, y = int(df.loc[idx]["X"]), int(df.loc[idx]["Y"])
                        frame = self._draw_tracknet_point(frame, x, y, ang)

            # Convert the frame to QPixmap
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb.shape
            img = QImage(rgb.data, w, h, ch * w, QImage.Format_RGB888)
            pix = QPixmap.fromImage(img)
            pw = self.panels[cam_id].width() 
            ph = self.panels[cam_id].height()
            pix = pix.scaled(pw, ph, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            return pix
            
        except Exception as e:
            # 處理所有錯誤
            print(f"渲染幀錯誤：{e}")
            # 返回空白圖片
            pix = QPixmap(self.panels[cam_id].width(), self.panels[cam_id].height())
            pix.fill(Qt.black)
            return pix
    
    def _draw_tracknet_point(self, frame, x: int, y: int, angle: int):
        # Adjust coordinates based on rotation angle
        if angle == 90:
            x, y = frame.shape[1] - y, x
        elif angle == 180:
            x, y = frame.shape[1] - x, frame.shape[0] - y
        elif angle == 270:
            x, y = y, frame.shape[0] - x

        cv2.circle(frame, (x, y), 3, (0, 0, 255), -1)
        return frame
 
    def play_next_frame(self):
        if self.info.frame_idx < self.info.max_frames - 1:
            next_fid = self.info.frame_idx + 1
            if not self._request_synced_frame(next_fid):
                return
            self.info.frame_idx = next_fid
            self.update_frames()
        else:
            self.timer.stop()
            self.play_button.setText("Play")
            self.info.playing = False
    
    def update_frames(self):
        if self.is_updating:
            return
        # start_ts = time.perf_counter()
        self.is_updating = True
        if self.info.frame_idx >= self.info.max_frames:
            self.status_label.setText("No more frames.")
            self.is_updating = False
            return
        
        try:
            ref_time, group = self.info.synced_groups[self.info.frame_idx]
            matches = []
            render_errors = []
            
            for cam_id, idx in enumerate(group):
                panel = self.panels[cam_id]
                if idx is None:
                    panel.set_pixmap(QPixmap())
                    matches.append("None")
                    continue
                
                else:
                    try:
                        pix = self._render_frame(cam_id, idx)
                        panel.set_pixmap(pix)
                        ts = self.info.timestamps[cam_id][idx]
                        matches.append(f"{ts:.3f}")
                    except Exception as e:
                        # 記錄渲染錯誤但不中斷整個更新
                        error_msg = f"Camera {cam_id} frame {idx}: {str(e)}"
                        render_errors.append(error_msg)
                        print(f"渲染錯誤：{error_msg}")
                        
                        # 顯示錯誤幀（黑色背景）
                        pix = QPixmap(panel.width(), panel.height())
                        pix.fill(Qt.black)
                        panel.set_pixmap(pix)
                        matches.append("Error")
            
            # 更新狀態標籤
            if render_errors:
                self.status_label.setText(f"Frame: {self.info.frame_idx} | Ref Time: {ref_time:.3f} | Errors: {len(render_errors)}")
            else:
                self.status_label.setText(f"Frame: {self.info.frame_idx} | Ref Time: {ref_time:.3f} | Matched: {matches}")
            
            self.slider.blockSignals(True)
            self.slider.setValue(self.info.frame_idx)
            self.slider.blockSignals(False)

            # Keep frame-dependent UI (for example, event buttons) in sync even
            # when slider signals are blocked during playback/rendering.
            if self.frame_changed_callback:
                self.frame_changed_callback(self.info.frame_idx)

            # 智能預取：在更新幀後觸發預取（非阻塞）
            self._trigger_prefetch_async()

            if (not self.info.playing and
                    not self._synced_frame_is_cached(self.info.frame_idx)):
                self.cache_refresh_timer.start(50)
            
            # end_ts = time.perf_counter()
            # elapsed = (end_ts - start_ts) * 1000  # Convert to milliseconds
            # print(f"Update frames took {elapsed:.2f} ms")
            
        except Exception as e:
            print(f"更新幀時發生錯誤：{e}")
            self.status_label.setText(f"Error: {str(e)}")
        
        self.is_updating = False
    
    def _trigger_prefetch_async(self):
        """非阻塞觸發智能預取"""
        try:
            # 使用執行緒池或異步方式觸發預取
            # FrameCache already owns a background worker. These are only
            # non-blocking queue operations, so a thread per frame is wasteful.
            self._trigger_prefetch()
        except Exception as e:
            print(f"預取觸發錯誤：{e}")

    def _trigger_prefetch(self):
        """Ask each cache's existing worker to extend its forward buffer."""
        for frame_cache in self.info.frames:
            if frame_cache:
                frame_cache.prefetch_around_current()

    def _synced_frame_is_cached(self, fid: int) -> bool:
        if fid < 0 or fid >= self.info.max_frames:
            return False
        _, group = self.info.synced_groups[fid]
        return all(
            idx is None or self.info.frames[cam_id].is_cached(idx)
            for cam_id, idx in enumerate(group)
        )

    def _request_synced_frame(self, fid: int, urgent: bool = False) -> bool:
        if fid < 0 or fid >= self.info.max_frames:
            return False
        _, group = self.info.synced_groups[fid]
        ready = True
        for cam_id, idx in enumerate(group):
            if idx is None:
                continue
            frame_cache = self.info.frames[cam_id]
            if not frame_cache.is_cached(idx):
                frame_cache.request_frame(idx, urgent=urgent)
                ready = False
        return ready

    def seek_frame(self, fid: int):
        """Start an exact background jump and return to Qt immediately."""
        if fid < 0 or fid >= self.info.max_frames:
            return
        self.info.frame_idx = fid
        if not self._synced_frame_is_cached(fid):
            self._request_synced_frame(fid, urgent=True)
        self.update_frames()
        if not self._synced_frame_is_cached(fid):
            self.cache_refresh_timer.start(50)

    def _refresh_pending_frame(self):
        fid = self.info.frame_idx
        self.update_frames()
        if (not self.info.playing and
                not self._synced_frame_is_cached(fid)):
            self.cache_refresh_timer.start(50)

    def on_slider_changed(self, value: int):
        self.info.frame_idx = value
        self.info.sliding = self.slider.isSliderDown()
        if not self.info.sliding:
            self.seek_frame(value)
        self.info.sliding = False    
    
    def toggle_playback(self):
        if self.info.playing:
            self.timer.stop()
            # self.prefetch.stop()
            self.play_button.setText("Play")
            self.info.playing = False
        else:
            self._request_synced_frame(self.info.frame_idx + 1)
            self.timer.start(9)
            self.play_button.setText("Pause")
            self.info.playing = True
            # self.prefetch.start()

    def rotate_single_camera(self, cam_id: int):
        self.info.rotation_angles[cam_id] = (self.info.rotation_angles[cam_id] + 90) % 360
        self.update_frames()
    
    def adjust_brightness(self, factor: float):
        self.info.brightness_factor *= factor
        self.update_frames()

    def record_event(self, event_type: str):
        fid = self.info.frame_idx
        ts, _ = self.info.synced_groups[fid]
        pos = []
        self.annot.toggle_event(event_type, fid, ts, pos)
    
    def save_annotations(self):
        end_ts, _ = self.info.synced_groups[-1]
        self.annot.save_annotations(end_ts)
        self.status_label.setText("Annotations saved successfully.")

    def cleanup(self):
        """Release resources and save annotations."""
        self.timer.stop()
        self.cache_optimization_timer.stop()
        self.cache_refresh_timer.stop()
        for fc in self.info.frames:
            if fc:
                # fc.clear()
                # fc.__del__()
                fc.stop()

    def _optimize_caches(self):
        """定期優化所有快取"""
        try:
            for fc in self.info.frames:
                if fc:
                    fc.optimize_cache()
        except Exception as e:
            print(f"快取優化錯誤：{e}")

