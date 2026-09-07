import sys
from PyQt5.QtWidgets import QApplication
from ui.main_window import MainWindow

def main():
    app = QApplication(sys.argv)
    window = MainWindow(base_dir="./Data", sync_tol=0.0045)
    window.show()
    sys.exit(app.exec_())

if __name__ == "__main__":
    main()