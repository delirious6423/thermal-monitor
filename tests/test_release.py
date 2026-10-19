"""Release safeguards use temporary files and mocked service commands on the host."""
import hashlib
import importlib.util
import io
from pathlib import Path
import stat
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


installer = module('installer', ROOT / 'install.py')
audit = module('audit', ROOT / 'scripts/audit-release.py')


class ReleaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def test_checksums_reject_duplicates_and_malformed_lines(self):
        line = 'a' * 64 + '  asset.tar.gz\n'
        self.assertEqual(installer.checksums(line.encode()), {'asset.tar.gz': 'a' * 64})
        for value in [line + line, 'bad  asset.tar.gz\n', line.replace('  ', ' ')]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                installer.checksums(value.encode())

    def test_archive_rejects_escape_absolute_and_link_entries(self):
        for name, kind in [('thermal-monitor/../outside', tarfile.REGTYPE),
                           ('/thermal-monitor/outside', tarfile.REGTYPE),
                           ('thermal-monitor/link', tarfile.SYMTYPE),
                           ('thermal-monitor/link', tarfile.LNKTYPE)]:
            archive = self.root / 'bad.tar.gz'
            with tarfile.open(archive, 'w:gz') as stream:
                info = tarfile.TarInfo(name)
                info.type = kind
                info.linkname = '../../outside' if kind != tarfile.REGTYPE else ''
                stream.addfile(info)
            with self.subTest(name=name, kind=kind), self.assertRaises(ValueError):
                installer.unpack(archive, self.root / 'out')
        self.assertFalse((self.root / 'outside').exists())

    def test_archive_rejects_duplicate_files(self):
        archive = self.root / 'duplicate.tar.gz'
        with tarfile.open(archive, 'w:gz') as stream:
            for _ in range(2):
                stream.addfile(tarfile.TarInfo('thermal-monitor/file'))
        with self.assertRaises(ValueError):
            installer.unpack(archive, self.root / 'out')

    def test_corrupt_payload_is_rejected_before_executing_binary(self):
        (self.root / 'bin').mkdir()
        (self.root / 'bin/thermal-monitor').write_bytes(b'changed')
        (self.root / 'MANIFEST.sha256').write_text(hashlib.sha256(b'original').hexdigest() + '  bin/thermal-monitor\n')
        with patch.object(installer.subprocess, 'check_output') as execute:
            with self.assertRaisesRegex(ValueError, 'Checksum mismatch'):
                installer.verify_payload(self.root)
            execute.assert_not_called()

    def test_failed_service_start_restores_files_receipt_and_service(self):
        binary = self.root / 'binary'
        binary.write_bytes(b'previous')
        binary.chmod(0o700)
        receipt = self.root / 'receipt.json'
        calls = []
        failed = False

        def control(*args, **_kwargs):
            nonlocal failed
            calls.append(args)
            if args[0] == 'start' and not failed:
                failed = True
                raise RuntimeError('Simulated start failure')
            return SimpleNamespace(returncode=0, stdout='', stderr='')

        with patch.object(installer, 'unit_state', return_value={'active': True, 'enabled': False}), \
                patch.object(installer, 'ctl', side_effect=control):
            with self.assertRaisesRegex(RuntimeError, 'Simulated'):
                installer.replace_files({binary: (b'updated', 0o755), receipt: (b'new', 0o600)},
                                        {'thermal-topbar.service': {'active': True, 'enabled': False}}, True)
        self.assertEqual(binary.read_bytes(), b'previous')
        self.assertEqual(stat.S_IMODE(binary.stat().st_mode), 0o700)
        self.assertFalse(receipt.exists())
        self.assertEqual(calls.count(('start', 'thermal-topbar.service')), 2)

    def test_logger_is_disabled_before_its_unit_is_removed(self):
        unit = self.root / 'logger.service'
        unit.write_text('existing')

        def control(*args, **_kwargs):
            if args[:2] == ('--no-reload', 'disable'):
                self.assertTrue(unit.exists())
            return SimpleNamespace(returncode=0, stdout='', stderr='')

        with patch.object(installer, 'unit_state', return_value={'active': True, 'enabled': True}), \
                patch.object(installer, 'ctl', side_effect=control):
            installer.replace_files({unit: None}, {'thermal-monitor.service': {'active': False, 'enabled': False}}, True)
        self.assertFalse(unit.exists())

    def test_privacy_scan_rejects_paths_credentials_keys_and_private_email(self):
        examples = [b'/home/' + b'private-builder/project', b'/mnt/' + b'private-volume/project', b'ghp_' + b'x' * 36,
                    b'-----BEGIN ' + b'PRIVATE KEY-----\n' + b'a' * 40,
                    b'person' + b'@' + b'private-domain.example']
        for value in examples:
            with self.subTest(value=value[:12]), self.assertRaises(ValueError):
                audit.check_bytes(value, 'synthetic fixture')
        audit.check_bytes(b'123+public@users.noreply.github.com', 'public GitHub identity')

    def test_audit_rejects_archive_owner_metadata(self):
        archive = self.root / 'owner.tar.gz'
        with tarfile.open(archive, 'w:gz') as stream:
            info = tarfile.TarInfo('thermal-monitor/file')
            info.uid = 1000
            info.uname = 'builder'
            stream.addfile(info, io.BytesIO())
        with self.assertRaisesRegex(ValueError, 'ownership'):
            audit.audit_archive(archive)

    def test_license_contact_exception_does_not_hide_credentials_or_paths(self):
        contact = b'upstream' + b'@' + b'public-author.example'
        audit.check_bytes(contact, 'public upstream attribution', public_license_contacts=True)
        for content in [b'ghp_' + b'x' * 36, b'/home/' + b'private-builder/project']:
            with self.assertRaises(ValueError):
                audit.check_bytes(content, 'upstream attribution', public_license_contacts=True)


if __name__ == '__main__':
    unittest.main()
