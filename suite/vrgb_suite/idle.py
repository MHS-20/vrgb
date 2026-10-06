"""VRGB Suite: idle."""

import ctypes
import os
import socket
import struct

from PyQt6.QtCore import QTimer, QObject, QSocketNotifier, pyqtSignal, pyqtSlot, QMetaType
from PyQt6.QtDBus import QDBusConnection, QDBusInterface, QDBusMessage, QDBusArgument


# ----------------------------------------------------------------------------
# Session idle detection — one backend per desktop family, picked at startup
# ----------------------------------------------------------------------------
#
# Every backend emits `idle` once the user has been inactive for the timeout and
# `active` on the next input after that. Order of preference:
#   1. GNOME        — Mutter IdleMonitor D-Bus watches (event-driven)
#   2. Wayland      — ext-idle-notify-v1 (KWin/Plasma, sway, Hyprland, labwc,
#                     wayfire, niri, COSMIC…), event-driven, own tiny client
#   3. X11          — MIT-SCREEN-SAVER via libXss; sleeps until the timeout could
#                     have elapsed, polls (2 Hz) only while dimmed

class IdleBackend(QObject):
    idle = pyqtSignal()
    active = pyqtSignal()
    name = "none"
    available = False

    def set_timeout(self, ms):
        """(Re)arm idle detection; 0 disables it."""

    def watch_active(self):
        """Called after `idle`; backends that need it arm a one-shot input watch."""


class MutterIdle(IdleBackend):
    name = "GNOME (Mutter IdleMonitor)"

    SERVICE = "org.gnome.Mutter.IdleMonitor"
    PATH = "/org/gnome/Mutter/IdleMonitor/Core"
    IFACE = "org.gnome.Mutter.IdleMonitor"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._bus = QDBusConnection.sessionBus()
        self._iface = QDBusInterface(self.SERVICE, self.PATH, self.IFACE, self._bus)
        self.available = self._iface.isValid() and self._bus.connect(
            self.SERVICE, self.PATH, self.IFACE, "WatchFired", self._on_fired)
        self._timeout_ms = 0
        self._idle_id = None
        self._active_id = None

    def _call_id(self, method, *args):
        reply = self._iface.call(method, *args)
        if reply.type() != QDBusMessage.MessageType.ReplyMessage or not reply.arguments():
            return None
        return int(reply.arguments()[0])

    def _remove(self, watch_id):
        if watch_id is not None:
            self._iface.call("RemoveWatch", QDBusArgument(watch_id, QMetaType.Type.UInt.value))

    def set_timeout(self, ms):
        if not self.available or ms == self._timeout_ms:
            return
        self._timeout_ms = ms
        self._remove(self._idle_id)
        self._idle_id = None
        if ms > 0:
            self._idle_id = self._call_id(
                "AddIdleWatch", QDBusArgument(ms, QMetaType.Type.ULongLong.value))

    def watch_active(self):
        if self.available and self._active_id is None:
            self._active_id = self._call_id("AddUserActiveWatch")

    @pyqtSlot(QDBusMessage)
    def _on_fired(self, msg):
        args = msg.arguments()
        watch_id = int(args[0]) if args else None
        if watch_id is not None and watch_id == self._idle_id:
            self.idle.emit()
        elif watch_id is not None and watch_id == self._active_id:
            self._active_id = None       # user-active watches are one-shot
            self.active.emit()


