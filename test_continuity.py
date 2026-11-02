"""Failure injection for continuity; no GPU writes, sleep changes or Defender actions."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from contribution import AUTHOR_WALLET,atomic_json
from gpu_telemetry import parse_gpu_log
from mining_continuity import ContinuityWindow,IdleSleepGuard
from mining_health import health,OperationalStore
from rig import RigManager,write_request
from test_rig import FakeBackend,detected


class ContinuityHelpersTests(unittest.TestCase):
    def test_sleep_guard_only_blocks_idle_system_sleep_and_releases(self):
        calls=[];guard=IdleSleepGuard(lambda flags:calls.append(flags) or 1)
        guard.update(True);guard.update(True);guard.update(False)
        self.assertEqual(calls,[0x80000001,0x80000000]);self.assertFalse(guard.active)

    def test_sleep_guard_failure_does_not_claim_success(self):
        guard=IdleSleepGuard(lambda flags:0);guard.update(True)
        self.assertFalse(guard.active);self.assertTrue(guard.error)

    def test_empty_and_partial_detection_get_ninety_second_grace(self):
        window=ContinuityWindow();window.inventory(detected(2),1000)
        self.assertEqual(len(window.inventory(detected(1),1030)),2)
        self.assertEqual(len(window.inventory(detected(1),1119)),2)
        self.assertEqual(len(window.inventory(detected(1),1120)),1)

    def test_returning_gpu_gets_a_new_detection_grace(self):
        window=ContinuityWindow();window.inventory(detected(1),1000)
        window.inventory([],1030);window.inventory([],1120)
        window.inventory(detected(1),1200)
        self.assertEqual(len(window.inventory([],1300)),1)

    def test_resume_and_clock_rewind_get_sixty_second_grace(self):
        window=ContinuityWindow();self.assertFalse(window.tick(1000))
        self.assertTrue(window.tick(1100));window.tick(1120);window.tick(1140)
        self.assertFalse(window.tick(1160))
        self.assertTrue(window.tick(900));window.tick(920);window.tick(940)
        self.assertFalse(window.tick(960))

    def test_non_numeric_and_nonfinite_hashrates_do_not_break_health(self):
        for rate in ('80',float('nan'),float('inf'),True,None):
            self.assertFalse(health({'hashrate':rate,'hashrate_at':1000},True,1000)['hashing'])

    def test_old_session_hash_and_ack_do_not_confirm_new_process(self):
        result=health({'hashrate':80,'hashrate_at':990,'last_share_at':990},True,1000,started_at=995)
        self.assertFalse(result['hashing']);self.assertIsNone(result['last_accepted_at'])

    def test_future_persisted_acks_and_recoveries_do_not_block_present(self):
        with tempfile.TemporaryDirectory() as folder:
            store=OperationalStore(Path(folder)/'ops.sqlite3')
            store.assess('GPU-a',{'last_share_at':3000},True,3000,1000)
            for now in (3000,3060,3180):self.assertTrue(store.reserve('GPU-a','clock test',now))
            self.assertFalse(store.budget('GPU-a',2000)['blocked'])
            self.assertIsNone(store.assess('GPU-a',{},True,2000,1000)['last_accepted_at'])
            self.assertTrue(store.reserve('GPU-a','clock recovered',2000))

    def test_component_budgets_are_durable_and_independent_from_miner_budget(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'ops.sqlite3';store=OperationalStore(path)
            self.assertTrue(store.reserve_component('GPU-a','controller',1000))
            self.assertFalse(OperationalStore(path).reserve_component('GPU-a','controller',1001))
            self.assertTrue(store.reserve_component('GPU-b','controller',1001))
            self.assertTrue(store.reserve('GPU-a','miner',1001))

    def test_incidents_survive_restart_and_deduplicate_repeated_errors(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'ops.sqlite3';store=OperationalStore(path)
            store.event('GPU-a','blocked','file missing',1000);store.event('GPU-a','blocked','file missing',1001)
            rows=OperationalStore(path).recent_events('GPU-a');self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['message'],'file missing')

    def test_jobs_recover_connection_when_connect_line_has_rotated(self):
        result=parse_gpu_log('[2026-11-02 10:00:00] GPU0 listed\n[2026-11-02 10:00:01] Job received [job]', 'GPU-a',0,2e9)
        self.assertEqual(result['connection'],'Conectado à pool');self.assertIsNotNone(result['last_job_at'])

    def test_out_of_order_connection_and_ack_lines_do_not_rewind_state(self):
        text='\n'.join(['[2026-11-02 10:00:00] GPU0 listed',
            '[2026-11-02 10:00:10] Connected to pool', '[2026-11-02 10:00:05] Reconnecting to pool',
            '[2026-11-02 10:00:10] GPU0 share accepted', '[2026-11-02 10:00:05] GPU0 share accepted'])
        result=parse_gpu_log(text,'GPU-a',0,2e9)
        self.assertEqual(result['connection'],'Conectado à pool')
        self.assertEqual(result['last_share_at'],result['connection_at'])


class RigContinuityTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.clock=[1000]
        self.settings=self.root/'settings.json';atomic_json(self.settings,{'wallet':AUTHOR_WALLET})
        self.backend=FakeBackend();self.manager=RigManager(self.root/'state',self.root/'logs',self.settings,
            self.root/'miner.exe',backend=self.backend,detector=lambda:detected(2),clock=lambda:self.clock[0])
        self.manager.reconcile();self.entry=self.manager.devices['GPU-test-0']

    def tearDown(self):self.temp.cleanup()

    def fail_miner(self):
        self.backend.stop(self.entry['miner']);self.manager.reconcile()
        for key in ('error','retry_after'):self.assertIn(key,self.entry)

    def test_manual_start_does_not_wait_for_old_automatic_recovery_budget(self):
        self.fail_miner()
        for now in (1000,1060,1180):self.assertTrue(self.manager.operations.reserve('GPU-test-0','old failure',now))
        self.clock[0]=1200;self.manager.manual_start('GPU-test-0');self.manager.reconcile()
        self.assertEqual(self.manager.devices['GPU-test-0']['state'],'running')
        self.assertTrue(self.manager.operations.budget('GPU-test-0',1200)['blocked'])

    def test_manual_start_still_refuses_hot_or_missing_sensor(self):
        self.fail_miner();self.manager.require_recovery_sensors=True
        for sensor in ({},{'temperature.gpu':75,'sampled_at':1000}):
            self.entry['last_gpu_sample']=sensor
            with self.assertRaises(RuntimeError):self.manager.manual_start('GPU-test-0')

    def test_repeated_manual_start_is_limited_without_erasing_history(self):
        self.fail_miner();self.manager.manual_start('GPU-test-0')
        with self.assertRaises(RuntimeError):self.manager.manual_start('GPU-test-0')
        self.assertTrue(self.manager.operations.recent_events('GPU-test-0'))

    def test_start_on_running_miner_is_idempotent(self):
        pid=self.entry['miner_pid'];self.manager.manual_start('GPU-test-0');self.manager.reconcile()
        self.assertEqual(self.entry['miner_pid'],pid);self.assertFalse(self.backend.stopped)

    def test_dead_controller_recovers_without_stopping_any_miner(self):
        pid=self.entry['miner_pid'];other=self.manager.devices['GPU-test-1']['miner_pid']
        self.backend.processes[self.entry['controller_pid']]['alive']=False
        self.manager.reconcile()
        self.assertEqual(self.entry['miner_pid'],pid);self.assertTrue(self.backend.inspect(other))
        self.assertFalse(self.backend.stopped);self.assertTrue(self.backend.is_owned(self.entry['controller']))

    def test_controller_failure_storm_is_bounded_and_keeps_miner(self):
        pid=self.entry['miner_pid']
        for now in (1000,1060,1120,1180):
            self.clock[0]=now;self.backend.processes[self.entry['controller_pid']]['alive']=False
            self.manager.reconcile()
        self.assertEqual(self.entry['miner_pid'],pid);self.assertIn('controller_warning',self.entry)
        self.assertFalse(self.backend.stopped)

    def test_unverified_live_controller_is_never_duplicated(self):
        controller=self.entry['controller'];original=self.backend.is_owned
        with patch.object(self.backend,'is_owned',side_effect=lambda x:False if x==controller else original(x)):
            self.manager.reconcile()
        self.assertEqual(self.entry['controller_pid'],controller['pid']);self.assertIn('controller_warning',self.entry)

    def test_binary_verification_precedes_execution_and_return_can_resume(self):
        self.fail_miner();self.clock[0]=1200
        with patch.object(self.backend,'verify_miner',side_effect=ValueError('blocked executable')):
            self.manager.reconcile()
        entry=self.manager.devices['GPU-test-0'];self.assertTrue(entry['binary_blocked'])
        count=len(self.manager.operations.recent_events('GPU-test-0'))
        self.manager.reconcile()
        self.assertEqual(self.manager.devices['GPU-test-0']['state'],'running')
        self.assertGreater(len(self.manager.operations.recent_events('GPU-test-0')),count)

    def test_controller_replacement_removes_stale_stop_request(self):
        stop=Path(self.entry['log_dir'])/'gpu-agent-stop.json';atomic_json(stop,{'pid':self.entry['controller_pid']})
        self.backend.processes[self.entry['controller_pid']]['alive']=False;self.manager.reconcile()
        self.assertFalse(stop.exists())

    def test_temporary_log_error_does_not_trigger_miner_restart(self):
        self.clock[0]=3000
        snapshot={'miner':{},'miner_running':True,'gpu':{'temperature.gpu':60,'sampled_at':3000},'log_error':'temporarily locked'}
        for now in (3000,3005,3010,3020):
            self.clock[0]=now;snapshot['gpu']['sampled_at']=now;self.manager.observe_health({'GPU-test-0':snapshot})
        self.assertFalse(self.backend.stopped)

    def test_resume_grace_prevents_immediate_restart_of_stale_log(self):
        self.manager.continuity.tick(1000);self.manager.continuity.tick(3000)
        snapshot={'miner':{},'miner_running':True,'gpu':{'temperature.gpu':60,'sampled_at':3000}}
        for now in (3000,3010,3020):
            self.clock[0]=now;snapshot['gpu']['sampled_at']=now;self.manager.observe_health({'GPU-test-0':snapshot})
        self.assertFalse(self.backend.stopped)

    def test_status_publishes_sleep_guard_and_recovery_incidents(self):
        self.manager.observe_health({'GPU-test-0':{'miner':{},'miner_running':True,'gpu':{}}});self.manager.publish()
        data=json.loads(self.manager.registry_path.read_text())
        self.assertIn('continuity_v1',data['capabilities']);self.assertIn('continuity',data)
        self.assertTrue(data['devices']['GPU-test-0']['incidents'])
