import os
from PyQt5.QtWidgets import (
    QLabel, QMainWindow, QGridLayout,
    QVBoxLayout, QWidget, QGroupBox, QSlider,
    QPushButton, QHBoxLayout, QComboBox, QToolBar,
    QAction, QSizePolicy
)
from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtGui import QFont
from ui.camera_panel import CameraPanel
from controllers.playback_controller import PlaybackController
from loaders.sync_manager import AllCamerasInfo, load_all_cameras_data
import time

class MainWindow(QMainWindow):
    def __init__(self, base_dir = "./Data", sync_tol = 0.004166):
        super().__init__()
        self.base_dir = base_dir
        self.sync_tol = sync_tol
        self.control = None
        self.current_allowed_btn = set()
        
        # 防抖機制
        self.last_key_press_time = 0
        self.key_debounce_ms = 100  # 100ms 防抖
        self.pending_frame_update = None
        self.update_timer = QTimer()
        self.update_timer.setSingleShot(True)
        self.update_timer.timeout.connect(self._process_pending_frame_update)
        
        # 初始化UI
        self.file_name = None
        self.setWindowTitle("3D Trajectory Event Labeling Tool")
        self.resize(940,950)
        self.setFocusPolicy(Qt.StrongFocus)

        # Initialize the main layout:
        self.status_label = QLabel(self)
        self.status_label.setFixedHeight(20)
        
        self._init_toolbar()

    def _init_toolbar(self):

        # Create the toolbar and set its style
        self.toolbar = QToolBar("Tools")
        self.toolbar.setStyleSheet(
            """
            QToolBar {
                background: #4d4d4d;
                color: white;
                border-radius: 4px;
                padding: 4px 6px;
            }
            
            QToolButton:hover {
                background: #666;
            }
            """
        )
        self.addToolBar(self.toolbar)

        # Create the folder selection action
        self.folder_combo = QComboBox()
        self.folder_combo.addItem("Select a folder")
        
        # 檢查基礎目錄是否存在
        if os.path.exists(self.base_dir):
            try:
                folders = [d for d in os.listdir(self.base_dir) 
                         if os.path.isdir(os.path.join(self.base_dir, d))]
                self.folder_combo.addItems(folders)
            except Exception as e:
                print(f"載入文件夾列表時發生錯誤：{e}")
        else:
            print(f"基礎目錄不存在：{self.base_dir}")
        
        self.toolbar.addWidget(self.folder_combo)

        # Create the load action
        self.load_action = QAction("Load Data", self)
        self.load_action.triggered.connect(self.on_load_clicked)
        self.toolbar.addAction(self.load_action)
        self.toolbar.addSeparator()

    def on_load_clicked(self):
        if self.folder_combo.currentIndex() == 0:
            self.status_label.setText("Please select a folder to load data.")
            return

        # if the selected folder is same as the current one, do nothing
        if getattr(self, 'file_name', None) == self.folder_combo.currentText():
            self.status_label.setText("Data already loaded.")
            return

        folder_path = os.path.join(self.base_dir, self.folder_combo.currentText())
        
        try:
            # Load all camera data
            info = load_all_cameras_data(folder_path, self.sync_tol)
            
            # Build the main window with the loaded data
            self._build_central_widget(info)

            # Set playback controller
            self.control = PlaybackController(
                cameras_info=info,
                status_label=self.status_label,
                panels=self.panels,
                slider=self.slider,
                play_button=self.play_button,
                output_dir=folder_path
            )

            # Connect toolbar actions to the playback controller
            if self.file_name is None:
                self._bind_toolbar_actions(info.cam_ids)
            self.file_name = self.folder_combo.currentText()

            # Connect the save button to the controller
            self.save_button.clicked.connect(self.control.save_annotations)

            # Set the initial frame after loading
            QTimer.singleShot(0, lambda: self._on_frame_changed(self.slider.value()))
            
        except Exception as e:
            print(f"載入數據時發生錯誤：{e}")
            self.status_label.setText(f"Error loading data: {str(e)}")

    def _build_central_widget(self, info: AllCamerasInfo):
        
        try:
            # Panels
            self.panels = []
            grid = QGridLayout()
            for idx, cam_id in enumerate(info.cam_ids):
                box = QGroupBox(f"Camera {cam_id}")
                vbox = QVBoxLayout(box)
                panel = CameraPanel()
                panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
                vbox.addWidget(panel)
                grid.addWidget(box, idx // 2, idx % 2)
                self.panels.append(panel)

            # Labeling tool box
            tool_box = self._build_labeling_toolbox()

            main_grid = QHBoxLayout()
            main_grid.addLayout(grid, 1)
            main_grid.addWidget(tool_box, 0)

            self.slider = QSlider(Qt.Horizontal)
            self.slider.setRange(0, max(0, info.max_frames - 1))
            self.slider.setValue(info.frame_idx)
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
            # self.slider.valueChanged.connect(self._on_slider_value_changed)
            # self.slider.sliderReleased.connect(self._on_slider_released)
            self.slider.valueChanged.connect(self._on_frame_changed)


            self.play_button = QPushButton("Play")

            control_bar = QHBoxLayout()
            control_bar.setSpacing(8)
            control_bar.addWidget(self.slider, 1)
            control_bar.addWidget(self.play_button)

            central_layout = QVBoxLayout()
            central_layout.addLayout(main_grid, 1)
            central_layout.addWidget(self.status_label)
            central_layout.addLayout(control_bar)

            central = QWidget()
            central.setLayout(central_layout)
            self.setCentralWidget(central)
            
        except Exception as e:
            print(f"構建UI時發生錯誤：{e}")
            raise

    def _build_labeling_toolbox(self):
        
        try:
            tool_box = QWidget()
            tool_layout = QVBoxLayout(tool_box)
            tool_layout.setContentsMargins(10, 10, 10, 10)
            tool_layout.setSpacing(40)
            tool_box.setFixedWidth(200)
            tool_layout.addStretch(1)

            # Event buttons
            self.event_box = QGroupBox("Event")
            self.event_box.setFont(QFont("", 20, QFont.Bold))
            event_layout = QVBoxLayout()
            self.event_buttons = {}
            self.current_allowed_btn = {"serve", "rest-hit"} # Record the current allowed events for the next frame
            events = [("serve", "Serve"), ("hit", "Hit"), 
                      ("touch", "Touch"), ("dead", "Dead"), 
                      ("rest-hit", "Rest\nHit"), ("rest-dead","Rest\nDead")
                      ]
            
            for key, label in events:
                btn = QPushButton(label)
                btn.setFont(QFont("", 18))
                btn.setFixedHeight(55)
                btn.setCheckable(True)
                btn.clicked.connect(lambda _, k=key: self._on_event_clicked(k))
                event_layout.addWidget(btn)
                self.event_buttons[key] = btn
                if key not in ["serve", "rest-hit"]:
                    btn.setEnabled(False)

            self.event_box.setLayout(event_layout)
            tool_layout.addWidget(self.event_box)

            # Save
            tool_layout.addStretch(1)
            self.save_button = QPushButton("Save")
            self.save_button.setFont(QFont("", 18, QFont.Bold))
            self.save_button.setFixedWidth(100)
            tool_layout.addWidget(self.save_button, alignment=Qt.AlignCenter)
            
            return tool_box
            
        except Exception as e:
            print(f"構建標註工具箱時發生錯誤：{e}")
            raise

    def _bind_toolbar_actions(self, cam_idx):
        try:
            # Rotation action
            for i, cam_id in enumerate(cam_idx):
                action = QAction(f"Rotate Cam {cam_id}", self)
                # 使用 functools.partial 或默認參數來正確捕獲變數
                action.triggered.connect(lambda checked, idx=i: self.control.rotate_single_camera(idx))
                self.toolbar.addAction(action)
            
            # Brighten / Darken action
            for factor, label in ((1.1, "Brighten"), (0.9, "Darken")):
                action = QAction(label, self)
                # 使用默認參數來正確捕獲變數
                action.triggered.connect(lambda checked, f=factor: self.control.adjust_brightness(f))
                self.toolbar.addAction(action)
                
        except Exception as e:
            print(f"綁定工具欄動作時發生錯誤：{e}")

    def _on_event_clicked(self, event_type:str):
        try:
            if not self.control: 
                return
            
            # Check if the button is already checked
            btn = self.event_buttons[event_type]
            checked = btn.isChecked()

            self.control.record_event(event_type)
            # If the button is unchecked, we just update the frame
            if not checked:
                self._on_frame_changed(self.control.info.frame_idx)
                return

            # If the button is checked, we need to disable other buttons
            if event_type in ("hit","touch","dead"):
                for e in ("hit","touch","dead"):
                    self.event_buttons[e].setEnabled(e == event_type)
            elif event_type == "rest-hit":
                self.event_buttons["rest-dead"].setEnabled(False)
            elif event_type == "rest-dead":
                self.event_buttons["rest-hit"].setEnabled(False)
            elif event_type == "serve":
                self.event_buttons["rest-hit"].setEnabled(False)
                
        except Exception as e:
            print(f"處理事件點擊時發生錯誤：{e}")
    
    def _compute_allowed_events(self, prev_fid: int):
        try:
            initial = {"serve", "rest-hit"}

            if prev_fid < 0:
                return initial
            
            prev_events = self.control.annot.by_frame.get(prev_fid, [])
            # print(f"Previous events for frame {prev_fid}: {prev_events}")
            if prev_events:
                prev_event_type = prev_events[-1]["event_type"]
                if prev_event_type == "serve":
                    return {"hit", "touch", "dead"}
                elif prev_event_type in {"hit", "touch"}:
                    return {"hit", "touch", "dead"}
                elif prev_event_type == "dead":
                    return initial  # 回到初始狀態
                elif prev_event_type == "rest-hit":
                    return {"rest-hit", "rest-dead"}
                elif prev_event_type == "rest-dead":
                    return initial  # 回到初始狀態
                return initial
                        
            return self.current_allowed_btn
            
        except Exception as e:
            print(f"計算允許事件時發生錯誤：{e}")
            return {"serve", "rest-hit"}  # 返回初始狀態
    
    def _on_frame_changed(self, fid: int):
        try:
            if not self.control:
                return

            # If the frame is annotated, show the checked state
            current_events = self.control.annot.by_frame.get(fid, [])
            if current_events and len(current_events) > 0:
                print(f"Current events for frame {fid}: {current_events}")
                self.current_allowed_btn = {current_events[-1]["event_type"]}
                for _, btn in self.event_buttons.items():
                    btn.setEnabled(False)
                    btn.setChecked(False)
                self.event_buttons[current_events[-1]["event_type"]].setEnabled(True)
                self.event_buttons[current_events[-1]["event_type"]].setChecked(True)
                return

            # If the frame is not annotated, compute the allowed events
            else:
                closest_annotated_frame = self.control.annot.get_events_for_frame(fid)
                allowed = self._compute_allowed_events(closest_annotated_frame)

                for _, btn in self.event_buttons.items():
                    btn.setEnabled(False)
                    btn.setChecked(False)

                for key in allowed:
                    self.event_buttons[key].setEnabled(True)

                # Update the allowed events for the next frame        
                self.current_allowed_btn = allowed
            
            self.control.update_frames()
            
        except Exception as e:
            print(f"處理幀變更時發生錯誤：{e}")

    def _on_slider_value_changed(self, value: int):
        """拖動過程中只更新 frame_idx，不做任何 redraw。"""
        try:
            if not self.control:
                return
            # 只改位置，不呼叫 update_frames()
            self.control.info.frame_idx = value
        except Exception as e:
            print(f"處理滑桿值變更時發生錯誤：{e}")

    def _on_slider_released(self):
        """滑桿放手後，呼叫一次 __getitem__ 觸發精準 random access，然後 update_frames()。"""
        try:
            if not self.control:
                return
            fid = self.control.info.frame_idx

            # 對每支 camera 的 cache 都叫一次 __getitem__，啟動我們加強版 random_access
            for cache in self.control.info.frames:
                try:
                    _ = cache[fid]
                except Exception as e:
                    print(f"載入幀 {fid} 時發生錯誤：{e}")

            # 最後一次性更新畫面
            self.control.update_frames()
        except Exception as e:
            print(f"處理滑桿釋放時發生錯誤：{e}")
    
    def resizeEvent(self, event):
        """Ensure frames redraw on window resize."""
        try:
            super().resizeEvent(event)
            if self.control is not None:
                self.control.update_frames()
        except Exception as e:
            print(f"處理視窗大小變更時發生錯誤：{e}")

    def _process_pending_frame_update(self):
        """處理待處理的幀更新"""
        try:
            if self.pending_frame_update and self.control:
                try:
                    fid = self.pending_frame_update
                    self.pending_frame_update = None
                    
                    # 對每支 camera 的 cache 都叫一次 __getitem__，啟動精確 random access
                    for cache in self.control.info.frames:
                        try:
                            _ = cache[fid]
                        except Exception as e:
                            print(f"載入幀 {fid} 時發生錯誤：{e}")
                    
                    # 最後一次性更新畫面
                    self.control.update_frames()
                except Exception as e:
                    print(f"處理幀更新時發生錯誤：{e}")
        except Exception as e:
            print(f"處理待處理幀更新時發生錯誤：{e}")

    def keyPressEvent(self, event):
        """Handle key events for navigation and tracking toggle."""
        try:
            if self.control is None:
                return super().keyPressEvent(event)
            
            # 防抖機制
            current_time = time.time() * 1000
            if current_time - self.last_key_press_time < self.key_debounce_ms:
                # 如果按鍵太頻繁，忽略
                return
            
            self.last_key_press_time = current_time
            
            k = event.key()
            if k == Qt.Key_T:
                self.control.show_tracknet = not self.control.show_tracknet

            elif k == Qt.Key_Right and self.control.info.frame_idx < self.control.info.max_frames - 1:
                new_fid = self.control.info.frame_idx + 1
                self.control.info.frame_idx = new_fid
                self.slider.setValue(new_fid)

            elif k == Qt.Key_Left and self.control.info.frame_idx > 0:
                new_fid = self.control.info.frame_idx - 1
                self.control.info.frame_idx = new_fid
                self.slider.setValue(new_fid)

            elif k == Qt.Key_Space:
                if self.control.info.playing:
                    self.control.timer.stop()
                    self.play_button.setText("Play")
                else:
                    self.control.timer.start()
                    self.play_button.setText("Pause")
                self.control.info.playing = not self.control.info.playing
            
            elif k == Qt.Key_Up and self.control.info.frame_idx < self.control.info.max_frames - 1:
                if self.control.info.frame_idx + 30 < self.control.info.max_frames - 1:
                    new_fid = self.control.info.frame_idx + 30
                else:
                    new_fid = self.control.info.max_frames - 1
                self.control.info.frame_idx = new_fid
                self.slider.setValue(new_fid)
                
                # 使用防抖機制觸發幀載入
                self._schedule_frame_update(new_fid)
        
            elif k == Qt.Key_Down and self.control.info.frame_idx > 0:
                if self.control.info.frame_idx - 30 > 0:
                    new_fid = self.control.info.frame_idx - 30
                else:
                    new_fid = 0
                self.control.info.frame_idx = new_fid
                self.slider.setValue(new_fid)
                
                # 使用防抖機制觸發幀載入
                self._schedule_frame_update(new_fid)

            else:
                super().keyPressEvent(event)
                
        except Exception as e:
            print(f"處理鍵盤事件時發生錯誤：{e}")

    def _schedule_frame_update(self, fid: int):
        """使用防抖機制安排幀更新"""
        try:
            self.pending_frame_update = fid
            # 取消之前的計時器並重新開始
            self.update_timer.stop()
            self.update_timer.start(50)  # 50ms 後執行
        except Exception as e:
            print(f"安排幀更新時發生錯誤：{e}")

    def closeEvent(self, event):
        """Handle window close event to release resources."""
        try:
            if self.control is not None:
                self.control.cleanup()
            # 停止防抖計時器
            if hasattr(self, 'update_timer'):
                self.update_timer.stop()
            super().closeEvent(event)
        except Exception as e:
            print(f"處理視窗關閉時發生錯誤：{e}")
            super().closeEvent(event)
        