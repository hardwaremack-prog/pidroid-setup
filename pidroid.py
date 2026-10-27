#!/usr/bin/env python3
"""
PiDroid Setup — a GUI for getting Waydroid (and Roblox) running on a
Raspberry Pi 500 / Pi 5 with Raspberry Pi OS Trixie (labwc Wayland desktop).

Every fix is a button. Every command is shown in the log. Root steps use
sudo (Pi OS default) and fall back to pkexec, which pops up a password box.
"""
import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk, GLib, Pango, Gdk

import glob
import os
import re
import shutil
import subprocess
import tempfile
import threading
import zipfile

APP = "PiDroid Setup"
ROBLOX_PKG = "com.roblox.client"
MARK = "# Added by PiDroid Setup"

# ---------------------------------------------------------------- root scripts

BOOT_FIND = r'''
CFG=/boot/firmware/config.txt;  [ -f "$CFG" ] || CFG=/boot/config.txt
CMD=/boot/firmware/cmdline.txt; [ -f "$CMD" ] || CMD=/boot/cmdline.txt
'''

ROOT_SCRIPTS = {
    "pagesize": BOOT_FIND + r'''
set -e
cp -n "$CFG" "$CFG.pidroid.bak" || true
if grep -q '^kernel=kernel8.img' "$CFG"; then
  echo "4K page kernel is already selected in $CFG"
else
  printf '\n%s: 4K page kernel for Waydroid\n[all]\nkernel=kernel8.img\n' "''' + MARK + r'''" >> "$CFG"
  echo "Added kernel=kernel8.img to $CFG"; echo PIDROID_CHANGED
fi
ls -l /boot/firmware/kernel8.img 2>/dev/null || ls -l /boot/kernel8.img
echo ">>> Reboot needed for this to take effect."
''',

    "pagesize_undo": BOOT_FIND + r'''
sed -i '/^''' + MARK + r''': 4K page kernel/d; /^kernel=kernel8.img$/d' "$CFG"
echo "Removed the 4K kernel line from $CFG. Reboot to go back to the 16K kernel."; echo PIDROID_CHANGED
''',

    "psi": BOOT_FIND + r'''
set -e
cp -n "$CMD" "$CMD.pidroid.bak" || true
if grep -qw 'psi=1' "$CMD"; then
  echo "psi=1 is already in $CMD"
else
  sed -i '1 s/$/ psi=1/' "$CMD"
  echo "Added psi=1 to $CMD"; echo PIDROID_CHANGED
fi
cat "$CMD"
echo ">>> Reboot needed for this to take effect."
''',

    "install": r'''
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y curl ca-certificates nftables
echo "--- Adding the official Waydroid repository"
if curl -fsSL https://repo.waydro.id | bash; then
  echo "Official repo added."
else
  echo "Repo script failed, will use the Debian Trixie package instead."
fi
if ! apt-get update; then
  echo "Waydroid repo not usable on this release, removing it and using Debian's package."
  rm -f /etc/apt/sources.list.d/waydroid.list
  apt-get update
fi
apt-get install -y waydroid
systemctl enable --now waydroid-container
waydroid --version || true
echo ">>> Waydroid installed. Next: Download Android."
''',

    "init_gapps": r'''
set -e
waydroid session stop >/dev/null 2>&1 || true
systemctl enable --now waydroid-container || true
echo "Downloading Android with Google Play (about 1 GB, be patient)..."
waydroid init -s GAPPS -f
grep -qi GAPPS /var/lib/waydroid/waydroid.cfg || { echo "The download did not switch to the Google Play image."; exit 1; }
systemctl restart waydroid-container
echo ">>> Android image installed (with Google Play)."
''',

    "init_vanilla": r'''
set -e
systemctl enable --now waydroid-container || true
echo "Downloading plain Android (about 800 MB, be patient)..."
waydroid init -f
systemctl restart waydroid-container
echo ">>> Android image installed (no Google Play)."
''',

    "netfix": r'''
set -e
export DEBIAN_FRONTEND=noninteractive
command -v nft >/dev/null || apt-get install -y nftables
F=/usr/lib/waydroid/data/scripts/waydroid-net.sh
if [ -f "$F" ]; then
  sed -i 's/^LXC_USE_NFT="false"/LXC_USE_NFT="true"/' "$F"
  grep -n '^LXC_USE_NFT' "$F" || true
fi
printf 'net.ipv4.ip_forward=1\n' > /etc/sysctl.d/99-waydroid.conf
sysctl -p /etc/sysctl.d/99-waydroid.conf
if command -v ufw >/dev/null && ufw status | grep -q active; then
  echo "ufw is on, allowing Waydroid traffic"
  ufw allow 53; ufw allow 67; ufw default allow FORWARD
fi
if command -v firewall-cmd >/dev/null; then
  firewall-cmd --zone=trusted --add-interface=waydroid0 --permanent && firewall-cmd --reload
fi
systemctl restart waydroid-container
echo ">>> Network fix applied. Restart the Android session if it was open."
''',

    "spoof": r'''
set -e
P=/var/lib/waydroid/waydroid_base.prop
[ -f "$P" ] || { echo "Waydroid is not initialized yet."; exit 1; }
cp -n "$P" "$P.pidroid.bak" || true
sed -i '/^''' + MARK + r'''/,/^# end PiDroid/d' "$P"
cat >> "$P" <<'EOF'
''' + MARK + r''' (phone identity)
ro.product.brand=google
ro.product.manufacturer=Google
ro.product.model=Pixel 5
ro.product.name=redfin
ro.product.device=redfin
ro.system.build.product=redfin
ro.build.characteristics=phone
# end PiDroid
EOF
systemctl restart waydroid-container
echo ">>> Android now reports itself as a Pixel 5. Restart the session."
''',

    "screen_lock": BOOT_FIND + r'''
set -e
OUT=""
for s in /sys/class/drm/card*-HDMI-A-*/status; do
  [ "$(cat "$s" 2>/dev/null)" = connected ] && { OUT=$(basename "$(dirname "$s")" | sed 's/^card[0-9]*-//'); break; }
done
OUT=${OUT:-HDMI-A-1}
cp -n "$CMD" "$CMD.pidroid.bak" || true
sed -i -E '1 s/ ?video=HDMI-A-[0-9]:[^ ]*//g' "$CMD"
sed -i "1 s/\$/ video=$OUT:1920x1080@60/" "$CMD"
echo "Monitor: $OUT"
echo "Boot line now ends with: $(grep -o 'video=[^ ]*' "$CMD")"
echo PIDROID_CHANGED
echo ">>> Reboot and the Pi's screen will start at 1920x1080."
''',

    "screen_unlock": BOOT_FIND + r'''
if grep -q 'video=HDMI-A-' "$CMD"; then
  sed -i -E '1 s/ ?video=HDMI-A-[0-9]:[^ ]*//g' "$CMD"
  echo "Removed the screen lock from $CMD."
  echo PIDROID_CHANGED
  echo ">>> Reboot and the Pi picks its screen size by itself again."
else
  echo "The Pi's screen isn't locked."
fi
''',

    "spoof_undo": r'''
P=/var/lib/waydroid/waydroid_base.prop
sed -i '/^''' + MARK + r'''/,/^# end PiDroid/d' "$P"
systemctl restart waydroid-container
echo "Phone identity removed."
''',

    "android_id": r'''
waydroid shell -- sh -c "ANDROID_RUNTIME_ROOT=/apex/com.android.runtime ANDROID_DATA=/data ANDROID_TZDATA_ROOT=/apex/com.android.tzdata ANDROID_I18N_ROOT=/apex/com.android.i18n sqlite3 /data/data/com.google.android.gsf/databases/gservices.db \"select * from main where name = 'android_id';\""
echo ">>> Copy the number after 'android_id|' into the Google page that just opened."
''',

    "playcheck": r'''
CFG=/var/lib/waydroid/waydroid.cfg
echo "Android image:"
grep -E '^(system_ota|images_path)' "$CFG" 2>/dev/null | sed 's/^/  /'
grep -qi 'GAPPS' "$CFG" && echo "  -> this image was downloaded WITH Google Play" \
  || echo "  -> this image was downloaded WITHOUT Google Play (vanilla)"
if ! waydroid status 2>/dev/null | grep -q 'Session:.*RUNNING'; then
  echo; echo ">>> Click 'Open Android', wait for the home screen, then click Check Play Store again."
  exit 1
fi
PK=$(waydroid shell -- pm list packages 2>/dev/null)
echo; echo "Google parts inside Android:"
for p in com.android.vending com.google.android.gms com.google.android.gsf; do
  if echo "$PK" | grep -q "^package:$p\s*$"; then echo "  OK       $p"; else echo "  MISSING  $p"; fi
done
if echo "$PK" | grep -q '^package:com.android.vending\s*$'; then
  waydroid shell -- pm enable com.android.vending >/dev/null 2>&1 || true
  waydroid shell -- pm enable com.google.android.gms >/dev/null 2>&1 || true
  echo; echo ">>> The Play Store is installed and switched on. Click 'Open Play Store'."
  echo "    If it says the device isn't certified, do 'Get Play Store ID' and wait 15-60 minutes."
else
  echo; echo ">>> Google Play is not in this Android image."
  echo "    Click '④ Android + Play Store' to download Android with Google Play."
  echo "    Your installed apps and their data are kept."
fi
''',

    "restart_container": r'''
systemctl restart waydroid-container && systemctl --no-pager status waydroid-container | head -5
''',

    "wayland": r"""
WS=/usr/share/wayland-sessions
LC=/etc/lightdm/lightdm.conf
echo "Wayland desktops on this Pi:"; ls "$WS" 2>/dev/null || echo "  (none)"
pick() {
  for s in rpd-labwc LXDE-pi-labwc labwc-pi rpd-wayland labwc; do
    [ -f "$WS/$s.desktop" ] && { echo "$s"; return; }
  done
  ls "$WS" 2>/dev/null | grep -i labwc | head -1 | sed 's/\.desktop$//'
}
SESS=$(pick)
if [ -z "$SESS" ]; then
  echo "labwc isn't installed, installing it..."
  DEBIAN_FRONTEND=noninteractive apt-get install -y labwc || exit 1
  SESS=$(pick)
fi
[ -n "$SESS" ] || { echo "Could not find a labwc session to switch to."; exit 1; }
echo "Using session: $SESS"

# 1) try raspi-config (codes differ between releases)
if command -v raspi-config >/dev/null; then
  for code in W3 W2; do
    raspi-config nonint do_wayland $code >/dev/null 2>&1 || continue
    if grep -Eq "^(user|autologin)-session=.*labwc" "$LC" 2>/dev/null; then
      echo "raspi-config switched the desktop to labwc ($code)."
      echo PIDROID_CHANGED; echo ">>> Reboot needed."; exit 0
    fi
  done
  echo "raspi-config didn't change it, setting LightDM directly..."
fi

# 2) set LightDM ourselves
[ -f "$LC" ] || { echo "No $LC found - is LightDM your login manager?"; exit 1; }
cp -n "$LC" "$LC.pidroid.bak" || true
setkey() {
  if grep -Eq "^#?$1=" "$LC"; then
    sed -i -E "s|^#?$1=.*|$1=$2|" "$LC"
  else
    sed -i "/^\[Seat:\*\]/a $1=$2" "$LC"
  fi
}
grep -q '^\[Seat:\*\]' "$LC" || printf '\n[Seat:*]\n' >> "$LC"
setkey user-session "$SESS"
grep -Eq '^autologin-user=.+' "$LC" && setkey autologin-session "$SESS"
[ -f /usr/share/xgreeters/pi-greeter-labwc.desktop ] && setkey greeter-session pi-greeter-labwc
echo "LightDM now set to:"; grep -E '^(user|autologin|greeter)-session=' "$LC"
echo PIDROID_CHANGED
echo ">>> Reboot needed. After the reboot, open PiDroid Setup again."
""",

    "wayland_undo": r"""
LC=/etc/lightdm/lightdm.conf
[ -f "$LC.pidroid.bak" ] && cp "$LC.pidroid.bak" "$LC" && echo "LightDM settings restored." && echo PIDROID_CHANGED || echo "No backup to restore."
""",

    "weston": r'''
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get install -y weston
echo ">>> Weston installed."
''',

    "reboot": "systemctl reboot",
}

