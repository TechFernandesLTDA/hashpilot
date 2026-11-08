"""Regression for SRBMiner self-restart, lost session metadata and thermal pause loops."""
import copy
import unittest
from unittest.mock import patch
from gpu_telemetry import parse_gpu_log
from miner_identity import match_successor
import test_continuity as fixtures


class SuccessorTests(unittest.TestCase):
    setUp=fixtures.RigContinuityTests.setUp
    tearDown=fixtures.RigContinuityTests.tearDown
    fail_miner=fixtures.RigContinuityTests.fail_miner
    def successor(self,parent=True):
        old=self.entry['miner'];candidate=self.backend.start(old['argv'],self.root)
        candidate['parent_pid']=old['pid'] if parent else 99999
        self.backend.processes[candidate['pid']]['identity']=copy.deepcopy(candidate)
        self.backend.processes[old['pid']]['alive']=False
        self.backend.legacy=[candidate]
        return old,candidate

    def test_confirmed_parent_and_session_recover_controller_without_stopping_miner(self):
        old,candidate=self.successor();other=self.manager.devices['GPU-test-1']['miner_pid']
        self.manager.reconcile()
        self.assertEqual(self.entry['miner_pid'],candidate['pid']);self.assertTrue(self.entry['managed'])
        self.assertFalse(self.entry['external_readonly']);self.assertTrue(self.backend.is_owned(self.entry['controller']))
        self.assertNotIn(candidate['pid'],self.backend.stopped);self.assertTrue(self.backend.inspect(other))

    def test_unrelated_python_process_does_not_block_successor(self):
        old,candidate=self.successor()
        self.backend.legacy.append({'pid':99999,'executable':str(self.root/'python.exe'),'argv':['python.exe','rig.py']})
        self.manager.reconcile()
        self.assertEqual(self.entry['miner_pid'],candidate['pid'])
        self.assertTrue(self.entry['managed'])

    def test_missing_parent_proof_only_observes_process_readonly(self):
        old,candidate=self.successor(False);self.manager.reconcile()
        self.assertTrue(self.entry['external_readonly']);self.assertFalse(self.entry['managed'])
        self.assertEqual(self.entry['miner_pid'],candidate['pid']);self.assertIsNone(self.entry['controller'])

    def test_late_successor_can_use_short_lived_previous_session(self):
        old=copy.deepcopy(self.entry['miner']);self.manager.stop_gpu('GPU-test-0')
        candidate=self.backend.start(old['argv'],self.root);candidate['parent_pid']=old['pid']
        self.backend.processes[candidate['pid']]['identity']=copy.deepcopy(candidate);self.backend.legacy=[candidate]
        self.manager.reconcile();self.assertEqual(self.entry['miner_pid'],candidate['pid'])

    def test_expired_previous_session_cannot_authorize_recovery(self):
        self.manager.stop_gpu('GPU-test-0');self.clock[0]+=121
        self.assertFalse(self.manager.reconcile_successor(self.entry,self.entry['identity']))

    def test_manual_stop_discards_previous_session_authority(self):
        self.manager.stop_gpu('GPU-test-0',restore=True)
        self.assertNotIn('last_miner_session',self.entry)

    def test_foreign_wallet_pool_log_worker_and_gpu_are_rejected(self):
        old,candidate=self.successor()
        for flag,bad in [('--wallet','prl1foreign'),('--pool','foreign:1'),('--log-file',str(self.root/'foreign.log')),('--worker','foreign'),('--gpu-id','0,1')]:
            with self.subTest(flag=flag):
                value=copy.deepcopy(candidate);value['argv'][value['argv'].index(flag)+1]=bad
                self.assertFalse(match_successor(value,self.entry,self.entry['identity'],self.manager.miner_path))

    def test_duplicate_flags_or_older_creation_time_are_rejected(self):
        old,candidate=self.successor();value=copy.deepcopy(candidate)
        value['argv']+=['--gpu-id','0'];self.assertFalse(match_successor(value,self.entry,self.entry['identity'],self.manager.miner_path))
        value=copy.deepcopy(candidate);value['created_at']=old['created_at']
        self.assertFalse(match_successor(value,self.entry,self.entry['identity'],self.manager.miner_path))

    def test_multiple_matching_miners_do_not_authorize_adoption(self):
        old,candidate=self.successor();second=copy.deepcopy(candidate);second['pid']+=100
        self.backend.legacy.append(second)
        self.assertFalse(self.manager.reconcile_successor(self.entry,self.entry['identity']))

    def test_overlapping_or_unreadable_second_miner_blocks_adoption(self):
        old,candidate=self.successor()
        for scope in ('0,1', 'invalid', None):
            with self.subTest(scope=scope):
                second=copy.deepcopy(candidate);second['pid']+=100
                second['argv']=[second['executable']] if scope is None else [second['executable'],'--gpu-id',scope]
                self.backend.legacy=[candidate,second];self.entry.pop('successor_probe_after',None)
                self.assertFalse(self.manager.reconcile_successor(self.entry,self.entry['identity']))

    def test_preflight_failure_preserves_original_session_and_does_not_fake_stop(self):
        self.backend.processes[self.entry['miner_pid']]['alive']=False
        self.backend.processes[self.entry['controller_pid']]['alive']=False
        before=copy.deepcopy(self.entry);stops=list(self.backend.stopped)
        with patch.object(self.backend,'assert_gpu_available',side_effect=RuntimeError('GPU already occupied')):
            self.assertFalse(self.manager.start_gpu(self.entry['identity'],{'wallet':self.entry['wallet']}))
        self.assertEqual(self.entry['miner_log'],before['miner_log']);self.assertEqual(self.entry['miner'],before['miner'])
        self.assertEqual(self.backend.stopped,stops)

    def test_conflict_does_not_consume_automatic_recovery_budget(self):
        self.fail_miner();self.clock[0]+=60
        with patch.object(self.backend,'assert_gpu_available',side_effect=RuntimeError('GPU already occupied')):
            self.manager.reconcile()
        self.assertEqual(self.manager.operations.budget('GPU-test-0',self.clock[0])['attempts_last_hour'],0)


class LogSessionTests(unittest.TestCase):
    def test_new_process_in_same_log_resets_hash_counter_pause_and_ack(self):
        text='\n'.join(['[2026-11-03 10:00:00] GPU0 RTX: 80 TH/s [A:20 R:1 HW:0]',
                       '[2026-11-03 10:00:01] GPU0 share accepted',
                       '[2026-11-03 10:00:02] GPU0 turned off',
                       '[2026-11-03 11:00:00] Miner version: 3.8.3'])
        result=parse_gpu_log(text,'GPU-a',0,2e9)
        self.assertIsNone(result['hashrate']);self.assertIsNone(result['last_share_at'])
        self.assertFalse(result['paused']);self.assertEqual(result['accepted'],0)

    def test_delayed_old_thermal_event_cannot_pause_current_session(self):
        text='\n'.join(['[2026-11-03 10:00:00] GPU0 listed',
                       '[2026-11-03 10:00:20] GPU0 turned on',
                       '[2026-11-03 10:00:10] GPU0 turned off'])
        self.assertFalse(parse_gpu_log(text,'GPU-a',0,2e9)['paused'])
