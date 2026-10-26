"""Integra benchmark/IA ao painel em uma thread; nunca disputa controle da GPU."""
from __future__ import annotations
import json
from pathlib import Path
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk

from config import STATE_DIR, LOG_DIR
from gpu_agent import GPUAgent, BackgroundAdvisor, detect_nvidia_gpus,numeric
from ai_advisor import RoutedAdvisor, configuration
from onboarding import read_settings
from rig_config import resolve_devices
from gpu_telemetry import read_gpu_samples, read_device_payload
from rig_panel import totals

def read_json(path):
    try:
        value=json.loads(Path(path).read_text(encoding='utf-8-sig'))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):return {}

class RigTelemetry:
    """One batch sensor query; each process/log belongs to an explicit UUID."""
    def __init__(self):
        self.detected=[];self.detected_at=0;self.error=None
    def sample(self,processes=None):
        now=time.time()
        if now-self.detected_at>=30:
            try:self.detected=detect_nvidia_gpus(max_devices=6);self.error=None
            except Exception as error:self.error=str(error);self.detected=[]
            self.detected_at=now
        settings=read_settings()
        devices=resolve_devices(self.detected,settings,LOG_DIR)
        manager=read_json(STATE_DIR/'rig-manager.json')
        manager_at=numeric(manager.get('sampled_at',manager.get('updated_at',0)))
        manager_fresh=manager_at is not None and 0<=now-manager_at<=15
        managed_devices=manager.get('devices')
        if not isinstance(managed_devices,dict):managed_devices={}
        samples=read_gpu_samples(devices)
        result=[]
        for device in devices:
            identifier=device['uuid'];managed=managed_devices.get(identifier,{})
            if not isinstance(managed,dict):managed={}
            logs=list(Path(device['log_dir']).glob('mining-*.log'))
            path=Path(managed['miner_log']) if managed.get('miner_log') else max(logs,key=lambda p:p.stat().st_mtime) if logs else None
            legacy=False
            if path is None and device['index']==0:
                logs=list(LOG_DIR.glob('mining-*.log'))
                path=max(logs,key=lambda p:p.stat().st_mtime) if logs else None
                legacy=path is not None
            pid=managed.get('miner_pid')
            running=(pid in processes) if pid and processes is not None else False if managed and not pid else None
            if managed and not manager_fresh:running=None
            if legacy and not managed:
                # Match the legacy supervisor PID; another GPU's miner is not GPU0 evidence.
                legacy_control=read_json(LOG_DIR/'thermal-control.json')
                legacy_pid=legacy_control.get('miner_pid')
                running=(legacy_pid in processes) if legacy_pid and processes is not None else None
            payload=read_device_payload(device,path,miner_running=running,now=now,allow_local_gpu0=True)
            control=read_json(Path(device['state_dir'])/'gpu-agent-control.json')
            if legacy and not control:control=read_json(LOG_DIR/'thermal-control.json')
            if control:
                control_at=numeric(control.get('sampled_at'))
                control['age_seconds']=now-(control_at or 0);control['stale']=control_at is None or not -5<=control['age_seconds']<=15
            entry={**device,**payload,'gpu':samples.get(identifier), 'thermal_control':control or None,
                'health':managed.get('health') if manager_fresh else None,
                'recovery':managed.get('recovery') if manager_fresh else None,
                'process_error':managed.get('error') if manager_fresh else None,
                'state':'manager_unavailable' if managed and not manager_fresh else managed.get('state','legacy_readonly' if legacy else 'disabled' if not device['enabled'] else 'stopped'),
                'collected_at':now,'support_contribution':{},'manager_stale':bool(managed and not manager_fresh)}
            result.append(entry)
        return {'schema_version':2,'devices':result,'totals':totals(result,now),'ignored_devices':manager.get('ignored_devices',manager.get('ignored',[])),
                'sampled_at':now,'error':self.error,'manager':{'state':manager.get('state'),'stale':not manager_fresh,'software':manager.get('software')}}


