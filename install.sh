#!/usr/bin/env bash

set -e

# Run installer from its own directory (prevents path issues)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

SUITE_LIB=/usr/local/lib/vrgb-suite

echo "VRGB Installer (v0.3.5)"
echo "---------------"

# Ensure script exists
if [ ! -f "$SCRIPT_DIR/vrgb.py" ]; then
    echo "Error: vrgb.py not found"
    exit 1
fi

# What to install: `./install.sh core|suite`, or ask.
EDITION="$1"
if [[ "$EDITION" != "core" && "$EDITION" != "suite" ]]; then
    echo "  1) VRGB Core  - the vrgb command line tool only (no dependencies)"
    echo "  2) VRGB Suite - Core + GUI and tray (PyQt6): idle auto-off, daytime-off"
    read -p "Install Core or Suite? [1/2]: " CHOICE
    [[ "$CHOICE" == "2" ]] && EDITION=suite || EDITION=core
fi

if [[ "$EDITION" == "suite" ]] && ! python3 -c "import PyQt6.QtWidgets" 2>/dev/null; then
    echo "VRGB Suite needs PyQt6. Install it with one of:"
    echo "    sudo dnf install python3-pyqt6        # Fedora"
    echo "    sudo apt install python3-pyqt6        # Debian / Ubuntu"
    echo "    sudo pacman -S python-pyqt6           # Arch"
    echo "Then re-run this script (or choose Core)."
    exit 1
fi

echo "[1/5] Installing binary..."

sudo install -m 755 vrgb.py /usr/local/bin/vrgb

echo "[2/5] Creating vrgb group (if needed)..."

sudo groupadd -f vrgb

echo "[3/5] Adding user to vrgb group..."

sudo usermod -aG vrgb "$USER"

echo "[4/5] Installing udev rule..."

# 70- so the uaccess tag is applied (it must sort before 73-seat-late.rules)
sudo install -m 644 packaging/70-vrgb.rules /etc/udev/rules.d/70-vrgb.rules
sudo rm -f /etc/udev/rules.d/99-vrgb.rules   # rule name used before v0.4

echo "[5/5] Reloading udev rules..."

sudo udevadm control --reload-rules
sudo udevadm trigger

if [[ "$EDITION" == "suite" ]]; then
    echo
    echo "[Suite 1/3] Installing the GUI to $SUITE_LIB ..."
    sudo rm -rf "$SUITE_LIB/vrgb_suite"
    sudo install -d "$SUITE_LIB/vrgb_suite"
    sudo install -m 644 suite/vrgb_suite/*.py "$SUITE_LIB/vrgb_suite/"
    sudo tee /usr/local/bin/vrgb-gui > /dev/null <<EOF
#!/usr/bin/env python3
import sys
sys.path.insert(0, "$SUITE_LIB")
from vrgb_suite.app import main
sys.exit(main())
EOF
    sudo chmod 755 /usr/local/bin/vrgb-gui

    echo "[Suite 2/3] Installing launcher and icon ..."
    sudo install -m 644 suite/data/vrgb-gui.desktop /usr/share/applications/vrgb-gui.desktop
    sudo install -m 644 assets/vrgblogodark.png /usr/share/pixmaps/vrgb.png
    sudo update-desktop-database /usr/share/applications 2>/dev/null || true

    echo "[Suite 3/3] Installing the systemd user unit (optional autostart) ..."
    sudo install -Dm644 suite/data/vrgb-gui.service /usr/local/lib/systemd/user/vrgb-gui.service
fi

echo
if [[ "$EDITION" == "suite" ]]; then
    read -p "Start VRGB in the tray at login (restores lighting, automatic off)? (y/n): " AUTOSTART
    if [[ "$AUTOSTART" == "y" || "$AUTOSTART" == "Y" ]]; then
        mkdir -p ~/.config/autostart
        rm -f ~/.config/autostart/vrgb.desktop   # the tray restores the lighting itself
        cat <<EOF > ~/.config/autostart/vrgb-gui.desktop
[Desktop Entry]
Type=Application
Name=VRGB (tray)
Comment=Keyboard RGB tray: restores lighting at login, automatic off
Exec=vrgb-gui --tray
Icon=vrgb
Terminal=false
X-GNOME-Autostart-enabled=true
EOF
        echo "Autostart installed. (No XDG autostart on your desktop? See README: vrgb-gui.service)"
    fi
else
read -p "Install autostart restore? (y/n): " AUTOSTART

if [[ "$AUTOSTART" == "y" || "$AUTOSTART" == "Y" ]]; then

mkdir -p ~/.config/autostart

cat <<EOF > ~/.config/autostart/vrgb.desktop
[Desktop Entry]
Type=Application
Exec=/usr/local/bin/vrgb restore
Hidden=false
NoDisplay=false
X-GNOME-Autostart-enabled=true
Name=VRGB Restore
Comment=Restore keyboard RGB state
EOF

echo "Autostart installed."

fi
fi

echo
read -p "Install systemd user autostart restore (works on any desktop environment)? (y/n): " SYSTEMD_AUTOSTART

if [[ "$SYSTEMD_AUTOSTART" == "y" || "$SYSTEMD_AUTOSTART" == "Y" ]]; then

mkdir -p ~/.config/systemd/user

install -m 644 "$SCRIPT_DIR/systemd/vrgb-restore.service" ~/.config/systemd/user/vrgb-restore.service

systemctl --user daemon-reload
systemctl --user enable vrgb-restore.service

echo "systemd autostart installed and enabled."
echo "It will start restoring your saved state from your next login onward."

fi

echo
echo "Installation complete."
echo
echo "The logged-in user can control the keyboard right away (udev uaccess)."
echo "Group membership (for other sessions) applies after the next login."
[[ "$EDITION" == "suite" ]] && echo "Launch the GUI from your app menu (search 'VRGB') or run: vrgb-gui"
exit 0
