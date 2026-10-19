#!/usr/bin/python3
"""Real installer/service checks. Requires the disposable release-test VM."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time

assert socket.gethostname() == 'thermal-release-test', 'Disposable VM only'
assert os.getuid() == 1000
os.environ['XDG_RUNTIME_DIR'] = '/run/user/1000'
os.environ['DBUS_SESSION_BUS_ADDRESS'] = 'unix:path=/run/user/1000/bus'
HOME = Path.home()
PROJECT = HOME / 'thermal-test'
ASSETS = HOME / 'release-test'
INSTALLER = PROJECT / 'install.py'
ARCHIVE = ASSETS / 'thermal-monitor-linux-x86_64.tar.gz'
RECEIPT = HOME / '.local/state/thermal-monitor-install/release.json'
BINARY = HOME / '.local/bin/thermal-monitor'
CONFIG = HOME / '.config/conky/thermal.conf'


def run(*command, success=True, env=None):
    result = subprocess.run(command, text=True, capture_output=True, timeout=120, env=env)
    assert (result.returncode == 0) == success, (command, result.returncode, result.stdout, result.stderr)
    return result


def active(name):
    return subprocess.run(['systemctl', '--user', 'is-active', '--quiet', name]).returncode == 0


def install(*flags, success=True, env=None):
    return run('python3', str(INSTALLER), '--archive', str(ARCHIVE), *flags, success=success, env=env)


result = run('sudo', 'python3', str(INSTALLER), '--archive', str(ARCHIVE), success=False)
assert 'without sudo' in result.stderr
with tempfile.TemporaryDirectory() as directory:
    bad = Path(directory) / ARCHIVE.name
    data = bytearray(ARCHIVE.read_bytes())
    data[-1] ^= 1
    bad.write_bytes(data)
    shutil.copy2(ASSETS / 'SHA256SUMS', Path(directory) / 'SHA256SUMS')
    result = run('python3', str(INSTALLER), '--archive', str(bad), success=False)
    assert 'checksum mismatch' in result.stderr and not BINARY.exists() and not RECEIPT.exists()
print('PASS root refusal and corrupt download rejected before file changes', flush=True)

install('--no-start')
assert BINARY.is_file() and not active('thermal-topbar')
assert RECEIPT.is_file() and (RECEIPT.stat().st_mode & 0o777) == 0o600
assert (HOME / '.config/autostart/thermal-topbar.desktop').is_file()
version = (ASSETS / 'thermal-monitor/VERSION').read_text().strip()
assert run(str(BINARY), '--version').stdout.strip() == 'thermal-monitor ' + version
install()
assert active('thermal-topbar')
run('python3', str(PROJECT / 'tests/vm/check-tray.py'))
install()
assert active('thermal-topbar') and not json.loads(RECEIPT.read_text())['files']['binary']['original']
print('PASS fresh install, no-start, actual static-binary tray protocol and repeated update', flush=True)

run('systemctl', '--user', 'stop', 'thermal-test-fixtures')
(HOME / '.local/state/thermal-monitor/gpu-20261020.jsonl').unlink(missing_ok=True)
CONFIG.parent.mkdir(parents=True, exist_ok=True)
CONFIG.write_text('Original VM configuration\n')
log = HOME / '.local/state/thermal-monitor/keep-this-private.log'
log.write_text('VM original log fixture\n')
install('--with-logger', '--with-overlay')
assert active('thermal-topbar') and active('thermal-monitor') and not active('thermal-desktop')
assert run('systemctl', '--user', 'is-enabled', 'thermal-monitor').stdout.strip() == 'enabled'
time.sleep(6)
sample = json.loads(run(str(BINARY), '--once').stdout)
assert sample['gpu_memory'] == {'used_mib': 4096.0, 'total_mib': 16384.0}, sample
run('systemctl', '--user', 'start', 'thermal-desktop')
time.sleep(3)
assert active('thermal-desktop')
print('PASS optional seven-day logger records VRAM and actual Conky service starts hidden', flush=True)

original_config = CONFIG.read_bytes()
CONFIG.write_bytes(original_config + b'\n# VM edit\n')
before = RECEIPT.read_bytes()
result = install(success=False)
assert 'File changed since installation' in result.stderr and RECEIPT.read_bytes() == before
result = run('python3', str(HOME / '.local/lib/thermal-monitor/install.py'), '--uninstall', success=False)
assert 'File changed since installation' in result.stderr and RECEIPT.read_bytes() == before
assert active('thermal-topbar') and active('thermal-monitor') and active('thermal-desktop')
CONFIG.write_bytes(original_config)
print('PASS edited files protected on update and removal; services left running', flush=True)

with tempfile.TemporaryDirectory() as directory:
    shim = Path(directory) / 'systemctl'
    shim.write_text('''#!/bin/sh
if [ "$*" = "--user start thermal-topbar.service" ] && [ ! -f "$HOME/thermal-fail-once" ]; then
    touch "$HOME/thermal-fail-once"
    echo 'Deliberate VM start failure' >&2
    exit 1
fi
exec /usr/bin/systemctl "$@"
''')
    shim.chmod(0o755)
    hashes = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in [BINARY, RECEIPT, CONFIG]}
    environment = {**os.environ, 'PATH': directory + ':' + os.environ['PATH']}
    result = install(success=False, env=environment)
    assert 'Deliberate VM start failure' in result.stderr
    for path, value in hashes.items():
        assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == value
    assert all(active(name) for name in ['thermal-topbar', 'thermal-monitor', 'thermal-desktop'])
print('PASS failed-start rollback restores files, receipt, enabled logger and visible overlay', flush=True)

run('python3', str(HOME / '.local/lib/thermal-monitor/install.py'), '--uninstall')
assert not BINARY.exists() and not RECEIPT.exists()
assert not (HOME / '.config/autostart/thermal-topbar.desktop').exists()
assert not active('thermal-topbar') and not active('thermal-monitor') and not active('thermal-desktop')
assert CONFIG.read_text() == 'Original VM configuration\n'
assert log.read_text() == 'VM original log fixture\n'
assert not (HOME / '.config/systemd/user/default.target.wants/thermal-monitor.service').is_symlink()
assert (HOME / '.local/state/thermal-monitor-install/release-backup').is_dir()
print('PASS uninstall restores original config and disabled services; logs/backups retained', flush=True)
