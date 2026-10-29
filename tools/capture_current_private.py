"""Captura isolada da interface atual; dados financeiros e logs ficam ocultos."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import tkinter as tk
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import monitor
from app_version import VERSION
from config import STATE_DIR
from test_responsive_ui import build_harness


def main():
    output=ROOT/'media'/'current'
    output.mkdir(parents=True,exist_ok=True)
    monitor.enable_native_dpi_awareness()
    with tempfile.TemporaryDirectory() as directory:
        temporary=Path(directory)
        root,_=build_harness(monitor,temporary)
        root.title('HashPilot - captura isolada')
        root.geometry('1500x1000+30+30')
        with patch.object(monitor,'STATE_DIR',temporary):
            actual=json.loads((STATE_DIR/'latest.json').read_text(encoding='utf-8-sig'))
            root.finance_state={};root.qa_finance=None
            root.apply(actual)
            selected=next((d for d in (actual.get('rig') or {}).get('devices',[]) if d.get('uuid')==root.selected_gpu_uuid),None)
            if selected:
                uri=(STATE_DIR/'history.sqlite3').as_uri()+'?mode=ro'
                with closing(sqlite3.connect(uri,uri=True)) as database:
                    rows=database.execute('SELECT timestamp,gpu_json FROM gpu_telemetry WHERE gpu_uuid=? AND wallet=? AND timestamp>=? ORDER BY timestamp',
                        (selected['uuid'],selected['wallet'],time.time()-900)).fetchall()
                for key in ('temp','power','fan'):root.charts[key].points.clear()
                for stamp,gpu_json in rows:
                    gpu=json.loads(gpu_json or '{}')
                    for key,field in (('temp','temperature.gpu'),('power','power.draw'),('fan','fan.speed')):
                        root.charts[key].add(stamp,gpu.get(field))
            root.overview_values['today'].configure(text='Oculto')
            root.overview_notes['today'].configure(text='Oculto para privacidade')
            payout=root.payout_threshold.master
            overlay=tk.Label(payout,text='Pagamento e saldos ocultos para privacidade',bg=monitor.PANEL,fg=monitor.MUTED,font=('Segoe UI',12),anchor='w')
            overlay.place(relx=0,rely=0,relwidth=1,relheight=1)
            root.wallet_var.set('Oculto para privacidade')
            for card in root.rig_cards.cards: card[3].configure(text='Identificador oculto para privacidade')
            selection=root.gpu_selection.get().split(' · ')
            root.gpu_selection.configure(values=[' · '.join(selection[:2]+['identificador oculto'])])
            root.gpu_selection.current(0)
            root.logs.configure(text='Eventos ocultos para privacidade')
            root.log_title.configure(text='ATIVIDADE DO MINERADOR · arquivo oculto para privacidade')
            root.info.configure(text='Detalhes de pool ocultos para privacidade')
            root.subtitle.configure(text='Captura isolada · dados financeiros ocultos para privacidade')
            root.update();root.lift();root.update()
            def capture(name,index):
                root.tabs.select(index);root.update()
                time.sleep(.5);root.update()
                process=subprocess.Popen(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(ROOT/'tools/capture_window.ps1'),
                    '-ClientHandle',str(root.winfo_id()),'-OutputPath',str(output/name)])
                deadline=time.monotonic()+15
                while process.poll() is None:
                    root.update();time.sleep(.01)
                    if time.monotonic()>deadline: process.kill();raise RuntimeError('Capture timed out')
                if process.returncode: raise RuntimeError('Capture failed')
            capture('overview.png',0);capture('gpu-mining.png',1)
            root.destroy()
    manifest={'captured_at':time.time(),'version':VERSION,'source':'isolated current UI, local snapshot and 15-minute GPU telemetry history read-only',
        'privacy':'financial values, wallet, GPU identifiers, payment panel, pool details and logs explicitly hidden; screenshot is not an unaltered full UI',
        'hardware':'local snapshot; no mining or GPU controls started or changed',
        'images':['overview.png','gpu-mining.png']}
    (output/'capture-manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

if __name__=='__main__':main()
