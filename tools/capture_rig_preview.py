"""Capturas da interface multiGPU: amostra real e simulação claramente rotulada."""
from __future__ import annotations
import argparse
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import monitor
from config import STATE_DIR
from test_responsive_ui import build_harness
from test_rig_ui import fixture


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ffmpeg',type=Path,required=True)
    args=parser.parse_args()
    output=ROOT/'media/v0.2.0'
    output.mkdir(parents=True,exist_ok=True)
    monitor.enable_native_dpi_awareness()
    manifest={'captured_at':time.time(),'images':[]}
    with tempfile.TemporaryDirectory() as directory:
        temporary=Path(directory)
        root,_=build_harness(monitor,temporary)
        root.title('HashPilot - Rig preview')
        root.geometry('1920x1080+30+30')
        with patch.object(monitor,'STATE_DIR',temporary):
            actual=json.loads((STATE_DIR/'latest.json').read_text(encoding='utf-8-sig'))
            cached=json.loads((STATE_DIR/'finance-cache.json').read_text(encoding='utf-8-sig'))
            root.finance_state={'schema_version':2,'primary_wallet':cached.get('wallet'),'wallets':{cached['wallet']:cached}}
            if actual.get('log_path'):
                parsed=monitor.parse_log(monitor.tail(Path(actual['log_path'])))
                actual['miner']['recent_logs']=parsed['recent_logs'];actual['miner']['rate_points']=parsed['rate_points']
            root.apply(actual);root.refresh_finance()
            def capture(name,simulated):
                root.update();root.lift();root.update_idletasks()
                end=time.monotonic()+.3
                while time.monotonic()<end:root.update();time.sleep(.01)
                subprocess.run([str(args.ffmpeg),'-hide_banner','-loglevel','error','-y','-f','gdigrab','-framerate','1','-draw_mouse','0','-i','title='+root.title(),'-frames:v','1','-update','1',str(output/name)],check=True,timeout=15)
                manifest['images'].append({'file':name,'simulated':simulated,'captured_at':time.time()})
            root.tabs.select(0);capture('overview-real-rtx3070.png',False)
            for chart in root.overview_charts.values(): chart.points.clear()
            root._overview_hash_signature=None
            simulated=fixture(6)
            names=['RTX 3070','RTX 3080','RTX 3090','RTX 4070','RTX 4080','RTX 4090']
            memories=[8192,10240,24576,12288,16384,24576]
            for index,device in enumerate(simulated['rig']['devices']):
                device['name']='NVIDIA GeForce '+names[index]
                device['gpu']['name']=device['name'];device['gpu']['memory.total']=memories[index]
                device['identity']={'uuid':device['uuid'],'index':index,'name':device['name'],'driver_version':'SIMULATED','memory_mib':memories[index]}
            root.finance_state={}
            root.apply(simulated);root.refresh_finance()
            root.subtitle.configure(text='SIMULAÇÃO DE INTERFACE · 6 GPUs · valores fictícios, não medições de hardware')
            root.status.configure(text='● SIMULAÇÃO · não é um benchmark real',fg=monitor.ORANGE)
            root.tabs.select(0);capture('six-gpus-simulation.png',True)
            root.destroy()
    (output/'capture-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(manifest,ensure_ascii=False))


if __name__=='__main__':main()
