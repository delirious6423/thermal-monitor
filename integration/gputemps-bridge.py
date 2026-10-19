#!/usr/bin/python3
"""Publish read-only GPU telemetry and CPU package watts for user applications."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time

BINARY = '/usr/local/libexec/thermal-monitor/gputemps'
SNAPSHOT = Path('/run/thermal-monitor/gputemps.json')
process = None


class CpuPower:
    """Average Intel RAPL package-0 energy over the actual monotonic interval."""

    def __init__(self, root=Path('/sys/class/powercap')):
        self.root = root
        self.previous = None

    def sample(self, now):
        previous, self.previous = self.previous, None
        try:
            # Select the MSR package only: core/uncore and MMIO would double count.
            package = next(p for p in sorted(self.root.glob('intel-rapl:*'))[:64]
                           if (p / 'name').read_text()[:64].strip() == 'package-0')
            with (package / 'energy_uj').open() as source:
                energy = int(source.read(64))
            if energy < 0:
                return None
            self.previous = (package, energy, now)
            if previous is None or previous[0] != package:
                return None
            elapsed = now - previous[2]
            delta = energy - previous[1]
            # A wrap/reset or long gap is unknown for one interval, never a spike.
            if not 0 < elapsed <= 15 or delta < 0:
                return None
            return delta / elapsed / 1_000_000
        except (OSError, ValueError, StopIteration):
            return None


def stop(_signum, _frame):
    if process is not None:
        process.terminate()
    raise SystemExit(0)


def main():
    global process
    cpu_power = CpuPower()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, stop)
    process = subprocess.Popen(
        [BINARY, '--json', '--device', '0', '--refresh-ms', '5000'],
        stdout=subprocess.PIPE, text=True)
    try:
        for line in process.stdout:
            sample = json.loads(line)
            if not isinstance(sample.get('gpus'), list):
                raise ValueError('Invalid GPU telemetry')
            sample['source'] = 'gputemps-experimental-mmio'
            sample['received_monotonic'] = time.monotonic()
            sample['cpu_package_watts'] = cpu_power.sample(sample['received_monotonic'])
            temporary = SNAPSHOT.with_suffix('.tmp')
            with temporary.open('w', encoding='utf-8') as output:
                json.dump(sample, output, separators=(',', ':'))
            temporary.chmod(0o644)
            os.replace(temporary, SNAPSHOT)
        raise RuntimeError('GPU reader exited with status ' + str(process.wait()))
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


if __name__ == '__main__':
    main()