# User-level (no root) scripts
WAIT_SESSION = r'''
if ! waydroid status 2>/dev/null | grep -q 'Session:.*RUNNING'; then
  echo "Starting Android session..."
  (waydroid session start >/dev/null 2>&1 &)
  for i in $(seq 1 60); do
    waydroid status 2>/dev/null | grep -q 'Session:.*RUNNING' && break
    sleep 2
  done
fi
waydroid status | grep -q 'Session:.*RUNNING' || { echo "Session did not start. Check the log above."; exit 1; }
sleep 4
'''

# Opens the Android window (and starts Android if needed). App launches only show
# up inside this window, so without it a launch looks like nothing happened.
SHOW_UI = r'''
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
[ -z "$WAYLAND_DISPLAY" ] && [ -S "$XDG_RUNTIME_DIR/wayland-pidroid" ] && export WAYLAND_DISPLAY=wayland-pidroid
if [ -z "$WAYLAND_DISPLAY" ]; then
  echo "No Wayland desktop found. Use 'Open Android' first (it can run Android in a window on X11)."
  exit 1
fi
echo "Opening the Android window..."
setsid waydroid show-full-ui >/dev/null 2>&1 &
for i in $(seq 1 60); do
  waydroid status 2>/dev/null | grep -q 'Session:.*RUNNING' && break
  sleep 2
done
waydroid status | grep -q 'Session:.*RUNNING' || { echo "Android did not start. Click 'Waydroid log' and copy the log."; exit 1; }
# wait for Android to finish booting so the app list is ready
for i in $(seq 1 45); do
  waydroid app list >/dev/null 2>&1 && break
  sleep 2
done
'''

