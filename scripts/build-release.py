#!/usr/bin/env python3
"""Build a static x86-64 Linux package with normalized metadata and a privacy audit."""
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

ROOT = Path(__file__).resolve().parent.parent
TARGET = 'x86_64-unknown-linux-musl'
ASSET = 'thermal-monitor-linux-x86_64.tar.gz'


def output(*command):
    return subprocess.check_output(command, cwd=ROOT, text=True).strip()


def main():
    if output('git', 'status', '--porcelain'):
        raise SystemExit('Commit the reviewed source before building a release')
    version = json.loads(output('cargo', 'metadata', '--locked', '--no-deps', '--format-version', '1'))['packages'][0]['version']
    commit = output('git', 'rev-parse', 'HEAD')
    epoch = int(output('git', 'show', '-s', '--format=%ct', 'HEAD'))
    sysroot = Path(output(os.environ.get('RUSTC', 'rustc'), '--print', 'sysroot'))
    linker = sysroot / 'lib/rustlib/x86_64-unknown-linux-gnu/bin/rust-lld'
    if not (sysroot / 'lib/rustlib' / TARGET / 'lib').is_dir() or not linker.is_file():
        raise SystemExit('Build needs the official musl standard-library component and bundled rust-lld; see docs/RELEASING.md')
    flags = ['--remap-path-prefix', str(Path.home()) + '=/build/home',
             '--remap-path-prefix', str(Path(os.environ.get('CARGO_HOME', Path.home() / '.cargo')).resolve()) + '=/cargo',
             '--remap-path-prefix', str(sysroot) + '=/rust',
             '--remap-path-prefix', str(ROOT) + '=/src/thermal-monitor']
    environment = {**os.environ, 'RUSTC_WRAPPER': '', 'CARGO_ENCODED_RUSTFLAGS': '\x1f'.join(flags),
                   'CARGO_TARGET_DIR': str(ROOT / 'target'),
                   'CARGO_TARGET_X86_64_UNKNOWN_LINUX_MUSL_LINKER': str(linker)}
    subprocess.run(['cargo', 'build', '--locked', '--release', '--target', TARGET], cwd=ROOT, env=environment, check=True)
    binary = ROOT / 'target' / TARGET / 'release/thermal-monitor'
    files = {'bin/thermal-monitor': binary.read_bytes(), 'VERSION': (version + '\n').encode(),
             'SOURCE_COMMIT': (commit + '\n').encode()}
    metadata = json.loads(output('cargo', 'metadata', '--locked', '--format-version', '1', '--filter-platform', TARGET))
    notices = []
    for package in sorted(metadata['packages'], key=lambda item: (item['name'], item['version'])):
        if package['name'] == 'thermal-monitor':
            continue
        directory = Path(package['manifest_path']).parent
        licenses = sorted(path for path in directory.iterdir() if path.is_file()
                          and path.name.upper().startswith(('LICENSE', 'COPYING', 'COPYRIGHT', 'NOTICE', 'UNLICENSE')))
        if not licenses:
            raise ValueError('Missing upstream license files for ' + package['name'])
        notices.append(f"\n=== {package['name']} {package['version']} ({package['license']}) ===\n".encode())
        for license_file in licenses:
            notices.extend([(license_file.name + '\n').encode(), license_file.read_bytes(), b'\n'])
    files['licenses/DEPENDENCIES.txt'] = b''.join(notices)
    runtime_notices = sysroot / 'share/doc/rust/COPYRIGHT-library.html'
    if not runtime_notices.is_file():
        raise SystemExit('Build needs the matching official Rust COPYRIGHT-library.html runtime notices; see docs/RELEASING.md')
    files['licenses/RUST-LIBRARY.html'] = runtime_notices.read_bytes()
    names = ['README.md', 'LICENSE', 'CHANGELOG.md', 'CONTRIBUTING.md', 'install.py']
    names += output('git', 'ls-files', '--', 'docs', 'integration').splitlines()
    for name in names:
        path = ROOT / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('Release source must contain regular files: ' + name)
        files[name] = path.read_bytes()
    files['MANIFEST.sha256'] = ''.join(hashlib.sha256(data).hexdigest() + '  ' + name + '\n'
                                        for name, data in sorted(files.items())).encode()
    dist = ROOT / 'dist'
    dist.mkdir(exist_ok=True)
    with (dist / ASSET).open('wb') as stream:
        with gzip.GzipFile(filename='', fileobj=stream, mode='wb', mtime=0) as compressed:
            with tarfile.open(fileobj=compressed, mode='w', format=tarfile.USTAR_FORMAT) as archive:
                for name, data in sorted(files.items()):
                    header = tarfile.TarInfo('thermal-monitor/' + name)
                    header.size = len(data)
                    header.mtime = epoch
                    header.mode = 0o755 if name in ('bin/thermal-monitor', 'install.py') else 0o644
                    archive.addfile(header, io.BytesIO(data))
    shutil.copyfile(ROOT / 'install.py', dist / 'install.py')
    audit = subprocess.check_output(['python3', str(ROOT / 'scripts/audit-release.py'),
                                    '--archive', str(dist / ASSET), '--binary', str(binary)], cwd=ROOT)
    (dist / 'PRIVACY-AUDIT.json').write_bytes(audit)
    assets = [ASSET, 'install.py', 'PRIVACY-AUDIT.json']
    (dist / 'SHA256SUMS').write_text(''.join(hashlib.sha256((dist / name).read_bytes()).hexdigest() + '  ' + name + '\n'
                                             for name in assets))
    print(f'Built and audited {ASSET}; checksums and installer are in dist/')


if __name__ == '__main__':
    main()
