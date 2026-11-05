import os
import sys, traceback
sys.excepthook = lambda t,v,tb: traceback.print_exception(t,v,tb)
from pathlib import Path
from PyQt6.QtWidgets import QApplication
from PyQt6.QtGui import QIcon
from hayate.ui.main_window import MainWindow

def _autoscale():
  # X11/XWayland never tells Qt the desktop scale (only Xft.dpi at launch). Turn it into one
  # global Qt scale factor so px, QSS, text and pixmaps on every page scale together.
  # Skipped when QT_SCALE_FACTOR is already set or HAYATE_NO_AUTOSCALE=1.
  if not (sys.platform.startswith("linux") and os.environ.get("DISPLAY")):
    return
  qpa = os.environ.get("QT_QPA_PLATFORM", "")
  # Qt uses xcb when asked to, or when unset on a session without a Wayland display.
  if not (("xcb" in qpa) if qpa else not os.environ.get("WAYLAND_DISPLAY")):
    return
  if os.environ.get("QT_SCALE_FACTOR") or os.environ.get("HAYATE_NO_AUTOSCALE"):
    return
  dpi = None
  try:
    import ctypes, ctypes.util
    x = ctypes.CDLL(ctypes.util.find_library("X11"))
    x.XOpenDisplay.restype = ctypes.c_void_p
    x.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x.XGetDefault.restype = ctypes.c_char_p
    x.XGetDefault.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
    x.XCloseDisplay.argtypes = [ctypes.c_void_p]
    d = x.XOpenDisplay(None)
    if d:
      v = x.XGetDefault(d, b"Xft", b"dpi")
      dpi = float(v) if v else None
      x.XCloseDisplay(d)
  except Exception:
    pass
  if not dpi:
    try:
      import subprocess
      out = subprocess.run(["xrdb", "-query"], capture_output=True, text=True, timeout=2).stdout
      for line in out.splitlines():
        if line.lower().startswith("xft.dpi"):
          dpi = float(line.split(":", 1)[1])
    except Exception:
      pass
  if not dpi:
    return
  f = min(max(dpi / 96.0, 0.5), 3.0)
  if abs(f - 1) < 0.02:
    return
  os.environ["QT_SCALE_FACTOR"] = "%g" % round(f, 3)
  os.environ["QT_FONT_DPI"] = "96"
  os.environ["QT_SCALE_FACTOR_ROUNDING_POLICY"] = "PassThrough"
  os.environ["HAYATE_SCALED"] = "1"


def main():
  # Qt picks its own platform (native Wayland, or xcb on X11). Set QT_QPA_PLATFORM=xcb to force X11.
  _autoscale()
  from PyQt6.QtCore import Qt as _Qt, QCoreApplication as _QCA
  _QCA.setAttribute(_Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
  app = QApplication(sys.argv)

  from hayate.core.theme import theme
  from hayate.ui.autohide_scrollbar import install as install_autohide_scrollbar, BASE_QSS as SCROLLBAR_QSS
  def _apply_qss():
    app.setStyleSheet(theme.qss() + SCROLLBAR_QSS)
  _apply_qss()
  theme.changed.connect(_apply_qss)
  install_autohide_scrollbar(app)
  icon_file = Path(__file__).resolve().parents[2] / 'app_icon.png'
  if icon_file.exists():
      app.setWindowIcon(QIcon(str(icon_file)))
  elif not QIcon.fromTheme("hayate").isNull():
      app.setWindowIcon(QIcon.fromTheme("hayate"))

  # Neutral style base prevents theme engine border artifacts on KDE
  app.setStyle("Fusion")
  app.setApplicationName("hayate")
  app.setApplicationDisplayName("Hayate")

  window = MainWindow()
  window.show()
  sys.exit(app.exec())

if __name__ == "__main__":
  main()
