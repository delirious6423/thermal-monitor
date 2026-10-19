# Sensors and file formats

The Rust indicator uses Linux files and D-Bus, with no sensor network service.
The supported GPU is currently index 0. Errors in one input do not hide the others.

| Reading | Input | Notes |
| --- | --- | --- |
| CPU utilization | `/proc/stat` | Difference between samples; excludes guest double counting and treats iowait as idle |
| RAM | `/proc/meminfo` | `MemTotal - MemAvailable`, converted from KiB to GiB |
| CPU temperature | hwmon `coretemp` / `k10temp` | `temp1_input` in millidegrees Celsius |
| Fans | hwmon `hp` | `fan1_input` and `fan2_input` in RPM |
| NVMe temperature | hwmon `nvme` | `temp1_input`; model when discoverable |
| GPU temperatures | `/run/thermal-monitor/gputemps.json` | Core, junction/hotspot and VRAM in Celsius |
| GPU load, VRAM allocation | Newest `gpu-YYYYMMDD.jsonl` | Driver index `"0"`; utilization in percent, memory in MiB |

The indicator bounds sensor/snapshot reads to 64 KiB and GPU log tails to 16 KiB.
It checks at most 128 hwmon entries. A partial final log line is skipped; an
expired or invalid newest sample does not revive older samples. The optional
Conky helper reads its own Linux inputs and has the hardware mapping described
in the README; it is not another client of the Rust sampler.

## GPU snapshot

An external writer atomically replaces this file. Example values are synthetic:

```json
{
  "timestamp": 1792454400000,
  "received_monotonic": 12345.0,
  "source": "gputemps-experimental-mmio",
  "gpus": [{"index": 0, "core": 45, "junction": 52, "vram": 50}]
}
```

`timestamp` is Unix **milliseconds**. The Rust reader rejects future timestamps
and values older than 15 seconds, and treats GPU temperatures of zero as unavailable.
`received_monotonic` is Python's `time.monotonic()` in **seconds** on the same
machine/boot; the Python logger/overlay use it for the 15-second freshness check.
Both clocks must describe a newly received sample, never a stale cached sample
restamped as fresh. Missing temperatures should be JSON `null`.

The included wrapper preserves the upstream timestamp and adds monotonic receipt
time. It does not validate the physical accuracy of undocumented registers.

## GPU log

Daily files live under `~/.local/state/thermal-monitor`. Each line is a JSON
object; metadata records may precede sample records:

```json
{"type":"sample","timestamp":"2026-10-20T12:00:00+00:00","gpus":[{"index":"0","utilization.gpu":"40","memory.used":"4096","memory.total":"16384"}]}
```

The timestamp is timezone-aware RFC 3339. Values may be JSON numbers or numeric
strings in the Rust reader. The collector writes NVIDIA CSV fields as strings.
Future samples or samples older than 20 seconds are unavailable. Memory fields
are NVIDIA **MiB**, converted to GiB by dividing by 1024; utilization is 0–100%.
Memory must be finite with `0 <= used <= total` and `total > 0`. Allocation and
load are validated independently. Older records without memory fields still
provide load; VRAM allocation displays `N/A`.

The full collector also records GPU name/PCI location, temperature and power
fields, thermal-throttling flags, experimental temperature snapshots, CPU
throttle counters and boot metadata. `temperature.gpu` in the log is currently
recorded for analysis, not used as a fallback by the Rust temperature display.

## Desktop interface

The indicator owns `org.delirious6423.ThermalMonitor` on the session bus, exports
`/StatusNotifierItem` and `/MenuBar`, and registers with the session's
StatusNotifierWatcher. Numeric panel text is the `XAyatanaLabel` extension.
Menu updates publish changed labels without rebuilding open submenus; the
Show/Hide action follows the actual `thermal-desktop.service` state.

Menu actions execute fixed `systemctl --user` operations or `xdg-open` on the log
directory. Sensor strings are not used as commands. The optional overlay writes
presentation files into `$XDG_RUNTIME_DIR/thermal-desktop`; they are disposable
and are not the seven-day history.
