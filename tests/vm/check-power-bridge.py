"""VM-only: actual bridge loop with synthetic GPU output and RAPL counters."""
import json
from pathlib import Path
import socket
import subprocess
import tempfile
import time

assert socket.gethostname() == 'thermal-ux-test'
source = Path.home() / 'thermal-test/integration/gputemps-bridge.py'

with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    package = root / 'intel-rapl:0'
    package.mkdir()
    (package / 'name').write_text('package-0')
    fake_gpu = root / 'gputemps'
    fake_gpu.write_text('''#!/usr/bin/python3
import json
from pathlib import Path
import time
root = Path(__file__).parent
n = 0
while True:
    n += 1
    energy = root / 'intel-rapl:0/energy_uj'
    if (root / 'missing').exists(): energy.unlink(missing_ok=True)
    else: energy.write_text(str(n * 5_000_000))
    print(json.dumps({'timestamp': int(time.time()*1000),
        'gpus':[{'index':0,'core':45,'junction':52,'vram':50}]}), flush=True)
    time.sleep(.25)
''')
    fake_gpu.chmod(0o755)
    launch = f'''import importlib.util
from functools import partial
from pathlib import Path
spec = importlib.util.spec_from_file_location('bridge', {str(source)!r})
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
bridge.BINARY = {str(fake_gpu)!r}
bridge.SNAPSHOT = Path({str(root / 'snapshot.json')!r})
bridge.CpuPower = partial(bridge.CpuPower, Path({str(root)!r}))
bridge.main()
'''
    process = subprocess.Popen(['/usr/bin/python3', '-c', launch])

    def wait(predicate):
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            assert process.poll() is None, 'Bridge exited unexpectedly'
            try:
                sample = json.loads((root / 'snapshot.json').read_text())
                if predicate(sample): return sample
            except FileNotFoundError:
                pass
            time.sleep(.05)
        raise AssertionError('Bridge snapshot did not update')

    try:
        sample = wait(lambda s: s.get('cpu_package_watts') is not None)
        assert 10 < sample['cpu_package_watts'] < 30, sample
        assert sample['gpus'][0]['core'] == 45
        assert 0 <= time.monotonic() - sample['received_monotonic'] < 2
        (root / 'missing').touch()
        sample = wait(lambda s: s.get('cpu_package_watts') is None)
        assert sample['gpus'][0]['vram'] == 50
        (root / 'missing').unlink()
        wait(lambda s: s.get('cpu_package_watts') is not None)
        print('PASS bridge publishes average CPU watts; missing counter preserves GPU; recovery works')
    finally:
        process.terminate()
        process.wait(timeout=8)
