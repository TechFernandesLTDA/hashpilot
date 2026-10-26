"""Fixtures explicitamente simuladas; GUI opt-in, sem coletores ou comandos GPU."""
import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from rig_panel import totals,project_selected,save_enabled

def fixture(count=6,identical=False):
    now=time.time();devices=[]
    for index in range(count):
        identifier=f'GPU-fixture-{index}'
        gpu={'uuid':identifier,'index':index,'name':'NVIDIA GeForce RTX 3070' if identical else f'NVIDIA GeForce RTX {3070+index*10}',
            'temperature.gpu':65+index,'power.draw':150+index,'power.limit':240,'clocks.gr':1700,'clocks.mem':5001,
            'fan.speed':90,'utilization.gpu':100,'memory.used':2000,'memory.total':8192,'sampled_at':now,'driver_version':'SIMULATED'}
        miner={'hashrate':70+index,'hashrate_at':now,'accepted':10+index,'rejected':0,'hw_errors':0,'connection':'Conectado à pool',
            'paused':False,'started_at':now-300,'recent_logs':'[SIMULAÇÃO QA] evento por UUID','rate_points':[(now,70+index)]}
        devices.append({'uuid':identifier,'index':index,'name':gpu['name'],'enabled':index!=5,'uses_default_wallet':index%2==0,
            'wallet':'SIMULATED','wallet_override':None,'gpu':gpu,'miner':miner,'miner_running':index!=5,'log_path':None,
            'log_error':None,'thermal_control':None,'state':'running' if index!=5 else 'disabled','collected_at':now})
    return {'collected_at':now,'rig':{'schema_version':2,'devices':devices,'totals':totals(devices,now),'ignored_devices':[]},
        'gpu':devices[0]['gpu'],'miner':devices[0]['miner'],'miner_running':True,'log_path':None,'thermal_control':None}

class RigModelTests(unittest.TestCase):
    def test_selected_gpu_preserves_actual_start_failure(self):
        data=fixture(2)
        data['rig']['devices'][1].update(process_error='fixture: executable missing',state='error',miner_running=False)
        projected=project_selected(data,'GPU-fixture-1')
        self.assertEqual(projected['process_error'],'fixture: executable missing')
        self.assertEqual(projected['miner_state'],'error')
        self.assertFalse(projected['miner_running'])

    def test_projection_is_device_not_total(self):
        data=fixture(); projected=project_selected(data,'GPU-fixture-3')
        self.assertEqual(projected['miner']['hashrate'],73)
        self.assertEqual(projected['gpu']['uuid'],'GPU-fixture-3')
        self.assertEqual(projected['rig']['totals']['hashrate_ths'],360)
    def test_disabled_sensor_power_still_counted(self):
        data=fixture();self.assertEqual(data['rig']['totals']['enabled_count'],5)
        self.assertEqual(data['rig']['totals']['power_w'],915)
    def test_stale_hash_excluded(self):
        data=fixture();data['rig']['devices'][0]['miner']['hashrate_at']-=181
        self.assertEqual(totals(data['rig']['devices'])['mining_count'],4)
    def test_uuid_survives_index_swap(self):
        data=fixture(2,True);data['rig']['devices'][0]['index']=1;data['rig']['devices'][1]['index']=0
        self.assertEqual(project_selected(data,'GPU-fixture-0')['gpu']['uuid'],'GPU-fixture-0')
    def test_save_preserves_other_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'settings.json';path.write_text(json.dumps({'wallet':'existing','ai_enabled':True,'gpu_settings':{'GPU-other':{'enabled':False}}}))
            result=save_enabled(path,'GPU-fixture-0',False)
            self.assertEqual(result['wallet'],'existing');self.assertTrue(result['ai_enabled'])
            self.assertFalse(result['gpu_settings']['GPU-other']['enabled'])

def gui_report():
    import monitor
    from test_responsive_ui import build_harness,inspect_widgets
    reports=[]
    monitor.enable_native_dpi_awareness()
    with tempfile.TemporaryDirectory() as directory,patch.object(monitor,'STATE_DIR',Path(tempfile.gettempdir())/'HashPilot-rig-qa-no-live'):
        root,_=build_harness(monitor,Path(directory))
        root.maxsize(9000,9000)
        with patch.object(monitor,'STATE_DIR',Path(directory)):
            for scenario in ('different','identical','missing','index_swap'):
                data=fixture(5 if scenario=='missing' else 6,scenario=='identical')
                if scenario=='index_swap':data['rig']['devices'][0]['index'],data['rig']['devices'][1]['index']=1,0
                root.apply(data)
                if root.qa_finance:
                    with patch.object(monitor,'financial_model',return_value=root.qa_finance):root.refresh_finance()
                else:root.refresh_finance()
                for width,height in [(1264,628),(1350,676),(1920,1080),(3840,2160)]:
                    root.geometry(f'{width}x{height}+0+0');root.update();root.adapt_layout();root.update()
                    for tab in range(3):
                        root.tabs.select(tab);root.update()
                        reports.append({'scenario':scenario,'requested':[width,height],'actual':[root.winfo_width(),root.winfo_height()],
                            'tab':tab,'errors':inspect_widgets(root),'simulated':True})
            root.destroy()
    return reports

if __name__=='__main__':
    import sys
    if '--run' in sys.argv:
        report=gui_report();path=Path(tempfile.gettempdir())/'HashPilot-rig-layout-qa.json'
        path.write_text(json.dumps(report,indent=2),encoding='utf-8')
        errors=sum(len(r['errors']) for r in report)
        print(json.dumps({'cases':len(report),'errors':errors,'report':str(path)}));raise SystemExit(bool(errors))
    unittest.main()
