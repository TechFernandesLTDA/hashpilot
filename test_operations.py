"""Regressões de ACK, reinício limitado, backup WAL e confidencialidade."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zipfile
from backup import create_backup,validate_backup,restore_backup,public_settings
from contribution import AUTHOR_WALLET,atomic_json
from mining_health import OperationalStore,health
from earnings_quality import quality
from component_status import identity
from test_rig import FakeBackend,detected
from rig import RigManager,write_request

class HealthTests(unittest.TestCase):
    def miner(self,**fields):
        return {'hashrate':75,'hashrate_at':2000,'started_at':100,'connection':'Conectado à pool',**fields}
    def test_hashing_does_not_confirm_share(self):
        self.assertEqual(health(self.miner(started_at=1900),True,2000)['state'],'awaiting_share')
    def test_pool_ack_and_payment_are_distinct(self):
        value=health(self.miner(last_share_at=1990),True,2000)
        self.assertEqual(value['state'],'confirmed');self.assertIn('não comprova pagamento',value['ack_source'])
    def test_stopped_and_paused_do_not_warn(self):
        self.assertFalse(health(self.miner(),False,2000)['warning'])
        self.assertEqual(health(self.miner(paused=True),True,2000)['state'],'cooling')
    def test_stale_hash_does_not_warn_or_confirm(self):
        self.assertEqual(health(self.miner(hashrate_at=100),True,2000)['state'],'no_hashrate')
    def test_long_no_share_warns(self):
        self.assertTrue(health(self.miner(),True,2000)['warning'])
    def test_interval_history_adapts_threshold(self):
        self.assertEqual(health(self.miner(),True,2000,intervals=[600,500,700])['warning_after_seconds'],3600)
    def test_unknown_start_does_not_invent_timeout(self):
        self.assertFalse(health(self.miner(started_at=None),True,2000)['warning'])
    def test_future_ack_is_rejected(self):
        self.assertIsNone(health(self.miner(last_share_at=3000),True,2000)['last_accepted_at'])
    def test_ack_in_tail_can_confirm_even_when_connect_line_rotated(self):
        self.assertEqual(health(self.miner(connection='Aguardando conexão',last_share_at=1990),True,2000)['state'],'confirmed')
    def test_reconnection_overrides_old_ack(self):
        self.assertEqual(health(self.miner(connection='Reconectando à pool',last_share_at=1990),True,2000)['state'],'connecting')
    def test_persistent_budget_backoff_and_independent_gpus(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'operations.sqlite3';store=OperationalStore(path)
            self.assertTrue(store.reserve('GPU-a','test',1000))
            self.assertFalse(store.reserve('GPU-a','test',1001))
            self.assertTrue(OperationalStore(path).reserve('GPU-a','test',1060))
            self.assertFalse(store.reserve('GPU-a','test',1179))
            self.assertTrue(store.reserve('GPU-a','test',1180))
            self.assertFalse(store.reserve('GPU-a','test',2000))
            self.assertTrue(store.reserve('GPU-b','test',2000))
            self.assertTrue(store.budget('GPU-a',2000)['blocked'])
            self.assertTrue(store.reserve('GPU-a','test',5000))
    def test_ack_history_preserves_rotated_tail_but_not_previous_session(self):
        with tempfile.TemporaryDirectory() as temporary:
            store=OperationalStore(Path(temporary)/'operations.sqlite3')
            store.assess('GPU-a',self.miner(last_share_at=1990),True,2000,100)
            self.assertEqual(store.assess('GPU-a',self.miner(),True,2001,100)['last_accepted_at'],1990)
            self.assertIsNone(store.assess('GPU-a',self.miner(),True,2001,2000)['last_accepted_at'])

class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.state=self.root/'state';self.logs=self.root/'logs';self.state.mkdir();self.logs.mkdir()
        atomic_json(self.state/'settings.json',{'wallet':AUTHOR_WALLET,'log_dir':str(self.logs),'ai':{'api_key':'TEST-CANARY-DO-NOT-EXPORT'},'gpu_settings':{'GPU-a':{'enabled':True,'profile':'cool','unexpected_secret':'CANARY'}}})
        self.archive=self.root/'backup.zip'
    def database(self):
        db=sqlite3.connect(self.state/'history.sqlite3');db.execute('PRAGMA journal_mode=WAL');db.execute('CREATE TABLE points(value TEXT)');db.execute("INSERT INTO points VALUES('before')");db.commit();return db
    def test_backup_captures_live_wal_and_excludes_credentials(self):
        with closing(self.database()) as live:
            create_backup(self.archive,self.state,self.logs)
            with zipfile.ZipFile(self.archive) as z:
                self.assertNotIn(b'CANARY',z.read('settings.json'))
            stage=self.root/'stage';validate_backup(self.archive,stage)
            with closing(sqlite3.connect(stage/'history.sqlite3')) as db:self.assertEqual(db.execute('SELECT * FROM points').fetchall(),[('before',)])
            live.execute("INSERT INTO points VALUES('after')");live.commit()
            self.assertEqual(live.execute('SELECT COUNT(*) FROM points').fetchone()[0],2)
    def test_restore_keeps_credentials_and_local_path_and_saves_rollback(self):
        with closing(self.database()):pass
        create_backup(self.archive,self.state,self.logs)
        settings=json.loads((self.state/'settings.json').read_text());settings['ai']['api_key']='LOCAL-KEY';settings['log_dir']='local-current-path';atomic_json(self.state/'settings.json',settings)
        with closing(sqlite3.connect(self.state/'history.sqlite3')) as db:db.execute("INSERT INTO points VALUES('after')");db.commit()
        result=restore_backup(self.archive,self.state,self.logs)
        restored=json.loads((self.state/'settings.json').read_text())
        self.assertEqual(restored['ai']['api_key'],'LOCAL-KEY');self.assertEqual(restored['log_dir'],'local-current-path')
        self.assertTrue(Path(result['rollback']).is_file())
        with closing(sqlite3.connect(self.state/'history.sqlite3')) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM points').fetchone()[0],1)
        prior=self.root/'prior';validate_backup(result['rollback'],prior)
        with closing(sqlite3.connect(prior/'history.sqlite3')) as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM points').fetchone()[0],2)
    def test_hash_tamper_fails_before_restore(self):
        create_backup(self.archive,self.state,self.logs)
        with zipfile.ZipFile(self.archive) as z:files={name:z.read(name) for name in z.namelist()}
        files['settings.json']=b'{}'
        bad=self.root/'bad.zip'
        with zipfile.ZipFile(bad,'w') as z:
            for name,value in files.items():z.writestr(name,value)
        with self.assertRaises(ValueError):validate_backup(bad,self.root/'stage')
    def test_traversal_and_unlisted_file_rejected(self):
        create_backup(self.archive,self.state,self.logs)
        with zipfile.ZipFile(self.archive,'a') as z:z.writestr('../escape',b'bad')
        with self.assertRaises(ValueError):validate_backup(self.archive,self.root/'stage')
        self.assertFalse((self.root/'escape').exists())
    def test_duplicate_file_rejected(self):
        create_backup(self.archive,self.state,self.logs)
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            with zipfile.ZipFile(self.archive,'a') as z:z.writestr('settings.json',b'{}')
        with self.assertRaises(ValueError):validate_backup(self.archive,self.root/'stage')
    def test_existing_backup_is_preserved(self):
        create_backup(self.archive,self.state,self.logs);before=self.archive.read_bytes()
        with self.assertRaises(FileExistsError):create_backup(self.archive,self.state,self.logs)
        self.assertEqual(before,self.archive.read_bytes())
    def test_invalid_profile_and_wallet_rejected(self):
        with self.assertRaises(ValueError):public_settings({'wallet':'invalid'})
        with self.assertRaises(ValueError):public_settings({'gpu_settings':{'GPU-a':{'profile':'unbounded'}}})
    def test_ledger_is_never_exported(self):
        (self.logs/'support-ledger.json').write_text('private');(self.state/'operations.sqlite3').write_text('budget')
        create_backup(self.archive,self.state,self.logs)
        with zipfile.ZipFile(self.archive) as z:self.assertEqual(set(z.namelist()),{'settings.json','manifest.json'})

class QualityAndVersionTests(unittest.TestCase):
    def test_fresh_and_partial_and_missing_sources(self):
        sources={key:{'fetched_at':1000} for key in ('balance','price','network')}
        self.assertEqual(quality({'sources':sources},1001)['status'],'fontes atuais')
        self.assertIn('desatualizado',quality({'sources':sources},1400)['status'])
        self.assertEqual(quality({},1001)['status'],'indisponível')
        self.assertIn('desatualizado',quality({'sources':sources,'errors':{'balance':'offline'}},1001)['status'])
    def test_invalid_manifest_never_claims_source_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary);(path/'RELEASE-MANIFEST.json').write_text('{}')
            self.assertEqual(identity(path)['source'],'invalid')
    def test_components_report_manifest_not_latest_shortcut(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary);atomic_json(path/'RELEASE-MANIFEST.json',{'schema':1,'version':'v0.4.0','commit':'a'*40})
            self.assertEqual(identity(path)['version'],'v0.4.0')

class RigOperationTests(unittest.TestCase):
    def test_stall_reconnect_is_scoped_and_refuses_heat_pause_and_unknown_sensors(self):
        for condition in ('eligible','hot','paused','missing','disabled','external','stale'):
            with self.subTest(condition=condition),tempfile.TemporaryDirectory() as temporary:
                root=Path(temporary);settings=root/'settings.json';atomic_json(settings,{'wallet':AUTHOR_WALLET})
                backend=FakeBackend();clock=[1000]
                manager=RigManager(root/'state',root/'logs',settings,root/'miner.exe',backend=backend,detector=lambda:detected(2),clock=lambda:clock[0])
                manager.reconcile();identifier='GPU-test-0';other_pid=manager.devices['GPU-test-1']['miner_pid']
                clock[0]=3000
                miner={'hashrate':75,'hashrate_at':3000,'connection':'Conectado à pool','paused':condition=='paused'}
                if condition=='disabled':manager.devices[identifier]['enabled']=False
                if condition=='external':manager.devices[identifier]['external_readonly']=True
                snapshot={'miner':miner,'miner_running':True,'gpu':{'temperature.gpu':80 if condition=='hot' else None if condition=='missing' else 65,'sampled_at':1000 if condition=='stale' else 3000,'error':None}}
                manager.observe_health({identifier:snapshot})
                self.assertEqual(bool(backend.stopped),condition=='eligible')
                self.assertTrue(backend.inspect(other_pid))

    def test_profile_goes_to_its_own_controller_at_start(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);settings=root/'settings.json';atomic_json(settings,{'wallet':AUTHOR_WALLET,'gpu_settings':{'GPU-test-0':{'profile':'balanced'}}})
            backend=FakeBackend();manager=RigManager(root/'state',root/'logs',settings,root/'miner.exe',backend=backend,detector=lambda:detected(1))
            manager.reconcile();args=manager.devices['GPU-test-0']['controller']['argv']
            self.assertEqual(args[args.index('--profile')+1],'balanced');self.assertEqual(args[args.index('--objective')+1],'efficiency')

    def test_profile_is_persistent_and_isolated(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);settings=root/'settings.json';atomic_json(settings,{'wallet':AUTHOR_WALLET})
            backend=FakeBackend();manager=RigManager(root/'state',root/'logs',settings,root/'miner.exe',backend=backend,detector=lambda:detected(2))
            manager.reconcile();identifier='GPU-test-1'
            write_request(root/'state','profile',identifier,profile='cool');manager.process_requests()
            value=json.loads(settings.read_text());self.assertEqual(value['gpu_settings'][identifier]['profile'],'cool')
            self.assertNotIn('GPU-test-0',value['gpu_settings'])
            manager.reconcile();request=json.loads((root/'logs/gpus'/identifier/'gpu-agent-request.json').read_text())
            self.assertEqual(request['objective'],'efficiency');self.assertEqual(request['profile'],'cool')
    def test_restart_does_not_enable_disabled_gpu(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);settings=root/'settings.json';atomic_json(settings,{'wallet':AUTHOR_WALLET,'gpu_settings':{'GPU-test-0':{'enabled':False}}})
            backend=FakeBackend();manager=RigManager(root/'state',root/'logs',settings,root/'miner.exe',backend=backend,detector=lambda:detected(1))
            manager.reconcile();write_request(root/'state','restart','GPU-test-0');manager.process_requests();manager.reconcile()
            self.assertFalse(backend.started)

class AdjustmentAndAuditTests(unittest.TestCase):
    def test_adjustments_and_trials_can_be_read_together(self):
        from gpu_agent import GPUAgent
        from operations_ui import trial_rows
        with tempfile.TemporaryDirectory() as temporary:
            agent=GPUAgent(temporary)
            try:
                gpu=detected(1)[0]
                plan={'action':'clock_down','gpu_uuid':gpu['uuid'],'reason':'thermal'}
                agent.record_adjustment(gpu,{'clock_mhz':1800,'power_w':160,'temperature_c':75},
                    {'clock_mhz':1785,'power_w':155,'temperature_c':74},plan,75,now=1000)
                rows=trial_rows(temporary,gpu['uuid'])
                self.assertEqual(rows[0]['after']['clock_mhz'],1785);self.assertEqual(rows[0]['state'],'applied')
            finally:agent.close()
    def test_offline_ledger_remains_auditable_without_claiming_active_wallet(self):
        from contribution_audit import audit,audit_text,COUNTERS,SHARES
        with tempfile.TemporaryDirectory() as temporary:
            counters={key:10 for key in (*COUNTERS,*SHARES)}
            atomic_json(Path(temporary)/'support-mining-ledger.json',{'schema_version':2,'wallets':{AUTHOR_WALLET:counters}})
            status=audit(temporary,AUTHOR_WALLET,{})
            self.assertEqual(status['verified_primary_seconds'],10)
            self.assertIn('ledger',status['audit_source'])
            self.assertIn('Carteira da rota atual: não confirmada',audit_text({'wallet':AUTHOR_WALLET},status))
    def test_absent_ledger_is_unknown_not_zero(self):
        from contribution_audit import audit,audit_text
        with tempfile.TemporaryDirectory() as temporary:
            text=audit_text({},audit(temporary,AUTHOR_WALLET,{}))
            self.assertIn('Tempo primário verificado: não disponível',text)

if __name__=='__main__':unittest.main()
