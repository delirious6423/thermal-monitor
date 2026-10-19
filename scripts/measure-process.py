#!/usr/bin/python3
"""Read-only Linux process measurements; RSS includes shared library pages."""
import argparse
import json
import os
from pathlib import Path
import statistics
import time

parser = argparse.ArgumentParser()
parser.add_argument('pid', type=int)
parser.add_argument('--seconds', type=int, default=60)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
proc = Path('/proc') / str(args.pid)


def sample():
    stat = (proc / 'stat').read_text().rsplit(')', 1)[1].split()
    memory = {}
    for line in (proc / 'smaps_rollup').read_text().splitlines():
        if ':' in line:
            key, val = line.split(':', 1)
            if key in ['Rss', 'Pss', 'Private_Clean', 'Private_Dirty']:
                memory[key] = int(val.split()[0])
    return int(stat[11]) + int(stat[12]), memory


start = time.monotonic()
first, memory = sample()
samples = [memory]
while time.monotonic() - start < args.seconds:
    time.sleep(min(1, args.seconds - (time.monotonic() - start)))
    last, memory = sample()
    samples.append(memory)
elapsed = time.monotonic() - start
cpu = (last - first) / os.sysconf('SC_CLK_TCK')
result = {'pid': args.pid, 'elapsed_seconds': elapsed, 'samples': len(samples),
          'cpu_seconds': cpu, 'cpu_percent_one_core': 100 * cpu / elapsed,
          'median_kib': {k: statistics.median(s[k] for s in samples) for k in samples[0]}}
args.output.parent.mkdir(parents=True, exist_ok=True)
args.output.write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
