#!/usr/bin/env bash
# Disposable VM only. Never run this on a user's desktop.
set -euo pipefail
[[ $(hostname) == thermal-release-test ]]
export XDG_RUNTIME_DIR=/run/user/1000 DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
cd "$HOME/thermal-test"
mkdir -p ~/.config/systemd/user /tmp/thermal-fixture
echo '{}' > /tmp/thermal-fixture/control.json
cat > /tmp/thermal-release-sway.conf <<'EOF'
output HEADLESS-1 mode 1280x720
seat seat0 fallback true
xwayland force
for_window [class="ThermalMonitor"] floating enable, border none
EOF
systemd-run --user --unit=thermal-test-compositor --collect \
    --setenv=WLR_BACKENDS=headless --setenv=WLR_RENDERER=pixman \
    --setenv=WLR_LIBINPUT_NO_DEVICES=1 --setenv=XWAYLAND_NO_GLAMOR=1 \
    sway -c /tmp/thermal-release-sway.conf
for _ in {1..60}; do
    for socket in "$XDG_RUNTIME_DIR"/wayland-*; do
        if [[ -S "$socket" ]]; then export WAYLAND_DISPLAY; WAYLAND_DISPLAY=$(basename "$socket"); break 2; fi
    done
    sleep 0.25
done
[[ -n ${WAYLAND_DISPLAY:-} ]]
export DISPLAY=:0 THERMAL_MONITOR_ROOT=/tmp/thermal-fixture
systemctl --user import-environment DISPLAY WAYLAND_DISPLAY THERMAL_MONITOR_ROOT
echo '{"layer":"top","modules-right":["tray"],"tray":{"icon-size":20}}' > /tmp/thermal-release-waybar.json
systemd-run --user --unit=thermal-test-waybar --collect --setenv="WAYLAND_DISPLAY=$WAYLAND_DISPLAY" \
    waybar -c /tmp/thermal-release-waybar.json
systemd-run --user --unit=thermal-test-fixtures --collect \
    python3 "$PWD/tests/vm/fixture-server.py"
cat > ~/.config/systemd/user/thermal-desktop.service <<'EOF'
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/touch /tmp/thermal-graphs-active
ExecStop=/usr/bin/rm -f /tmp/thermal-graphs-active
EOF
systemctl --user daemon-reload
cat > /tmp/thermal-release-nvidia-smi <<'PY'
#!/usr/bin/python3
import csv
import sys
fields = sys.argv[1].split('=', 1)[1].split(',')
values = {'index': '0', 'name': 'Demo GPU', 'memory.used': '4096',
          'memory.total': '16384', 'utilization.gpu': '40', 'temperature.gpu': '45'}
csv.writer(sys.stdout).writerow([values.get(field, '0') for field in fields])
PY
sudo install -m755 /tmp/thermal-release-nvidia-smi /usr/bin/nvidia-smi
tar -xzf "$HOME/release-test/thermal-monitor-linux-x86_64.tar.gz" -C "$HOME/release-test"
cp "$HOME/release-test/thermal-monitor/bin/thermal-monitor" "$PWD/thermal-monitor"
