# Changelog

## 0.1.1 — 2026-10-20

- Show CPU package and GPU power in watts by default in the top bar and menu.
- Reuse logged NVIDIA power; add read-only Intel RAPL package-0 averages to the
  optional privileged snapshot bridge. Existing seven-day logs retain both.
- Expire power readings with their source; unknown values remain `N/A`.

## 0.1.0 — 2026-10-20

Initial public source version, tested on Ubuntu/GNOME with NVIDIA GPU 0.

- Prebuilt static Linux x86-64 release with checksums and a public privacy audit.
- User-level installer with optional logging/overlay, updates, original-file
  backups, failed-update rollback and removal that retains sensor history.

- Safe Rust StatusNotifierItem indicator with numeric CPU/GPU panel readings.
- CPU, GPU, VRAM allocation and RAM overview, plus additional sensors.
- Five-second sampling, validated inputs and independent stale/missing handling.
- Embedded thermometer icon and menu updates that preserve open submenus.
- Optional seven-calendar-day Python/sysstat/NVIDIA collector.
- Optional compact Conky card with four graphs and VRAM/RAM bars.
- Optional reference wrapper for a separately built experimental GPU reader.
- Reversible existing-installation updates, fresh-install service templates,
  fixture tests and disposable-desktop checks.
