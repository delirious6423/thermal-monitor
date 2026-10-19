#!/usr/bin/python3
"""Install or restore the Conky presentation, preserving its visibility."""
import argparse
import os
from pathlib import Path
import subprocess


def atomic_write(path, content, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.new')
    with temporary.open('wb') as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.chmod(mode)
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--restore', action='store_true', help='Restore the saved pre-update overlay')
    args = parser.parse_args()
    home = Path.home()
    source = Path(__file__).resolve().parent.parent / 'integration'
    backup = home / '.local/state/thermal-monitor-install/overlay-before-streamline'
    targets = [(home / '.config/conky/thermal.conf', 'thermal.conf', 0o600),
               (home / '.local/lib/thermal-monitor/desktop-widget.py', 'desktop-widget.py', 0o755)]
    previous = {name: path.read_bytes() for path, name, _ in targets}
    desired = {name: ((backup if args.restore else source) / name).read_bytes()
               for _, name, _ in targets}
    if previous == desired:
        print('Overlay already matches; visibility unchanged.')
        return
    if not args.restore:
        for _, name, mode in targets:
            if not (backup / name).exists():
                atomic_write(backup / name, previous[name], mode)
    ctl = ['systemctl', '--user']
    active = subprocess.run(ctl + ['is-active', '--quiet', 'thermal-desktop.service']).returncode == 0
    if active:
        subprocess.run(ctl + ['stop', 'thermal-desktop.service'], check=True)
    try:
        for path, name, mode in targets:
            atomic_write(path, desired[name], mode)
        if active:
            subprocess.run(ctl + ['start', 'thermal-desktop.service'], check=True)
    except BaseException:
        for path, name, mode in targets:
            atomic_write(path, previous[name], mode)
        if active:
            subprocess.run(ctl + ['start', 'thermal-desktop.service'], check=True)
        raise
    print(f'Overlay {"restored" if args.restore else "installed"}; remains {"visible" if active else "hidden"}.')


if __name__ == '__main__':
    main()
