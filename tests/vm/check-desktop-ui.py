#!/usr/bin/python3
"""Native Conky rendering checks; disposable thermal-ux-test VM only."""
import json
import os
from pathlib import Path
import socket
import subprocess
import time

assert socket.gethostname() == 'thermal-ux-test', 'Disposable VM only'
os.environ['XDG_RUNTIME_DIR'] = '/run/user/1000'
os.environ['DBUS_SESSION_BUS_ADDRESS'] = 'unix:path=/run/user/1000/bus'
os.environ['WAYLAND_DISPLAY'] = 'wayland-1'
os.environ['SWAYSOCK'] = str(next(Path('/run/user/1000').glob('sway-ipc*.sock')))
HOME = Path.home()
RESULTS = HOME / 'thermal-test/results'
RESULTS.mkdir(exist_ok=True)
DATA = Path('/run/user/1000/thermal-desktop')
CONTROL = Path('/tmp/thermal-fixture/control.json')


def run(*args):
    return subprocess.check_output(args, text=True, timeout=15)


def windows(node):
    if node.get('window_properties', {}).get('class') == 'ThermalMonitor':
        yield node
    for child in node.get('nodes', []) + node.get('floating_nodes', []):
        yield from windows(child)


def capture(name):
    entries = list(windows(json.loads(run('swaymsg', '-t', 'get_tree'))))
    assert len(entries) == 1, entries
    rect = entries[0]['rect']
    target = RESULTS / (name + '.png')
    geometry = f"{rect['x']},{rect['y']} {rect['width']}x{rect['height']}"
    run('grim', '-g', geometry, str(target))
    text = run('tesseract', str(target), 'stdout', '--psm', '6')
    (RESULTS / (name + '.txt')).write_text(text)
    return rect, text


def wait_for(check):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(1)
    raise AssertionError('Timed out waiting for live Conky update')


installer = str(HOME / 'thermal-test/scripts/install-desktop.py')
run('python3', installer, '--restore')
assert run('systemctl', '--user', 'is-active', 'thermal-desktop').strip() == 'active'
run('python3', installer)
assert run('systemctl', '--user', 'is-active', 'thermal-desktop').strip() == 'active'
wait_for(lambda: (DATA / 'vram_label').read_text().startswith('25%'))
time.sleep(5)
rect, initial = capture('desktop-compact')
assert rect['width'] == 284, rect
assert rect['height'] < 350, rect
assert '4.0/16.0GiB' in ''.join(initial.split()) and '25%' in initial, initial
assert '40%' in initial and '45' in initial and 'Hotspot' in initial, initial

CONTROL.write_text(json.dumps({'vram_used': 8192, 'load': 75}))
wait_for(lambda: (DATA / 'vram_label').read_text().startswith('50%'))
time.sleep(5)
_, updated = capture('desktop-updated')
assert '8.0/16.0GiB' in ''.join(updated.split()) and '50%' in updated and '75%' in updated, updated

CONTROL.write_text(json.dumps({'age': 60}))
wait_for(lambda: (DATA / 'gpu_valid').read_text() == '0' and
         (DATA / 'vram_valid').read_text() == '0' and (DATA / 'gpu_load_valid').read_text() == '0')
time.sleep(5)
expired_rect, expired = capture('desktop-expired')
assert (expired_rect['width'], expired_rect['height']) == (rect['width'], rect['height']), expired_rect
assert expired.count('N/A') >= 3 and '4.0/16.0GiB' not in ''.join(expired.split()), expired

CONTROL.write_text('{}')
wait_for(lambda: (DATA / 'vram_label').read_text().startswith('25%'))
time.sleep(5)
rect, recovered = capture('desktop-compact')
assert '4.0/16.0GiB' in ''.join(recovered.split()) and '45' in recovered, recovered
invocation = run('systemctl', '--user', 'show', '-p', 'InvocationID', '--value', 'thermal-desktop').strip()
diagnostics = run('journalctl', '--user', '_SYSTEMD_INVOCATION_ID=' + invocation, '--no-pager', '-o', 'cat')
assert not any(token in diagnostics.lower() for token in ['error', 'failed', 'bad arguments', 'traceback']), diagnostics
(RESULTS / 'desktop-conky.log').write_text(diagnostics)
summary = {'bounds': rect, 'install_hidden_restore_idempotence': 'PASS',
           'install_restore_while_visible': 'PASS', 'live_vram_and_gpu': 'PASS',
           'expired_graphs_and_recovery': 'PASS', 'conky_diagnostics': 'PASS'}
(RESULTS / 'desktop-checks.json').write_text(json.dumps(summary, indent=2) + '\n')
print(json.dumps(summary))
