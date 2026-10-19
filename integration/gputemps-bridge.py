#!/usr/bin/python3
"""Publish read-only experimental GPU telemetry for the unprivileged logger."""
import json
import os
from pathlib import Path
import signal
import subprocess
import time

BINARY = '/usr/local/libexec/thermal-monitor/gputemps'
SNAPSHOT = Path('/run/thermal-monitor/gputemps.json')
process = None


def stop(_signum, _frame):
    if process is not None:
        process.terminate()
    raise SystemExit(0)


def main():
    global process
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
