import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contribution import atomic_json
from gpu_agent import write_status
from thermal_controller import atomic_json as legacy_status

class AtomicStatusTests(unittest.TestCase):
    def test_transient_windows_lock_keeps_old_complete_json_then_recovers(self):
        for writer in (atomic_json,write_status,legacy_status):
            with self.subTest(writer=writer), tempfile.TemporaryDirectory() as d:
                path=Path(d)/'status.json';path.write_text('{"old":true}')
                replace=Path.replace;attempts=[]
                def locked(source,target):
                    attempts.append(1)
                    if len(attempts)<7:
                        self.assertEqual(json.loads(path.read_text()),{'old':True})
                        raise PermissionError('Windows sharing lock')
                    return replace(source,target)
                with patch.object(Path,'replace',locked),patch('contribution.time.sleep'):
                    writer(path,{'new':True})
                self.assertEqual(json.loads(path.read_text()),{'new':True})
                self.assertEqual(len(attempts),7)

    def test_permanent_access_denied_is_bounded_and_not_silently_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'status.json';path.write_text('{"old":true}')
            with patch.object(Path,'replace',side_effect=PermissionError('denied')) as replace,patch('contribution.time.sleep'):
                with self.assertRaises(PermissionError):atomic_json(path,{'new':True})
            self.assertEqual(replace.call_count,21)
            self.assertEqual(json.loads(path.read_text()),{'old':True})
