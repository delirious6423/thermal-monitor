#!/usr/bin/python3
"""Feed Conky from the existing logger and read-only hardware sensors."""
import datetime as dt
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import time

DATA = Path(os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')) / 'thermal-desktop'
running = True


def stop(_signum, _frame):
    global running
    running = False


def read_number(path):
    try:
        return number(path.read_text())
    except (OSError, ValueError):
        return None


def number(value, maximum=None):
    """Reject unsupported, nonfinite and negative sensor values independently."""
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 and (maximum is None or result <= maximum) else None
    except (ValueError, TypeError, OverflowError):
        return None


def temperature(value):
    return f'{value:.0f}°C' if value is not None else 'N/A'


def sample(root=Path('/'), home=None, data=DATA):
    home = Path.home() if home is None else home
    cpu = None
    fans = []
    disks = {}
    for hw in (root / 'sys/class/hwmon').glob('*'):
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
            try:
                device += (hw / 'model').read_text()
            except OSError:
                pass
            if '0000:02:00.0' in device or 'GM7000' in device:
                disks['GM7000'] = temp
            else:
                disks['TEAM'] = temp
    gpu = {}
    try:
        snapshot = json.loads((root / 'run/thermal-monitor/gputemps.json').read_text())
        if 0 <= time.monotonic() - snapshot['received_monotonic'] <= 15:
            gpu = {key: number(snapshot['gpus'][0].get(key), 200) for key in ('core', 'junction', 'vram')}
    except (OSError, ValueError, KeyError, IndexError, TypeError, AttributeError):
        pass
    load = None
    used = total = None
    try:
        paths = sorted((home / '.local/state/thermal-monitor').glob('gpu-*.jsonl'))
        with paths[-1].open('rb') as log:
            log.seek(0, 2)
            log.seek(max(0, log.tell() - 16384))
            lines = log.read().decode(errors='replace').splitlines()
        for line in reversed(lines):
            try:
                record = json.loads(line)
            except ValueError:
                continue  # An unfinished final write is not a sensor record.
            if not isinstance(record, dict) or record.get('type') != 'sample':
                continue
            try:
                age = (dt.datetime.now().astimezone() - dt.datetime.fromisoformat(record['timestamp'])).total_seconds()
                if 0 <= age <= 20:
                    device = record['gpus'][0]
                    load = number(device.get('utilization.gpu'), 100)
                    used, total = number(device.get('memory.used')), number(device.get('memory.total'))
            except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                pass
            break  # Never revive an older reading behind a stale/invalid sample.
    except (OSError, IndexError):
        pass
    rpm = ' / '.join(f'{v:.0f}' if v is not None else 'N/A' for v in fans) or 'N/A'
    valid_memory = used is not None and total is not None and total > 0 and used <= total
    percent = used / total * 100 if valid_memory else None
    values = {
        'cpu_valid': int(cpu is not None),
        'cpu_temp': str(cpu or 0),
        'cpu_label': temperature(cpu),
        'gpu_load': str(load or 0),
        'gpu_load_valid': int(load is not None),
        'gpu_load_label': f'{load:.0f}%' if load is not None else 'N/A',
        'gpu_valid': int(gpu.get('core') is not None),
        'gpu_temp_label': temperature(gpu.get('core')),
        'gpu_temp': str(gpu.get('core') or 0),
        'vram_valid': int(valid_memory),
        'vram_percent': str(percent or 0),
        'vram_label': f'{percent:.0f}% · {used / 1024:.1f} / {total / 1024:.1f} GiB' if valid_memory else 'N/A',
        'extra': f"Hotspot {temperature(gpu.get('junction'))} · VRAM {temperature(gpu.get('vram'))}",
        'disks': f"SSD  {temperature(disks.get('GM7000'))} Predator · {temperature(disks.get('TEAM'))} TEAM",
        'fans': 'Fans  ' + rpm + ' RPM',
    }
    for key, value in values.items():
        tmp = data / (key + '.tmp')
        # Conky's head includes line endings, which split rows and if_match expressions.
        tmp.write_text(str(value))
        tmp.replace(data / key)
    return values


def main():
    DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, stop)
    root = Path(os.environ.get('THERMAL_MONITOR_ROOT', '/'))
    sample(root=root)
    conky = subprocess.Popen(['/usr/bin/conky', '-c', str(Path.home() / '.config/conky/thermal.conf')])
    try:
        while running and conky.poll() is None:
            deadline = time.monotonic() + 5
            sample(root=root)
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
