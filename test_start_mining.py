import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
from contribution import AUTHOR_WALLET,atomic_json
from rig import RigManager
from test_rig import FakeBackend,detected
from start_mining import request_start
from stop_mining import status_path

class StartMiningTests(unittest.TestCase):
    def setUp_confirmation(self):
        patcher=patch('onboarding.messagebox.askyesno',return_value=True)
        patcher.start();self.addCleanup(patcher.stop)
    def setUp(self):
        self.setUp_confirmation()
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.state=self.root/'state';self.logs=self.root/'logs';self.state.mkdir();self.settings=self.state/'settings.json'
        self.ids=['GPU-test-0','GPU-test-1']
        atomic_json(self.settings,{'wallet':AUTHOR_WALLET,'ai':{'provider':'preserve'},'support_enabled':False,
            'gpu_settings':{identifier:{'enabled':False,'wallet':None,'profile':'cool'} for identifier in self.ids}})
        self.backend=FakeBackend();self.manager=RigManager(self.state,self.logs,self.settings,self.root/'miner.exe',backend=self.backend,detector=lambda:detected(2))
        self.manager.reconcile()
    def test_start_after_stop_enables_and_launches_selected_worker(self):
        request_start([self.ids[1]],self.state,self.logs,self.settings)
        self.manager.process_requests();self.manager.reconcile()
        self.assertEqual(self.manager.devices[self.ids[1]]['state'],'running')
        self.assertIsNotNone(self.manager.devices[self.ids[1]]['miner_pid'])
        self.assertEqual(self.manager.devices[self.ids[0]]['state'],'stopped')
        self.assertIsNone(self.manager.devices[self.ids[0]]['miner_pid'])
        settings=json.loads(self.settings.read_text());self.assertEqual(settings['ai'],{'provider':'preserve'})
        self.assertEqual(settings['gpu_settings'][self.ids[1]]['profile'],'cool')
    def test_header_start_after_stop_all_starts_actual_miners_not_only_service(self):
        import monitor
        root=SimpleNamespace(last_data={'rig':{'devices':detected(2)}},rig_service_running=lambda:True,status=Mock())
        with patch.object(monitor,'STATE_DIR',self.state),patch.object(monitor,'LOG_DIR',self.logs),patch.object(monitor,'SETTINGS_PATH',self.settings),patch('onboarding.launch_rig_service') as launch:
            monitor.Monitor.start_rig(root)
        launch.assert_not_called()
        self.manager.process_requests();self.manager.reconcile()
        self.assertTrue(all(entry['state']=='running' and entry['miner_pid'] for entry in self.manager.devices.values()))
        self.assertEqual(len(self.backend.started),4)
    def test_declining_author_wallet_confirmation_does_not_start_or_change_settings(self):
        import monitor
        root=SimpleNamespace(last_data={'rig':{'devices':detected(2)}},rig_service_running=lambda:False,status=Mock())
        before=self.settings.read_bytes()
        with patch.object(monitor,'STATE_DIR',self.state),patch.object(monitor,'LOG_DIR',self.logs),patch.object(monitor,'SETTINGS_PATH',self.settings),patch('onboarding.messagebox.askyesno',return_value=False),patch('onboarding.launch_rig_service') as launch:
            monitor.Monitor.start_rig(root)
        launch.assert_not_called()
        self.assertEqual(self.settings.read_bytes(),before)
        self.assertFalse(list((self.state/'rig-requests').glob('*.json')))
    def test_declining_author_wallet_for_single_gpu_has_no_side_effects(self):
        import monitor
        root=SimpleNamespace(rig_service_running=lambda:False,status=Mock())
        before=self.settings.read_bytes()
        with patch.object(monitor,'STATE_DIR',self.state),patch.object(monitor,'LOG_DIR',self.logs),patch.object(monitor,'SETTINGS_PATH',self.settings),patch('onboarding.messagebox.askyesno',return_value=False),patch('onboarding.launch_rig_service') as launch:
            monitor.Monitor.toggle_gpu(root,self.ids[1],True)
        launch.assert_not_called()
        self.assertEqual(self.settings.read_bytes(),before)
        self.assertFalse(list((self.state/'rig-requests').glob('*.json')))
        self.assertFalse(list(self.logs.rglob('*.json')))
    def test_header_launches_service_only_when_it_is_absent(self):
        import monitor
        root=SimpleNamespace(last_data={'rig':{'devices':detected(2)}},rig_service_running=lambda:False,status=Mock())
        with patch.object(monitor,'STATE_DIR',self.state),patch.object(monitor,'LOG_DIR',self.logs),patch.object(monitor,'SETTINGS_PATH',self.settings),patch('onboarding.launch_rig_service') as launch:
            monitor.Monitor.start_rig(root)
        launch.assert_called_once()
        self.assertTrue(all(value['enabled'] for value in json.loads(self.settings.read_text())['gpu_settings'].values()))
    def test_card_start_keeps_other_stopped_board_disabled(self):
        import monitor
        root=SimpleNamespace(rig_service_running=lambda:True,status=Mock())
        with patch.object(monitor,'STATE_DIR',self.state),patch.object(monitor,'LOG_DIR',self.logs),patch.object(monitor,'SETTINGS_PATH',self.settings):
            monitor.Monitor.toggle_gpu(root,self.ids[1],True)
        settings=json.loads(self.settings.read_text())
        self.assertTrue(settings['gpu_settings'][self.ids[1]]['enabled']);self.assertFalse(settings['gpu_settings'][self.ids[0]]['enabled'])
    def test_pending_restore_blocks_start_before_changing_any_preference(self):
        atomic_json(status_path(self.logs,self.ids[0]),{'state':'restoring','at':time.time()})
        before=self.settings.read_bytes()
        with self.assertRaises(ValueError):request_start(self.ids,self.state,self.logs,self.settings)
        self.assertEqual(before,self.settings.read_bytes());self.assertFalse(list((self.state/'rig-requests').glob('*.json')))
    def test_new_start_invalidates_old_restored_indicator(self):
        atomic_json(status_path(self.logs,self.ids[0]),{'state':'restored','at':1000})
        request_start([self.ids[0]],self.state,self.logs,self.settings)
        self.assertEqual(json.loads(status_path(self.logs,self.ids[0]).read_text())['state'],'start_requested')
    def test_invalid_selection_never_enables_other_devices(self):
        before=self.settings.read_bytes()
        for identifiers in ([],['../GPU-a'],['GPU-'+str(i) for i in range(7)]):
            with self.assertRaises(ValueError):request_start(identifiers,self.state,self.logs,self.settings)
        self.assertEqual(before,self.settings.read_bytes())

    def test_hidden_start_script_failure_reaches_the_panel(self):
        # Em 2026-11-24 o Iniciar-Rig.ps1 elevado abortou sem aviso; só o diagnóstico registrou.
        import monitor
        from onboarding import rig_launch_failure
        log=self.logs/'diagnostics'/'iniciar-rig.log'
        log.parent.mkdir(parents=True,exist_ok=True)
        log.write_text('2026-11-24T23:00:00-03:00 antiga\n',encoding='utf-8')
        old=time.time()-3600
        import os;os.utime(log,(old,old))
        clicked=time.time()
        self.assertIsNone(rig_launch_failure(self.logs,clicked),'falha anterior ao clique não conta')
        with log.open('a',encoding='utf-8') as handle:handle.write('2026-11-24T23:53:49-03:00 JSON primitivo inválido: .\n')
        self.assertEqual(rig_launch_failure(self.logs,clicked),'JSON primitivo inválido: .')
        scheduled=[]
        root=SimpleNamespace(after=lambda delay,*args:scheduled.append(args),rig_service_running=lambda:False,status=Mock())
        with patch.object(monitor,'LOG_DIR',self.logs),patch('tkinter.messagebox.showerror') as shown:
            monitor.watch_rig_launch(root,clicked)
            self.assertEqual(len(scheduled),1);shown.assert_not_called()
            callback,*arguments=scheduled.pop();callback(*arguments)
        shown.assert_called_once()
        self.assertIn('JSON primitivo inválido',root.status.configure.call_args.kwargs['text'])
        self.assertEqual(scheduled,[],'para de acompanhar depois de avisar')

    def test_launch_watch_stops_quietly_once_the_service_is_up(self):
        import monitor
        scheduled=[]
        root=SimpleNamespace(after=lambda delay,*args:scheduled.append(args),rig_service_running=lambda:True,status=Mock())
        with patch.object(monitor,'LOG_DIR',self.logs),patch('tkinter.messagebox.showerror') as shown:
            monitor.watch_rig_launch(root,time.time())
            callback,*arguments=scheduled.pop();callback(*arguments)
        shown.assert_not_called();root.status.configure.assert_not_called();self.assertEqual(scheduled,[])

if __name__=='__main__':unittest.main()