class GPURuntime:
    def __init__(self, state_dir=STATE_DIR):
        self.state_dir=Path(state_dir)
        self.stop=threading.Event()
        self.commands=queue.Queue()
        self.lock=threading.Lock()
        self.latest={'state':'starting','reason':'Identificando GPU NVIDIA…'}
        self.selected_uuid=None
        self.telemetry={}
        self.thread=threading.Thread(target=self.work,daemon=True,name='gpu-benchmark')
        self.thread.start()

    def snapshot(self, gpu_uuid=None):
        with self.lock:
            data=dict(self.latest)
            selected=gpu_uuid or self.selected_uuid
            if selected and selected in data.get('devices',{}):
                return {**data['devices'][selected],'devices':data['devices'],'selected_uuid':selected}
            return data

    def update(self,data):
        with self.lock:self.telemetry=data

    def select(self,gpu_uuid):
        self.selected_uuid=gpu_uuid

    def benchmark(self, objective='performance', gpu_uuid=None):
        identifier=gpu_uuid or self.selected_uuid
        self.commands.put(('benchmark',objective,identifier))
        gpu=self.snapshot(identifier).get('gpu') or {}
        if gpu.get('uuid'):
            try:
                request={'action':'benchmark','gpu_uuid':gpu['uuid'],'objective':objective,'duration_seconds':300,'at':time.time()}
                from rig_config import gpu_paths
                directory=Path(gpu_paths(LOG_DIR,gpu['uuid'])['state_dir'])
                directory.mkdir(parents=True,exist_ok=True)
                target=directory/'gpu-agent-request.json'
                temporary=target.with_suffix('.tmp')
                temporary.write_text(json.dumps(request),encoding='utf-8')
                temporary.replace(target)
            except OSError as error:
                with self.lock: self.latest['request_error']=str(error)

    def work(self):
        agent=GPUAgent(self.state_dir)
        advisor=BackgroundAdvisor(RoutedAdvisor(self.state_dir))
        advice_uuid=None
        try:
            while not self.stop.is_set():
                with self.lock: data=self.telemetry
                if not data:
                    data=read_json(self.state_dir/'latest.json')
                devices=(data.get('rig') or {}).get('devices') or []
                commands=[]
                while not self.commands.empty(): commands.append(self.commands.get_nowait())
                states={}
                for device in devices:
                    gpu=device.get('identity') or device
                    identifier=device['uuid']
                    try:
                        status=agent.status(identifier)
                        if device.get('enabled') and status['state']=='idle':
                            agent.start_benchmark(gpu,duration_seconds=300)
                        for _,objective,target in commands:
                            if target==identifier:agent.start_benchmark(gpu,duration_seconds=300,objective=objective)
                        sample=device.get('gpu') or {};miner=device.get('miner') or {}
                        fresh=0<=time.time()-float(device.get('collected_at',0))<=10
                        if fresh and sample and not sample.get('error') and device.get('enabled'):
                            status=agent.observe(gpu,sample,hashrate_ths=miner.get('hashrate') if device.get('miner_running') and not miner.get('paused') else None,
                                accepted=miner.get('accepted',0),rejected=miner.get('rejected',0),hash_sampled_at=miner.get('hashrate_at'),hw_errors=miner.get('hw_errors',0))
                        plan=agent.recommend(gpu,sample,ollama=False) if sample and not sample.get('error') else {}
                        ai_settings=read_settings()
                        try:ai_config=configuration(ai_settings)
                        except ValueError:
                            ai_config={'provider':'unavailable'}
                            plan['ai_status']='Configuração de IA inválida; benchmark local continua ativo'
                        control=device.get('thermal_control') or {}
                        if ai_settings.get('ai_enabled') and ai_config['provider']!='ollama':
                            # The miner controller owns cloud consultations; the UI never duplicates them.
                            plan['ai_status']=control.get('ai_status') or 'Cloud configurada; aguardando supervisor/consulta'
                            if control.get('proposal'):plan['cloud_proposal']=control['proposal']
                        elif ai_settings.get('ai_enabled') and identifier==(self.selected_uuid or devices[0]['uuid']):
                            if advice_uuid!=identifier:
                                if advisor.thread is None or not advisor.thread.is_alive():
                                    with advisor.lock:advisor.advice=None;advisor.completed_at=None
                                    advice_uuid=identifier
                            if advice_uuid==identifier:
                                response=advisor.poll({'sample':sample,'benchmark':status})
                                plan['ai_status']=response['status']
                                if response.get('advice'):plan['local_advice']=response['advice']
                        else:plan['ai_status']='Desativada; controle adaptativo ativo'
                        status.update(gpu=gpu,recommendation=plan,controller=device.get('thermal_control') or {},
                            control_mode='read-only monitor; exclusive miner supervisor controls hardware',sampled_at=time.time())
                    except (OSError,ValueError,RuntimeError,KeyError) as error:
                        status={'state':'unavailable','reason':str(error),'gpu':gpu,'sampled_at':time.time()}
                    states[identifier]=status
                selected=self.selected_uuid if self.selected_uuid in states else next(iter(states),None)
                with self.lock:self.latest={**states.get(selected,{'state':'unavailable','reason':'No NVIDIA telemetry'}),'devices':states,'selected_uuid':selected}
                self.stop.wait(2)
        finally:agent.close()