class WaylandIdle(IdleBackend):
    """Minimal Wayland client speaking just enough protocol for ext-idle-notify-v1.

    It opens its own connection to the compositor (Qt's connection is not exposed
    to Python), binds wl_seat + ext_idle_notifier_v1 and creates one idle
    notification; the compositor then sends `idled` / `resumed` events.
    """

    name = "Wayland (ext-idle-notify-v1)"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sock = None
        self._buf = b""
        self._next_id = 2               # 1 is wl_display
        self._globals = {}              # interface -> (name, version)
        self._handlers = {}             # object id -> callable(opcode, payload)
        self._seat = self._notifier = self._notifier_ver = None
        self._notification = None
        self._timeout_ms = 0
        self._notifier_qt = None
        try:
            self._connect()
        except OSError:
            self._close()

    # -- wire format helpers --
    def _new_id(self):
        oid = self._next_id
        self._next_id += 1
        return oid

    @staticmethod
    def _u32(v):
        return struct.pack("=I", v)

    @staticmethod
    def _str(s):
        b = s.encode() + b"\0"
        return struct.pack("=I", len(b)) + b + b"\0" * (-len(b) % 4)

    def _send(self, obj, opcode, payload=b""):
        size = 8 + len(payload)
        self._sock.sendall(struct.pack("=II", obj, (size << 16) | opcode) + payload)

    @staticmethod
    def _read_str(payload, off):
        (n,) = struct.unpack_from("=I", payload, off)
        s = payload[off + 4:off + 4 + n - 1].decode(errors="replace")
        return s, off + 4 + n + (-n % 4)

    # -- connection --
    def _connect(self):
        disp = os.environ.get("WAYLAND_DISPLAY")
        if not disp:
            return
        path = disp if disp.startswith("/") else os.path.join(
            os.environ.get("XDG_RUNTIME_DIR", ""), disp)
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
        self._sock.settimeout(2.0)
        self._sock.connect(path)

        self._handlers[1] = self._on_display
        registry = self._new_id()
        self._handlers[registry] = self._on_registry
        self._registry = registry
        self._send(1, 1, self._u32(registry))            # wl_display.get_registry
        done = self._new_id()
        finished = []
        self._handlers[done] = lambda op, p: finished.append(True)
        self._send(1, 0, self._u32(done))                # wl_display.sync
        while not finished and self._sock is not None:   # one blocking roundtrip
            self._pump(self._sock.recv(65536))
        if self._sock is None or "ext_idle_notifier_v1" not in self._globals \
                or "wl_seat" not in self._globals:
            self._close()
            return

        name, ver = self._globals["wl_seat"]
        self._seat = self._bind(name, "wl_seat", 1)
        name, ver = self._globals["ext_idle_notifier_v1"]
        self._notifier_ver = min(ver, 2)
        self._notifier = self._bind(name, "ext_idle_notifier_v1", self._notifier_ver)

        self._sock.setblocking(False)
        self._notifier_qt = QSocketNotifier(self._sock.fileno(), QSocketNotifier.Type.Read, self)
        self._notifier_qt.activated.connect(self._on_readable)
        self.available = True

    def _bind(self, name, interface, version):
        oid = self._new_id()
        self._handlers[oid] = lambda op, p: None          # ignore seat events
        self._send(self._registry, 0, self._u32(name) + self._str(interface)
                   + self._u32(version) + self._u32(oid))
        return oid

    def _close(self):
        if self._notifier_qt is not None:
            self._notifier_qt.setEnabled(False)
            self._notifier_qt = None
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
        self._sock = None
        self.available = False

    def _on_readable(self, *_args):
        try:
            data = self._sock.recv(65536)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:                                       # compositor went away
            self._close()
            return
        self._pump(data)

    def _pump(self, data):
        if not data:
            self._close()
            return
        self._buf += data
        while len(self._buf) >= 8:
            obj, word = struct.unpack_from("=II", self._buf)
            size, opcode = word >> 16, word & 0xFFFF
            if size < 8 or len(self._buf) < size:
                break
            payload, self._buf = self._buf[8:size], self._buf[size:]
            handler = self._handlers.get(obj)
            if handler is not None:
                handler(opcode, payload)

    # -- event handlers --
    def _on_display(self, opcode, payload):
        if opcode == 0:                                    # wl_display.error
            self._close()

    def _on_registry(self, opcode, payload):
        if opcode == 0:                                    # wl_registry.global
            (name,) = struct.unpack_from("=I", payload)
            iface, off = self._read_str(payload, 4)
            (ver,) = struct.unpack_from("=I", payload, off)
            self._globals.setdefault(iface, (name, ver))

    def _on_notification(self, opcode, payload):
        if opcode == 0:
            self.idle.emit()                               # idled
        elif opcode == 1:
            self.active.emit()                             # resumed

    # -- API --
    def set_timeout(self, ms):
        if not self.available or ms == self._timeout_ms:
            return
        self._timeout_ms = ms
        try:
            if self._notification is not None:
                self._handlers.pop(self._notification, None)
                self._send(self._notification, 0)          # destroy
                self._notification = None
            if ms > 0:
                oid = self._new_id()
                self._handlers[oid] = self._on_notification
                # v2 get_input_idle_notification ignores idle inhibitors (video
                # players), which is what a keyboard backlight wants.
                opcode = 2 if self._notifier_ver >= 2 else 1
                self._send(self._notifier, opcode,
                           self._u32(oid) + self._u32(ms) + self._u32(self._seat))
                self._notification = oid
        except OSError:
            self._close()


