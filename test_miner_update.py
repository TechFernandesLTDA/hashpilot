import copy
import io
import json
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import miner_update as update
from contribution import atomic_json
import test_rig as fixture


class PackageTests(unittest.TestCase):
    def test_unknown_version_and_tampered_selection_fail_closed(self):
        with self.assertRaises(ValueError):update.descriptor('../../outside')
        with tempfile.TemporaryDirectory() as folder:
            atomic_json(Path(folder)/'miner-install.json',{'version':'99.0.0'})
            with self.assertRaises(ValueError):update.active_executable(folder)

    def test_official_release_requires_exact_asset_url_and_sha256(self):
        item=update.descriptor('3.8.1')
        payload={'tag_name':'3.8.1','assets':[{'name':item['asset'],'browser_download_url':item['url'],'digest':'sha256:'+item['archive_sha256']}]}
        def run(value):return update.latest(opener=lambda *a,**k:io.BytesIO(json.dumps(value).encode()))
        self.assertTrue(run(payload)['installable'])
        for field,value in [('digest','sha256:'+'0'*64),('browser_download_url','https://example.org/miner.zip')]:
            bad=copy.deepcopy(payload);bad['assets'][0][field]=value
            self.assertFalse(run(bad)['installable'])
        payload['tag_name']='3.9.0';self.assertFalse(run(payload)['installable'])

    def test_rejects_prereleases(self):
        with self.assertRaises(ValueError):update.latest(opener=lambda *a,**k:io.BytesIO(b'{"tag_name":"3.8.2","prerelease":true}'))

    def package(self):
        binary=b'fixture, never executable';stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as z:
            z.writestr('SRBMiner-Multi-3-8-1/SRBMiner-MULTI.exe',binary)
            z.writestr('../../outside.txt','must not extract')
        data=stream.getvalue();entry={'archive_sha256':hashlib.sha256(data).hexdigest(),'exe_sha256':hashlib.sha256(binary).hexdigest()}
        return binary,data,entry

    def test_stage_extracts_only_verified_binary_and_selection_is_explicit(self):
        binary,data,entry=self.package()
        with tempfile.TemporaryDirectory() as folder,patch.dict(update.CATALOG,{'3.8.1':entry}):
            target=update.stage('3.8.1',folder,opener=lambda *a,**k:io.BytesIO(data))
            self.assertEqual(target.read_bytes(),binary)
            self.assertEqual(update.selected_version(folder),'3.8.0')
            self.assertFalse((Path(folder)/'outside.txt').exists())
            self.assertEqual(len(list(Path(folder).rglob('*.exe'))),1)
            update.select('3.8.1',folder);self.assertEqual(update.selected_version(folder),'3.8.1')

    def test_bad_archive_or_binary_never_installs_or_selects(self):
        binary,data,entry=self.package()
        for corrupted in ('archive_sha256','exe_sha256'):
            with self.subTest(corrupted=corrupted),tempfile.TemporaryDirectory() as folder:
                bad={**entry,corrupted:'0'*64}
                with patch.dict(update.CATALOG,{'3.8.1':bad}),self.assertRaises(ValueError):
                    update.stage('3.8.1',folder,opener=lambda *a,**k:io.BytesIO(data))
                self.assertFalse(list(Path(folder).rglob('*.exe')))
                self.assertEqual(update.selected_version(folder),'3.8.0')

    def test_old_or_stale_service_cannot_activate(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(RuntimeError):update.request_activation('3.8.1',folder)

    def test_corrupt_background_cache_does_not_prevent_app_start(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder,'miner-release-check.json').write_text('{broken')
            checker=update.MinerUpdateChecker(folder);self.assertEqual(checker.next_check,0)

    def test_background_check_respects_persistent_six_hour_interval(self):
        with tempfile.TemporaryDirectory() as folder:
            atomic_json(Path(folder)/'miner-release-check.json',{'checked_at':update.time.time()})
            checker=update.MinerUpdateChecker(folder)
            with patch.object(update,'latest') as fetch:
                self.assertFalse(checker.start());fetch.assert_not_called()


class ActivationOnlyTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixture.RigManagerTests('test_start_six_independent_miners_controllers_scoped_index_uuid');self.fixture.setUp()
        self.m=self.fixture.manager;self.backend=self.fixture.backend
        self.paths={'3.8.0':self.m.miner_path,'3.8.1':self.fixture.root/'SRBMiner-Multi-3-8-1/SRBMiner-MULTI.exe'}
        self.patches=[patch.object(update,'executable',side_effect=lambda v,*a,**k:self.paths[v]),patch.object(update,'selected_version',return_value='3.8.0'),patch.object(update,'select')]
        self.selection=self.patches[-1].start()
        for item in self.patches[:-1]:item.start()

    def tearDown(self):
        for item in self.patches:item.stop()
        self.fixture.tearDown()

    def test_update_preserves_disabled_boards_wallets_and_settings(self):
        settings=self.m.settings();settings['gpu_settings']={'GPU-test-1':{'enabled':False,'wallet':'prl1'+'q'*40}}
        atomic_json(self.m.settings_path,settings);self.m.reconcile();before=self.m.settings_path.read_bytes()
        self.m.activate_miner('3.8.1');self.m.reconcile()
        self.assertEqual(self.m.settings_path.read_bytes(),before)
        self.assertIsNone(self.m.devices['GPU-test-1']['miner_pid'])
        self.assertEqual(self.m.miner_path,self.paths['3.8.1'])
        snapshots={key:{'miner_running':True,'miner':{'version':'3.8.1','hashrate':80,'hashrate_at':1000}} for key,e in self.m.devices.items() if e['enabled']}
        self.m.validate_miner_update(snapshots)
        self.assertIsNone(self.m.miner_update_pending)
        self.assertEqual(update.read(self.m.state_dir/'miner-update.json')['state'],'installed')

    def test_failed_health_rolls_back_without_changing_settings(self):
        self.m.reconcile();self.m.activate_miner('3.8.1');self.m.reconcile()
        self.m.clock=lambda:1200;self.m.validate_miner_update({})
        self.assertEqual(self.m.miner_path,self.paths['3.8.0'])
        self.assertEqual(update.read(self.m.state_dir/'miner-update.json')['state'],'rolled_back')
        self.m.reconcile();self.assertTrue(all(e['miner_pid'] for e in self.m.devices.values()))

    def test_external_or_unconfirmed_stop_preserves_selection(self):
        self.m.reconcile();self.m.devices['GPU-test-0']['external_readonly']=True
        with self.assertRaises(RuntimeError):self.m.activate_miner('3.8.1')
        self.selection.assert_not_called()
        self.assertEqual(self.backend.stopped,[])

    def test_failed_stop_never_changes_selected_executable(self):
        self.m.reconcile()
        with patch.object(self.m,'stop_gpu',return_value=False),self.assertRaises(RuntimeError):
            self.m.activate_miner('3.8.1')
        self.selection.assert_not_called();self.assertEqual(self.m.miner_path,self.paths['3.8.0'])

    def test_wrong_version_or_stale_hash_does_not_validate_update(self):
        self.m.reconcile();self.m.activate_miner('3.8.1');self.m.reconcile()
        for version,stamp in [('3.8.0',1000),('3.8.1',700)]:
            snapshots={key:{'miner_running':True,'miner':{'version':version,'hashrate':80,'hashrate_at':stamp}} for key in self.m.devices}
            self.m.validate_miner_update(snapshots)
            self.assertIsNotNone(self.m.miner_update_pending)

    def test_pending_validation_survives_service_recreation(self):
        self.m.reconcile();self.m.activate_miner('3.8.1');self.m.publish()
        recreated=self.fixture.make_manager()
        self.assertEqual(recreated.miner_update_pending['previous_version'],'3.8.0')
