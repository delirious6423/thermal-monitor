#!/usr/bin/python3
"""Add memory allocation to the existing query; retain the logger and its data."""
import ast
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def prepare(text):
    tree = ast.parse(text)
    fields = next(ast.literal_eval(node.value) for node in tree.body
                  if isinstance(node, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id == 'GPU_FIELDS' for t in node.targets))
    missing = [field for field in ['memory.used', 'memory.total'] if field not in fields]
    if missing:
        anchor = "'temperature.memory', 'power.draw', 'utilization.gpu',"
        if text.count(anchor) != 1:
            raise ValueError('Unrecognized GPU_FIELDS layout; the existing logger was not changed')
        text = text.replace(anchor, anchor + '\n              ' + ', '.join(repr(f) for f in missing) + ',')
    if "'memory.used': 'MiB'" not in text or "'memory.total': 'MiB'" not in text:
        anchor = "'power.draw': 'W', 'utilization.gpu': '%'}"
        if text.count(anchor) != 1:
            raise ValueError('Unrecognized unit metadata; the existing logger was not changed')
        text = text.replace(anchor, "'power.draw': 'W', 'utilization.gpu': '%',\n                          'memory.used': 'MiB', 'memory.total': 'MiB'}")
    ast.parse(text)
    return text


def atomic_write(path, text, mode):
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        try:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
            temporary.chmod(mode)
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def main():
    path = Path.home() / '.local/lib/thermal-monitor/thermal-monitor.py'
    original = path.read_text()
    updated = prepare(original)
    if original == updated:
        print('VRAM allocation logging is already enabled.')
        return
    state = Path.home() / '.local/state/thermal-monitor-install'
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    backup = state / 'logger-before-vram.py'
    if not backup.exists():
        shutil.copy2(path, backup)
    mode = path.stat().st_mode & 0o777
    atomic_write(path, updated, mode)
    try:
        subprocess.run(['/usr/bin/systemctl', '--user', 'restart', 'thermal-monitor.service'], check=True)
        subprocess.run(['/usr/bin/systemctl', '--user', 'is-active', '--quiet', 'thermal-monitor.service'], check=True)
    except (OSError, subprocess.CalledProcessError):
        atomic_write(path, original, mode)
        subprocess.run(['/usr/bin/systemctl', '--user', 'restart', 'thermal-monitor.service'], check=False)
        raise
    print('VRAM usage added to the existing five-second query; seven-day retention preserved.')


if __name__ == '__main__':
    main()
