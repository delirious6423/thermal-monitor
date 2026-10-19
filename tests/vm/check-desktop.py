#!/usr/bin/python3
"""Exercise the actual overlay feeder against fresh, stale and malformed inputs."""
import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import time
import unittest

HERE = Path(__file__).resolve().parent
SOURCE = HERE / 'desktop-widget.py'
if not SOURCE.exists():
    SOURCE = HERE.parent.parent / 'integration/desktop-widget.py'
spec = importlib.util.spec_from_file_location('desktop', SOURCE)
desktop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(desktop)


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.logs = self.root / '.local/state/thermal-monitor'
        self.logs.mkdir(parents=True)
        self.data = self.root / 'data'
        self.data.mkdir()
        self.snapshot = self.root / 'run/thermal-monitor/gputemps.json'
        self.snapshot.parent.mkdir(parents=True)
        self.snapshot.write_text(json.dumps({'received_monotonic': time.monotonic(),
            'gpus': [{'core': 45, 'junction': 52, 'vram': 50}]}))
        cpu = self.root / 'sys/class/hwmon/hwmon0'
        cpu.mkdir(parents=True)
        (cpu / 'name').write_text('coretemp')
        (cpu / 'temp1_input').write_text('51000')

    def record(self, used=4096, total=16384, load=40, age=0):
        return {'type': 'sample', 'timestamp': (dt.datetime.now(dt.timezone.utc)
            - dt.timedelta(seconds=age)).isoformat(), 'gpus': [{'memory.used': used,
                'memory.total': total, 'utilization.gpu': load}]}

    def sample(self, *records):
        (self.logs / 'gpu-20261020.jsonl').write_text(
            ''.join(json.dumps(r) + '\n' for r in records) + '{partial')
        return desktop.sample(root=self.root, home=self.root, data=self.data)

    def test_live_allocation_and_separate_temperatures(self):
        values = self.sample(self.record())
        self.assertEqual(values['vram_label'], '25% · 4.0 / 16.0 GiB')
        self.assertEqual(values['vram_percent'], '25.0')
        self.assertEqual(values['gpu_temp_label'], '45°C')
        self.assertEqual(values['extra'], 'Hotspot 52°C · VRAM 50°C')
        self.assertEqual(values['cpu_label'], '51°C')
        self.assertEqual((self.data / 'vram_label').read_text(), values['vram_label'])

    def test_zero_allocation_and_utilization_are_valid(self):
        values = self.sample(self.record(used=0, load=0))
        self.assertEqual(values['vram_valid'], 1)
        self.assertEqual(values['gpu_load_valid'], 1)
        self.assertEqual(values['gpu_load_label'], '0%')

    def test_invalid_memory_does_not_hide_valid_load(self):
        for used, total in [('N/A', 16384), (float('nan'), 16384),
                            (20000, 16384), (0, 0), (-1, 16384), (True, 16384)]:
            with self.subTest(used=used, total=total):
                values = self.sample(self.record(used=used, total=total))
                self.assertEqual(values['vram_label'], 'N/A')
                self.assertEqual(values['vram_valid'], 0)
                self.assertEqual(values['gpu_load_label'], '40%')

    def test_expired_latest_record_never_revives_older_sample(self):
        values = self.sample(self.record(), self.record(age=60))
        self.assertEqual(values['vram_valid'], 0)
        self.assertEqual(values['gpu_load_valid'], 0)
        self.assertEqual(values['gpu_temp_label'], '45°C')

    def test_invalid_latest_record_never_revives_older_sample(self):
        values = self.sample(self.record(), {'type': 'sample', 'timestamp': 'bad'})
        self.assertEqual(values['vram_valid'], 0)
        self.assertEqual(values['gpu_load_valid'], 0)

    def test_expired_gpu_temperature_is_not_plotted_as_zero(self):
        self.snapshot.write_text(json.dumps({'received_monotonic': time.monotonic()-30,
                                            'gpus': [{'core': 45}]}))
        values = self.sample(self.record())
        self.assertEqual(values['gpu_valid'], 0)
        self.assertEqual(values['gpu_temp_label'], 'N/A')
        self.assertEqual(values['vram_valid'], 1)

    def test_malformed_device_does_not_crash(self):
        self.snapshot.write_text(json.dumps({'received_monotonic': time.monotonic(), 'gpus': [None]}))
        record = self.record()
        record['gpus'] = [None]
        values = self.sample(record)
        self.assertEqual(values['gpu_valid'], 0)
        self.assertEqual(values['vram_valid'], 0)


if __name__ == '__main__':
    unittest.main()
