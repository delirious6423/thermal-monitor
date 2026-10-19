#!/usr/bin/env bash
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -yq --no-install-recommends sway waybar grim tesseract-ocr fonts-dejavu-core \
    python3-gi gir1.2-gtk-3.0 gir1.2-ayatanaappindicator3-0.1 python3-dbus \
    dbus-user-session dbus-x11 xwayland conky-std
loginctl enable-linger tester
