import os
import sys
from pathlib import Path
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon
from hayate.ui.main_window import MainWindow

def main():
  # Linux: libmpv embeds through an X11 window id, so default to Qt's xcb platform
  # (XWayland on Wayland). A user-set QT_QPA_PLATFORM or a missing X server wins.
  if sys.platform.startswith("linux") and os.environ.get("DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
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
