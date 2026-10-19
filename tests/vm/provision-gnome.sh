#!/usr/bin/env bash
# Additional GNOME packages for the disposable tray test VM.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -yq --no-install-recommends gnome-shell gnome-session-bin \
    gnome-settings-daemon gsettings-desktop-schemas dbus-user-session dbus-x11 \
    python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 libgtk-4-1 libadwaita-1-0 \
    fonts-dejavu-core adwaita-icon-theme gnome-shell-extension-appindicator \
    sway grim tesseract-ocr mutter-dev-bin pipewire wireplumber
loginctl enable-linger tester
