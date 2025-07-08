from PyQt5.QtWidgets import QLabel, QVBoxLayout, QWidget, QSizePolicy
from PyQt5.QtGui import QPixmap
from PyQt5.QtCore import Qt

class CameraPanel(QWidget):
    """
    Widget to display a single camera's video frame.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.image_label = QLabel(alignment=Qt.AlignCenter)
        self.image_label.setMinimumSize(160, 120)
        self.image_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.image_label)

    def set_pixmap(self, pixmap: QPixmap):
        """Update the panel's image."""
        self.image_label.setPixmap(pixmap)