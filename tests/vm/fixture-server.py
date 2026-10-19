#!/usr/bin/python3
"""Deterministic, changing inputs shared by both implementations in the VM."""
import datetime as dt
import json
from pathlib import Path
import time

ROOT = Path('/tmp/thermal-fixture')
CONTROL = ROOT / 'control.json'
HOME = Path.home()


def put(relative, value):
    target = ROOT / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix('.tmp')
    tmp.write_text(str(value))
    tmp.replace(target)


ticks = 0
while True:
    try:
        controls = json.loads(CONTROL.read_text())
    except (OSError, ValueError):
        controls = {}
    ticks += 1
    put('proc/stat', f'cpu {100 + ticks * 25} 0 50 {800 + ticks * 75} 0 0 0 0 0 0\n')
    put('proc/meminfo', 'MemTotal: 8388608 kB\nMemAvailable: 4194304 kB\n')
    for number, name, temperature in [(0, 'coretemp', controls.get('cpu', 51000)),
                                      (1, 'nvme', 59000), (2, 'hp', 0)]:
        put(f'sys/class/hwmon/hwmon{number}/name', name)
        put(f'sys/class/hwmon/hwmon{number}/temp1_input', temperature)
    put('sys/class/hwmon/hwmon1/model', 'Predator SSD GM7000 2TB\n')
    put('sys/class/hwmon/hwmon2/fan1_input', '2000')
    put('sys/class/hwmon/hwmon2/fan2_input', '1800')
    age = controls.get('age', 0)
    now = time.time() - age
    if not controls.get('missing_gpu'):
        put('run/thermal-monitor/gputemps.json', json.dumps({
            'timestamp': int(now * 1000), 'received_monotonic': time.monotonic() - age,
            'gpus': [{'index': 0, 'core': controls.get('gpu', 45), 'junction': 52, 'vram': 50}]}))
    else:
        (ROOT / 'run/thermal-monitor/gputemps.json').unlink(missing_ok=True)
    logs = HOME / '.local/state/thermal-monitor'
    logs.mkdir(parents=True, exist_ok=True)
    # A fixed bounded file keeps every benchmark interval on the same workload.
    (logs / 'gpu-20261020.jsonl').write_text(json.dumps({
        'type': 'sample', 'timestamp': dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(),
        'gpus': [{'index': '0', 'utilization.gpu': str(controls.get('load', 40)),
                  'memory.used': str(controls.get('vram_used', 4096)), 'memory.total': '16384'}]
    }) + '\n{partial')
    time.sleep(1)
