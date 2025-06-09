import sys, os
import pandas as pd
import cv2
import numpy as np
from tqdm import tqdm
from PyQt5.QtWidgets import (
    QApplication, QLabel, QMainWindow, QGridLayout, QVBoxLayout,
    QWidget, QGroupBox, QSlider, QPushButton, QHBoxLayout,
    QComboBox, QToolBar, QAction
)
from PyQt5.QtGui import QPixmap, QImage
from PyQt5.QtCore import Qt, QTimer

class VideoSyncViewer(QMainWindow):
    is_updating = False  # lock to prevent overlapping updates
    show_tracking = True  # toggle for red dot visibility
    def __init__(self, base_dir='./Data', sync_tol=0.004166):
        super().__init__()
        self.setWindowTitle("Synchronized Video Viewer")
        self.resize(940, 950)
        # 讓這個 window 能接收 keyPressEvent
        self.setFocusPolicy(Qt.StrongFocus)

        # # 4. 準備一些屬性
        self.sync_tol = sync_tol
        # self.rotation_angles = []
        # self.brightness = 1.0
        self.frame_idx = 0  # frame_idx 也可以預設為 0
        # self.synced_groups = []  # 初始為空，之後在 load_folder 更新
        self.max_frames = 0
        self.status = QLabel(self)
        self.status.setFixedHeight(30)

        # 1. 掃描所有子資料夾
        self.base_dir = base_dir
        self.folders = [
            d for d in os.listdir(base_dir)
            if os.path.isdir(os.path.join(base_dir, d))
        ]

        # 2. 建 toolbar，先放下拉選單 + Load action
        self.toolbar = QToolBar("Tools")
        self.addToolBar(self.toolbar)

        self.combo = QComboBox()
        self.combo.addItem("Select a folder")
        self.combo.addItems(self.folders)
        self.combo.setCurrentIndex(0)
        self.toolbar.addWidget(self.combo)

        load_act = QAction("Load Folder", self)
        load_act.triggered.connect(self.on_load_clicked)
        self.toolbar.addAction(load_act)
        self.toolbar.addSeparator()

        # 3. 旋轉 & 亮度按鈕，暫時先不初始化 cam_ids 也不會馬上用到
        # self.rotate_acts = []
        # self.brighten_act = QAction("Brighten", self, triggered=lambda: self.adjust_brightness(1.1))
        # self.darken_act   = QAction("Darken",   self, triggered=lambda: self.adjust_brightness(0.9))
        # toolbar.addAction(self.brighten_act)
        # toolbar.addAction(self.darken_act)

    def on_load_clicked(self):
        """當按下 Load Folder，可以重複呼叫來重新載入不同資料夾。"""
        if self.combo.currentIndex() == 0:
            return
        folder_name = self.combo.currentText()
        folder_path = os.path.join(self.base_dir, folder_name)
        self.load_folder(folder_path)

    def load_folder(self, folder):
        """讀取 metadata, 影片, tracknet, 並同步成 synced_groups"""
        self.folder = folder
        print(f"Loading folder: {self.folder}") 
        # 相機 ID
        self.cam_ids = sorted([
            int(f.split('_')[1])
            for f in os.listdir(self.folder)
            if f.startswith('CameraReader_') and f.endswith('_meta.csv')
        ])[:4]
        self.num_cams = len(self.cam_ids)
        # reset 動態資料結構
        self.meta = []
        self.vcaps = []
        self.frame_buffers = [[] for _ in range(self.num_cams)]
        self.timestamps_list = []
        self.synced_groups = []
        self.track_data = []
        self.frame_idx = 0
        self.playing = False

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.play_next_frame)

        # 讀取所有 frame & timestamps & track
        for i, cam_id in enumerate(self.cam_ids):
            df = pd.read_csv(os.path.join(self.folder, f"CameraReader_{cam_id}_meta.csv"))
            self.meta.append(df)
            cap = cv2.VideoCapture(os.path.join(self.folder, f"CameraReader_{cam_id}.mp4"))
            self.vcaps.append(cap)

            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            for _ in tqdm(range(total), desc=f"Cam{cam_id}", leave=False):
                ret, fr = cap.read()
                if not ret:
                    print(f"Failed to read frame for Camera {cam_id}. Stopping.")
                    break
                self.frame_buffers[i].append(fr)
            self.timestamps_list.append(df['timestamp'].tolist())

            track_csv = os.path.join(self.folder, f"TrackNet_{cam_id}.csv")
            if os.path.exists(track_csv):
                td = pd.read_csv(track_csv).set_index('Frame')
            else:
                td = None
            self.track_data.append(td)

        # 同步
        cur = [0] * self.num_cams
        self.synced_groups = []
        
        while True:
            candidates = []
            for i in range(self.num_cams):
                if cur[i] < len(self.timestamps_list[i]):
                    candidates.append((self.timestamps_list[i][cur[i]], i))
            if not candidates:
                break
            
            ref_ts, _ = min(candidates)
            print(f"Syncing at reference timestamp: {ref_ts:.6f} seconds")
            group=[]
            used=False
            for i in range(self.num_cams):
                ts_list= self.timestamps_list[i]
                while cur[i] < len(ts_list) and ts_list[cur[i]] < ref_ts - self.sync_tol:
                    cur[i] += 1

                # 如果目前這個時間戳在容差範圍內，就視為配對
                if cur[i] < len(ts_list) and abs(ts_list[cur[i]] - ref_ts) <= self.sync_tol:
                    group.append(cur[i])
                    used = True
                    cur[i] += 1
                else:
                    group.append(None)

            if used:
                self.synced_groups.append((ref_ts,group))

        self.max_frames = len(self.synced_groups)
        print(f"Total synced frames: {self.max_frames}")

        # 建立畫面：labels、slider、status
        self.build_ui()

    def build_ui(self):
        """把主畫面重建一次（Grid + slider + 播放按鈕 + 計時器）"""
        # 清空舊的 central widget
        # old = self.centralWidget()
        # if old:
        #     old.deleteLater()

        # Grid of image+text
        self.labels = []
        self.text_labels = []
        layout = QGridLayout()

        for i, cam_id in enumerate(self.cam_ids):
            group = QGroupBox(f"Camera {cam_id}")
            group_layout = QHBoxLayout(group)

            img_wrapper = QVBoxLayout()
            text_wrapper = QVBoxLayout()

            img_label = QLabel(self)
            img_label.setAlignment(Qt.AlignCenter)
            img_label.setMinimumSize(200, 150)

            text_label = QLabel(self)
            text_label.setAlignment(Qt.AlignLeft | Qt.AlignTop)
            text_label.setWordWrap(True)
            img_label.setMinimumSize(200, 150)

            # widgets added via layout wrappers
            img_wrapper.addWidget(img_label)
            text_wrapper.addWidget(text_label)

            group_layout.addLayout(img_wrapper, 4)
            group_layout.addLayout(text_wrapper, 1)  # text narrower

            layout.addWidget(group, i // 2, i % 2)

            self.labels.append(img_label)
            self.text_labels.append(text_label)

        self.status = QLabel(self)
        self.status.setFixedHeight(30)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimum(0)
        self.slider.setMaximum(self.max_frames - 1)
        self.slider.setValue(self.frame_idx)
        self.slider.valueChanged.connect(self.on_slider_changed)

        self.play_button = QPushButton("Play")
        self.play_button.clicked.connect(self.toggle_playback)

        control_layout = QHBoxLayout()
        control_layout.addWidget(self.slider)
        control_layout.addWidget(self.play_button)

        main_layout = QVBoxLayout()
        main_layout.addLayout(layout)
        main_layout.addWidget(self.status)
        main_layout.addLayout(control_layout)

        central_widget = QWidget()
        central_widget.setLayout(main_layout)
        self.setCentralWidget(central_widget)

        self.rotation_angles = [0] * self.num_cams
        self.brightness_factor = 1.0

        for i, cam_id in enumerate(self.cam_ids):
            rotate_action = QAction(f"Rotate Cam {cam_id}", self)
            rotate_action.triggered.connect(lambda checked, idx=i: self.rotate_single_camera(idx))
            self.toolbar.addAction(rotate_action)

        brighten_action = QAction("Brighten", self)
        brighten_action.triggered.connect(lambda: self.adjust_brightness(1.1))
        self.toolbar.addAction(brighten_action)

        darken_action = QAction("Darken", self)
        darken_action.triggered.connect(lambda: self.adjust_brightness(0.9))
        self.toolbar.addAction(darken_action)

        # QTimer.singleShot(0, self.update_frames)
        self.update_frames()

        print("""
[Keys]
  → : next frame
  ← : previous frame
  T : toggle tracknet points
""")
    
    def rotate_single_camera(self, i):
        self.rotation_angles[i] = (self.rotation_angles[i] + 90) % 360
        self.update_frames()

    def adjust_brightness(self, factor):
        self.brightness_factor *= factor
        self.update_frames()

    def update_frames(self):
        if self.is_updating:
            return
        self.is_updating = True
        if self.frame_idx >= self.max_frames:
            self.status.setText("No more frames.")
            self.is_updating = False
            return

        t_ref, group_indices = self.synced_groups[self.frame_idx]
        timestamps_shown = []

        for i in range(self.num_cams):
            idx = group_indices[i]
            ts_list = self.timestamps_list[i]

            if idx is None:
                self.labels[i].clear()
                self.text_labels[i].setText("No match")
                timestamps_shown.append("None")
                continue

            buffer = self.frame_buffers[i]
            if idx >= len(buffer):
                continue
            frame = buffer[idx]

            # Apply brightness
            frame = cv2.convertScaleAbs(frame, alpha=self.brightness_factor, beta=0)

            # Apply rotation
            angle = self.rotation_angles[i]
            if angle == 90:
                frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
            elif angle == 180:
                frame = cv2.rotate(frame, cv2.ROTATE_180)
            elif angle == 270:
                frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)

            coords_text = ""

            # Draw tracking point if available
            if self.track_data[i] is not None and idx in self.track_data[i].index:
                row = self.track_data[i].loc[idx]
                if row['Visibility'] == 1:
                    x, y = int(row['X']), int(row['Y'])
                    coords_text = f"X={x}, Y={y}"
                    if self.show_tracking:

                        angle = self.rotation_angles[i]
                        if angle == 90:
                            x, y = frame.shape[1] - y, x
                        elif angle == 180:
                            x, y = frame.shape[1] - x, frame.shape[0] - y
                        elif angle == 270:
                            x, y = y, frame.shape[0] - x

                        cv2.circle(frame, (x, y), 6, (0, 0, 255), -1)

            rgb_image = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, ch = rgb_image.shape
            qt_img = QImage(rgb_image.data, w, h, ch * w, QImage.Format_RGB888)
            pixmap = QPixmap.fromImage(qt_img)

            # label_size = self.labels[i].size()
            # scaled_pixmap = pixmap.scaled(label_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            w = self.labels[i].width()  or self.labels[i].minimumWidth()
            h = self.labels[i].height() or self.labels[i].minimumHeight()
            scaled_pixmap = pixmap.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.labels[i].setPixmap(scaled_pixmap)

            ts_value = ts_list[idx]
            delta_ms = (ts_value - t_ref) * 1000
            delta_str = f"{delta_ms:+.3f} ms"

            if abs(delta_ms) > 1.0:
                delta_html = f'<span style="color:red;">Δ: {delta_str}</span>'
            else:
                delta_html = f'Δ: {delta_str}'

            self.text_labels[i].setText(
                f"frame: {idx}<br>ts: {ts_value:.6f}<br>{delta_html}<br>{coords_text}"
            )
            self.text_labels[i].setTextFormat(Qt.RichText)

            timestamps_shown.append(f"{ts_value:.6f}")

        self.status.setText(f"Frame: {self.frame_idx} | Ref Time: {t_ref:.6f} | Matched: {timestamps_shown}")
        self.slider.blockSignals(True)
        self.slider.setValue(self.frame_idx)
        self.slider.blockSignals(False)
        self.is_updating = False

    def on_slider_changed(self, value):
        self.frame_idx = value
        self.update_frames()

    def play_next_frame(self):
        if self.frame_idx < self.max_frames - 1:
            self.frame_idx += 1
            self.update_frames()
        else:
            self.timer.stop()
            self.play_button.setText("Play")
            self.playing = False

    def toggle_playback(self):
        if self.playing:
            self.timer.stop()
            self.play_button.setText("Play")
            self.playing = False
        else:
            self.timer.start(100)
            self.play_button.setText("Pause")
            self.playing = True

    def resizeEvent(self, event):
        self.update_frames()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_T:
            self.show_tracking = not self.show_tracking
            self.update_frames()
            return
        if event.key() == Qt.Key_Right and self.frame_idx < self.max_frames - 1:
            self.frame_idx += 1
            self.update_frames()
        elif event.key() == Qt.Key_Left and self.frame_idx > 0:
            self.frame_idx -= 1
            self.update_frames()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    win = VideoSyncViewer()
    win.show()
    sys.exit(app.exec_())