# Setup and removal

These instructions target Ubuntu with systemd and a working desktop session.
Build first with `cargo build --release --locked`. Run user commands without sudo.
The service templates are examples; review paths and sensor support for your system.

## Existing installation

If these services already exist, keep their settings and use the installers:

```sh
bash scripts/install-user.sh
python3 scripts/install-desktop.py  # Requires the existing overlay files
```

The top-bar installer retains the previous service unit. The overlay installer
backs up its config and feeder and preserves visibility. The optional
`python3 scripts/enable-vram-logging.py` patches an older version of this project's
collector to record allocation; the included collector already has those fields.

## Fresh user installation

The following commands install the indicator and its GNOME login entry. They
do not install the logger, Conky or the root bridge.

```sh
install -Dm755 target/release/thermal-monitor "$HOME/.local/bin/thermal-monitor"
install -Dm644 integration/systemd/thermal-topbar.service \
  "$HOME/.config/systemd/user/thermal-topbar.service"
install -Dm644 integration/thermal-topbar.desktop \
  "$HOME/.config/autostart/thermal-topbar.desktop"
systemctl --user daemon-reload
systemctl --user start thermal-topbar.service
```

GNOME must have an enabled AppIndicator extension. In a different desktop, check
that a StatusNotifierItem tray host is running. The unit is started at GNOME login
by the desktop entry; it is deliberately not enabled as a background login service.

## Optional seven-day collector

Requires Python 3, `sysstat` and a working `/usr/bin/nvidia-smi`. On Ubuntu,
`sudo apt install sysstat` supplies the CPU/disk/power collector. NVIDIA drivers
are managed separately. Unsupported query fields may cause a GPU error record;
check the local JSONL log rather than treating missing GPU fields as zero.

```sh
install -Dm755 integration/thermal-monitor.py \
  "$HOME/.local/lib/thermal-monitor/thermal-monitor.py"
install -Dm644 integration/systemd/thermal-monitor.service \
  "$HOME/.config/systemd/user/thermal-monitor.service"
systemctl --user daemon-reload
systemctl --user enable --now thermal-monitor.service
```

The collector runs every five seconds, supervises `sadc` and writes daily JSONL
GPU logs plus sysstat archives. At each observed date change it removes files
older than six days before today: seven calendar dates, not a rolling 168 hours.
It is independent of the display services. User services normally stop after
logout unless the administrator has enabled lingering for that user.

## Optional desktop graphs

Requires Python 3, Conky with X11 support, and Xwayland in a Wayland session.
Ubuntu's `conky-std` provides the required X11 features. Review the CPU/fan/SSD
mapping in `integration/desktop-widget.py` for different hardware.

```sh
install -Dm755 integration/desktop-widget.py \
  "$HOME/.local/lib/thermal-monitor/desktop-widget.py"
install -Dm644 integration/thermal.conf "$HOME/.config/conky/thermal.conf"
install -Dm644 integration/systemd/thermal-desktop.service \
  "$HOME/.config/systemd/user/thermal-desktop.service"
systemctl --user daemon-reload
```

Choose **Show graphs** in the top-bar menu, or run
`systemctl --user start thermal-desktop.service`. Hide with the matching menu
action or `systemctl --user stop thermal-desktop.service`. It is not enabled at
login. The graphical session must supply `DISPLAY` and `XDG_RUNTIME_DIR` to its
user service manager. Config uses the top-right corner with a 24 px horizontal
and 52 px vertical gap; the window manager may handle placement differently.

## Optional GPU temperature bridge

GPU utilization and allocation need no root bridge. Current GPU **temperature**
readings use `/run/thermal-monitor/gputemps.json`, supplied by the separate bridge.
Missing bridge data leaves those temperatures unavailable; CPU/RAM and GPU load
continue to work.

The provided reference wrapper expects a separately built
[gputemps](https://github.com/ThomasBaruzier/gddr6-core-junction-vram-temps)
binary at `/usr/local/libexec/thermal-monitor/gputemps`. Follow that project's
compatibility and build documentation. It reads undocumented registers through
device memory as root; unsupported hardware/readings may be inaccurate. Installing
this system service is an explicit administrator action, separate from user setup.

After reviewing and building the reader, an administrator can install the binary
and the included wrapper/unit:

```sh
sudo install -Dm755 /path/to/built/gputemps /usr/local/libexec/thermal-monitor/gputemps
sudo install -Dm644 integration/gputemps-bridge.py \
  /usr/local/libexec/thermal-monitor/gputemps-bridge.py
sudo install -Dm644 integration/systemd/gputemps-bridge.service \
  /etc/systemd/system/gputemps-bridge.service
sudo systemctl daemon-reload
sudo systemctl enable --now gputemps-bridge.service
```

The wrapper selects GPU 0, samples every five seconds, stamps receipt time and
atomically replaces a world-readable JSON snapshot. The unit restricts its
capability set and writable paths; it still has privileged device-memory access.
Do not run the Rust indicator or Conky as root.

## Diagnose, undo or remove

```sh
systemctl --user status thermal-topbar thermal-monitor thermal-desktop
journalctl --user -u thermal-topbar -u thermal-monitor -u thermal-desktop --since today
./target/release/thermal-monitor --once
```

`--root PATH` redirects telemetry reads for fixtures, and `--home PATH` selects
the directory containing `.local/state/thermal-monitor`. `--interval-seconds`
changes indicator polling; it does not change the collector's cadence or freshness
limits. `--once` does not open the tray and its first CPU load is unavailable.

For an installation upgraded using the reversible scripts:

```sh
bash scripts/rollback-user.sh
python3 scripts/install-desktop.py --restore
```

For a fresh installation, stop the three user services, disable
`thermal-monitor.service`, and remove the installed files listed above, including
the login entry. Then run `systemctl --user daemon-reload`. Keep
`~/.local/state/thermal-monitor` if you want to retain your logs.

If installed, the root bridge is removed separately by an administrator:
disable/stop `gputemps-bridge.service`, remove its system unit and the two files
under `/usr/local/libexec/thermal-monitor`, then reload the system service manager.
Removing a display does not automatically remove the collector or root service.
