"""VRGB Suite: system."""

import subprocess
from pathlib import Path


# ----------------------------------------------------------------------------
# Firmware keyboard backlight (FN+F4 / FN+F3) via logind
# ----------------------------------------------------------------------------

class KbdBacklight:
    """Reads/writes /sys/class/leds/asus::kbd_backlight.

    Reads come straight from sysfs (world-readable). Writes go through logind's
    SetBrightness, which Polkit allows for the active local session without a
    password. Returns gracefully degraded values when the node is absent.
    """

    PATH = Path("/sys/class/leds/asus::kbd_backlight")
    LED_NAME = "asus::kbd_backlight"

    def __init__(self):
        self.available = self.PATH.exists()
        self.max = (self._read_int("max_brightness") or 0) if self.available else 0
        if self.max <= 0:
            self.available = False

    def _read_int(self, name):
        try:
            return int((self.PATH / name).read_text().strip())
        except (OSError, ValueError):
            return None

    def level(self):
        return self._read_int("brightness")

    @staticmethod
    def set_level(level):
        try:
            subprocess.run(
                ["busctl", "call", "org.freedesktop.login1",
                 "/org/freedesktop/login1/session/auto",
                 "org.freedesktop.login1.Session", "SetBrightness", "ssu",
                 "leds", KbdBacklight.LED_NAME, str(int(level))],
                check=True, capture_output=True, text=True, timeout=10,
            )
            return True
        except Exception:
            return False


# ----------------------------------------------------------------------------
# Login autostart (~/.config/autostart) — user-managed, no root needed
# ----------------------------------------------------------------------------

class Autostart:
    """Create/remove the per-user XDG autostart .desktop entries.

    Two alternative entries:
      * 'tray'    -> start the Suite in the tray (`vrgb-gui --tray`); at session
                     start it restores the lighting itself (or keeps it off by
                     day) and runs the automation
      * 'restore' -> Core only: reapply the saved lighting (`vrgb restore`)
    """

    DIR = Path.home() / ".config" / "autostart"
    # Exec uses bare command names (resolved via PATH) so entries keep working
    # whether vrgb was installed by a package (/usr/bin) or ./install.sh.
    ENTRIES = {
        "restore": {
            "file": "vrgb.desktop",
            "name": "VRGB Restore",
            "comment": "Restore keyboard RGB state on login",
            "exec": "vrgb restore",
            "icon": "vrgb",
        },
        "tray": {
            "file": "vrgb-gui.desktop",
            "name": "VRGB (tray)",
            "comment": "Keyboard RGB tray: restores lighting at login, automatic off",
            "exec": "vrgb-gui --tray",
            "icon": "vrgb",
        },
    }

    @classmethod
    def path(cls, key):
        return cls.DIR / cls.ENTRIES[key]["file"]

    @classmethod
    def is_enabled(cls, key):
        p = cls.path(key)
        if not p.exists():
            return False
        try:
            low = p.read_text(errors="ignore").lower()
        except OSError:
            return False
        if "hidden=true" in low:
            return False
        if "x-gnome-autostart-enabled=false" in low:
            return False
        return True

    @classmethod
    def set_enabled(cls, key, enabled):
        p = cls.path(key)
        if enabled:
            e = cls.ENTRIES[key]
            cls.DIR.mkdir(parents=True, exist_ok=True)
            p.write_text(
                "[Desktop Entry]\n"
                "Type=Application\n"
                f"Name={e['name']}\n"
                f"Comment={e['comment']}\n"
                f"Exec={e['exec']}\n"
                f"Icon={e['icon']}\n"
                "Terminal=false\n"
                "X-GNOME-Autostart-enabled=true\n"
            )
        else:
            try:
                p.unlink()
            except FileNotFoundError:
                pass

    @classmethod
    def migrate(cls):
        """Fix entries written by older versions: an Exec pointing at a binary
        that no longer exists (/usr/local/bin after switching to a package), or
        the fork-only `vrgb startup` command, which the tray now covers."""
        if cls.is_enabled("restore"):
            try:
                text = cls.path("restore").read_text(errors="ignore")
            except OSError:
                text = ""
            if "vrgb startup" in text:
                try:
                    cls.set_enabled("restore", not cls.is_enabled("tray"))
                except OSError:
                    pass
        for key in cls.ENTRIES:
            if not cls.is_enabled(key):
                continue
            try:
                text = cls.path(key).read_text(errors="ignore")
            except OSError:
                continue
            for line in text.splitlines():
                if line.startswith("Exec="):
                    cmd = line[5:].split()[0] if line[5:].split() else ""
                    if cmd.startswith("/") and not Path(cmd).exists():
                        try:
                            cls.set_enabled(key, True)
                        except OSError:
                            pass
                    break