LAUNCH = r'''
if ! waydroid app list 2>/dev/null | grep -q "PKG"; then
  echo "Can't find NAME in Android's app list."
  if [ "PKG" = "com.android.vending" ]; then
    echo ">>> Click 'Check Play Store' to see why and fix it."
  else
    echo ">>> Install it from the Play Store or with 'Install APK file...' first."
  fi
  exit 1
fi
waydroid app launch PKG
echo "NAME is opening in the Android window."
'''

USER_SCRIPTS = {
    "roblox_tune": WAIT_SESSION + r'''
set -e
waydroid prop set persist.waydroid.multi_windows false
waydroid prop set persist.waydroid.suspend false
waydroid prop set persist.waydroid.fake_touch ""
echo "Android stays awake, and the mouse works as a normal mouse in Roblox."
echo "(If the mouse misbehaves in a game, try 'Mouse: touch'.)"
waydroid session stop >/dev/null 2>&1 || true
echo ">>> Open Android or Launch Roblox to use the new settings."
''',

    "mouse_normal": WAIT_SESSION + r'''
set -e
waydroid prop set persist.waydroid.fake_touch ""
waydroid session stop >/dev/null 2>&1 || true
echo "Mouse is now a normal mouse: point, click, scroll, and hold the right"
echo "button and move to turn the camera in Roblox."
echo ">>> Launch Roblox again to use it."
''',

    "mouse_touch": WAIT_SESSION + r'''
set -e
waydroid prop set persist.waydroid.fake_touch "com.roblox.*"
waydroid session stop >/dev/null 2>&1 || true
echo "Mouse now acts like a finger in Roblox: click = tap, hold left and drag"
echo "to turn the camera, and use the on-screen joystick to walk."
echo ">>> Launch Roblox again to use it."
''',

    "session_stop": "waydroid session stop; echo Session stopped.",

    "roblox_launch": SHOW_UI + LAUNCH.replace("PKG", ROBLOX_PKG).replace("NAME", "Roblox"),

    "playstore": SHOW_UI + LAUNCH.replace("PKG", "com.android.vending").replace("NAME", "Play Store"),
}


# ---------------------------------------------------------------- helpers

