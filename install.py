#!/usr/bin/env python3
"""Install Thermal Monitor for your user; no sudo, telemetry upload or root reader."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request

REPOSITORY = 'https://github.com/delirious6423/thermal-monitor'
ASSET = 'thermal-monitor-linux-x86_64.tar.gz'
MAX_DOWNLOAD = 32 * 1024 * 1024


def digest(content):
    return hashlib.sha256(content).hexdigest()


def atomic_write(path, content, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            temporary.chmod(mode)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def ctl(*arguments, check=False):
    result = subprocess.run(['systemctl', '--user', *arguments],
                            capture_output=True, text=True, timeout=30)
    if check and result.returncode:
        raise RuntimeError(result.stderr.strip() or f'systemctl {arguments} failed')
    return result


def unit_state(name):
    return {'active': ctl('is-active', '--quiet', name).returncode == 0,
            'enabled': ctl('is-enabled', name).stdout.strip() == 'enabled'}


def set_units(states, bus):
    for name, state in states.items():
        if name == 'thermal-monitor.service' and state['enabled']:
            ctl('--no-reload', 'enable', name, check=True)
    if bus:
        ctl('daemon-reload', check=True)
        for name, state in states.items():
            if state['active']:
                ctl('start', name, check=True)
                if ctl('is-active', '--quiet', name).returncode:
                    raise RuntimeError(f'{name} did not remain active')


def replace_files(files, states, bus):
    """Restore the pre-operation files and services if an update fails."""
    previous = {}
    for path in files:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise RuntimeError(f'Refusing to replace a symlink or non-file: {path}')
        previous[path] = (path.read_bytes(), stat.S_IMODE(path.stat().st_mode)) if path.exists() else None
    previous_units = {name: unit_state(name) for name in states}
    try:
        if bus:
            for name, state in previous_units.items():
                if state['active']:
                    ctl('stop', name, check=True)
        if previous_units.get('thermal-monitor.service', {}).get('enabled'):
            ctl('--no-reload', 'disable', 'thermal-monitor.service', check=True)
        for path, replacement in files.items():
            if replacement is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, *replacement)
        set_units(states, bus)
    except BaseException:
        if bus:
            for name in states:
                ctl('stop', name)
        if 'thermal-monitor.service' in states and unit_state('thermal-monitor.service')['enabled']:
            ctl('--no-reload', 'disable', 'thermal-monitor.service')
        for path, replacement in previous.items():
            if replacement is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, *replacement)
        set_units(previous_units, bus)
        raise
    return previous


def checksums(content):
    result = {}
    for line in content.decode('ascii').splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        if not match or match[2] in result:
            raise ValueError('Invalid or duplicate checksum entry')
        result[match[2]] = match[1]
    return result


def fetch(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'thermal-monitor-installer'})
    with urllib.request.urlopen(request, timeout=30) as response:
        if not response.geturl().startswith('https://'):
            raise ValueError('Refusing a non-HTTPS download')
        data = response.read(MAX_DOWNLOAD + 1)
    if len(data) > MAX_DOWNLOAD:
        raise ValueError('Release download exceeds the size limit')
    return data


def unpack(archive, destination):
    total = 0
    with tarfile.open(archive, 'r:gz') as package:
        for count, member in enumerate(package, 1):
            parts = PurePosixPath(member.name).parts
            if count > 256 or not parts or parts[0] != 'thermal-monitor' or '..' in parts:
                raise ValueError('Invalid release archive path or too many files')
            if not member.isdir() and not member.isfile():
                raise ValueError('Release archives must not contain links or special files')
            target = destination.joinpath(*parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            total += member.size
            if member.size < 0 or total > 64 * 1024 * 1024 or target.exists():
                raise ValueError('Invalid release archive size or duplicate file')
            target.parent.mkdir(parents=True, exist_ok=True)
            with package.extractfile(member) as stream:
                target.write_bytes(stream.read(member.size + 1))
            target.chmod(0o755 if parts[-2:] == ('bin', 'thermal-monitor') else 0o644)
    return destination / 'thermal-monitor'


def verify_payload(root):
    actual = set()
    for path in root.rglob('*'):
        if path.is_symlink():
            raise ValueError('Release payload must not contain symlinks')
        if path.is_file() and path != root / 'MANIFEST.sha256':
            actual.add(path.relative_to(root).as_posix())
    manifest = checksums((root / 'MANIFEST.sha256').read_bytes())
    if actual != set(manifest):
        raise ValueError('Release payload does not match its manifest')
    for name in actual:
        if digest((root / name).read_bytes()) != manifest[name]:
            raise ValueError(f'Checksum mismatch: {name}')
    version = (root / 'VERSION').read_text().strip()
    if not re.fullmatch(r'\d+\.\d+\.\d+', version):
        raise ValueError('Invalid release version')
    output = subprocess.check_output([str(root / 'bin/thermal-monitor'), '--version'], text=True, timeout=10)
    if output.strip() != 'thermal-monitor ' + version:
        raise ValueError('Binary version does not match the release')
    return version


def targets(home, config):
    return {
        'binary': ('bin/thermal-monitor', home / '.local/bin/thermal-monitor', 0o755),
        'installer': ('install.py', home / '.local/lib/thermal-monitor/install.py', 0o755),
        'topbar': ('integration/systemd/thermal-topbar.service', config / 'systemd/user/thermal-topbar.service', 0o644),
        'login': ('integration/thermal-topbar.desktop', config / 'autostart/thermal-topbar.desktop', 0o644),
        'logger': ('integration/thermal-monitor.py', home / '.local/lib/thermal-monitor/thermal-monitor.py', 0o755),
        'logger-unit': ('integration/systemd/thermal-monitor.service', config / 'systemd/user/thermal-monitor.service', 0o644),
        'overlay': ('integration/desktop-widget.py', home / '.local/lib/thermal-monitor/desktop-widget.py', 0o755),
        'overlay-config': ('integration/thermal.conf', config / 'conky/thermal.conf', 0o600),
        'overlay-unit': ('integration/systemd/thermal-desktop.service', config / 'systemd/user/thermal-desktop.service', 0o644),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release', help='Install a pinned tag, e.g. v0.1.0; default: latest')
    parser.add_argument('--archive', type=Path, help='Offline archive with SHA256SUMS beside it')
    parser.add_argument('--with-logger', action='store_true', help='Enable the independent seven-day collector')
    parser.add_argument('--with-overlay', action='store_true', help='Install desktop graphs, initially hidden')
    parser.add_argument('--no-start', action='store_true', help='Do not start new services now; preserve running services')
    parser.add_argument('--uninstall', action='store_true', help='Restore original files/services; keep logs and backups')
    args = parser.parse_args()
    if os.geteuid() == 0:
        parser.error('Run as your desktop user, without sudo')
    if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'amd64'):
        parser.error('This release supports Linux x86-64 only')
    if args.release and not re.fullmatch(r'v\d+\.\d+\.\d+', args.release):
        parser.error('--release must be a version tag such as v0.1.0')
    if args.uninstall and any((args.release, args.archive, args.with_logger, args.with_overlay, args.no_start)):
        parser.error('--uninstall cannot be combined with install options')
    if not shutil.which('systemctl'):
        parser.error('The installer needs systemd user services')
    home = Path.home()
    config = Path(os.environ.get('XDG_CONFIG_HOME', home / '.config'))
    state = Path(os.environ.get('XDG_STATE_HOME', home / '.local/state')) / 'thermal-monitor-install'
    if not all(path.is_absolute() for path in (home, config, state)):
        parser.error('HOME and XDG directories must be absolute paths')
    receipt_file = state / 'release.json'
    backup = state / 'release-backup'
    receipt = json.loads(receipt_file.read_text()) if receipt_file.exists() else None
    mapping = targets(home, config)
    if receipt and (receipt.get('schema') != 1 or receipt.get('config') != str(config)
                    or not set(receipt.get('files', {})) <= mapping.keys()
                    or not set(receipt.get('units', {})) <= {'thermal-topbar.service', 'thermal-monitor.service', 'thermal-desktop.service'}):
        raise ValueError('Incompatible install receipt or changed XDG_CONFIG_HOME')
    bus = ctl('show-environment').returncode == 0
    if not bus and not args.no_start:
        parser.error('No user service manager; log in to your desktop, or install with --no-start')

    if args.uninstall:
        if receipt is None:
            parser.error('No release installation receipt found; nothing was removed')
        files = {}
        for name, entry in receipt['files'].items():
            path = mapping[name][1]
            if path.is_symlink() or (path.exists() and digest(path.read_bytes()) != entry['installed']):
                raise ValueError(f'File changed since installation; preserving all files: {path}')
            files[path] = ((backup / name).read_bytes(), entry['original_mode']) if entry['original'] else None
        replace_files(files, receipt['units'], bus)
        receipt_file.unlink()
        print('Uninstalled. Original files/services restored; sensor logs and backups retained.')
        return

    selected = {'binary', 'installer', 'topbar', 'login'} | (set(receipt['files']) if receipt else set())
    if args.with_logger:
        selected |= {'logger', 'logger-unit'}
    if args.with_overlay:
        selected |= {'overlay', 'overlay-config', 'overlay-unit'}
    if 'logger' in selected and (not Path('/usr/bin/nvidia-smi').is_file()
                                or not Path('/usr/libexec/sysstat/sadc').is_file()):
        parser.error('Logging requires nvidia-smi and /usr/libexec/sysstat/sadc (Ubuntu package: sysstat)')
    if 'overlay' in selected and not shutil.which('conky'):
        parser.error('Desktop graphs require Conky with X11 support (Ubuntu package: conky-std)')

    with tempfile.TemporaryDirectory(prefix='thermal-monitor-') as temporary:
        temporary = Path(temporary)
        local = Path(__file__).resolve().parent
        if args.archive:
            raw = args.archive.read_bytes()
            sums = checksums((args.archive.parent / 'SHA256SUMS').read_bytes())
        elif (local / 'MANIFEST.sha256').is_file() and (local / 'bin/thermal-monitor').is_file():
            raw = None
        else:
            base = REPOSITORY + ('/releases/download/' + args.release if args.release else '/releases/latest/download')
            sums = checksums(fetch(base + '/SHA256SUMS'))
            raw = fetch(base + '/' + ASSET)
        if raw is not None:
            if digest(raw) != sums.get(ASSET):
                raise ValueError('Release archive checksum mismatch; no installed files were changed')
            archive = temporary / ASSET
            archive.write_bytes(raw)
            local = unpack(archive, temporary / 'payload')
        version = verify_payload(local)
        if args.release and args.release != 'v' + version:
            raise ValueError('Requested release does not match the payload')
        units = {'thermal-topbar.service': unit_state('thermal-topbar.service')}
        if 'logger' in selected:
            units['thermal-monitor.service'] = unit_state('thermal-monitor.service')
        if 'overlay' in selected:
            units['thermal-desktop.service'] = unit_state('thermal-desktop.service')
        original_units = dict(receipt['units']) if receipt else {}
        for name, value in units.items():
            original_units.setdefault(name, value.copy())
        if bus and not args.no_start:
            units['thermal-topbar.service']['active'] = True
            if 'logger' in selected:
                units['thermal-monitor.service']['active'] = True
        if 'logger' in selected:
            units['thermal-monitor.service']['enabled'] = True
        new_receipt = {'schema': 1, 'version': version, 'config': str(config),
                       'units': original_units, 'files': dict(receipt['files']) if receipt else {}}
        files = {mapping[name][1]: ((local / mapping[name][0]).read_bytes(), mapping[name][2]) for name in selected}
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        backup.mkdir(exist_ok=True, mode=0o700)
        # Capture originals before touching either binaries or service files.
        for name in selected:
            path = mapping[name][1]
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise ValueError(f'Refusing to replace a symlink or non-file: {path}')
            if receipt and name in receipt['files'] and path.exists() and digest(path.read_bytes()) != receipt['files'][name]['installed']:
                raise ValueError(f'File changed since installation; preserving all installed files: {path}')
            if name not in new_receipt['files']:
                if path.exists():
                    atomic_write(backup / name, path.read_bytes())
                new_receipt['files'][name] = {'original': path.exists(),
                    'original_mode': stat.S_IMODE(path.stat().st_mode) if path.exists() else None}
            new_receipt['files'][name]['installed'] = digest(files[path][0])
        # Commit the receipt in the same rollback transaction as the app files.
        files[receipt_file] = (json.dumps(new_receipt, indent=2).encode() + b'\n', 0o600)
        replace_files(files, units, bus)
    print(f'Installed Thermal Monitor {version} for your user. Login startup is enabled.')
    print('GNOME needs an enabled AppIndicator extension. Desktop graphs remain hidden unless already visible.')
    print(f'Uninstall: python3 {shlex.quote(str(home / ".local/lib/thermal-monitor/install.py"))} --uninstall')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, tarfile.TarError) as error:
        print(f'Install failed: {error}', file=sys.stderr)
        raise SystemExit(1)
