import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from config import DEFAULT_WALLET
from onboarding import save_settings, read_settings


class OnboardingSettingsTests(unittest.TestCase):
    def test_explicit_author_wallet_and_existing_configuration_are_preserved(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'settings.json'
            path.write_text(json.dumps({'pool':'custom:123','future_setting':True}),encoding='utf-8')
            save_settings(DEFAULT_WALLET,'D:/Mining/logs',path)
            saved=read_settings(path)
            self.assertEqual(saved['wallet'],DEFAULT_WALLET)
            self.assertEqual(saved['pool'],'custom:123')
            self.assertTrue(saved['future_setting'])
            self.assertTrue(saved['onboarding_complete'])

    def test_invalid_address_never_overwrites_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'settings.json'
            original=json.dumps({'wallet':DEFAULT_WALLET})
            path.write_text(original,encoding='utf-8')
            with self.assertRaises(ValueError): save_settings('not-a-public-pearl-address','',path)
            self.assertEqual(path.read_text(),original)

    def test_failed_atomic_replace_preserves_previous_configuration(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'settings.json'
            path.write_text('{"existing": true}',encoding='utf-8')
            with patch.object(Path,'replace',side_effect=PermissionError('busy')):
                with self.assertRaises(PermissionError): save_settings(DEFAULT_WALLET,'',path)
            self.assertEqual(read_settings(path),{'existing':True})
