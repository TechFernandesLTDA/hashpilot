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
from gpu_agent import GPUAgent, OllamaAdvisor, detect_nvidia_gpus
from onboarding import read_settings


class GPURuntime:
    def __init__(self, state_dir=STATE_DIR):
        self.state_dir=Path(state_dir)
        self.stop=threading.Event()
        self.commands=queue.Queue()
        self.lock=threading.Lock()
        self.latest={'state':'starting','reason':'Identificando GPU NVIDIA…'}
        self.thread=threading.Thread(target=self.work,daemon=True,name='gpu-benchmark')
        self.thread.start()

    def snapshot(self):
        with self.lock: return dict(self.latest)

    def benchmark(self, objective='performance'):
        self.commands.put(('benchmark',objective))
        gpu=self.snapshot().get('gpu') or {}
        if gpu.get('uuid'):
            try:
                request={'action':'benchmark','gpu_uuid':gpu['uuid'],'objective':objective,'duration_seconds':300,'at':time.time()}
                LOG_DIR.mkdir(parents=True,exist_ok=True)
                target=LOG_DIR/'gpu-agent-request.json'
                temporary=target.with_suffix('.tmp')
                temporary.write_text(json.dumps(request),encoding='utf-8')
                temporary.replace(target)
            except OSError as error:
                with self.lock: self.latest['request_error']=str(error)

    def work(self):
        agent=GPUAgent(self.state_dir,advisor=OllamaAdvisor())
        gpu=None
        decision={}
        last_advice=0
        try:
            while not self.stop.is_set():
                try:
                    if gpu is None:
                        devices=detect_nvidia_gpus()
                        if not devices: raise RuntimeError('Nenhuma GPU NVIDIA compatível encontrada.')
                        # Telemetria do monitor atual é da GPU 0. UUID mantém históricos separados.
                        gpu=next((item for item in devices if int(item['index'])==0),devices[0])
                    status=agent.status(gpu['uuid'])
                    if status['state']=='idle' or (status['state']=='complete' and not status.get('validated')):
                        agent.start_benchmark(gpu,duration_seconds=300)
                    while not self.commands.empty():
                        _,objective=self.commands.get_nowait()
                        agent.start_benchmark(gpu,duration_seconds=300,objective=objective)
                    data=json.loads((self.state_dir/'latest.json').read_text(encoding='utf-8-sig'))
                    sample=data.get('gpu') or {}
                    miner=data.get('miner') or {}
                    fresh=time.time()-float(data.get('collected_at',0))<=10
                    if fresh and sample:
                        status=agent.observe(gpu,sample,hashrate_ths=miner.get('hashrate'),
                            accepted=miner.get('accepted',0),rejected=miner.get('rejected',0),
                            hash_sampled_at=miner.get('hashrate_at'))
                        if time.time()-last_advice>=60:
                            decision=agent.recommend(gpu,sample,ollama=bool(read_settings().get('ai_enabled')))
                            last_advice=time.time()
                    else:
                        status=agent.status(gpu['uuid'])
                        status['reason']='Aguardando telemetria recente do monitor e hashrate real.'
                    try:
                        controller=json.loads((LOG_DIR/'gpu-agent-control.json').read_text(encoding='utf-8-sig'))
                    except (OSError,ValueError): controller={}
                    status.update(gpu=gpu,recommendation=decision,controller=controller,
                        control_mode='observação; controle térmico pertence ao supervisor do minerador',sampled_at=time.time())
                    with self.lock: self.latest=status
                except (OSError,ValueError,RuntimeError,KeyError) as error:
                    with self.lock: self.latest={'state':'unavailable','reason':str(error),'sampled_at':time.time()}
                self.stop.wait(2)
        finally:
            agent.close()


def show_gpu_panel(parent, runtime):
    window=tk.Toplevel(parent)
    window.title('Benchmark e IA local — NVIDIA')
    window.configure(bg='#0A1628')
    window.geometry('820x530')
    window.minsize(650,460)
    window.columnconfigure(0,weight=1)
    text=tk.StringVar()
    title=tk.Label(window,text='Benchmark por GPU • IA local gratuita',font=('Segoe UI',17,'bold'),bg='#0A1628',fg='#12A98E')
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
            'Histórico por UUID é preservado. A IA usa Ollama na CPU; não executa comandos de texto.'
        ]))
        progress['value']=state.get('progress',0)*100
        label.configure(wraplength=max(500,window.winfo_width()-40))
        window.after(1000,refresh)
    refresh()
    return window
