#!/usr/bin/env python3
"""Reject private data markers and unsafe release metadata before publication."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import tarfile

PATTERNS = {
    'private home path': rb'(?:/(?:home|Users)/[A-Za-z0-9_.-]+/|[A-Za-z]:\\Users\\[A-Za-z0-9_.-]+\\)',
    'credential': rb'\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9-]{20,})\b',
    'private key': rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----\r?\n[A-Za-z0-9+/=]{30,}',
}


def check_bytes(content, label):
    for name, pattern in PATTERNS.items():
        if re.search(pattern, content):
            # Do not echo the matched secret/path into a log or public report.
            raise ValueError(f'{name} detected in {label}')
    emails = re.findall(rb'\b[A-Za-z0-9_.+%-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b', content)
    for email in emails:
        if (email not in {b'ubuntu-appindicators@ubuntu.com', b'noreply@github.com'}
                and not email.endswith(b'@users.noreply.github.com')):
            raise ValueError(f'Non-public email address detected in {label}')


def git(root, *arguments):
    return subprocess.check_output(['git', '-C', str(root), *arguments])


def audit_history(root):
    objects = git(root, 'rev-list', '--objects', 'HEAD').splitlines()
    checked = 0
    for line in objects:
        identifier = line.split(b' ', 1)[0].decode('ascii')
        if git(root, 'cat-file', '-t', identifier).strip() == b'blob':
            check_bytes(git(root, 'cat-file', 'blob', identifier), 'source object ' + identifier)
            checked += 1
    metadata = git(root, 'log', '--format=%an%n%ae%n%B', 'HEAD')
    check_bytes(metadata, 'public commit metadata')
    emails = git(root, 'log', '--format=%ae%n%ce', 'HEAD').decode().splitlines()
    if any(not (email.endswith('@users.noreply.github.com') or email == 'noreply@github.com') for email in emails):
        raise ValueError('A public commit uses an email outside GitHub noreply addresses')
    return checked


def audit_archive(path):
    files = {}
    with tarfile.open(path, 'r:gz') as archive:
        for member in archive:
            parts = PurePosixPath(member.name).parts
            if (not parts or parts[0] != 'thermal-monitor' or '..' in parts
                    or not member.isfile() or member.uid or member.gid
                    or member.uname or member.gname or member.pax_headers
                    or member.mode not in (0o644, 0o755) or member.size > 32 * 1024 * 1024):
                raise ValueError('Unsafe archive entry or non-normalized ownership metadata')
            relative = PurePosixPath(*parts[1:]).as_posix()
            if relative in files or relative.startswith(('evidence/', '.git/', 'target/', '.local/')):
                raise ValueError('Duplicate or private archive content')
            with archive.extractfile(member) as stream:
                content = stream.read()
            check_bytes(content, 'archive member ' + relative)
            files[relative] = content
    expected = {}
    for line in files['MANIFEST.sha256'].decode('ascii').splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
        if not match or match[2] in expected:
            raise ValueError('Malformed archive manifest')
        expected[match[2]] = match[1]
    actual = {name: hashlib.sha256(data).hexdigest() for name, data in files.items() if name != 'MANIFEST.sha256'}
    if actual != expected:
        raise ValueError('Archive manifest mismatch')
    if not {'bin/thermal-monitor', 'install.py', 'LICENSE', 'VERSION', 'SOURCE_COMMIT'} <= files.keys():
        raise ValueError('Required release files are missing')
    return len(files)


def audit_binary(path):
    content = path.read_bytes()
    check_bytes(content, 'release binary')
    if not content.startswith(b'\x7fELF'):
        raise ValueError('Release executable is not ELF')
    headers = subprocess.check_output(['readelf', '--program-headers', str(path)], text=True)
    dynamic = subprocess.check_output(['readelf', '--dynamic', str(path)], text=True)
    sections = subprocess.check_output(['readelf', '--sections', str(path)], text=True)
    if 'INTERP' in headers or '(NEEDED)' in dynamic or '.debug_' in sections:
        raise ValueError('Release executable must be static and stripped of debug sections')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument('--archive', required=True, type=Path)
    parser.add_argument('--binary', required=True, type=Path)
    args = parser.parse_args()
    blobs = audit_history(args.repo)
    members = audit_archive(args.archive)
    audit_binary(args.binary)
    print(json.dumps({'source_commit': git(args.repo, 'rev-parse', 'HEAD').decode().strip(),
                      'source_blobs_checked': blobs, 'archive_files_checked': members,
                      'source_and_binary_marker_scan': 'PASS', 'normalized_archive_metadata': 'PASS',
                      'static_stripped_binary': 'PASS', 'scope': 'Reachable HEAD history and release payload; heuristic scan, not a security certification'}, indent=2))


if __name__ == '__main__':
    main()
