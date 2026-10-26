import ctypes as c
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock
from contribution import AUTHOR_WALLET,atomic_json
from gpu_defaults import NvidiaDefaults,ClockOffset,restore_defaults,NvmlError
from stop_mining import request_stop,restore_offline,status_path
from stop_ui import describe
from rig import RigManager,write_request
from test_rig import FakeBackend,detected

class FakeNvml(NvidiaDefaults):
    def __init__(self,identifier='GPU-qa'):
        self.handle=c.c_void_p(123)
        self.offsets={(domain,state):150 if domain==0 else 700 for state in (0,2,8) for domain in (0,2)}
        self.policies={0:1,1:1};self.power=170000;self.writes=[];self.fail=None
    def states(self):return [0,2,8]
    def offset(self,domain,state):return ClockOffset(c.sizeof(ClockOffset)|(1<<24),domain,state,self.offsets[domain,state],-1000,2000)
    def value(self,name):
        return {'nvmlDeviceGetNumFans':2,'nvmlDeviceGetPowerManagementDefaultLimit':240000,'nvmlDeviceGetPowerManagementLimit':self.power}[name]
    def fan_policy(self,index):return self.policies[index]
    def call(self,name,types,*args):
        self.writes.append(name)
        if self.fail==name:raise NvmlError(name,4)
        if name=='nvmlDeviceSetDefaultFanSpeed_v2':self.policies[args[1]]=0
        elif name=='nvmlDeviceSetPowerManagementLimit':self.power=args[1]
        elif name=='nvmlDeviceSetClockOffsets':
            offset=args[1]._obj
            self.offsets[offset.type,offset.pstate]=offset.offset
        elif name not in ('nvmlDeviceResetGpuLockedClocks','nvmlDeviceResetMemoryLockedClocks'):
            raise AssertionError(name)

class DefaultsTests(unittest.TestCase):
    def test_all_offsets_and_both_fans_are_restored_with_factory_power(self):
        api=FakeNvml();report=api.restore()
        self.assertEqual(report['state'],'restored')
        self.assertTrue(all(v==0 for v in api.offsets.values()))
        self.assertEqual(api.policies,{0:0,1:0});self.assertEqual(api.power,240000)
        self.assertIn('nvmlDeviceResetGpuLockedClocks',api.writes)
        self.assertIn('nvmlDeviceResetMemoryLockedClocks',api.writes)
        self.assertFalse(any('SetFanSpeed' in name for name in api.writes))
    def test_failure_does_not_skip_other_restoration_components(self):
        api=FakeNvml();api.fail='nvmlDeviceSetClockOffsets';report=api.restore()
        self.assertEqual(report['state'],'partial');self.assertIn('offsets',report['failed_components'])
        self.assertEqual(api.policies,{0:0,1:0});self.assertEqual(api.power,240000)
        self.assertIn('nvmlDeviceResetMemoryLockedClocks',api.writes)
    def test_fan_reset_failure_is_never_reported_as_restored(self):
        api=FakeNvml();api.fail='nvmlDeviceSetDefaultFanSpeed_v2';report=api.restore()
        self.assertEqual(report['state'],'partial');self.assertIn('fans',report['failed_components'])
        self.assertEqual(api.power,240000)
    def test_offset_readback_is_required(self):
        api=FakeNvml();old=api.call
        api.call=lambda name,*args:None if name=='nvmlDeviceSetClockOffsets' else old(name,*args)
        self.assertEqual(api.restore()['state'],'partial')
    def test_factory_power_readback_is_required(self):
        api=FakeNvml();old=api.call
        api.call=lambda name,*args:None if name=='nvmlDeviceSetPowerManagementLimit' else old(name,*args)
        self.assertIn('power',api.restore()['failed_components'])
    def test_fan_auto_policy_is_verified_instead_of_using_fan_percentage(self):
        api=FakeNvml();api.fan_policy=lambda _:1
        self.assertIn('fans',api.restore()['failed_components'])
    def test_already_zero_offsets_do_not_write_unnecessarily(self):
        api=FakeNvml();api.offsets={key:0 for key in api.offsets};api.restore()
        self.assertNotIn('nvmlDeviceSetClockOffsets',api.writes)
    def test_abi_version_has_exact_nvidia_struct_size(self):
        self.assertEqual(c.sizeof(ClockOffset),24)
        value=ClockOffset(c.sizeof(ClockOffset)|(1<<24),0,0,0,-1000,1000)
        self.assertEqual(value.version,0x1000018)
    def test_lease_refusal_never_initializes_driver_backend(self):
        actuator=Mock();actuator.acquire.side_effect=RuntimeError('already owned');factory=Mock()
        result=restore_defaults({'uuid':'GPU-qa'},'.',backend_factory=factory,actuator_factory=lambda *a,**k:actuator)
        factory.assert_not_called();self.assertEqual(result['state'],'failed');actuator.close.assert_called_once()
    def test_lease_is_held_until_driver_shutdown(self):
        order=[];actuator=Mock(spec=['acquire','assert_owner','close'])
        actuator.acquire.side_effect=lambda *_:order.append('acquire')
        actuator.assert_owner.side_effect=lambda *_:order.append('owner')
        actuator.close.side_effect=lambda:order.append('release')
        api=Mock();api.restore.side_effect=lambda:(order.append('restore') or {'state':'restored'});api.close.side_effect=lambda:order.append('shutdown')
        result=restore_defaults({'uuid':'GPU-qa'},'.',backend_factory=lambda _:api,actuator_factory=lambda *a,**k:actuator)
        self.assertEqual(order,['acquire','owner','restore','shutdown','release']);self.assertEqual(result['state'],'restored')

class StopRigTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.path=self.root/'settings.json';atomic_json(self.path,{'wallet':AUTHOR_WALLET,'support_enabled':False,'untouched':{'theme':'dark'}})
        self.backend=FakeBackend();self.manager=RigManager(self.root/'state',self.root/'logs',self.path,self.root/'miner.exe',backend=self.backend,detector=lambda:detected(6))
        self.manager.reconcile()
    def test_stop_one_preserves_five_other_workers_and_wallet_settings(self):
        others={key:entry['miner_pid'] for key,entry in self.manager.devices.items() if key!='GPU-test-3'}
        write_request(self.manager.state_dir,'stop','GPU-test-3');self.manager.process_requests();self.manager.reconcile()
        self.assertEqual(self.backend.restored,['GPU-test-3'])
        self.assertEqual(self.manager.devices['GPU-test-3']['restoration']['state'],'restored')
        self.assertTrue(all(self.backend.inspect(pid) for pid in others.values()))
        self.assertEqual(json.loads(self.path.read_text())['untouched'],{'theme':'dark'})
    def test_stop_all_disables_all_before_first_reset_and_does_not_restart(self):
        old=self.backend.restore_defaults
        def restored(gpu,directory):
            saved=json.loads(self.path.read_text())
            self.assertTrue(all(not saved['gpu_settings'][identifier]['enabled'] for identifier in self.manager.devices))
            return old(gpu,directory)
        self.backend.restore_defaults=restored
        write_request(self.manager.state_dir,'stop_all');self.manager.process_requests();starts=len(self.backend.started)
        self.manager.reconcile();self.manager.reconcile()
        self.assertEqual(len(self.backend.started),starts);self.assertEqual(len(self.backend.restored),6)
        self.assertTrue(all(d['state']=='stopped' for d in self.manager.devices.values()))
    def test_driver_failure_keeps_mining_disabled_and_reports_incomplete(self):
        self.backend.restore_defaults=lambda *args:{'state':'partial','failed_components':['fans']}
        write_request(self.manager.state_dir,'stop','GPU-test-0');self.manager.process_requests();self.manager.reconcile()
        entry=self.manager.devices['GPU-test-0']
        self.assertIsNone(entry['miner_pid']);self.assertEqual(entry['restoration']['state'],'partial')
        self.assertIn('incompleta',entry['restore_warning'])
        self.assertFalse(json.loads(self.path.read_text())['gpu_settings']['GPU-test-0']['enabled'])
    def test_no_reset_before_miner_exit_is_confirmed(self):
        self.backend.wait=lambda *args,**kwargs:False
        self.assertFalse(self.manager.stop_gpu('GPU-test-0',restore=True))
        self.assertFalse(self.backend.restored)
    def test_reused_pid_is_not_killed_while_selected_gpu_returns_to_default(self):
        entry=self.manager.devices['GPU-test-0'];pid=entry['miner_pid']
        self.backend.processes[pid]['identity']['created_at']='123456789'
        self.assertTrue(self.manager.stop_gpu('GPU-test-0',restore=True))
        self.assertNotIn(pid,self.backend.stopped);self.assertTrue(self.backend.inspect(pid))
        self.assertEqual(self.backend.restored,['GPU-test-0'])
    def test_idle_disabled_devices_are_not_repeatedly_reset(self):
        write_request(self.manager.state_dir,'stop','GPU-test-0');self.manager.process_requests()
        self.manager.reconcile();self.manager.reconcile()
        self.assertEqual(self.backend.restored.count('GPU-test-0'),1)

class StopRequestTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.state=self.root/'state';self.state.mkdir();self.logs=self.root/'logs';self.settings=self.state/'settings.json'
        atomic_json(self.settings,{'wallet':AUTHOR_WALLET,'gpu_settings':{'GPU-a':{'enabled':True},'GPU-b':{'enabled':True}}})
    def test_active_new_service_uses_queue_without_uac(self):
        atomic_json(self.state/'rig-manager.json',{'marker':'HashPilotRig-v1','ready':True,'sampled_at':time.time(),'capabilities':['stop_restore_v1'],'devices':{'GPU-a':{},'GPU-b':{}}})
        launch=Mock();result=request_stop(['GPU-a'],self.state,self.logs,self.settings,launcher=launch)
        launch.assert_not_called();self.assertFalse(result['requires_uac'])
        saved=json.loads(self.settings.read_text());self.assertFalse(saved['gpu_settings']['GPU-a']['enabled']);self.assertTrue(saved['gpu_settings']['GPU-b']['enabled'])
        queued=[json.loads(p.read_text()) for p in (self.state/'rig-requests').glob('*.json')]
        self.assertEqual(queued[0]['gpu_uuid'],'GPU-a')
    def test_repeated_click_does_not_open_multiple_uac_prompts(self):
        launch=Mock()
        request_stop(['GPU-a'],self.state,self.logs,self.settings,launcher=launch)
        again=request_stop(['GPU-a'],self.state,self.logs,self.settings,launcher=launch)
        self.assertTrue(again['already_pending']);self.assertEqual(launch.call_count,1)
    def test_cancelled_uac_can_be_retried_and_is_never_success(self):
        launch=Mock();request_stop(['GPU-a'],self.state,self.logs,self.settings,launcher=launch)
        atomic_json(status_path(self.logs,'GPU-a'),{'state':'failed','at':time.time(),'error':'UAC cancelado'})
        request_stop(['GPU-a'],self.state,self.logs,self.settings,launcher=launch)
        self.assertEqual(launch.call_count,2)
    def test_mixed_pending_selection_keeps_per_gpu_request_times(self):
        launch=Mock();first=request_stop(['GPU-a'],self.state,self.logs,self.settings,launcher=launch)
        second=request_stop(['GPU-a','GPU-b'],self.state,self.logs,self.settings,launcher=launch)
        self.assertEqual(second['requested_at_by_gpu']['GPU-a'],first['requested_at'])
        self.assertEqual(launch.call_args.args[0],['GPU-b'])
    def test_all_devices_use_single_stop_all_request(self):
        atomic_json(self.state/'rig-manager.json',{'marker':'HashPilotRig-v1','ready':True,'sampled_at':time.time(),'capabilities':['stop_restore_v1'],'devices':{'GPU-a':{},'GPU-b':{}}})
        request_stop(['GPU-a','GPU-b'],self.state,self.logs,self.settings,launcher=Mock())
        queued=[json.loads(p.read_text()) for p in (self.state/'rig-requests').glob('*.json')]
        self.assertEqual(len(queued),1);self.assertEqual(queued[0]['action'],'stop_all')
    def test_no_service_never_starts_a_miner_and_reports_uac_pending(self):
        launch=Mock();result=request_stop(['GPU-a'],self.state,self.logs,self.settings,launcher=launch)
        launch.assert_called_once_with(['GPU-a']);self.assertTrue(result['requires_uac'])
        status=json.loads(status_path(self.logs,'GPU-a').read_text());self.assertEqual(status['state'],'pending');self.assertIn('UAC',status['message'])
    def test_old_service_stops_first_then_requests_restoration_permission(self):
        atomic_json(self.state/'rig-manager.json',{'marker':'HashPilotRig-v1','ready':True,'sampled_at':time.time(),'devices':{'GPU-a':{}}})
        launch=Mock();request_stop(['GPU-a'],self.state,self.logs,self.settings,launcher=launch)
        launch.assert_called_once();self.assertTrue(list((self.state/'rig-requests').glob('*.json')))
    def test_unknown_uuid_or_missing_gpu_never_resets_other_device(self):
        restored=Mock();result=restore_offline(['GPU-a'],self.state,self.logs,self.settings,backend=FakeBackend(),detector=lambda:[{'uuid':'GPU-b'}],restorer=restored)
        restored.assert_not_called();self.assertEqual(result['GPU-a']['state'],'failed')
    def test_offline_helper_restores_only_requested_gpu_without_starting_worker(self):
        backend=FakeBackend();restore=Mock(return_value={'state':'restored','at':time.time()})
        result=restore_offline(['GPU-a'],self.state,self.logs,self.settings,backend=backend,detector=lambda:[{'uuid':'GPU-a'},{'uuid':'GPU-b'}],restorer=restore)
        self.assertEqual(result['GPU-a']['state'],'restored');self.assertEqual(restore.call_count,1);self.assertFalse(backend.started)
    def test_invalid_and_oversized_selection_refused_before_launch(self):
        launch=Mock()
        for ids in ([],['../GPU-a'],['GPU-'+str(i) for i in range(7)]):
            with self.assertRaises(ValueError):request_stop(ids,self.state,self.logs,self.settings,launcher=launch)
        launch.assert_not_called()
    def test_partial_results_never_say_restored(self):
        self.assertIn('incompleta',describe({'state':'partial','failed_components':['fans']}))
        self.assertNotIn('Padrões restaurados',describe({'state':'pending'}))

class RuntimePackagingTests(unittest.TestCase):
    def test_installed_service_runtime_imports_without_source_checkout(self):
        import os,shutil,subprocess,sys
        source=Path(__file__).resolve().parent
        files=json.loads((source/'data/rig-runtime.json').read_text())['files']
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);runtime=root/'runtime';runtime.mkdir()
            for name in files:
                self.assertEqual(Path(name).name,name)
                shutil.copyfile(source/name,runtime/name)
            env=os.environ.copy();env['PYTHONPATH']=str(runtime);env['LOCALAPPDATA']=str(root/'appdata')
            result=subprocess.run([sys.executable,'-c','import rig,gpu_agent,gpu_defaults,stop_mining,stop_ui; print("runtime-ok")'],cwd=runtime,env=env,capture_output=True,text=True,timeout=20)
            self.assertEqual(result.returncode,0,result.stderr);self.assertIn('runtime-ok',result.stdout)

if __name__=='__main__':unittest.main()
