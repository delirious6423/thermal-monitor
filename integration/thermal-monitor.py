#!/usr/bin/python3
"""Supervise sysstat and record NVIDIA telemetry with seven daily log files."""
import csv
import datetime as dt
import json
import os
from pathlib import Path
import signal
import subprocess
import time

INTERVAL = 5
LOG_DIR = Path.home() / '.local/state/thermal-monitor'
GPU_FIELDS = ['index', 'name', 'pci.bus_id', 'temperature.gpu',
              'temperature.memory', 'power.draw', 'utilization.gpu',
              'memory.used', 'memory.total',
              'clocks_event_reasons.sw_thermal_slowdown',
              'clocks_event_reasons.hw_thermal_slowdown']
running = True


def stop(_signum, _frame):
    global running
    running = False


def prune(directory, today):
    cutoff = today - dt.timedelta(days=6)
    for pattern, prefix, suffix in [('sa????????', 'sa', ''),
                                    ('gpu-????????.jsonl', 'gpu-', '.jsonl')]:
        for path in directory.glob(pattern):
            date_string = path.name[len(prefix):]
            if suffix:
                date_string = date_string[:-len(suffix)]
            try:
                date = dt.datetime.strptime(date_string, '%Y%m%d').date()
            except ValueError:
                continue
            if date < cutoff:
                path.unlink()


def gpu_sample():
    try:
        result = subprocess.run(
            ['/usr/bin/nvidia-smi', '--query-gpu=' + ','.join(GPU_FIELDS),
             '--format=csv,noheader,nounits'],
            capture_output=True, text=True, timeout=3,
            env={**os.environ, 'LC_ALL': 'C'})
        if result.returncode:
            return {'gpu_error': (result.stderr or result.stdout).strip()[:1000]}
        return {'gpus': [dict(zip(GPU_FIELDS, row, strict=True))
                         for row in csv.reader(result.stdout.splitlines(),
                                               skipinitialspace=True)]}
    except (OSError, subprocess.TimeoutExpired, ValueError) as error:
        return {'gpu_error': str(error)}


def throttle_counts():
    counts = {}
    for path in Path('/sys/devices/system/cpu').glob(
            'cpu*/thermal_throttle/*throttle_count'):
        try:
            counts[str(path.relative_to('/sys/devices/system/cpu'))] = int(path.read_text())
        except (OSError, ValueError):
            pass
    return counts


def experimental_gpu_sample():
    try:
        sample = json.loads(Path('/run/thermal-monitor/gputemps.json').read_text())
        age = time.monotonic() - float(sample['received_monotonic'])
        if not 0 <= age <= 15:
            return {'error': 'GPU hotspot/VRAM snapshot is stale', 'age_seconds': age}
        return sample
    except (OSError, ValueError, KeyError, TypeError) as error:
        return {'error': str(error)}


def metadata():
    devices = {}
    for path in Path('/sys/class/nvme').glob('nvme*'):
        try:
            devices[path.name] = {'model': (path / 'model').read_text().strip(),
                                  'pci_path': str((path / 'device').resolve())}
        except OSError:
            pass
    return {'type': 'metadata', 'interval_seconds': INTERVAL,
            'retention_calendar_days': 7, 'nvme_devices': devices,
            'gpu_units': {'temperature.gpu': 'C', 'temperature.memory': 'C',
                          'power.draw': 'W', 'utilization.gpu': '%',
                          'memory.used': 'MiB', 'memory.total': 'MiB'},
            'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}


def write_record(record, now):
    path = LOG_DIR / ('gpu-' + now.strftime('%Y%m%d') + '.jsonl')
    record = {'timestamp': now.isoformat(timespec='milliseconds'), **record}
    with path.open('a', encoding='utf-8') as output:
        output.write(json.dumps(record, separators=(',', ':')) + '\n')
        output.flush()
        os.fsync(output.fileno())


def main():
    LOG_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, stop)
    collector = None
    last_day = None
    try:
        while running:
            start = time.monotonic()
            now = dt.datetime.now().astimezone()
            if now.date() != last_day:
                prune(LOG_DIR, now.date())
                write_record(metadata(), now)
                last_day = now.date()
            if collector is None or collector.poll() is not None:
                if collector is not None:
                    print('Sysstat collector exited; restarting', flush=True)
                collector = subprocess.Popen(
                    ['/usr/libexec/sysstat/sadc', '-D', '-f', '-L',
                     '-S', 'DISK,POWER', str(INTERVAL), str(LOG_DIR)])
            write_record({'type': 'sample', **gpu_sample(),
                          'experimental_temperatures': experimental_gpu_sample(),
                          'cpu_thermal_throttle_counts': throttle_counts()}, now)
            deadline = start + INTERVAL
            while running and time.monotonic() < deadline:
                time.sleep(min(0.25, max(0, deadline - time.monotonic())))
    finally:
        if collector is not None and collector.poll() is None:
            collector.terminate()
            try:
                collector.wait(timeout=5)
            except subprocess.TimeoutExpired:
                collector.kill()
                collector.wait()


if __name__ == '__main__':
    main()
