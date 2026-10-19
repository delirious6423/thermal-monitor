# Privacy and release checks

The indicator and optional collector/overlay do not send readings anywhere.
There is no analytics, crash uploader, network listener or background update check.
They use local kernel files, local GPU tools and the session D-Bus.

The installer contacts GitHub over HTTPS only to download the release and its
checksums. GitHub necessarily receives the normal download request, including the
client IP. The installer does not attach usernames, machine IDs, environment
variables, sensor values or logs. Offline installation is also supported.

## What stays local

- Sensor history: `~/.local/state/thermal-monitor`. It can contain boot IDs,
  hardware models, PCI locations, timestamps and temperature/load readings.
- Installer receipt and original-file backups: the `thermal-monitor-install`
  directory under `XDG_STATE_HOME`, or `~/.local/state` by default. Receipts and
  backups are private files. They are not part of release packages.
- Service diagnostics in the local journal. Review them before sharing a report.

Uninstall restores the files/services that existed before installation and keeps
sensor logs and backups. It refuses to overwrite files edited after installation.
The installer never installs or changes the privileged GPU reader.

## What is public

The repository owner's GitHub username, GitHub noreply commit identity and D-Bus
application identifier are intentionally public. Screenshots use demo GPU values
in disposable VMs. Source examples describe supported hardware, not device serial
numbers. Original machine telemetry and earlier private development history were
excluded when the public repository was created.

## Release safeguards

`scripts/build-release.py` builds a static musl executable from committed source.
It replaces custom compiler flags with path-remapping flags for source, Cargo,
toolchain and home directories. Release debug symbols are stripped. It packages
only selected tracked files, with numeric owner/group zero, empty owner names,
the public commit timestamp and a deterministic gzip header.

Before publication, `scripts/audit-release.py` checks reachable `HEAD` history,
commit email addresses, archive contents and the executable for common credential
formats, private keys, private home/mount paths and email addresses outside
the explicitly public GitHub/extension identities. It rejects unsafe archive
paths, links, non-normalized owner metadata, extra/missing manifest files and
dynamically linked/debug executables. Its public `PRIVACY-AUDIT.json` records the
source commit and check results without copying matched values or local paths.

SHA-256 files detect mismatches and corrupted downloads. They are downloaded from
the same GitHub release, so they do not provide an independent publisher signature
or protect against a compromised publisher account. These automated scans and
manual review reduce accidental disclosure; they are not a certification that
every possible secret or personal detail can be recognized.