class _XScreenSaverInfo(ctypes.Structure):
    _fields_ = [("window", ctypes.c_ulong), ("state", ctypes.c_int),
                ("kind", ctypes.c_int), ("til_or_since", ctypes.c_ulong),
                ("idle", ctypes.c_ulong), ("eventMask", ctypes.c_ulong)]


class X11Idle(IdleBackend):
    name = "X11 (XScreenSaver)"

    ACTIVE_POLL_MS = 500

    def __init__(self, parent=None):
        super().__init__(parent)
        self._timeout_ms = 0
        self._dimmed = False
        self._last_idle = 0
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._check)
        try:
            self._x11 = ctypes.CDLL("libX11.so.6")
            self._xss = ctypes.CDLL("libXss.so.1")
        except OSError:
            return
        self._x11.XOpenDisplay.restype = ctypes.c_void_p
        self._x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        self._x11.XDefaultRootWindow.restype = ctypes.c_ulong
        self._x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
        self._xss.XScreenSaverAllocInfo.restype = ctypes.POINTER(_XScreenSaverInfo)
        self._xss.XScreenSaverQueryInfo.argtypes = [
            ctypes.c_void_p, ctypes.c_ulong, ctypes.POINTER(_XScreenSaverInfo)]
        self._xss.XScreenSaverQueryExtension.argtypes = [
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int)]
        self._dpy = self._x11.XOpenDisplay(None)
        if not self._dpy:
            return
        ev, err = ctypes.c_int(), ctypes.c_int()
        if not self._xss.XScreenSaverQueryExtension(self._dpy, ctypes.byref(ev), ctypes.byref(err)):
            return
        self._root = self._x11.XDefaultRootWindow(self._dpy)
        self._info = self._xss.XScreenSaverAllocInfo()
        self.available = bool(self._info)

    def _idle_ms(self):
        self._xss.XScreenSaverQueryInfo(self._dpy, self._root, self._info)
        return int(self._info.contents.idle)

    def set_timeout(self, ms):
        if not self.available or ms == self._timeout_ms:
            return
        self._timeout_ms = ms
        self._timer.stop()
        if ms > 0:
            self._check()
        elif self._dimmed:
            self._dimmed = False

    def _check(self):
        if self._timeout_ms <= 0:
            return
        idle = self._idle_ms()
        if self._dimmed:
            if idle < self._last_idle:                     # input since last look
                self._dimmed = False
                self.active.emit()
                self._timer.start(self._timeout_ms)
            else:
                self._last_idle = idle
                self._timer.start(self.ACTIVE_POLL_MS)
            return
        if idle >= self._timeout_ms:
            self._dimmed = True
            self._last_idle = idle
            self.idle.emit()
            self._timer.start(self.ACTIVE_POLL_MS)
        else:                                              # sleep until it could fire
            self._timer.start(self._timeout_ms - idle + 50)


def make_idle_backend(parent=None):
    """First working backend for this session (GNOME, then Wayland, then X11)."""
    session = os.environ.get("XDG_SESSION_TYPE", "")
    candidates = [MutterIdle, WaylandIdle]
    # On Wayland, $DISPLAY is XWayland and would only see X clients' input.
    if session == "x11" or not os.environ.get("WAYLAND_DISPLAY"):
        candidates.append(X11Idle)
    for cls in candidates:
        backend = cls(parent)
        if backend.available:
            return backend
        backend.deleteLater()
    return IdleBackend(parent)


