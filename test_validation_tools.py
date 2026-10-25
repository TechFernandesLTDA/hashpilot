import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import zipfile
from tools import release_package as release
from tools import validation_capture as validation


class ReleaseTests(unittest.TestCase):
    def repository(self, root):
        subprocess.run(['git', 'init', '-q', str(root)], check=True)
        for key, value in [('user.name', 'Test'), ('user.email', 'test@example.invalid')]:
            subprocess.run(['git', 'config', key, value], cwd=root, check=True)
        (root/'monitor.py').write_text('print("test")\n')
        subprocess.run(['git', 'add', '.'], cwd=root, check=True)
        subprocess.run(['git', 'commit', '-qm', 'fixture'], cwd=root, check=True)

    def test_identical_committed_input_produces_identical_package_and_ignores_local_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)/'repo'
            self.repository(root)
            (root/'.env').write_text('DO_NOT_EXPORT=sentinel')
            (root/'monitor.py').write_text('uncommitted')
            first = release.build('HEAD', 'v1.2.3', Path(temporary)/'a', root)
            second = release.build('HEAD', 'v1.2.3', Path(temporary)/'b', root)
            self.assertEqual(first['sha256'], second['sha256'])
            with zipfile.ZipFile(first['package']) as archive:
                self.assertNotIn('.env', archive.namelist())
                self.assertEqual(archive.read('monitor.py'), b'print("test")\n')
            self.assertTrue(release.verify(Path(first['package']), Path(temporary)/'a/SHA256SUMS')['verified'])

    def test_changed_zip_and_wrong_checksum_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)/'repo'
            self.repository(root)
            result = release.build('HEAD', 'v1.2.3', Path(temporary)/'out', root)
            archive = Path(result['package'])
            archive.write_bytes(archive.read_bytes()+b'tamper')
            with self.assertRaises(ValueError):
                release.verify(archive, archive.parent/'SHA256SUMS')

    @unittest.skipUnless(sys.platform == 'win32', 'PowerShell Windows')
    def test_actual_installer_verifier_accepts_valid_package_and_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)/'repo'
            self.repository(root)
            result = release.build('HEAD', 'v1.2.3', Path(temporary)/'out', root)
            archive = Path(result['package'])
            def literal(path):
                return "'" + str(path).replace("'", "''") + "'"
            script = "& ([scriptblock]::Create([IO.File]::ReadAllText(" + literal(release.ROOT/'tools/qa_release_installer.ps1') + ")))"
            script += ' -Archive '+literal(archive)+' -Sums '+literal(archive.parent/'SHA256SUMS')+' -Destination '+literal(Path(temporary)/'expanded')
            command = ['powershell.exe', '-NoProfile', '-Command', script]
            good = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(good.returncode, 0, good.stderr)
            archive.write_bytes(archive.read_bytes()+b'tampered')
            bad = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(bad.returncode, 0)
            self.assertIn('SHA256', bad.stderr)

    def test_unsafe_paths_are_not_package_members(self):
        for name in ('../escape', '/absolute', 'a\\b', 'C:/secret', 'a/./b', 'a//b', ''):
            self.assertFalse(release.safe_name(name), name)
        self.assertTrue(release.safe_name('docs/release.md'))

    def test_member_tampering_is_rejected_even_when_zip_checksum_is_replaced(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)/'repo'
            self.repository(root)
            result = release.build('HEAD', 'v1.2.3', Path(temporary)/'out', root)
            archive = Path(result['package'])
            with zipfile.ZipFile(archive) as bundle:
                files = {name: bundle.read(name) for name in bundle.namelist()}
            files['monitor.py'] = b'changed code'
            with zipfile.ZipFile(archive, 'w') as bundle:
                for name, data in files.items():
                    bundle.writestr(name, data)
            sums = archive.parent/'SHA256SUMS'
            sums.write_text(release.digest(archive.read_bytes())+'  '+archive.name+'\n')
            with self.assertRaisesRegex(ValueError, 'Conteúdo'):
                release.verify(archive, sums)

    def test_checksum_valid_archive_with_traversal_is_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as temporary:
            archive = Path(temporary)/'hashpilot-v1.2.3.zip'
            with zipfile.ZipFile(archive, 'w') as bundle:
                bundle.writestr('../escape', 'untrusted')
            sums = archive.parent/'SHA256SUMS'
            sums.write_text(release.digest(archive.read_bytes())+'  '+archive.name+'\n')
            with self.assertRaisesRegex(ValueError, 'inseguro'):
                release.verify(archive, sums)