def sh(cmd, timeout=10):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as e:  # noqa
        return 1, str(e)


def root_cmd(script):
    if shutil.which("sudo") and sh(["sudo", "-n", "true"])[0] == 0:
        return ["sudo", "-n", "bash", "-c", script]
    return ["pkexec", "bash", "-c", script]


def read(path):
    try:
        with open(path) as f:
            return f.read()
    except Exception:
        return ""


def wayland_state():
    """(is_wayland, desktop_name). Also exports WAYLAND_DISPLAY if a compositor
    is running but this app was started without it in the environment."""
    procs = set()
    for p in glob.glob("/proc/[0-9]*/comm"):
        try:
            if os.stat(p).st_uid == os.getuid():
                procs.add(read(p).strip())
        except Exception:
            pass
    comp = next((c for c in ("labwc", "wayfire", "sway", "kwin_wayland", "gnome-shell") if c in procs), None)
    rt = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    socks = sorted(s for s in glob.glob(os.path.join(rt, "wayland-[0-9]")) if not s.endswith(".lock"))
    env_wl = bool(os.environ.get("WAYLAND_DISPLAY")) or os.environ.get("XDG_SESSION_TYPE") == "wayland"
    if comp and socks and not os.environ.get("WAYLAND_DISPLAY"):
        os.environ["WAYLAND_DISPLAY"] = os.path.basename(socks[0])
        os.environ["XDG_RUNTIME_DIR"] = rt
    if comp or env_wl:
        return True, comp or os.environ.get("XDG_CURRENT_DESKTOP", "Wayland")
    x = next((c for c in ("openbox", "lxsession", "xfwm4", "mutter") if c in procs), "X11")
    return False, x


def gather_checks():
    """Return list of dicts: name, state ('ok'|'warn'|'bad'), detail, fix."""
    out = []

    model = read("/proc/device-tree/model").strip("\x00\n ") or "Unknown"
    pi5 = "Raspberry Pi 5" in model or "Raspberry Pi 500" in model or "Compute Module 5" in model
    out.append(dict(name="Hardware", state="ok" if pi5 else "warn",
                    detail=model, fix=None))

    osr = read("/etc/os-release")
    code = (re.search(r"VERSION_CODENAME=(\w+)", osr) or [None, "?"])[1]
    out.append(dict(name="OS release", state="ok" if code == "trixie" else "warn",
                    detail=f"{code} ({os.uname().machine})", fix=None))

    mem = read("/proc/meminfo")
    m = re.search(r"MemTotal:\s+(\d+)", mem)
    gb = int(m.group(1)) / 1024 / 1024 if m else 0
    out.append(dict(name="Memory", state="ok" if gb >= 3.5 else "warn",
                    detail=f"{gb:.1f} GB", fix=None))

    page = os.sysconf("SC_PAGE_SIZE")
    out.append(dict(name="Kernel page size",
                    state="ok" if page == 4096 else "bad",
                    detail=f"{page // 1024}K " + ("(good)" if page == 4096 else "(Android needs 4K)"),
                    fix="pagesize" if page != 4096 else None))

    psi_ok = False
    try:
        with open("/proc/pressure/cpu") as f:
            f.read()
        psi_ok = True
    except Exception:
        pass
    out.append(dict(name="Pressure info (PSI)", state="ok" if psi_ok else "bad",
                    detail="on" if psi_ok else "off (Android's memory manager needs it)",
                    fix=None if psi_ok else "psi"))

    fs = read("/proc/filesystems")
    binder = "binder" in fs or os.path.exists("/dev/binder") or os.path.isdir("/dev/binderfs")
    if not binder:
        binder = sh(["modinfo", "binder_linux"])[0] == 0
    out.append(dict(name="Binder driver", state="ok" if binder else "bad",
                    detail="present in kernel" if binder else "missing from this kernel",
                    fix=None))

    wl, desk = wayland_state()
    if wl:
        out.append(dict(name="Desktop session", state="ok", detail=f"Wayland ({desk})", fix=None))
    else:
        out.append(dict(name="Desktop session", state="bad",
                        detail=f"X11 ({desk}) - Android needs the Wayland desktop",
                        fix="wayland"))

    have = bool(shutil.which("waydroid"))
    ver = sh(["waydroid", "--version"])[1] if have else ""
    out.append(dict(name="Waydroid", state="ok" if have else "bad",
                    detail=ver if have else "not installed",
                    fix=None if have else "install"))

    inited = os.path.exists("/var/lib/waydroid/images/system.img") or \
        bool(glob.glob("/var/lib/waydroid/images/*.img"))
    gapps = bool(re.search(r"^system_ota\s*=.*GAPPS", read("/var/lib/waydroid/waydroid.cfg"), re.M | re.I))
    out.append(dict(name="Android image", state="ok" if inited else ("bad" if have else "warn"),
                    detail=("installed" + (" with Google Play" if gapps else " (no Google Play)"))
                    if inited else "not downloaded",
                    fix=None if inited or not have else "init_gapps"))

    if have:
        rc, act = sh(["systemctl", "is-active", "waydroid-container"])
        out.append(dict(name="Container service", state="ok" if act == "active" else "warn",
                        detail=act or "unknown",
                        fix=None if act == "active" else "restart_container"))
        nft_file = read("/usr/lib/waydroid/data/scripts/waydroid-net.sh")
        nft_ok = 'LXC_USE_NFT="true"' in nft_file
        out.append(dict(name="Android networking", state="ok" if nft_ok else "warn",
                        detail="nftables mode (Trixie-friendly)" if nft_ok else
                        "iptables mode (often no internet on Trixie)",
                        fix=None if nft_ok else "netfix"))
        rc, s = sh(["waydroid", "status"])
        sess = "RUNNING" if re.search(r"Session:\s*RUNNING", s) else "stopped"
        out.append(dict(name="Android session", state="ok" if sess == "RUNNING" else "warn",
                        detail=sess, fix=None))

    apps = os.path.expanduser("~/.local/share/applications")
    rob = glob.glob(os.path.join(apps, "*roblox*"))
    out.append(dict(name="Roblox", state="ok" if rob else "warn",
                    detail="installed" if rob else "not installed yet", fix=None))
    return out


