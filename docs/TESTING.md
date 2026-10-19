# Tests and measurements

Use Rust 1.99+ and Python 3. `cargo nextest run --all-features` is an alternative
to `cargo test --locked --all-features`. `cargo-deny`, `cargo-machete` and Nextest
are separate developer tools, not runtime dependencies.

```sh
cargo fmt --all --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test --locked --all-features
cargo test --locked --doc
cargo deny check
cargo machete
python3 tests/vm/check-desktop.py
python3 -m unittest discover -s tests -p test_release.py
```

Rust fixture tests cover CPU deltas/resets, memory parsing, stale/invalid GPU
samples, partial JSONL tails, independent VRAM parsing, menu structure and bounded
reads. Seven Python feeder regressions cover live/zero/invalid allocation, stale
and malformed newest samples, expired temperature and malformed devices.
Release tests cover checksum/manifest failures before execution, unsafe archive
entries, file/service rollback, removal order and privacy scanner regression cases.
See [RELEASING.md](RELEASING.md) for real installer tests in a disposable VM.

## Disposable desktop tests

GUI and service tests belong in a disposable Ubuntu VM, not a user's desktop.
The helpers install/run services and use fixed fixture paths. Never run the VM
guest scripts on a real desktop. GNOME capture helpers deliberately enable shell
debug evaluation only on the guarded VM hostname `thermal-ux-test`.

`tests/vm/run.sh` requires the separate `throwvm.py` KVM harness, a release binary,
and an Ubuntu image. Set `THROWVM` to that tool and `VM_DIR` to a writable cache
directory. It provisions Sway/Waybar, copies test helpers and the binary, runs
protocol/install/rollback checks and process measurements, and collects results
under `target/vm-test`. The harness is not bundled; see the script for its CLI
contract. The original convenience wrapper was syntax checked; the guest
workflow was exercised over SSH, not through a complete wrapper run.

For the standalone desktop-card checks, provision a VM with
`tests/vm/provision.sh`, hostname `thermal-ux-test`, user `tester` (UID 1000), and
copy this repository to `~/thermal-test`. In that VM:

```sh
cd ~/thermal-test
bash tests/vm/setup-desktop.sh
python3 tests/vm/check-desktop.py
python3 tests/vm/check-desktop-ui.py
```

These checks use real Conky/Xwayland rendering, native screenshots and OCR. They
verify install/restore while hidden and visible, live GPU/VRAM changes, stale
graphs, stable card size, recovery and clean Conky diagnostics. They use simulated
GPU readings and actual VM CPU/RAM. Their headless compositor handles placement
differently from GNOME; a screenshot proves layout, not universal window placement.

For GNOME tray checks, `tests/vm/provision-gnome.sh` supplies extra packages.
Copy the release executable to `~/thermal-test/thermal-monitor`, then run
`setup-gnome.sh`, `check-tray.py --gnome`, and `check-gnome-ui.py` from `tests/vm`
inside a fresh VM with the same guarded hostname. The native tests inspect actual
GNOME actors for live values, submenu stability and duplicate indicators.
`check-vram-logger.py` additionally tests simulated driver CSV through the real
collector and Rust reader, including the seven-day retention boundary.

## Recorded results, 2026-10-20

| Check | Result |
| --- | --- |
| Rust tests | 19 passed |
| Clippy / formatting / dependency checks | Passed |
| Doc tests | Passed; no doctests currently defined |
| Python overlay regressions | 7 passed |
| Tray protocol, native GNOME menu and Conky/Sway checks | Passed in disposable VMs |
| Logger CSV → record → Rust reader and retention | Passed with a simulated driver |
| Nested GNOME Conky rendering | Not established: software Xwayland initialization crashed |

Earlier GNU-build Rust indicator, 60 seconds / 61 VM samples, five-second cadence:
median RSS **5.15 MiB**, PSS **3.14 MiB**. CPU counters recorded 0.01 seconds,
near tick resolution. GNOME, the collector and Conky are excluded. No battery-life
or meaningful CPU-saving claim follows from this short idle run.

The initial port was measured against the preserved Python GTK/AppIndicator
baseline on the same changing VM fixture workload: median RSS 49.57 MiB versus
4.91 MiB, PSS 29.51 MiB versus 2.91 MiB. That was an earlier Rust build; it is
not a comparison with vitals-rs or an estimate of total desktop monitoring cost.

The Conky card measured **284 × 318 px**, down from **340 × 633 px** at the same
VM font scaling: 58% less area, with all four graphs retained. This is a layout
measurement, not a process-footprint measurement. Public screenshots are VM
captures. Machine-specific logs and original private development history are
excluded from the public repository; these measurements are reported observations,
not independently reproduced results.

## Binary release checks

The initial static musl build passed all 19 Rust tests on the musl target, the
doc-test command, seven overlay regressions and nine release-safeguard tests.
The exact release archive was installed in a disposable Ubuntu VM and passed the
real tray protocol checks, fresh/repeated install, no-start, optional collector
and Conky startup, edited-file protection, failed-start rollback and uninstall.
Original configuration and log contents survived removal. Its memory footprint
has not been remeasured; the earlier GNU-build measurements above are not static
release measurements. See the release's privacy audit for artifact checks.
