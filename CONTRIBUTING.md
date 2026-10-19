# Contributing

Open an issue or a small pull request describing the concrete behavior and how
you checked it. For hardware support, include the OS/desktop, GPU driver and
relevant hwmon names/units, with identifiers removed. Raw logs may include boot
IDs and hardware metadata; inspect them before sharing.

Keep the Rust indicator unprivileged and separate from optional integrations.
Unknown readings must remain unavailable; validate timestamps, ranges and units.
Avoid new dependencies for small changes. Do not silently broaden privileged
access or change logging retention.

Run the README's local checks. For a parser or logic fix, add a focused fixture
regression. Test UI/service behavior in a disposable VM and state which desktop
was tested. Describe performance measurements with their limits.

See [docs/TESTING.md](docs/TESTING.md) for the existing harness and baselines.
Contributions are under this repository's Apache-2.0 license.
