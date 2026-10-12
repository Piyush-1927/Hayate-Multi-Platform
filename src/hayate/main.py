import sys
from PyQt6.QtWidgets import QApplication
from hayate.ui.main_window import MainWindow

def main():
    app = QApplication(sys.argv)
    # Neutral style base prevents theme engine border artifacts on KDE
    app.setStyle("Fusion")
    app.setApplicationName("hayate")
    app.setApplicationDisplayName("Hayate")

    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
