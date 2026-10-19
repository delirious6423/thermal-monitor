#!/usr/bin/env bash
# Runs only inside the disposable Ubuntu VM, as tester.
set -euo pipefail
XDG_RUNTIME_DIR="/run/user/$(id -u)"
export XDG_RUNTIME_DIR
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
project_root="$HOME/thermal-test"
cd "$project_root"
mkdir -p results "$HOME/.config/systemd/user" "$HOME/.local/lib/thermal-monitor"
cleanup() {
    systemctl --user stop thermal-topbar.service thermal-test-fixtures.service \
        thermal-test-waybar.service thermal-test-compositor.service thermal-desktop.service || true
    systemctl --user unset-environment THERMAL_MONITOR_ROOT
}
trap cleanup EXIT
cat > /tmp/thermal-sway.conf <<'SWAY'
output HEADLESS-1 mode 1280x720
seat seat0 fallback true
xwayland enable
SWAY
cat > /tmp/thermal-waybar.json <<'BAR'
{"layer":"top","position":"top","height":32,"modules-right":["tray"],"tray":{"icon-size":20,"spacing":8}}
BAR
echo '* { font-family: sans-serif; } window#waybar { background: #202530; color: white; }' > /tmp/thermal-waybar.css
systemd-run --user --unit=thermal-test-compositor --collect \
    --setenv=WLR_BACKENDS=headless --setenv=WLR_RENDERER=pixman \
    --setenv=WLR_LIBINPUT_NO_DEVICES=1 /usr/bin/sway -c /tmp/thermal-sway.conf
wayland_socket=''
for _ in {1..60}; do
    for socket in "$XDG_RUNTIME_DIR"/wayland-*; do
        if [[ -S "$socket" ]]; then wayland_socket=$(basename "$socket"); break; fi
    done
    [[ -n "$wayland_socket" ]] && break
    sleep 0.25
done
[[ -n "$wayland_socket" ]] || { journalctl --user -u thermal-test-compositor.service; exit 1; }
export WAYLAND_DISPLAY="$wayland_socket"
systemctl --user import-environment WAYLAND_DISPLAY
systemd-run --user --unit=thermal-test-waybar --collect --setenv="WAYLAND_DISPLAY=$WAYLAND_DISPLAY" \
    /usr/bin/waybar -c /tmp/thermal-waybar.json -s /tmp/thermal-waybar.css
mkdir -p /tmp/thermal-fixture
echo '{}' > /tmp/thermal-fixture/control.json
systemctl --user set-environment THERMAL_MONITOR_ROOT=/tmp/thermal-fixture
systemd-run --user --unit=thermal-test-fixtures --collect \
    /usr/bin/python3 "$project_root/tests/vm/fixture-server.py"
cat > "$HOME/.config/systemd/user/thermal-desktop.service" <<'UNIT'
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/touch /tmp/thermal-graphs-active
ExecStop=/usr/bin/rm -f /tmp/thermal-graphs-active
UNIT
python3 - <<'PY'
from pathlib import Path
import shutil
base = Path.home() / 'thermal-test/tests/vm/baseline'
dest = Path.home() / '.local/lib/thermal-monitor'
for name in ['topbar-monitor.py', 'desktop-widget.py']:
    text = (base / name).read_text()
    for original in ['/sys/class/hwmon', '/run/thermal-monitor/gputemps.json', '/proc/stat', '/proc/meminfo']:
        text = text.replace("'" + original + "'", "'/tmp/thermal-fixture" + original + "'")
    (dest / name).write_text(text)
PY
cat > "$HOME/.config/systemd/user/thermal-topbar.service" <<'UNIT'
[Unit]
Description=Python baseline
[Service]
ExecStart=/usr/bin/python3 %h/.local/lib/thermal-monitor/topbar-monitor.py
Environment=GDK_BACKEND=wayland
UNIT
systemctl --user daemon-reload
if [[ ${SKIP_BASELINE:-0} != 1 || ! -s results/python-vm.json ]]; then
    systemctl --user start thermal-topbar.service
    sleep 10
    python3 scripts/measure-process.py "$(systemctl --user show thermal-topbar.service -p MainPID --value)" \
        --seconds 60 --output results/python-vm.json
    echo 'PASS Python baseline measured on fixture workload'
else
    echo 'Using the existing Python baseline from this VM and workload'
fi

bash scripts/install-user.sh "$project_root/thermal-monitor"
python3 tests/vm/check-tray.py
sleep 6
python3 scripts/measure-process.py "$(systemctl --user show thermal-topbar.service -p MainPID --value)" \
    --seconds 60 --output results/rust-vm.json
grim results/rust-tray.png
echo 'PASS Rust measured on the same fixture workload'

python3 tests/vm/check-tray.py --quit-only
sleep 1
if systemctl --user is-active --quiet thermal-topbar.service; then
    echo 'FAIL menu quit left the service active' >&2; exit 1
fi
echo 'PASS quit action exits cleanly'
systemctl --user start thermal-topbar.service
bash scripts/rollback-user.sh
sleep 2
pid=$(systemctl --user show thermal-topbar.service -p MainPID --value)
grep -q python3 "/proc/$pid/cmdline"
echo 'PASS rollback restores the Python service'
bash scripts/install-user.sh "$project_root/thermal-monitor"
sleep 2
pid=$(systemctl --user show thermal-topbar.service -p MainPID --value)
[[ $(readlink "/proc/$pid/exe") == "$HOME/.local/bin/thermal-monitor" ]]
echo 'PASS reinstall restores the Rust service'
journalctl --user -u thermal-topbar.service --no-pager > results/service.log