class DiagnosticsTests(unittest.TestCase):
    def payload(self, at=1000):
        return {'collected_at': at, 'wallet': 'prl1PRIVATE', 'api_key': 'TOKEN_SENTINEL',
                'log_path': 'C:\\Users\\private\\file',
                'gpu': {'name': 'NVIDIA GeForce RTX 3070', 'sampled_at': at, 'driver_version': '617.14',
                        'temperature.gpu': 76, 'power.draw': 160, 'uuid': 'GPU-PRIVATE'},
                'miner': {'hashrate': 75, 'hashrate_at': at, 'accepted': 10, 'rejected': 0, 'hw_errors': 0,
                          'recent_logs': 'TOKEN_SENTINEL'}, 'miner_running': True}

    def test_only_approved_fields_leave_the_diagnostic(self):
        result = validation.sanitize_snapshot(self.payload(), 1000)
        exported = json.dumps(result)
        for sensitive in ('PRIVATE', 'TOKEN_SENTINEL', 'wallet', 'log_path', 'uuid', 'recent_logs', 'api_key'):
            self.assertNotIn(sensitive, exported)
        self.assertEqual(result['devices'][0]['model'], 'NVIDIA GeForce RTX 3070')

    def test_injected_strings_and_nonfinite_values_are_not_exported(self):
        payload = self.payload()
        payload['gpu'].update(name='token=SECRET', driver_version='617.14 SECRET', **{'power.draw': float('nan'), 'temperature.gpu': True})
        result = validation.sanitize_snapshot(payload, 1000)
        self.assertNotIn('SECRET', json.dumps(result, allow_nan=False))
        self.assertIsNone(result['devices'][0]['gpu']['power.draw'])
        self.assertIsNone(result['devices'][0]['gpu']['temperature.gpu'])

    def test_missing_or_corrupt_snapshot_has_no_raw_exception_content(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual(validation.capture(root, 1000)['read_status'], 'unavailable')
            (root/'latest.json').write_text('SECRET broken json')
            self.assertNotIn('SECRET', json.dumps(validation.capture(root, 1000)))

    def sample(self, at):
        result = validation.sanitize_snapshot(self.payload(at), at)
        result['read_status'] = 'ok'
        return result

    def test_short_observation_cannot_claim_24_hours(self):
        summary = validation.summarize([self.sample(1000), self.sample(1030)], 24, 30)
        self.assertEqual(summary['status'], 'incomplete')

    def test_sleep_gap_and_stale_data_cannot_pass_as_healthy(self):
        stale = self.sample(1000)
        stale['observed_at'] = 5000
        summary = validation.summarize([self.sample(1000), stale], 1, 30)
        self.assertEqual(summary['status'], 'needs_review')
        self.assertEqual(summary['gaps_over_two_intervals'], 1)
        self.assertEqual(summary['healthy_samples'], 1)

    def test_repeated_hashrate_is_not_counted_as_new_measurement(self):
        rows = [self.sample(1000), self.sample(1030)]
        rows[1]['devices'][0]['miner']['hashrate_at'] = 1000
        self.assertEqual(validation.summarize(rows, 24, 30)['independent_hashrate_samples'], 1)

    def test_hardware_errors_or_hot_gpu_require_review(self):
        for key, value in [('temperature.gpu', 84), ('hw_errors', 1)]:
            rows = [self.sample(1000), self.sample(1030)]
            target = 'gpu' if key == 'temperature.gpu' else 'miner'
            rows[1]['devices'][0][target][key] = value
            self.assertEqual(validation.summarize(rows, 30/3600, 30)['status'], 'needs_review')


if __name__ == '__main__':
    unittest.main()
