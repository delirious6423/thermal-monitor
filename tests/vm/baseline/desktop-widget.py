#!/usr/bin/python3
"""Feed Conky from the existing logger and read-only hardware sensors."""
import datetime as dt
import json
import os
from pathlib import Path
import signal
import subprocess
import time

DATA = Path('/run/user') / str(os.getuid()) / 'thermal-desktop'
running = True


def stop(_signum, _frame):
    global running
    running = False


def read_number(path):
    try:
        return float(path.read_text())
    except (OSError, ValueError):
        return None


def temperature(value):
    return f'{value:.0f}°C' if value is not None else 'N/A'


def sample():
    cpu = None
    fans = []
    disks = {}
    for hw in Path('/sys/class/hwmon').glob('*'):
        try:
            name = (hw / 'name').read_text().strip()
        except OSError:
            continue
        raw = read_number(hw / 'temp1_input')
        temp = raw / 1000 if raw is not None else None
        if name == 'coretemp':
            cpu = temp
        elif name == 'hp':
            fans = [read_number(hw / f'fan{i}_input') for i in (1, 2)]
        elif name == 'nvme':
            device = str(hw.resolve())
            if '0000:02:00.0' in device:
                disks['GM7000'] = temp
            else:
                disks['TEAM'] = temp
    gpu = {}
    try:
        snapshot = json.loads(Path('/run/thermal-monitor/gputemps.json').read_text())
        if 0 <= time.monotonic() - snapshot['received_monotonic'] <= 15:
            gpu = snapshot['gpus'][0]
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        pass
    load = None
    try:
        paths = sorted((Path.home() / '.local/state/thermal-monitor').glob('gpu-*.jsonl'))
        with paths[-1].open('rb') as log:
            log.seek(0, 2)
            log.seek(max(0, log.tell() - 16384))
            lines = log.read().decode(errors='replace').splitlines()
        for line in reversed(lines):
            try:
                record = json.loads(line)
                if record.get('type') != 'sample':
                    continue
                age = (dt.datetime.now().astimezone() - dt.datetime.fromisoformat(record['timestamp'])).total_seconds()
                if 0 <= age <= 20:
                    load = float(record['gpus'][0]['utilization.gpu'])
                break
            except (ValueError, KeyError, IndexError, TypeError):
                continue
    except (OSError, IndexError):
        pass
    rpm = ' / '.join(f'{v:.0f}' if v is not None else 'N/A' for v in fans) or 'N/A'
    values = {
        'cpu_temp': str(cpu or 0),
        'cpu_label': temperature(cpu),
        'gpu_load': str(load or 0),
        'gpu_label': (f'{load:.0f}%' if load is not None else 'N/A') + '   ' + temperature(gpu.get('core')),
        'gpu_temp': str(gpu.get('core') or 0),
        'extra': f"Hotspot {temperature(gpu.get('junction'))}   VRAM {temperature(gpu.get('vram'))}",
        'ssd1': 'Predator SSD   ' + temperature(disks.get('GM7000')),
        'ssd2': 'TEAM SSD      ' + temperature(disks.get('TEAM')),
        'fans': 'Fans   ' + rpm + ' RPM',
    }
    for key, value in values.items():
        tmp = DATA / (key + '.tmp')
        tmp.write_text(value + '\n')
        tmp.replace(DATA / key)


def main():
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, stop)
    sample()
    conky = subprocess.Popen(['/usr/bin/conky', '-c', str(Path.home() / '.config/conky/thermal.conf')])
    try:
        while running and conky.poll() is None:
            deadline = time.monotonic() + 5
            sample()
            while running and conky.poll() is None and time.monotonic() < deadline:
                time.sleep(0.25)
        if running and conky.returncode:
            raise SystemExit(conky.returncode)
    finally:
        if conky.poll() is None:
            conky.terminate()
            try:
                conky.wait(timeout=5)
            except subprocess.TimeoutExpired:
                conky.kill()
                conky.wait()


if __name__ == '__main__':
    main()
