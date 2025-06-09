import sys
import os
import argparse
import pandas as pd
import cv2
import numpy as np
from tqdm import tqdm

from PyQt5.QtWidgets import (
    QApplication, QLabel, QMainWindow, QGridLayout, QVBoxLayout,
    QWidget, QGroupBox, QSlider, QPushButton, QHBoxLayout
)
from PyQt5.QtGui import QPixmap, QImage
from PyQt5.QtCore import Qt, QTimer


from PyQt5.QtWidgets import QToolBar, QAction

class VideoSyncViewer(QMainWindow):
    is_updating = False  # lock to prevent overlapping updates
    show_tracking = True  # toggle for red dot visibility
    def __init__(self, folder, sync_tolerance=0.004166):
        super().__init__()
        self.folder = folder
        self.cam_ids = sorted([
            int(name.split('_')[1])
            for name in os.listdir(folder)
            if name.startswith('CameraReader_') and name.endswith('_meta.csv')
        ])[:4] # limited to 4 camera
        self.num_cams = len(self.cam_ids)
        self.sync_tolerance = sync_tolerance
        self.setWindowTitle("Synchronized Video Viewer")
        self.resize(940, 950)

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

        # Initialize data structures
        for i, cam_id in enumerate(self.cam_ids):
            # Read metadata and video capture for each camera
            df = pd.read_csv(os.path.join(folder, f"CameraReader_{cam_id}_meta.csv"))
            self.meta.append(df)
            cap = cv2.VideoCapture(os.path.join(folder, f"CameraReader_{cam_id}.mp4"))
            self.vcaps.append(cap)

            # Read all frames of the ith camera into its buffer
            total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            for _ in tqdm(range(total_frames), desc=f"Reading frames for Camera {cam_id}", leave=False):
                ret, frame = cap.read()
                if not ret:
                    break
                self.frame_buffers[i].append(frame)
            self.timestamps_list.append(df['timestamp'].tolist())

            # Read trackNet points if the file exists
            track_path = os.path.join(folder, f"TrackNet_{cam_id}.csv")
            if os.path.exists(track_path):
                track_df = pd.read_csv(track_path)
                self.track_data.append(track_df.set_index('Frame'))
            else:
                self.track_data.append(None)

        cur = [0] * self.num_cams # current frame index for each camera
        while True:
            current_ts = [] # current timestamps for each camera
            for i in range(self.num_cams):
                if cur[i] < len(self.timestamps_list[i]):
                    current_ts.append((self.timestamps_list[i][cur[i]], i))
            if not current_ts:
                break

            ref_ts, _ = min(current_ts)
            group = []
            used = False
            for i in range(self.num_cams):
                ts_list = self.timestamps_list[i]
                while cur[i] < len(ts_list):
                    diff = abs(ts_list[cur[i]] - ref_ts) # time difference between current and reference timestamp
                    if diff <= self.sync_tolerance:
                        group.append(cur[i])
                        cur[i] += 1
                        used = True
                        break
                    elif ts_list[cur[i]] < ref_ts: # if the current timestamp is less than the reference
                        cur[i] += 1 # move to the next timestamp
                    else:
                        group.append(None) # no match found
                        break
                else:
                    group.append(None) # no match found
            if used:# at least one camera matched the reference timestamp
                self.synced_groups.append((ref_ts, group))
        # the number of cameras in the synced groups
        self.max_frames = len(self.synced_groups)

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

        # Toolbar for image adjustments
        toolbar = QToolBar("Tools")
        self.addToolBar(toolbar)

        self.rotation_angles = [0] * self.num_cams
        self.brightness_factor = 1.0

        for i, cam_id in enumerate(self.cam_ids):
            rotate_action = QAction(f"Rotate Cam {cam_id}", self)
            rotate_action.triggered.connect(lambda checked, idx=i: self.rotate_single_camera(idx))
            toolbar.addAction(rotate_action)

        brighten_action = QAction("Brighten", self)
        brighten_action.triggered.connect(lambda: self.adjust_brightness(1.1))
        toolbar.addAction(brighten_action)

        darken_action = QAction("Darken", self)
        darken_action.triggered.connect(lambda: self.adjust_brightness(0.9))
        toolbar.addAction(darken_action)

        QTimer.singleShot(0, self.update_frames)
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

            label_size = self.labels[i].size()
            scaled_pixmap = pixmap.scaled(label_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
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
    parser = argparse.ArgumentParser()
    parser.add_argument('--dir', type=str, required=True, help='Path to folder containing video and CSV files')
    args = parser.parse_args()

    app = QApplication(sys.argv)
    viewer = VideoSyncViewer(args.dir)
    viewer.show()
    sys.exit(app.exec_())
