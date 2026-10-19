# Thermal Monitor

Safe Rust 2024, Rust 1.99+, committed Cargo.lock. Logic belongs in library modules;
main wires CLI and services. Keep changes small and explain hardware assumptions.

Keep root/MMIO access out of the Rust executable. Reuse the read-only GPU snapshot
and local logs. Missing, invalid or expired readings are unavailable, never live
zero. Bound reads and never build commands from sensor data. Preserve logger data
and keep rollback working.

Run the checks in README.md for code changes. GUI/service behavior is tested in a
disposable VM before deployment, never on a user's desktop. Do not publish actual
hardware logs, credentials or machine-specific paths. Performance claims need
measurements with stated workload, scope and limitations.
