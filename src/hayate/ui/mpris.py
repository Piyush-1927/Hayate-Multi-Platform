import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib
from PyQt6.QtCore import QObject

PATH = "/org/mpris/MediaPlayer2"
IF_ROOT = "org.mpris.MediaPlayer2"
IF_PLAYER = "org.mpris.MediaPlayer2.Player"
IF_PROPS = "org.freedesktop.DBus.Properties"

_XML = """<node>
<interface name="org.mpris.MediaPlayer2">
<method name="Raise"/><method name="Quit"/>
<property name="CanQuit" type="b" access="read"/>
<property name="CanRaise" type="b" access="read"/>
<property name="HasTrackList" type="b" access="read"/>
<property name="Identity" type="s" access="read"/>
<property name="DesktopEntry" type="s" access="read"/>
<property name="SupportedUriSchemes" type="as" access="read"/>
<property name="SupportedMimeTypes" type="as" access="read"/>
</interface>
<interface name="org.mpris.MediaPlayer2.Player">
<method name="Next"/><method name="Previous"/><method name="Pause"/>
<method name="PlayPause"/><method name="Stop"/><method name="Play"/>
<method name="Seek"><arg type="x" direction="in"/></method>
<method name="SetPosition"><arg type="o" direction="in"/><arg type="x" direction="in"/></method>
<method name="OpenUri"><arg type="s" direction="in"/></method>
<property name="PlaybackStatus" type="s" access="read"/>
<property name="LoopStatus" type="s" access="read"/>
<property name="Rate" type="d" access="read"/>
<property name="Shuffle" type="b" access="read"/>
<property name="Metadata" type="a{sv}" access="read"/>
<property name="Volume" type="d" access="readwrite"/>
<property name="Position" type="x" access="read"/>
<property name="MinimumRate" type="d" access="read"/>
<property name="MaximumRate" type="d" access="read"/>
<property name="CanGoNext" type="b" access="read"/>
<property name="CanGoPrevious" type="b" access="read"/>
<property name="CanPlay" type="b" access="read"/>
<property name="CanPause" type="b" access="read"/>
<property name="CanSeek" type="b" access="read"/>
<property name="CanControl" type="b" access="read"/>
</interface>
</node>"""

_METHODS = {"Raise": "raise", "Play": "play", "Pause": "pause", "PlayPause": "toggle",
            "Stop": "stop", "Next": "next", "Previous": "prev"}
V = GLib.Variant


class Mpris(QObject):
    """callbacks: dict name -> fn. names: play pause toggle stop next prev raise
    seek_rel(us) seek_abs(us) volume(0..1).  position_fn() -> microseconds."""

    def __init__(self, callbacks, position_fn=lambda: 0, name="hayate", parent=None):
        super().__init__(parent)
        self._cbs, self._pos_fn = callbacks, position_fn
        self.status, self.title, self.length_us, self.volume = "Stopped", "", 0, 1.0
        self.can_next = self.can_prev = False
        self.ok = False
        self._regs, self._owner, self._conn = [], 0, None
        try:
            self._conn = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            node = Gio.DBusNodeInfo.new_for_xml(_XML)
            for iface in (IF_ROOT, IF_PLAYER):
                self._regs.append(self._conn.register_object(
                    PATH, node.lookup_interface(iface),
                    self._on_call, self._on_get, self._on_set))
            self._owner = Gio.bus_own_name_on_connection(
                self._conn, f"org.mpris.MediaPlayer2.{name}", Gio.BusNameOwnerFlags.NONE, None, None)
            self.ok = True
        except Exception as e:
            print("MPRIS registration failed:", e, flush=True)

    def shutdown(self):
        if not self.ok:
            return
        self.ok = False
        try:
            for r in self._regs:
                self._conn.unregister_object(r)
            Gio.bus_unown_name(self._owner)
        except Exception as e:
            print("MPRIS shutdown error:", e, flush=True)

    def cb(self, name, *a):
        fn = self._cbs.get(name)
        if fn:
            try:
                fn(*a)
            except Exception as e:
                print("MPRIS callback error:", name, e, flush=True)

    def position(self):
        try:
            return int(self._pos_fn())
        except Exception:
            return 0

    def _meta(self):
        d = {"mpris:trackid": V("o", PATH + "/track/0")}
        if self.title:
            d["xesam:title"] = V("s", self.title)
        if self.length_us:
            d["mpris:length"] = V("x", int(self.length_us))
        return V("a{sv}", d)

    def _on_call(self, conn, sender, path, iface, method, params, inv):
        try:
            if method in _METHODS:
                self.cb(_METHODS[method])
            elif method == "Seek":
                self.cb("seek_rel", int(params.unpack()[0]))
            elif method == "SetPosition":
                self.cb("seek_abs", int(params.unpack()[1]))
            inv.return_value(None)
        except Exception as e:
            print("MPRIS call error:", e, flush=True)
            inv.return_dbus_error("org.freedesktop.DBus.Error.Failed", str(e))

    def _on_get(self, conn, sender, path, iface, prop):
        if iface == IF_ROOT:
            return {"CanQuit": V("b", False), "CanRaise": V("b", True),
                    "HasTrackList": V("b", False), "Identity": V("s", "Hayate"),
                    "DesktopEntry": V("s", "hayate"),
                    "SupportedUriSchemes": V("as", ["http", "https", "file"]),
                    "SupportedMimeTypes": V("as", ["video/mp4", "video/x-matroska", "video/webm"])}.get(prop)
        return {"PlaybackStatus": lambda: V("s", self.status), "LoopStatus": lambda: V("s", "None"),
                "Rate": lambda: V("d", 1.0), "Shuffle": lambda: V("b", False),
                "Metadata": self._meta, "Volume": lambda: V("d", float(self.volume)),
                "Position": lambda: V("x", self.position()),
                "MinimumRate": lambda: V("d", 1.0), "MaximumRate": lambda: V("d", 1.0),
                "CanGoNext": lambda: V("b", self.can_next), "CanGoPrevious": lambda: V("b", self.can_prev),
                "CanPlay": lambda: V("b", True), "CanPause": lambda: V("b", True),
                "CanSeek": lambda: V("b", True), "CanControl": lambda: V("b", True)}[prop]()

    def _on_set(self, conn, sender, path, iface, prop, value):
        if iface == IF_PLAYER and prop == "Volume":
            self.cb("volume", max(0.0, float(value.unpack())))
            return True
        return False

    def update(self, status=None, title=None, length_us=None, volume=None, can_next=None, can_prev=None):
        ch = {}
        if status is not None and status != self.status:
            self.status = status
            ch["PlaybackStatus"] = V("s", status)
        if title is not None and title != self.title:
            self.title = title
        if length_us is not None and length_us != self.length_us:
            self.length_us = int(length_us)
        if title is not None or length_us is not None:
            ch["Metadata"] = self._meta()
        if volume is not None and abs(volume - self.volume) > 1e-3:
            self.volume = volume
            ch["Volume"] = V("d", float(volume))
        if can_next is not None and can_next != self.can_next:
            self.can_next = can_next
            ch["CanGoNext"] = V("b", bool(can_next))
        if can_prev is not None and can_prev != self.can_prev:
            self.can_prev = can_prev
            ch["CanGoPrevious"] = V("b", bool(can_prev))
        if ch and self.ok:
            try:
                self._conn.emit_signal(None, PATH, IF_PROPS, "PropertiesChanged",
                                       V("(sa{sv}as)", (IF_PLAYER, ch, [])))
            except Exception as e:
                print("MPRIS signal error:", e, flush=True)
