"""VM-only integration: driver CSV -> seven-day record -> Rust memory display."""
import csv
import datetime as dt
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

root = Path.home() / 'thermal-test'
spec = importlib.util.spec_from_file_location('collector', root / 'integration/thermal-monitor.py')
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)

def fake_driver(command, **_kwargs):
    assert command[0] == '/usr/bin/nvidia-smi'
    fields = command[1].split('=', 1)[1].split(',')
    assert fields.count('memory.used') == fields.count('memory.total') == 1
    values = {'index':'0', 'name':'Demo GPU', 'memory.used':'4096', 'memory.total':'16384',
              'utilization.gpu':'40', 'temperature.gpu':'45'}
    output = io.StringIO()
    csv.writer(output).writerow([values.get(field, '0') for field in fields])
    return SimpleNamespace(returncode=0, stdout=output.getvalue(), stderr='')

with tempfile.TemporaryDirectory() as home:
    collector.LOG_DIR = Path(home) / '.local/state/thermal-monitor'
    collector.LOG_DIR.mkdir(parents=True)
    now = dt.datetime.now(dt.timezone.utc)
    with patch.object(collector.subprocess, 'run', side_effect=fake_driver):
        collector.write_record({'type':'sample', **collector.gpu_sample()}, now)
    record = json.loads(next(collector.LOG_DIR.glob('gpu-*.jsonl')).read_text())
    assert record['gpus'][0]['memory.used'] == '4096'
    assert record['gpus'][0]['memory.total'] == '16384'
    assert collector.metadata()['gpu_units']['memory.used'] == 'MiB'
    result = subprocess.run([str(root / 'thermal-monitor'), '--once', '--root', '/tmp/thermal-fixture',
        '--home', home], check=True, text=True, capture_output=True)
    memory = json.loads(result.stdout)['gpu_memory']
    assert memory == {'used_mib':4096.0, 'total_mib':16384.0}, memory
    old = collector.LOG_DIR / ('gpu-' + (now.date()-dt.timedelta(days=7)).strftime('%Y%m%d') + '.jsonl')
    kept = collector.LOG_DIR / ('gpu-' + (now.date()-dt.timedelta(days=6)).strftime('%Y%m%d') + '.jsonl')
    old.touch(); kept.touch()
    collector.prune(collector.LOG_DIR, now.date())
    assert not old.exists() and kept.exists()
print('PASS one GPU query records allocation in MiB; Rust reads it; seven-day retention preserved')