# ---------------------------------------------------------------- GUI

CSS = b"""
.dot-ok   { color: #2fb36a; }
.dot-warn { color: #e0a020; }
.dot-bad  { color: #e04848; }
.title    { font-size: 20px; font-weight: 800; }
.sub      { opacity: 0.7; }
.step     { padding: 6px 10px; }
.log      { font-family: monospace; font-size: 10pt; }
"""

SIZES = {
    "Auto": (0, 0),
    "1280 × 720": (1280, 720),
    "1600 × 900": (1600, 900),
    "1920 × 1080": (1920, 1080),
    "2560 × 1440": (2560, 1440),
}
SIZES_LIST = list(SIZES)

FIX_LABELS = {
    "pagesize": "Switch to 4K kernel",
    "psi": "Turn on PSI",
    "install": "Install Waydroid",
    "init_gapps": "Download Android",
    "restart_container": "Start service",
    "netfix": "Fix network",
    "wayland": "Switch to Wayland",
}


class App(Gtk.Window):
    def __init__(self):
        super().__init__(title=APP)
        self.set_default_size(900, 820)
        self.set_icon_name("phone")
        self.busy = False
        self.buttons = []
        self.reboot_pending = False
        self.last_out = ""

        prov = Gtk.CssProvider()
        prov.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=14)
        self.add(root)

        head = Gtk.Box(spacing=10)
        t = Gtk.Label(label="PiDroid Setup", xalign=0)
        t.get_style_context().add_class("title")
        s = Gtk.Label(label="Waydroid + Roblox for Raspberry Pi 500 on Trixie", xalign=0)
        s.get_style_context().add_class("sub")
        tb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        tb.pack_start(t, False, False, 0)
        tb.pack_start(s, False, False, 0)
        head.pack_start(tb, True, True, 0)
        self.spinner = Gtk.Spinner()
        head.pack_end(self.spinner, False, False, 0)
        refresh = Gtk.Button(label="Re-check")
        refresh.connect("clicked", lambda *_: self.refresh())
        head.pack_end(refresh, False, False, 0)
        self.buttons.append(refresh)
        root.pack_start(head, False, False, 0)

        self.banner = Gtk.InfoBar(message_type=Gtk.MessageType.WARNING)
        self.banner_label = Gtk.Label(xalign=0)
        self.banner_label.set_line_wrap(True)
        self.banner.get_content_area().add(self.banner_label)
        self.banner_btn = self.banner.add_button("Reboot now", 1)
        self.banner.connect("response", lambda *_: self.run_root("reboot", "Reboot"))
        self.banner.set_no_show_all(True)
        root.pack_start(self.banner, False, False, 0)

        # checks
        cf = Gtk.Frame(label=" 1 · System check ")
        self.grid = Gtk.Grid(column_spacing=12, row_spacing=4, margin=10)
        cf.add(self.grid)
        root.pack_start(cf, False, False, 0)

        # setup buttons
        sf = Gtk.Frame(label=" 2 · Setup (top to bottom, rebooting when asked) ")
        sb = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=4,
                         column_spacing=6, row_spacing=6, margin=10, homogeneous=True)
        for label, fn in [
            ("① 4K page kernel", lambda: self.run_root("pagesize", "4K page kernel")),
            ("② Turn on PSI", lambda: self.run_root("psi", "PSI")),
            ("③ Install Waydroid", lambda: self.run_root("install", "Install Waydroid")),
            ("④ Android + Play Store", lambda: self.run_root("init_gapps", "Download Android (GAPPS)")),
            ("④ Android (no Google)", lambda: self.run_root("init_vanilla", "Download Android (vanilla)")),
            ("⑤ Fix Android network", lambda: self.run_root("netfix", "Network fix")),
            ("Open Android", self.open_full_ui),
            ("Stop Android", lambda: self.run_user("session_stop", "Stop session")),
        ]:
            b = Gtk.Button(label=label)
            b.connect("clicked", lambda _b, f=fn: f())
            sb.add(b)
            self.buttons.append(b)
        sf.add(sb)
        root.pack_start(sf, False, False, 0)

        # roblox
        rf = Gtk.Frame(label=" 3 · Roblox ")
        rb = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=4,
                         column_spacing=6, row_spacing=6, margin=10, homogeneous=True)
        for label, fn, tip in [
            ("Get Play Store ID", self.play_cert,
             "Shows your Android ID and opens Google's page to register it, so Play Store logs in."),
            ("Open Play Store", lambda: self.run_user("playstore", "Play Store"),
             "Opens the Android window and the Play Store. Search for Roblox and install it."),
            ("Check Play Store", lambda: self.run_root("playcheck", "Check Play Store"),
             "Checks whether Google Play is installed and switched on, and tells you how to fix it."),
            ("Install APK file…", self.install_apk,
             "Install Roblox from an .apk, .apkm or .xapk you downloaded (arm64-v8a)."),
            ("Tune for Roblox", lambda: self.run_user("roblox_tune", "Tune for Roblox"),
             "Keeps Android awake and sets a normal mouse for Roblox."),
            ("Mouse: normal", lambda: self.run_user("mouse_normal", "Mouse: normal"),
             "The mouse works like on a PC: point and click, right-drag turns the camera."),
            ("Mouse: touch", lambda: self.run_user("mouse_touch", "Mouse: touch"),
             "The mouse acts like a finger: click = tap, left-drag turns the camera."),
            ("Phone identity (if it crashes)", lambda: self.run_root("spoof", "Phone identity"),
             "Makes Android report as a Pixel 5. Try this if Roblox closes right away."),
            ("Undo phone identity", lambda: self.run_root("spoof_undo", "Undo phone identity"),
             "Removes the Pixel 5 identity."),
            ("▶ Launch Roblox", lambda: self.run_user("roblox_launch", "Launch Roblox"),
             "Starts Android if needed, then opens Roblox."),
            ("Roblox on Desktop", self.desktop_shortcut,
             "Copies the Roblox launcher onto your desktop."),
        ]:
            b = Gtk.Button(label=label)
            b.set_tooltip_text(tip)
            b.connect("clicked", lambda _b, f=fn: f())
            rb.add(b)
            self.buttons.append(b)
        rf.add(rb)
        root.pack_start(rf, False, False, 0)

        # screen size
        zf = Gtk.Frame(label=" 4 · Screen size ")
        zb = Gtk.Box(spacing=8, margin=10)
        zb.pack_start(Gtk.Label(label="Android window:"), False, False, 0)
        self.size_combo = Gtk.ComboBoxText()
        for label in SIZES:
            self.size_combo.append_text(label)
        self.size_combo.set_active(SIZES_LIST.index("1920 × 1080"))
        self.size_combo.set_tooltip_text("Smaller sizes run Roblox much smoother on the Pi.")
        zb.pack_start(self.size_combo, False, False, 0)
        ab = Gtk.Button(label="Apply to Android")
        ab.set_tooltip_text("Locks the Android window (and Roblox) to this size. Android restarts.")
        ab.connect("clicked", lambda *_: self.apply_android_size())
        zb.pack_start(ab, False, False, 0)
        self.buttons.append(ab)
        zb.pack_start(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL), False, False, 6)
        for label, key, tip in [
            ("Lock Pi screen 1920×1080", "screen_lock",
             "Makes the Pi's own screen always start at 1920x1080 (needs a reboot)."),
            ("Unlock Pi screen", "screen_unlock", "Lets the Pi pick its screen size again (needs a reboot)."),
        ]:
            b = Gtk.Button(label=label)
            b.set_tooltip_text(tip)
            b.connect("clicked", lambda _b, k=key, t=label: self.run_root(k, t))
            zb.pack_start(b, False, False, 0)
            self.buttons.append(b)
        zf.add(zb)
        root.pack_start(zf, False, False, 0)

        # log
        lf = Gtk.Frame(label=" Log ")
        sc = Gtk.ScrolledWindow(vexpand=True)
        sc.set_min_content_height(160)
        self.log = Gtk.TextView(editable=False, cursor_visible=False, monospace=True)
        self.log.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.log.get_style_context().add_class("log")
        self.buf = self.log.get_buffer()
        self.tag_cmd = self.buf.create_tag("cmd", weight=Pango.Weight.BOLD, foreground="#3a8ee6")
        self.tag_ok = self.buf.create_tag("ok", weight=Pango.Weight.BOLD, foreground="#2fb36a")
        self.tag_bad = self.buf.create_tag("bad", weight=Pango.Weight.BOLD, foreground="#e04848")
        sc.add(self.log)
        lb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        lb.pack_start(sc, True, True, 0)
        extra = Gtk.Box(spacing=6, margin=6)
        for label, fn in [("Copy log", self.copy_log),
                          ("Undo 4K kernel", lambda: self.run_root("pagesize_undo", "Undo 4K kernel")),
                          ("Undo Wayland", lambda: self.run_root("wayland_undo", "Undo Wayland")),
                          ("Restart service", lambda: self.run_root("restart_container", "Restart service")),
                          ("Waydroid log", lambda: self.run_root("waydroid log | tail -60", "Waydroid log", raw=True)),
                          ("Uninstall…", self.uninstall)]:
            b = Gtk.Button(label=label)
            b.connect("clicked", lambda _b, f=fn: f())
            extra.pack_start(b, False, False, 0)
            self.buttons.append(b)
        lb.pack_start(extra, False, False, 0)
        lf.add(lb)
        root.pack_start(lf, True, True, 0)

        self.connect("destroy", Gtk.main_quit)
        self.show_all()
        self.say("Welcome! Work through the checks above. Anything red has a fix button.\n")
        self.refresh()

    # ----- log
    def say(self, text, tag=None):
        end = self.buf.get_end_iter()
        if tag:
            self.buf.insert_with_tags(end, text, tag)
        else:
            self.buf.insert(end, text)
        self.buf.place_cursor(self.buf.get_end_iter())
        self.log.scroll_mark_onscreen(self.buf.get_insert())
        return False

    def stream(self, chunk):
        chunk = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", chunk)
        parts = chunk.split("\r")
        self.say(parts[0])
        for p in parts[1:]:
            # carriage return: overwrite the current line (download progress)
            end = self.buf.get_end_iter()
            start = end.copy()
            start.set_line_offset(0)
            self.buf.delete(start, end)
            self.say(p)
        return False

    def copy_log(self):
        s, e = self.buf.get_bounds()
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(self.buf.get_text(s, e, False), -1)
        self.say("Log copied to clipboard.\n", self.tag_ok)

    # ----- checks
    def refresh(self):
        for c in self.grid.get_children():
            if c in self.buttons:
                self.buttons.remove(c)
            self.grid.remove(c)
        try:
            checks = gather_checks()
        except Exception as e:  # noqa
            self.say(f"Check failed: {e}\n", self.tag_bad)
            return
        for i, c in enumerate(checks):
            dot = Gtk.Label(label="●")
            dot.get_style_context().add_class("dot-" + c["state"])
            name = Gtk.Label(label=c["name"], xalign=0)
            name.set_markup(f"<b>{GLib.markup_escape_text(c['name'])}</b>")
            det = Gtk.Label(label=c["detail"], xalign=0, hexpand=True)
            det.set_ellipsize(Pango.EllipsizeMode.END)
            det.set_tooltip_text(c["detail"])
            self.grid.attach(dot, 0, i, 1, 1)
            self.grid.attach(name, 1, i, 1, 1)
            self.grid.attach(det, 2, i, 1, 1)
            if c["fix"]:
                b = Gtk.Button(label=FIX_LABELS.get(c["fix"], "Fix"))
                key = c["fix"]
                b.connect("clicked", lambda _b, k=key: self.run_root(k, FIX_LABELS.get(k, k)))
                b.set_sensitive(not self.busy)
                self.buttons.append(b)
                self.grid.attach(b, 3, i, 1, 1)
        self.grid.show_all()

        bad_binder = any(c["name"] == "Binder driver" and c["state"] == "bad" for c in checks)
        if self.reboot_pending:
            self.banner_label.set_text("Boot settings changed. Reboot so the new kernel settings load, "
                                       "then open PiDroid Setup again.")
            self.banner_btn.show()
            self.banner.show_all()
        elif bad_binder:
            self.banner_label.set_text("This kernel has no binder driver, which Android needs. Update with "
                                       "'sudo apt full-upgrade' and reboot, or use the stock Raspberry Pi kernel.")
            self.banner.show_all()
            self.banner_btn.hide()
        else:
            self.banner.hide()

    # ----- running things
    def set_busy(self, on):
        self.busy = on
        for b in self.buttons:
            try:
                b.set_sensitive(not on)
            except Exception:
                pass
        (self.spinner.start if on else self.spinner.stop)()

    def run_root(self, key, title, raw=False):
        script = key if raw else ROOT_SCRIPTS[key]
        self._run(root_cmd(script), title, key)

    def run_user(self, key, title):
        self._run(["bash", "-c", USER_SCRIPTS[key]], title, key)

    def _run(self, cmd, title, key, after=None):
        if self.busy:
            return
        self.set_busy(True)
        self.say(f"\n▶ {title}\n", self.tag_cmd)
        self.last_out = ""

        def work():
            try:
                p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
                fd = p.stdout.fileno()
                while True:
                    data = os.read(fd, 4096)
                    if not data:
                        break
                    txt = data.decode(errors="replace")
                    self.last_out += txt
                    GLib.idle_add(self.stream, txt.replace("PIDROID_CHANGED\n", ""))
                rc = p.wait()
            except FileNotFoundError as e:
                GLib.idle_add(self.stream, f"{e}\n")
                rc = 127
            GLib.idle_add(self.done, title, key, rc, after)

        threading.Thread(target=work, daemon=True).start()

    def done(self, title, key, rc, after):
        if rc == 0:
            self.say(f"✔ {title} finished\n", self.tag_ok)
            if "PIDROID_CHANGED" in self.last_out:
                self.reboot_pending = True
        else:
            self.say(f"✘ {title} failed (code {rc}). Use 'Copy log' to share the output.\n", self.tag_bad)
        self.set_busy(False)
        if after:
            after(rc)
        self.refresh()
        return False

    # ----- special actions
    def open_full_ui(self):
        if not shutil.which("waydroid"):
            self.say("Install Waydroid first.\n", self.tag_bad)
            return
        if wayland_state()[0]:
            subprocess.Popen(["waydroid", "show-full-ui"], stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            self.say("Opening the Android window (first boot can take a minute)...\n", self.tag_cmd)
            return
        # X11 fallback: a Weston window acts as a mini Wayland desktop for Android
        if not shutil.which("weston"):
            self.say("You're on X11. Installing Weston so Android can run in a window "
                     "(or click 'Switch to Wayland' for the best experience)...\n")
            self.run_root("weston", "Install Weston", )
            return
        script = r'''
export XDG_RUNTIME_DIR=${XDG_RUNTIME_DIR:-/run/user/$(id -u)}
waydroid session stop >/dev/null 2>&1 || true
if ! [ -S "$XDG_RUNTIME_DIR/wayland-pidroid" ]; then
  weston --backend=x11-backend.so --width=1280 --height=800 --socket=wayland-pidroid --idle-time=0 >/dev/null 2>&1 &
  for i in $(seq 1 20); do [ -S "$XDG_RUNTIME_DIR/wayland-pidroid" ] && break; sleep 0.5; done
fi
export WAYLAND_DISPLAY=wayland-pidroid
setsid waydroid show-full-ui >/dev/null 2>&1 &
echo "Android is starting inside the Weston window (first boot can take a minute)."
echo "Close the Weston window or click Stop Android when you're done."
'''
        self._run(["bash", "-c", script], "Open Android (X11 window mode)", "openx11")

    def play_cert(self):
        subprocess.Popen(["xdg-open", "https://www.google.com/android/uncertified"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.say("Note: Android must be running (click 'Open Android' first) and have opened the "
                 "Play Store once.\n")
        self.run_root("android_id", "Get Android ID")

    def install_apk(self):
        dlg = Gtk.FileChooserDialog(title="Choose the Roblox APK", parent=self,
                                    action=Gtk.FileChooserAction.OPEN)
        dlg.add_buttons("Cancel", Gtk.ResponseType.CANCEL, "Install", Gtk.ResponseType.OK)
        f = Gtk.FileFilter()
        f.set_name("Android apps (.apk .apkm .xapk .apks)")
        for pat in ("*.apk", "*.apkm", "*.xapk", "*.apks"):
            f.add_pattern(pat)
        dlg.add_filter(f)
        dl = os.path.expanduser("~/Downloads")
        if os.path.isdir(dl):
            dlg.set_current_folder(dl)
        resp = dlg.run()
        path = dlg.get_filename()
        dlg.destroy()
        if resp != Gtk.ResponseType.OK or not path:
            return

        if path.lower().endswith(".apk"):
            script = WAIT_SESSION + f'waydroid app install "{path}" && echo "Installed. It may take a moment to appear."'
            self._run(["bash", "-c", script], "Install APK", "apk")
            return

        # split bundle: unzip and use adb install-multiple over Waydroid's network adb
        tmp = tempfile.mkdtemp(prefix="pidroid-")
        try:
            with zipfile.ZipFile(path) as z:
                names = [n for n in z.namelist() if n.lower().endswith(".apk")]
                for n in names:
                    z.extract(n, tmp)
        except Exception as e:  # noqa
            self.say(f"Couldn't open that bundle: {e}\n", self.tag_bad)
            return
        apks = []
        for n in names:
            low = os.path.basename(n).lower()
            # skip splits for other CPUs
            if any(a in low for a in ("x86", "armeabi_v7a", "armeabi-v7a")) and "arm64" not in low:
                continue
            apks.append(os.path.join(tmp, n))
        quoted = " ".join(f'"{a}"' for a in apks)
        script = WAIT_SESSION + r'''
if ! command -v adb >/dev/null; then
  echo "adb is needed for split bundles, installing it..."
  if sudo -n true 2>/dev/null; then sudo -n apt-get install -y adb; else pkexec apt-get install -y adb; fi
fi
IP=$(waydroid status | awk '/IP address/ {print $3}')
[ -n "$IP" ] || { echo "Could not find Android's IP. Is networking fixed?"; exit 1; }
adb connect "$IP:5555"
sleep 2
adb -s "$IP:5555" install-multiple -r ''' + quoted + r'''
'''
        self._run(["bash", "-c", script], f"Install bundle ({len(apks)} parts)", "apk",
                  after=lambda rc: shutil.rmtree(tmp, ignore_errors=True))

    def apply_android_size(self):
        label = self.size_combo.get_active_text() or "Auto"
        w, h = SIZES[label]
        if w:
            sets = (f'waydroid prop set persist.waydroid.width {w}\n'
                    f'waydroid prop set persist.waydroid.height {h}\n'
                    f'echo "Android window locked to {w} x {h}."\n')
        else:
            sets = ('waydroid prop set persist.waydroid.width ""\n'
                    'waydroid prop set persist.waydroid.height ""\n'
                    'echo "Android window size is automatic again."\n')
        script = WAIT_SESSION + "set -e\n" + sets + (
            "waydroid session stop >/dev/null 2>&1 || true\n"
            'echo ">>> Click Open Android to use the new size."\n')
        self._run(["bash", "-c", script], f"Android window: {label}", "size")

    def uninstall(self):
        here = os.path.dirname(os.path.abspath(__file__))
        cands = [os.path.join(here, "uninstall.sh"),
                 os.path.expanduser("~/.local/share/pidroid/uninstall.sh")]
        script = next((c for c in cands if os.path.isfile(c)), None)
        if not script:
            self.say("uninstall.sh wasn't found. Run it from the PiDroid Setup folder you unzipped.\n",
                     self.tag_bad)
            return
        # with no terminal attached, uninstall.sh opens its own terminal window to ask its questions
        subprocess.Popen(["bash", script], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
        self.say("The uninstaller opened in a terminal window. Answer its questions there.\n", self.tag_cmd)

    def desktop_shortcut(self):
        apps = os.path.expanduser("~/.local/share/applications")
        src = glob.glob(os.path.join(apps, "*roblox*.desktop"))
        if not src:
            self.say("Roblox isn't installed yet (no launcher found).\n", self.tag_bad)
            return
        desk = os.path.expanduser("~/Desktop")
        os.makedirs(desk, exist_ok=True)
        dst = os.path.join(desk, "Roblox.desktop")
        shutil.copy(src[0], dst)
        os.chmod(dst, 0o755)
        self.say(f"Roblox launcher placed on your desktop ({dst}).\n", self.tag_ok)


def main():
    App()
    Gtk.main()


if __name__ == "__main__":
    main()
