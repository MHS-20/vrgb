#!/usr/bin/env bash

set -e

# Run installer from its own directory (prevents path issues)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "VRGB Installer (v0.3.5)"
echo "---------------"

# Ensure script exists
if [ ! -f "$SCRIPT_DIR/vrgb.py" ]; then
    echo "Error: vrgb.py not found"
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

echo
read -p "Install KDE autostart restore? (y/n): " AUTOSTART

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

echo
echo "Installation complete."
echo
echo "The logged-in user can control the keyboard right away (udev uaccess)."
echo "Group membership (for other sessions) applies after the next login."
