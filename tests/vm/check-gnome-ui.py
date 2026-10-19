"""Check real GNOME actors, beyond the application's D-Bus values (VM only)."""
import json
from pathlib import Path
import socket
import time
from gi.repository import Gio, GLib

assert socket.gethostname() == 'thermal-ux-test'
bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
control = Path('/tmp/thermal-fixture/control.json')

def evaluate(script):
    ok, value = bus.call_sync('org.gnome.Shell', '/org/gnome/Shell',
        'org.gnome.Shell', 'Eval', GLib.Variant('(s)', (script,)), None,
        Gio.DBusCallFlags.NONE, 5000, None).unpack()
    assert ok, value
    return json.loads(value) if value else None

def wait(predicate):
    until = time.monotonic() + 18
    while time.monotonic() < until:
        if predicate(): return
        time.sleep(.25)
    raise AssertionError('GNOME actor did not update')

prefix = 'Main.panel.statusArea["appindicator-org.delirious6423.ThermalMonitor"]'
labels_script = '''(() => {
    const labels = [];
    function walk(actor) {
        if (typeof actor.text === 'string') labels.push(actor.text);
        for (const child of actor.get_children()) walk(child);
    }
    walk(''' + prefix + '''.menu.actor);
    return labels;
})()'''
try:
    control.write_text('{}')
    evaluate(prefix + '.menu.open(); true')
    wait(lambda: 'VRAM   25% · 4.0 / 16.0 GiB' in evaluate(labels_script))
    wait(lambda: 'CPU   25% · 51°C · 24 W' in evaluate(labels_script))
    assert 'GPU   40% · 45°C · 36 W' in evaluate(labels_script)
    panel_labels = labels_script.replace('.menu.actor', '')
    wait(lambda: 'CPU 25% 51° 24W · GPU 40% 45° 36W' in evaluate(panel_labels))
    print('PASS native GNOME panel and primary rows include CPU/GPU watts', flush=True)
    print('PASS native GNOME menu renders actual allocation', flush=True)
    evaluate(prefix + '.menu._getMenuItems().find(i => i.label?.text === "More sensors").menu.open(); true')
    control.write_text('{"vram_used":8192}')
    wait(lambda: 'VRAM   50% · 8.0 / 16.0 GiB' in evaluate(labels_script))
    assert evaluate(prefix + '.menu.isOpen')
    assert evaluate(prefix + '.menu._getMenuItems().find(i => i.label?.text === "More sensors").menu.isOpen')
    print('PASS native rows update live without closing the open sensor submenu', flush=True)
    control.write_text('{"age":30}')
    wait(lambda: 'VRAM   N/A' in evaluate(labels_script))
    wait(lambda: 'GPU   N/A · N/A · N/A' in evaluate(labels_script))
    assert 'CPU   25% · 51°C · N/A' in evaluate(labels_script)
    print('PASS native menu does not retain expired VRAM usage', flush=True)
    control.write_text('{}')
    wait(lambda: 'VRAM   25% · 4.0 / 16.0 GiB' in evaluate(labels_script))
    indicators = evaluate('Object.keys(Main.panel.statusArea).filter(k => k.startsWith("appindicator-"))')
    assert len(indicators) == 1, indicators
    print('PASS exactly one tray entry and recovery after stale data', flush=True)
finally:
    control.write_text('{}')
