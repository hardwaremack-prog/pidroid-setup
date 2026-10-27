#!/bin/bash
# PiDroid Setup uninstaller.
# Double-click it (choose Execute). It asks before removing anything big.

SELF="$(readlink -f "$0")"

# Double-clicked? Open a terminal window so you can answer its questions.
if [ ! -t 0 ] && [ -z "$PIDROID_IN_TERM" ]; then
  export PIDROID_IN_TERM=1
  for t in lxterminal x-terminal-emulator foot xfce4-terminal gnome-terminal konsole xterm; do
    command -v "$t" >/dev/null || continue
    case "$t" in
      gnome-terminal) exec gnome-terminal --title="Uninstall PiDroid Setup" -- bash "$SELF" ;;
      lxterminal)     exec lxterminal -t "Uninstall PiDroid Setup" -e bash "$SELF" ;;
      foot)           exec foot -T "Uninstall PiDroid Setup" bash "$SELF" ;;
      *)              exec "$t" -e bash "$SELF" ;;
    esac
  done
  echo "No terminal program found. Run it from a terminal:  bash \"$SELF\""
  exit 1
fi

# Started with "sudo"? Run as the normal user so it cleans *your* desktop.
if [ "$(id -u)" = 0 ] && [ -n "$SUDO_USER" ] && [ "$SUDO_USER" != root ]; then
  exec sudo -u "$SUDO_USER" env PIDROID_IN_TERM="$PIDROID_IN_TERM" bash "$SELF"
fi

finish() {
  echo
  [ -n "$PIDROID_IN_TERM" ] && read -rp "Press Enter to close this window... " _
  exit "$1"
}
green() { printf '\033[1;32m%s\033[0m\n' "$*"; }
red()   { printf '\033[1;31m%s\033[0m\n' "$*"; }
yes_no() { local a; read -rp "$1 [y/N] " a; [[ "$a" =~ ^[Yy] ]]; }

DEST="$HOME/.local/share/pidroid"
DESK="$(xdg-user-dir DESKTOP 2>/dev/null || echo "$HOME/Desktop")"
CFG=/boot/firmware/config.txt;  [ -f "$CFG" ] || CFG=/boot/config.txt
CMD=/boot/firmware/cmdline.txt; [ -f "$CMD" ] || CMD=/boot/cmdline.txt
MARK="# Added by PiDroid Setup"

clear 2>/dev/null
echo "=============================================="
echo "       PiDroid Setup - uninstaller"
echo "=============================================="
echo
echo "This removes the PiDroid Setup app and its icons."
echo "It will ASK before removing Android (Waydroid) or changing boot settings."
echo
if ! yes_no "Uninstall PiDroid Setup?"; then
  echo "Nothing was changed."
  finish 0
fi
echo

# ---- 1. the PiDroid Setup app itself (no password needed) -------------------
pkill -f "$DEST/pidroid.py" 2>/dev/null || true
rm -f "$HOME/.local/share/applications/pidroid.desktop" \
      "$DESK/PiDroid Setup.desktop" "$DESK/pidroid.desktop"
rm -rf "$DEST"
green "Removed the PiDroid Setup app and its Desktop and menu icons."
echo

# ---- 2. Waydroid / Android (optional) ---------------------------------------
REMOVED_WAYDROID=""
if command -v waydroid >/dev/null || [ -d /var/lib/waydroid ] || [ -d "$HOME/.local/share/waydroid" ]; then
  echo "Waydroid (Android) is still installed. Removing it deletes Android and"
  echo "EVERYTHING inside it - Roblox, other apps, sign-ins and game data -"
  echo "and frees about 2-3 GB."
  if yes_no "Remove Waydroid and Android too?"; then
    echo
    echo "Type your password if asked (nothing shows while you type)."
    if sudo -v; then
      waydroid session stop >/dev/null 2>&1 || true
      sudo systemctl disable --now waydroid-container >/dev/null 2>&1 || true
      sudo apt-get purge -y waydroid
      sudo apt-get autoremove -y
      sudo rm -rf /var/lib/waydroid
      sudo rm -f /etc/apt/sources.list.d/waydroid.list /usr/share/keyrings/waydroid.gpg \
                 /etc/sysctl.d/99-waydroid.conf
      rm -rf "$HOME/.local/share/waydroid" "$HOME/.cache/waydroid"
      rm -f "$HOME/.local/share/applications/"waydroid.*.desktop \
            "$HOME/.local/share/applications/Waydroid.desktop" "$DESK/Roblox.desktop"
      green "Removed Waydroid and Android."
      REMOVED_WAYDROID=1
    else
      red "Password not accepted - Waydroid was left installed."
    fi
  else
    echo "Kept Waydroid and Android."
  fi
  echo
fi

# ---- 3. boot settings PiDroid changed (optional) -----------------------------
CHANGES=""
grep -q '^kernel=kernel8.img' "$CFG" 2>/dev/null && CHANGES="$CHANGES\n  - 4K page kernel (kernel=kernel8.img in $CFG)"
grep -qw 'psi=1' "$CMD" 2>/dev/null           && CHANGES="$CHANGES\n  - PSI (psi=1 in $CMD)"
grep -q 'video=HDMI-A-' "$CMD" 2>/dev/null     && CHANGES="$CHANGES\n  - Screen locked at 1920x1080 (video= in $CMD)"
if [ -n "$CHANGES" ]; then
  echo "These boot settings were made for Android:"
  printf "$CHANGES\n"
  if [ -z "$REMOVED_WAYDROID" ] && command -v waydroid >/dev/null; then
    echo "Waydroid is still installed and needs the 4K kernel and PSI to run."
  fi
  if yes_no "Put these boot settings back to normal?"; then
    if sudo -v; then
      sudo sed -i "/^$MARK: 4K page kernel/d; /^kernel=kernel8.img\$/d" "$CFG"
      sudo sed -i -E '1 s/ ?psi=1//g; 1 s/ ?video=HDMI-A-[0-9]:[^ ]*//g' "$CMD"
      green "Boot settings restored. Reboot to finish:  sudo reboot"
    else
      red "Password not accepted - boot settings were left as they are."
    fi
  else
    echo "Kept the boot settings."
  fi
  echo
fi

green "All done."
finish 0
