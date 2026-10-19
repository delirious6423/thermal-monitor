#!/usr/bin/env bash
# Disposable VM only. Never run this on the user's desktop.
set -euo pipefail
[[ $(hostname) == thermal-ux-test ]]
export XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
cd "$HOME/thermal-test"
mkdir -p results ~/.config/systemd/user /tmp/thermal-fixture
echo '{}' > /tmp/thermal-fixture/control.json
cat > /tmp/thermal-sway.conf <<'EOF'
output HEADLESS-1 mode 1440x1000
seat seat0 fallback true
xwayland disable
EOF
systemd-run --user --unit=thermal-test-compositor --setenv=WLR_BACKENDS=headless \
    --setenv=WLR_RENDERER=pixman --setenv=WLR_LIBINPUT_NO_DEVICES=1 sway -c /tmp/thermal-sway.conf
capture_dir="$HOME/.local/share/gnome-shell/extensions/thermal-preview@local"
mkdir -p "$capture_dir"
cat > "$capture_dir/metadata.json" <<'EOF'
{"uuid":"thermal-preview@local","name":"Disposable VM capture","description":"VM-only capture control","shell-version":["50"]}
EOF
cat > "$capture_dir/extension.js" <<'EOF'
import GLib from 'gi://GLib';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';
export default class Capture extends Extension {
    enable() { if (GLib.get_host_name() !== 'thermal-ux-test') throw new Error('VM only'); global.context.unsafe_mode = true; }
    disable() { global.context.unsafe_mode = false; }
}
EOF
gsettings set org.gnome.shell enabled-extensions "['thermal-preview@local','ubuntu-appindicators@ubuntu.com']"
gsettings set org.gnome.desktop.interface enable-animations false
gsettings set org.gnome.desktop.interface color-scheme prefer-dark
gsettings set org.gnome.desktop.background picture-uri ''
gsettings set org.gnome.desktop.background picture-uri-dark ''
gsettings set org.gnome.desktop.background primary-color '#233344'
gsettings set org.gnome.desktop.session idle-delay 0
systemctl --user start pipewire.service wireplumber.service
systemd-run --user --unit=thermal-test-gnome --setenv=WAYLAND_DISPLAY=wayland-1 \
    --setenv=LIBGL_ALWAYS_SOFTWARE=1 --setenv=GSK_RENDERER=cairo --setenv=GTK_A11Y=none \
    --setenv=XDG_SESSION_TYPE=wayland --setenv=XDG_CURRENT_DESKTOP=GNOME \
    gnome-shell --wayland --devkit --wayland-display=gnome-preview --no-x11
cat > ~/.config/systemd/user/thermal-desktop.service <<'EOF'
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/touch /tmp/thermal-graphs-active
ExecStop=/usr/bin/rm -f /tmp/thermal-graphs-active
EOF
cat > ~/.config/systemd/user/thermal-topbar.service <<'EOF'
[Service]
ExecStart=%h/thermal-test/thermal-monitor --root /tmp/thermal-fixture
EOF
systemctl --user daemon-reload
systemd-run --user --unit=thermal-test-fixtures /usr/bin/python3 "$HOME/thermal-test/tests/vm/fixture-server.py"
systemctl --user start thermal-topbar.service
