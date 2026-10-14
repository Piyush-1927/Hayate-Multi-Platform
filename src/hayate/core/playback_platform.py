"""Platform detection for native libmpv video embedding."""

from dataclasses import dataclass
import os
import sys

from PyQt6.QtGui import QGuiApplication


@dataclass(frozen=True)
class PlaybackPlatform:
    os_name: str
    qt_platform: str
    embedding: str
    supported: bool
    reason: str = ""


def detect_playback_platform() -> PlaybackPlatform:
    """Detect the native embedding backend once the Qt application exists."""
    os_name = (
        "windows" if sys.platform.startswith("win")
        else "macos" if sys.platform == "darwin"
        else "linux" if sys.platform.startswith("linux")
        else "unknown"
    )

    app = QGuiApplication.instance()
    qt_platform = (
        app.platformName().lower()
        if app is not None
        else os.environ.get("QT_QPA_PLATFORM", "").lower()
    )

    if os_name == "windows":
        return PlaybackPlatform(
            os_name, qt_platform, "hwnd", True
        )

    if os_name == "macos":
        return PlaybackPlatform(
            os_name,
            qt_platform,
            "nsview",
            False,
            "Native NSView embedding is not implemented or verified yet.",
        )

    if os_name == "linux":
        if "wayland" in qt_platform:
            return PlaybackPlatform(
                os_name,
                qt_platform,
                "unsupported",
                False,
                "Native embedding is not supported through this Wayland path. "
                "Use an X11/XWayland Qt session.",
            )

        if qt_platform in ("xcb", "x11"):
            return PlaybackPlatform(
                os_name, qt_platform, "x11", True
            )

        if os.environ.get("WAYLAND_DISPLAY") and not os.environ.get("DISPLAY"):
            return PlaybackPlatform(
                os_name,
                qt_platform,
                "unsupported",
                False,
                "No X11 display is available for native libmpv embedding. "
                "Use an X11/XWayland Qt session.",
            )

        return PlaybackPlatform(
            os_name,
            qt_platform,
            "unsupported",
            False,
            "Could not confirm an X11 Qt backend. Native embedding was not attempted.",
        )

    return PlaybackPlatform(
        os_name,
        qt_platform,
        "unsupported",
        False,
        f"Unsupported operating system: {sys.platform}",
    )
