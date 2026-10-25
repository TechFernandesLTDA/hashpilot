"""Captura a UI real em um renderizador isolado, alimentado por dados locais reais.

Não inicia coletores, mineradores ou ajuste de GPU. Escritas da UI ficam em uma
pasta temporária; gráficos são restaurados por SQLite somente leitura.
Requer FFmpeg para capturar somente as janelas de demonstração por título.
"""
from __future__ import annotations
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import tkinter as tk
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import monitor
import onboarding
from config import STATE_DIR, WALLET
from test_responsive_ui import build_harness
from gpu_runtime import show_gpu_panel


def load(path):
    return json.loads(path.read_text(encoding='utf-8-sig'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ffmpeg',required=True,type=Path)
    parser.add_argument('--output',type=Path,default=ROOT/'media')
    args=parser.parse_args()
    output=args.output.resolve()
    shots=output/'screenshots'
    segments=output/'segments'
    shots.mkdir(parents=True,exist_ok=True)
    segments.mkdir(parents=True,exist_ok=True)
    monitor.enable_native_dpi_awareness()
    evidence={'captured_at':time.time(),'source':'actual HashPilot UI; live local snapshots; read-only SQLite history',
        'renderer':'isolated, no collectors or hardware writers','screenshots':[],'video_segments':[],'errors':[]}
    with tempfile.TemporaryDirectory() as directory:
        temporary=Path(directory)
        root,_=build_harness(monitor,temporary)
        root.title('HashPilot - Tech Fernandes - Demo')
        root.geometry('1920x1080+40+40')
        root.minsize(1120,620)
        root.ui_restored=False
        root.qa_finance=None
        real_state=STATE_DIR
        monitor.STATE_DIR=temporary
        root.lift()
        def refresh():
            try:
                latest=load(real_state/'latest.json')
                if latest.get('log_path'):
                    parsed=monitor.parse_log(monitor.tail(Path(latest['log_path'])))
                    latest['miner']['recent_logs']=parsed['recent_logs']
                    latest['miner']['rate_points']=parsed['rate_points']
                root.history.info=latest.get('history',{}).get('counts',{})
                root.finance_state=load(real_state/'finance-cache.json')
                root.apply(latest)
                root.refresh_finance()
            except (OSError,ValueError,KeyError) as error:
                evidence['errors'].append(str(error))
            if root.winfo_exists(): root.after(2000,refresh)
        with closing(sqlite3.connect((real_state/'history.sqlite3').as_uri()+'?mode=ro',uri=True)) as database:
            rows=database.execute('SELECT timestamp,gpu_json,miner_json FROM telemetry WHERE wallet=? AND timestamp>=? ORDER BY timestamp',(WALLET,time.time()-900)).fetchall()
        for stamp,gpu_json,miner_json in rows:
            gpu,miner=json.loads(gpu_json) or {},json.loads(miner_json)
            rate_stamp=miner.get('hashrate_at')
            if rate_stamp and miner.get('hashrate') is not None and 0<=stamp-rate_stamp<=180:
                root.charts['hash'].add(rate_stamp,miner['hashrate'])
            for key,field in (('temp','temperature.gpu'),('power','power.draw'),('fan','fan.speed')):
                root.charts[key].add(stamp,gpu.get(field))
        refresh()
        root.update()
        def settle():
            end=time.monotonic()+.3
            while time.monotonic()<end:
                root.update()
                time.sleep(.01)
        def capture(window,name,duration):
            window.lift()
            root.update()
            settle()
            title=window.title()
            image=shots/(name+'.png')
            common=[str(args.ffmpeg),'-hide_banner','-loglevel','error','-y','-f','gdigrab','-framerate','12','-draw_mouse','0','-i','title='+title]
            subprocess.run(common+['-frames:v','1','-update','1',str(image)],check=True,timeout=15)
            evidence['screenshots'].append({'file':str(image.relative_to(output)),'at':time.time(),'title':title})
            clip=segments/(name+'.mp4')
            log=(segments/(name+'.capture.log')).open('w',encoding='utf-8')
            process=subprocess.Popen(common+['-t',str(duration),'-an','-c:v','libx264','-preset','veryfast','-crf','21','-threads','2','-pix_fmt','yuv420p',str(clip)],stdout=log,stderr=log)
            deadline=time.monotonic()+duration+15
            while process.poll() is None:
                root.update()
                time.sleep(.02)
                if time.monotonic()>deadline:
                    process.terminate()
                    raise RuntimeError('Video capture timed out: '+name)
            log.close()
            if process.returncode: raise RuntimeError('FFmpeg capture failed: '+name)
            evidence['video_segments'].append({'file':str(clip.relative_to(output)),'seconds':duration})
            print(name,flush=True)
        try:
            for index,name in enumerate(('overview','gpu-mining','earnings')):
                root.tabs.select(index)
                settle()
                capture(root,name,8)
            panel=show_gpu_panel(root,SimpleNamespace(snapshot=lambda:load(real_state/'latest.json').get('gpu_agent',{}),benchmark=lambda *_:None))
            panel.title('HashPilot - Benchmark e IA - Demo')
            panel.geometry('1000x650+150+150')
            capture(panel,'benchmark',6)
            panel.destroy()
            def capture_setup():
                window=next(child for child in root.winfo_children() if isinstance(child,tk.Toplevel))
                window.title('HashPilot - Configurar carteira - Demo')
                try: capture(window,'onboarding',6)
                finally: window.destroy()
            root.after(250,capture_setup)
            # Cancelled by the capture callback; settings are never saved.
            assert onboarding.run_setup(root) is False
        finally:
            root.destroy()
    (output/'capture-manifest.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'output':str(output),'errors':evidence['errors']}))


if __name__=='__main__': main()
