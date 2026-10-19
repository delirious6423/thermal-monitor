#!/usr/bin/env bash
# Replace only the existing unprivileged top-bar service; retain its prior unit.
set -euo pipefail
project_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
binary=${1:-"$project_root/target/release/thermal-monitor"}
config_dir=${XDG_CONFIG_HOME:-"$HOME/.config"}
state_dir=${XDG_STATE_HOME:-"$HOME/.local/state"}/thermal-monitor-install
unit="$config_dir/systemd/user/thermal-topbar.service"
[[ $(id -u) != 0 ]] || { echo 'Run as your desktop user, without sudo.' >&2; exit 1; }
[[ -x "$binary" && -f "$unit" ]] || { echo 'Need a release executable and the existing thermal-topbar.service.' >&2; exit 1; }
mkdir -p "$state_dir" "$HOME/.local/bin"
chmod 700 "$state_dir"
if [[ ! -f "$state_dir/previous.service" ]]; then
    cp -p "$unit" "$state_dir/previous.service"
fi
rollback_on_error() {
    local status=$?
    trap - ERR
    bash "$project_root/scripts/rollback-user.sh" || true
    exit "$status"
}
trap rollback_on_error ERR
temporary=$(mktemp "$HOME/.local/bin/.thermal-monitor.XXXXXX")
install -m 755 "$binary" "$temporary"
systemctl --user stop thermal-topbar.service
mv "$temporary" "$HOME/.local/bin/thermal-monitor"
cat > "$unit" <<'UNIT'
[Unit]
Description=Compact CPU and GPU top-bar monitor (Rust)
PartOf=graphical-session.target
After=graphical-session.target

[Service]
Type=simple
ExecStart=%h/.local/bin/thermal-monitor
Restart=on-failure
RestartSec=10
UMask=0077
Nice=10
NoNewPrivileges=true
UNIT
systemctl --user daemon-reload
systemctl --user start thermal-topbar.service
systemctl --user is-active --quiet thermal-topbar.service
trap - ERR
echo 'Rust top-bar service started; previous service unit saved for rollback.'
