#!/usr/bin/python3
"""Independent D-Bus client: validates the real app running in the VM."""
import argparse
import json
from pathlib import Path
import subprocess
import time

from gi.repository import Gio, GLib

parser = argparse.ArgumentParser()
parser.add_argument('--quit-only', action='store_true')
parser.add_argument('--gnome', action='store_true')
args = parser.parse_args()
BUS = 'org.delirious6423.ThermalMonitor'
ITEM = '/StatusNotifierItem'
IFACE = 'org.kde.StatusNotifierItem'
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
observed_labels = []
def on_properties(_connection, _sender, _path, _interface, _signal, parameters, _data):
    changed = parameters.unpack()[1]
    if 'XAyatanaLabel' in changed:
        observed_labels.append(changed['XAyatanaLabel'])
bus.signal_subscribe(BUS, 'org.freedesktop.DBus.Properties', 'PropertiesChanged', ITEM,
                     None, Gio.DBusSignalFlags.NONE, on_properties, None)


def call(path, interface, method, variant=None):
    return bus.call_sync(BUS, path, interface, method, variant, None,
                         Gio.DBusCallFlags.NONE, 3000, None).unpack()


def props():
    return call(ITEM, 'org.freedesktop.DBus.Properties', 'GetAll', GLib.Variant('(s)', (IFACE,)))[0]


def wait_until(predicate, timeout=18):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        context = GLib.MainContext.default()
        while context.pending():
            context.iteration(False)
        try:
            if predicate():
                return
        except GLib.Error:
            pass
        time.sleep(0.25)
    raise AssertionError('Condition did not become true before timeout')


def click(item):
    call('/MenuBar', 'com.canonical.dbusmenu', 'Event',
         GLib.Variant('(isvu)', (item, 'clicked', GLib.Variant('i', 0), 0)))


if args.quit_only:
    click(1005)
    print('PASS quit event sent')
    raise SystemExit(0)

wait_until(lambda: 'CPU 25% 51° 24W · GPU 40% 45° 36W' == props()['XAyatanaLabel'])
p = props()
assert p['Id'] == 'thermal-monitor' and p['Status'] == 'Active'
assert p['ItemIsMenu'] is True and p['Menu'] == '/MenuBar'
reply = call('/MenuBar', 'com.canonical.dbusmenu', 'GetLayout', GLib.Variant('(iias)', (0, -1, [])))
labels = []
def collect(node):
    if node[1].get('label'):
        labels.append(node[1]['label'])
    for child in node[2]:
        collect(child.unpack() if hasattr(child, 'unpack') else child)
collect(reply[1])
assert 'CPU   25% · 51°C · 24 W' in labels, labels
assert 'GPU   40% · 45°C · 36 W' in labels, labels
assert 'GPU hotspot   52°C' in labels, labels
assert 'VRAM temperature   50°C' in labels
assert 'VRAM   25% · 4.0 / 16.0 GiB' in labels
assert 'More sensors' in labels
assert 'Fans   2000 RPM / 1800 RPM' in labels
assert 'Predator SSD GM7000 2TB: 59°C' in labels
assert 'RAM   50% · 4.0 / 8.0 GiB' in labels
assert 'Show graphs' in labels and 'Open 7-day logs' in labels
print('PASS label, protocol properties and complete menu')

control = Path('/tmp/thermal-fixture/control.json')
control.write_text(json.dumps({'cpu': 62000, 'gpu': 60, 'load': 80, 'cpu_watts': 30, 'gpu_watts': 80}))
wait_until(lambda: props()['XAyatanaLabel'] == 'CPU 25% 62° 30W · GPU 80% 60° 80W')
wait_until(lambda: 'CPU 25% 62° 30W · GPU 80% 60° 80W' in observed_labels)
print('PASS changing values and live label notifications')
control.write_text(json.dumps({'vram_used':8192}))
wait_until(lambda: call('/MenuBar', 'com.canonical.dbusmenu', 'GetProperty', GLib.Variant('(is)', (3,'label')))[0] == 'VRAM   50% · 8.0 / 16.0 GiB')
print('PASS live VRAM allocation updates')
control.write_text(json.dumps({'age': 30}))
wait_until(lambda: props()['XAyatanaLabel'] == 'CPU 25% 51° N/A · GPU N/A N/A N/A')
print('PASS stale data becomes N/A')
control.write_text(json.dumps({'missing_gpu': True}))
wait_until(lambda: 'GPU 40% N/A' in props()['XAyatanaLabel'])
print('PASS missing GPU sensor retains independent load')
control.write_text('{}')
wait_until(lambda: props()['XAyatanaLabel'] == 'CPU 25% 51° 24W · GPU 40% 45° 36W')

click(1001)
wait_until(lambda: Path('/tmp/thermal-graphs-active').exists())
wait_until(lambda: call('/MenuBar', 'com.canonical.dbusmenu', 'GetProperty', GLib.Variant('(is)', (1002, 'label')))[0] == 'Hide graphs')
click(1002)
wait_until(lambda: not Path('/tmp/thermal-graphs-active').exists())
print('PASS show/hide graph service actions')

if args.gnome:
    subprocess.run(['gnome-extensions','disable','ubuntu-appindicators@ubuntu.com'], check=True)
    subprocess.run(['gnome-extensions','enable','ubuntu-appindicators@ubuntu.com'], check=True)
else:
    subprocess.run(['systemctl', '--user', 'restart', 'thermal-test-waybar.service'], check=True)
owner = bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
    'org.freedesktop.DBus', 'GetNameOwner', GLib.Variant('(s)', (BUS,)), None,
    Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
def registered():
    items = bus.call_sync('org.kde.StatusNotifierWatcher', '/StatusNotifierWatcher',
        'org.freedesktop.DBus.Properties', 'Get',
        GLib.Variant('(ss)', ('org.kde.StatusNotifierWatcher', 'RegisteredStatusNotifierItems')),
        None, Gio.DBusCallFlags.NONE, 3000, None).unpack()[0]
    return any(owner in item or BUS in item for item in items)
wait_until(registered)
print('PASS re-registers after tray watcher restart')

duplicate = subprocess.run([str(Path.home() / 'thermal-test/thermal-monitor'),
                            '--root', '/tmp/thermal-fixture'], capture_output=True, text=True, timeout=8)
assert duplicate.returncode != 0, 'Duplicate instance replaced the running app'
assert props()['Status'] == 'Active'
print('PASS duplicate instance refused')