def show_gpu_panel(parent, runtime):
    window=tk.Toplevel(parent)
    window.title('Benchmark e IA — NVIDIA')
    window.configure(bg='#0A1628')
    window.geometry('820x530')
    window.minsize(650,460)
    window.columnconfigure(0,weight=1)
    text=tk.StringVar()
    title=tk.Label(window,text='Benchmark por GPU • IA local ou Cloud Free',font=('Segoe UI',17,'bold'),bg='#0A1628',fg='#12A98E')
    title.grid(row=0,column=0,sticky='w',padx=20,pady=15)
    label=tk.Label(window,textvariable=text,justify='left',anchor='nw',font=('Segoe UI',11),bg='#0A1628',fg='white',wraplength=760)
    label.grid(row=1,column=0,sticky='nsew',padx=20,pady=5)
    window.rowconfigure(1,weight=1)
    progress=ttk.Progressbar(window,maximum=100)
    progress.grid(row=2,column=0,sticky='ew',padx=20,pady=10)
    buttons=tk.Frame(window,bg='#0A1628')
    buttons.grid(row=3,column=0,sticky='ew',padx=20,pady=15)
    tk.Button(buttons,text='Benchmark 5 min · desempenho',command=runtime.benchmark,padx=8,pady=8).pack(side='left',padx=(0,8))
    tk.Button(buttons,text='Benchmark 5 min · eficiência',command=lambda:runtime.benchmark('efficiency'),padx=8,pady=8).pack(side='left')
    tk.Button(buttons,text='Configurar IA',command=lambda:__import__('ai_setup').show_ai_setup(window),padx=8,pady=8).pack(side='left',padx=6)
    tk.Button(buttons,text='Fechar',command=window.destroy,padx=8,pady=8).pack(side='right')
    def refresh():
        if not window.winfo_exists(): return
        state=runtime.snapshot()
        gpu=state.get('gpu',{})
        plan=state.get('recommendation') or {}
        def value(key,unit=''):
            number=state.get(key)
            return 'N/D' if number is None else f'{number:.2f}{unit}'
        text.set('\n'.join([
            gpu.get('name','Identificando NVIDIA…'),
            f"GPU UUID: {gpu.get('uuid','N/D')}",
            f"Benchmark: {state.get('state','')} · {state.get('elapsed_seconds',0):.0f}/300 s",
            state.get('reason',''),
            f"Hashrate médio: {value('mean_hashrate_ths',' TH/s')}   •   Consumo: {value('mean_power_w',' W')}",
            f"Eficiência: {value('efficiency_ths_per_w',' TH/s/W')}   •   Medições independentes: {state.get('independent_hash_samples',0)}",
            f"Validação: {'concluída' if state.get('validated') else 'ainda não validada'}",
            f"IA: {plan.get('ai_status','aguardando análise')} · Sugestão: {plan.get('action','hold')}",
            str(plan.get('reason','')),
            'Este painel observa e recomenda. Ajustes são aplicados pelo supervisor exclusivo do minerador.',
            'Histórico por UUID preservado. Escolha o provedor em Configurar IA; a política local valida as sugestões.'
        ]))
        progress['value']=state.get('progress',0)*100
        label.configure(wraplength=max(500,window.winfo_width()-40))
        window.after(1000,refresh)
    refresh()
    return window
