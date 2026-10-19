#!/usr/bin/env bash
# Disposable VM only; use tests/vm/provision.sh and hostname thermal-ux-test.
set -euo pipefail
[[ $(hostname) == thermal-ux-test ]]
export XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
cd "$HOME/thermal-test"
mkdir -p results ~/.config/systemd/user ~/.config/conky ~/.local/lib/thermal-monitor /tmp/thermal-fixture
echo '{}' > /tmp/thermal-fixture/control.json
cat > /tmp/thermal-overlay-sway.conf <<'EOF'
output HEADLESS-1 mode 1440x1000
seat seat0 fallback true
xwayland force
for_window [class="ThermalMonitor"] floating enable, border none
EOF
systemd-run --user --unit=thermal-overlay-compositor --setenv=WLR_BACKENDS=headless \
    --setenv=WLR_RENDERER=pixman --setenv=WLR_LIBINPUT_NO_DEVICES=1 \
    --setenv=XWAYLAND_NO_GLAMOR=1 sway -c /tmp/thermal-overlay-sway.conf
systemd-run --user --unit=thermal-overlay-fixtures /usr/bin/python3 "$PWD/tests/vm/fixture-server.py"
cp tests/vm/baseline/desktop-widget.py ~/.local/lib/thermal-monitor/desktop-widget.py
cp tests/vm/baseline/thermal.conf ~/.config/conky/thermal.conf
cat > ~/.config/systemd/user/thermal-desktop.service <<'EOF'
[Service]
ExecStart=/usr/bin/python3 %h/.local/lib/thermal-monitor/desktop-widget.py
Environment=DISPLAY=:0 THERMAL_MONITOR_ROOT=/tmp/thermal-fixture
EOF
systemctl --user daemon-reload
python3 scripts/install-desktop.py
! systemctl --user is-active --quiet thermal-desktop
python3 scripts/install-desktop.py
python3 scripts/install-desktop.py --restore
cmp ~/.config/conky/thermal.conf tests/vm/baseline/thermal.conf
python3 scripts/install-desktop.py
systemctl --user start thermal-desktop
