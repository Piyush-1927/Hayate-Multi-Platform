import sys
from pathlib import Path
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon
from hayate.ui.main_window import MainWindow

def main():
  app = QApplication(sys.argv)

  icon_file = Path(__file__).resolve().parents[2] / 'app_icon.png'
  if icon_file.exists():
      app.setWindowIcon(QIcon(str(icon_file)))

  # Neutral style base prevents theme engine border artifacts on KDE
  app.setStyle("Fusion")
  app.setApplicationName("hayate")
  app.setApplicationDisplayName("Hayate")

  window = MainWindow()
  window.show()
  sys.exit(app.exec())

if __name__ == "__main__":
  main()
