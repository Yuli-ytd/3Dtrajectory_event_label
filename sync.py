import sys
import os
import pandas as pd
import cv2
import numpy as np
from tqdm import tqdm
from PyQt5.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QGridLayout,
    QVBoxLayout,
    QWidget,
    QGroupBox,
    QSlider,
    QPushButton,
    QHBoxLayout,
    QComboBox,
    QToolBar,
    QAction,
    QSizePolicy
)
from PyQt5.QtGui import QPixmap, QImage
from PyQt5.QtCore import Qt, QTimer


class CameraPanel(QWidget):
    """
    Widget to display a single camera's video frame.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.image_label = QLabel(alignment=Qt.AlignCenter)
        self.image_label.setMinimumSize(180, 120)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.image_label)

    def set_pixmap(self, pixmap: QPixmap):
        """Update the panel's image."""
        self.image_label.setPixmap(pixmap)


class VideoSyncViewer(QMainWindow):
    """
    Main application window to load and play synchronized camera videos.
    """
    def __init__(self, base_dir='./Data', sync_tol=0.004166):
        super().__init__()
        self.setWindowTitle("Synchronized Video Viewer")
        self.resize(940, 950)
        self.setFocusPolicy(Qt.StrongFocus)

        # State flags
        self.is_updating = False
        self.show_tracking = True

        # Playback properties
        self.base_dir = base_dir
        self.sync_tol = sync_tol
        self.frame_idx = 0
        self.max_frames = 0
        self.playing = False
        self.brightness_factor = 1.0

        # Status display
        self.status_label = QLabel(self)
        self.status_label.setFixedHeight(30)

        # Initialize toolbar and UI
        self._init_toolbar()

    def _init_toolbar(self):
        """Set up the toolbar with folder selector and initial actions."""
        self.toolbar = QToolBar("Tools")
        self.toolbar.setStyleSheet(
            """
            QToolButton {
                background: #4d4d4d;
                color: white;
                border-radius: 4px;
                padding: 4px 6px;
            }
            QToolButton:hover { background: #666; }
            """
        )
        self.addToolBar(self.toolbar)

        self.folder_combo = QComboBox()
        self.folder_combo.addItem("Select a folder")
        self.folder_combo.addItems(
            [d for d in os.listdir(self.base_dir)
             if os.path.isdir(os.path.join(self.base_dir, d))]
        )
        self.toolbar.addWidget(self.folder_combo)

        load_action = QAction("Load Folder", self)
        load_action.triggered.connect(self.on_load_clicked)
        self.toolbar.addAction(load_action)
        self.toolbar.addSeparator()

    def on_load_clicked(self):
        """Handle 'Load Folder' action."""
        if self.folder_combo.currentIndex() == 0:
            return
        folder_name = self.folder_combo.currentText()
        folder_path = os.path.join(self.base_dir, folder_name)
        self.load_folder(folder_path)

    def load_folder(self, folder_path):
        """Read metadata, load frames, and synchronize across cameras."""
        meta_files = [f for f in os.listdir(folder_path)
                      if f.startswith('CameraReader_') and f.endswith('_meta.csv')]
        self.cam_ids = sorted(int(f.split('_')[1]) for f in meta_files)[:2]
        self.num_cams = len(self.cam_ids)

        self.meta = []
        self.vcaps = []
        self.frame_buffers = [[] for _ in range(self.num_cams)]
        self.timestamps_list = []
        self.track_data = []
        self.synced_groups = []
        self.frame_idx = 0
        self.max_frames = 0

        self.timer = QTimer(self)
        self.timer.timeout.connect(self.play_next_frame)

        for i, cam_id in enumerate(self.cam_ids):
            df = pd.read_csv(os.path.join(folder_path, f"CameraReader_{cam_id}_meta.csv"))
            self.meta.append(df)
            cap = cv2.VideoCapture(os.path.join(folder_path, f"CameraReader_{cam_id}.mp4"))
            playCapture = cap.opened()
            getfps = cap.get(cv2.CAP_PROP_FPS)
            self.vcaps.append(cap)
            total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            for i in tqdm(range(total), desc=f"Cam{cam_id}", leave=False):
                ret, frame = cap.read()
                playCapture.set(cv2.CAP_PROP_POS_FRAMES, i)
                pret, pframe = playCapture.read()                

                if not ret:
                    break
                self.frame_buffers[i].append(frame)
            self.timestamps_list.append(df['timestamp'].tolist())            
            track_path = os.path.join(folder_path, f"TrackNet_{cam_id}.csv")
            td = pd.read_csv(track_path).set_index('Frame') if os.path.exists(track_path) else None
            self.track_data.append(td)

        pointers = [0] * self.num_cams
        while True:
            candidates = [
                (self.timestamps_list[i][pointers[i]], i)
                for i in range(self.num_cams)
                if pointers[i] < len(self.timestamps_list[i])
            ]
            if not candidates:
                break
            ref_ts, _ = min(candidates)
            group = []
            used = False
            for i in range(self.num_cams):
                ts_list = self.timestamps_list[i]
                while pointers[i] < len(ts_list) and ts_list[pointers[i]] < ref_ts - self.sync_tol:
                    pointers[i] += 1
                if pointers[i] < len(ts_list) and abs(ts_list[pointers[i]] - ref_ts) <= self.sync_tol:
                    group.append(pointers[i])
                    used = True
                    pointers[i] += 1
                else:
                    group.append(None)
            if used:
                self.synced_groups.append((ref_ts, group))

        self.max_frames = len(self.synced_groups)
        self.rotation_angles = [0] * self.num_cams
        self.brightness_factor = 1.0
        self.build_ui()

    def build_ui(self):
        """Construct the main user interface layout."""
        for action in list(self.toolbar.actions()):
            txt = action.text()
            if txt.startswith("Rotate Cam") or txt in ("Brighten", "Darken"):
                self.toolbar.removeAction(action)

        self.panels = []
        grid = QGridLayout()
        for idx, cam_id in enumerate(self.cam_ids):
            box = QGroupBox(f"Camera {cam_id}")
            vbox = QVBoxLayout(box)
            panel = CameraPanel()
            panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            vbox.addWidget(panel)
            grid.addWidget(box, idx // 2, idx % 2)
            self.panels.append(panel)

        tool_box = QWidget()
        tool_box.setFixedWidth(160)
        tool_layout = QVBoxLayout(tool_box)
        tool_layout.addStretch(1)

        main_grid = QHBoxLayout()
        main_grid.addLayout(grid, 1)
        main_grid.addWidget(tool_box, 0)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setMinimum(0)
        self.slider.setMaximum(max(0, self.max_frames - 1))
        self.slider.setValue(self.frame_idx)
        self.slider.valueChanged.connect(self.on_slider_changed)
        self.slider.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.slider.setStyleSheet(
            """
            QSlider::handle:horizontal {
                background: #d6d6d6;
                border: 1px solid #5c5c5c;
                width: 10px;
                height: 10px;
                border-radius: 5px;
                margin: -5px 0;
            }
            """
        )

        self.play_button = QPushButton("Play")
        self.play_button.clicked.connect(self.toggle_playback)

        control = QHBoxLayout()
        control.setSpacing(8)
        control.addWidget(self.slider, 1)
        control.addWidget(self.play_button)

        central_layout = QVBoxLayout()
        central_layout.addLayout(main_grid, 1)
        central_layout.addWidget(self.status_label)
        central_layout.addLayout(control)

        central = QWidget()
        central.setLayout(central_layout)
        self.setCentralWidget(central)

        for i, cam_id in enumerate(self.cam_ids):
            act = QAction(f"Rotate Cam {cam_id}", self)
            act.triggered.connect(lambda _, x=i: self.rotate_single_camera(x))
            self.toolbar.addAction(act)
        bright = QAction("Brighten", self)
        bright.triggered.connect(lambda: self.adjust_brightness(1.1))
        self.toolbar.addAction(bright)
        dark = QAction("Darken", self)
        dark.triggered.connect(lambda: self.adjust_brightness(0.9))
        self.toolbar.addAction(dark)

        self.update_frames()

    def update_frames(self):
        """Render each synchronized frame on its panel."""
        if self.is_updating:
            return
        self.is_updating = True
        if self.frame_idx >= self.max_frames:
            self.status_label.setText("No more frames.")
            self.is_updating = False
            return
        ref_ts, idxs = self.synced_groups[self.frame_idx]
        matches = []
        for i, idx in enumerate(idxs):
            panel = self.panels[i]
            if idx is None:
                panel.set_pixmap(QPixmap())
                matches.append("None")
                continue
            frame = self.frame_buffers[i][idx]
            frame = cv2.convertScaleAbs(frame, alpha=self.brightness_factor, beta=0)
            ang = self.rotation_angles[i]
            if ang == 90:
                frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
            elif ang == 180:
                frame = cv2.rotate(frame, cv2.ROTATE_180)
            elif ang == 270:
                frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)
            td = self.track_data[i]
            if td is not None and idx in td.index and td.loc[idx].get('Visibility', 0) == 1 and self.show_tracking:
                x, y = int(td.loc[idx]['X']), int(td.loc[idx]['Y'])
                if ang == 90:
                    x, y = frame.shape[1] - y, x
                elif ang == 180:
                    x, y = frame.shape[1] - x, frame.shape[0] - y
                elif ang == 270:
                    x, y = y, frame.shape[0] - x
                cv2.circle(frame, (x, y), 6, (0, 0, 255), -1)
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            h, w, c = rgb.shape
            img = QImage(rgb.data, w, h, c * w, QImage.Format_RGB888)
            pix = QPixmap.fromImage(img)
            pw = panel.image_label.width() or panel.image_label.minimumWidth()
            ph = panel.image_label.height() or panel.image_label.minimumHeight()
            pix = pix.scaled(pw, ph, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            panel.set_pixmap(pix)
            matches.append(f"{self.timestamps_list[i][idx]:.3f}")
        self.status_label.setText(f"Frame: {self.frame_idx} | Ref Time: {ref_ts:.3f} | Matched: {matches}")
        self.slider.blockSignals(True)
        self.slider.setValue(self.frame_idx)
        self.slider.blockSignals(False)
        self.is_updating = False

    def rotate_single_camera(self, idx):
        """Rotate the specified camera view by 90 degrees."""
        self.rotation_angles[idx] = (self.rotation_angles[idx] + 90) % 360
        self.update_frames()

    def adjust_brightness(self, factor):
        """Adjust global brightness factor."""
        self.brightness_factor *= factor
        self.update_frames()

    def on_slider_changed(self, val):
        """Handle manual slider changes."""
        self.frame_idx = val
        self.update_frames()

    def play_next_frame(self):
        """Advance playback by one frame."""
        if self.frame_idx < self.max_frames - 1:
            self.frame_idx += 1
            self.update_frames()
        else:
            self.timer.stop()
            self.play_button.setText("Play")
            self.playing = False

    def toggle_playback(self):
        """Start or pause automatic playback."""
        if self.playing:
            self.timer.stop()
            self.play_button.setText("Play")
            self.playing = False
        else:
            self.timer.start(100)
            self.play_button.setText("Pause")
            self.playing = True

    def resizeEvent(self, event):
        """Ensure frames redraw on window resize."""
        super().resizeEvent(event)
        self.update_frames()

    def keyPressEvent(self, event):
        """Handle key events for navigation and tracking toggle."""
        k = event.key()
        if k == Qt.Key_T:
            self.show_tracking = not self.show_tracking
            self.update_frames()
        elif k == Qt.Key_Right and self.frame_idx < self.max_frames - 1:
            self.frame_idx += 1
            self.update_frames()
        elif k == Qt.Key_Left and self.frame_idx > 0:
            self.frame_idx -= 1
            self.update_frames()
        else:
            super().keyPressEvent(event)


if __name__ == '__main__':
    app = QApplication(sys.argv)
    viewer = VideoSyncViewer()
    viewer.show()
    sys.exit(app.exec_())
