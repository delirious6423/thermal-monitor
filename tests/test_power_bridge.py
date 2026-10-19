"""Synthetic CPU energy counters; no root or real sensors required."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location(
    'bridge', Path(__file__).resolve().parents[1] / 'integration/gputemps-bridge.py')
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


class PowerTests(unittest.TestCase):
    def test_average_and_failures_never_become_false_zero_or_spikes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / 'intel-rapl:0'
            package.mkdir()
            (package / 'name').write_text('package-0\n')
            energy = package / 'energy_uj'
            # Ignore MMIO and subdomains rather than summing the same energy twice.
            for name, domain in [('intel-rapl-mmio:0', 'package-0'),
                                 ('intel-rapl:0:0', 'core')]:
                path = root / name
                path.mkdir()
                (path / 'name').write_text(domain)
                (path / 'energy_uj').write_text('999999999')
            sensor = bridge.CpuPower(root)
            energy.write_text('1000000')
            self.assertIsNone(sensor.sample(10))
            energy.write_text('121000000')
            self.assertEqual(sensor.sample(15), 24)
            self.assertEqual(sensor.sample(20), 0)
            self.assertIsNone(sensor.sample(20))
            energy.write_text('1')  # Counter wrap/reset.
            self.assertIsNone(sensor.sample(25))
            energy.write_text('120000001')
            self.assertEqual(sensor.sample(30), 24)
            self.assertIsNone(sensor.sample(46))  # Suspend or stalled reader.
            energy.write_text('invalid')
            self.assertIsNone(sensor.sample(51))
            energy.write_text('120000001')
            self.assertIsNone(sensor.sample(56))  # Reset baseline after failure.
            energy.unlink()
            self.assertIsNone(sensor.sample(61))


if __name__ == '__main__':
    unittest.main()
