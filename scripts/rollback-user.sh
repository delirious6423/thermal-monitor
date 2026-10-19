#!/usr/bin/env bash
set -euo pipefail
config_dir=${XDG_CONFIG_HOME:-"$HOME/.config"}
state_dir=${XDG_STATE_HOME:-"$HOME/.local/state"}/thermal-monitor-install
unit="$config_dir/systemd/user/thermal-topbar.service"
[[ -f "$state_dir/previous.service" ]] || { echo 'No previous service unit has been saved.' >&2; exit 1; }
systemctl --user stop thermal-topbar.service
cp -p "$state_dir/previous.service" "$unit"
systemctl --user daemon-reload
systemctl --user start thermal-topbar.service
systemctl --user is-active --quiet thermal-topbar.service
echo 'Previous top-bar service restored.'
