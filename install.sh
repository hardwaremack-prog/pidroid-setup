#!/bin/bash
# PiDroid Setup installer.
# Double-click it (choose Execute), type your password once, and it does the rest.

SELF="$(readlink -f "$0")"
DIR="$(dirname "$SELF")"

# Double-clicked? Open a terminal window so you can see it work and type your password.
if [ ! -t 0 ] && [ -z "$PIDROID_IN_TERM" ]; then
  export PIDROID_IN_TERM=1
  for t in lxterminal x-terminal-emulator foot xfce4-terminal gnome-terminal konsole xterm; do
    command -v "$t" >/dev/null || continue
    case "$t" in
      gnome-terminal) exec gnome-terminal --title="Install PiDroid Setup" -- bash "$SELF" ;;
      lxterminal)     exec lxterminal -t "Install PiDroid Setup" -e bash "$SELF" ;;
      foot)           exec foot -T "Install PiDroid Setup" bash "$SELF" ;;
      *)              exec "$t" -e bash "$SELF" ;;
    esac
  done
  # no terminal program at all: carry on and use the password pop-up instead
  NO_TERM=1
fi

# Started with "sudo"? Run as the normal user so the icons land on *your* desktop.
if [ "$(id -u)" = 0 ] && [ -n "$SUDO_USER" ] && [ "$SUDO_USER" != root ]; then
  exec sudo -u "$SUDO_USER" env PIDROID_IN_TERM="$PIDROID_IN_TERM" bash "$SELF"
fi

finish() {
  echo
  if [ -n "$PIDROID_IN_TERM" ] && [ -z "$NO_TERM" ]; then
    read -rp "Press Enter to close this window... " _
  fi
  exit "$1"
}

green() { printf '\033[1;32m%s\033[0m\n' "$*"; }
red()   { printf '\033[1;31m%s\033[0m\n' "$*"; }

clear 2>/dev/null
echo "=============================================="
echo "        PiDroid Setup - installer"
echo "   Waydroid + Roblox for Raspberry Pi 5 / 500"
echo "=============================================="
echo

# ---- 1. admin password (asked once) -----------------------------------------
if [ -n "$NO_TERM" ]; then
  AS_ROOT="pkexec"
else
  echo "Type your password when asked (the one you use to log in to the Pi)."
  echo "Nothing shows on screen while you type - that's normal. Then press Enter."
  echo
  if ! sudo -v; then
    red "The password wasn't accepted, so nothing was installed."
    finish 1
  fi
  # keep the password fresh while we work
  ( while sleep 50; do sudo -n true 2>/dev/null || exit; done ) &
  KEEP=$!
  trap 'kill $KEEP 2>/dev/null' EXIT
  AS_ROOT="sudo"
  green "Password OK."
fi
echo

# ---- 2. things the app needs ------------------------------------------------
NEED=""
python3 -c 'import gi; gi.require_version("Gtk","3.0"); from gi.repository import Gtk' 2>/dev/null \
  || NEED="$NEED python3-gi gir1.2-gtk-3.0"
command -v curl >/dev/null || NEED="$NEED curl ca-certificates"
command -v xdg-open >/dev/null || NEED="$NEED xdg-utils"

if [ -n "$NEED" ]; then
  echo "Installing what PiDroid Setup needs:$NEED"
  if ! $AS_ROOT apt-get install -y $NEED; then
    echo "Updating the package list and trying again..."
    $AS_ROOT apt-get update && $AS_ROOT apt-get install -y $NEED || {
      red "Couldn't install:$NEED  (is the Pi online?)"; finish 1; }
  fi
else
  green "Everything PiDroid Setup needs is already installed."
fi
echo

# ---- 3. copy the app and make its icons -------------------------------------
DEST="$HOME/.local/share/pidroid"
DESK="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
mkdir -p "$DEST" "$HOME/.local/share/applications" "$DESK"

if [ ! -f "$DIR/pidroid.py" ]; then
  red "pidroid.py isn't next to install.sh. Unzip the whole folder first, then run install.sh from it."
  finish 1
fi
cp "$DIR/pidroid.py" "$DEST/"
[ -f "$DIR/pidroid.svg" ] && cp "$DIR/pidroid.svg" "$DEST/"
[ -f "$DIR/uninstall.sh" ] && cp "$DIR/uninstall.sh" "$DEST/" && chmod +x "$DEST/uninstall.sh"
chmod +x "$DEST/pidroid.py"
ICON=phone; [ -f "$DEST/pidroid.svg" ] && ICON="$DEST/pidroid.svg"

cat > "$HOME/.local/share/applications/pidroid.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=PiDroid Setup
Comment=Set up Waydroid (Android) and Roblox
Exec=python3 "$DEST/pidroid.py"
Icon=$ICON
Terminal=false
Categories=System;Settings;
EOF
cp "$HOME/.local/share/applications/pidroid.desktop" "$DESK/PiDroid Setup.desktop"
chmod +x "$HOME/.local/share/applications/pidroid.desktop" "$DESK/PiDroid Setup.desktop"
gio set "$DESK/PiDroid Setup.desktop" metadata::trusted true 2>/dev/null || true
rm -f "$DESK/pidroid.desktop"   # icon name used by older versions

green "Installed!"
echo "  - PiDroid Setup icon on your Desktop"
echo "  - In the Pi menu under Preferences / System Tools"
echo "  - To remove it later: the Uninstall button in the app, or uninstall.sh"
echo
echo "Opening PiDroid Setup now..."
setsid python3 "$DEST/pidroid.py" >/dev/null 2>&1 < /dev/null &
finish 0
