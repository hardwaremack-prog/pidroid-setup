#!/bin/bash
# Installs PiDroid Setup into your menu and onto your desktop.
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.local/share/pidroid"

echo "Making sure GTK for Python is present..."
python3 -c 'import gi; gi.require_version("Gtk","3.0")' 2>/dev/null || \
  { sudo -n apt-get install -y python3-gi gir1.2-gtk-3.0 || pkexec apt-get install -y python3-gi gir1.2-gtk-3.0; }

mkdir -p "$DEST" "$HOME/.local/share/applications" "$HOME/Desktop"
cp "$DIR/pidroid.py" "$DEST/"
[ -f "$DIR/pidroid.svg" ] && cp "$DIR/pidroid.svg" "$DEST/"
chmod +x "$DEST/pidroid.py"

cat > "$HOME/.local/share/applications/pidroid.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=PiDroid Setup
Comment=Set up Waydroid and Roblox
Exec=python3 $DEST/pidroid.py
Icon=$( [ -f "$DEST/pidroid.svg" ] && echo "$DEST/pidroid.svg" || echo phone )
Terminal=false
Categories=System;Settings;
EOF
cp "$HOME/.local/share/applications/pidroid.desktop" "$HOME/Desktop/"
chmod +x "$HOME/Desktop/pidroid.desktop"

echo "Done. Find 'PiDroid Setup' in the menu (Preferences/System) or on your desktop."
echo "Starting it now..."
nohup python3 "$DEST/pidroid.py" >/dev/null 2>&1 &
