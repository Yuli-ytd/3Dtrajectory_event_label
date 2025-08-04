from PyQt5.QtCore import QTimer, Qt
from PyQt5.QtWidgets import QLabel, QSlider, QPushButton
from PyQt5.QtGui import QPixmap, QImage
from loaders.sync_manager import AllCamerasInfo
from .annotation_controller import AnnotationController
from ui.camera_panel import CameraPanel
# from .prefetch_thread import PrefetchThread
import cv2
import time
import queue

class PlaybackController:
    def __init__(self, 
                 cameras_info: AllCamerasInfo,
                 panels: list[CameraPanel],
                 status_label: QLabel,
                 slider: QSlider,
                 play_button: QPushButton,
                 output_dir: str):
        
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

        self.timer = QTimer()
        self.timer.timeout.connect(self.play_next_frame)

        # create annotation controller
        self.annot = AnnotationController(output_dir, self.info.synced_groups[0][0])
        
        # ui event connections
        # self.slider.valueChanged.connect(self.on_slider_changed)
        self.play_button.clicked.connect(self.toggle_playback)

    def _render_frame(self, cam_id: int, idx: int) -> QPixmap:
        """渲染幀，包含錯誤處理"""
        try:
            # Get the frame and apply brightness adjustment
            frame = self.info.frames[cam_id][idx]
            frame = cv2.convertScaleAbs(frame, alpha = self.info.brightness_factor, beta = 0)
            
            # Rotate the frame if necessary
            ang = self.info.rotation_angles[cam_id]
            if ang != 0:
                rotate_control = {90: cv2.ROTATE_90_CLOCKWISE,
                                  180: cv2.ROTATE_180,
                                  270: cv2.ROTATE_90_COUNTERCLOCKWISE}
                frame = cv2.rotate(frame, rotate_control[ang])

            if not self.info.playing:
                
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
            self.info.frame_idx += 1
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
            for cam_id, idx in enumerate(group):
                panel = self.panels[cam_id]
                if idx is None:
                    panel.set_pixmap(QPixmap())
                    matches.append("None")
                    continue
                
                else:
                    pix = self._render_frame(cam_id, idx)
                    panel.set_pixmap(pix)
                    ts = self.info.timestamps[cam_id][idx]
                    matches.append(f"{ts:.3f}")
            
            self.status_label.setText(f"Frame: {self.info.frame_idx} | Ref Time: {ref_time:.3f} | Matched: {matches}")
            # self.status_label.setText(f"Frame: {self.info.frame_idx} | Ref Time: {ref_time:.3f}")
            self.slider.blockSignals(True)
            self.slider.setValue(self.info.frame_idx)
            self.slider.blockSignals(False)

            # 智能預取：在更新幀後觸發預取
            self._trigger_prefetch()
            
            # end_ts = time.perf_counter()
            # elapsed = (end_ts - start_ts) * 1000  # Convert to milliseconds
            # print(f"Update frames took {elapsed:.2f} ms")
            
        except Exception as e:
            print(f"更新幀時發生錯誤：{e}")
            self.status_label.setText(f"Error: {str(e)}")
        
        self.is_updating = False
    
    def _trigger_prefetch(self):
        """觸發智能預取"""
        try:
            # 預取目前幀附近的幀
            for fc in self.info.frames:
                if fc:
                    fc.prefetch_around_current()
            
            # 如果正在播放，預取下一批幀
            if self.info.playing:
                next_frames = []
                for i in range(1, 31):  # 預取接下來30幀
                    next_frame = self.info.frame_idx + i
                    if next_frame < self.info.max_frames:
                        next_frames.append(next_frame)
                
                # 觸發預取
                for fc in self.info.frames:
                    if fc and next_frames:
                        try:
                            fc.prefetch_queue.put_nowait(next_frames[0])
                        except queue.Full:
                            pass
            
            # 預取向後的幀（用於向後導航）
            prev_frames = []
            for i in range(1, 16):  # 預取前面15幀
                prev_frame = self.info.frame_idx - i
                if prev_frame >= 0:
                    prev_frames.append(prev_frame)
            
            # 觸發向後預取
            for fc in self.info.frames:
                if fc and prev_frames:
                    try:
                        fc.prefetch_queue.put_nowait(prev_frames[0])
                    except queue.Full:
                        pass
        except Exception as e:
            print(f"預取觸發錯誤：{e}")
    
    def on_slider_changed(self, value: int):
        self.info.frame_idx = value
        self.update_frames()
    
    def toggle_playback(self):
        if self.info.playing:
            self.timer.stop()
            # self.prefetch.stop()
            self.play_button.setText("Play")
            self.info.playing = False
        else:
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
        for fc in self.info.frames:
            if fc:
                # fc.clear()
                # fc.__del__()
                fc.stop()

