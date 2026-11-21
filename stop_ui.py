"""Parada fácil no painel e na bandeja; resultado visível para cada GPU."""
import argparse
import time
import tkinter as tk
from config import STATE_DIR,LOG_DIR,SETTINGS_PATH
from stop_mining import request_stop,read,status_path
from fsutil import atomic_json
from rig_config import device_key
from monitor_theme import ACCENT_TEXT,SURFACE,TEXT_SECONDARY,WARNING
import ui_kit as ui

def describe(value):
    state=value.get('state')
    if state=='restored':return 'Padrões restaurados · fan automático, offsets zero, clocks livres e potência de fábrica'
    if state=='restoring':return 'Minerador encerrado · restaurando os padrões do driver…'
    if state=='pending':return value.get('message','Parada solicitada · aguardando o serviço')
    if state in ('partial','failed'):
        failures=value.get('failed_components') or []
        names={'fans':'ventoinhas','offsets':'offsets de núcleo/memória','graphics_clock':'clock do núcleo','memory_clock':'clock da memória','power':'potência','driver_or_ownership':'driver ou controle exclusivo'}
        return 'Restauração incompleta: '+(', '.join(names.get(key,key) for key in failures) or value.get('error','confira o driver e a autorização UAC'))
    return 'Sem restauração confirmada nesta operação'

def show_stop(parent,devices,*,immediate_all=False):
    window=tk.Toplevel(parent);window.title('Parar mineração e restaurar padrões');ui.apply_styles(window);window.configure(bg=SURFACE)
    width=min(ui.px(window,820),window.winfo_screenwidth()-60);height=min(ui.px(window,640),window.winfo_screenheight()-90)
    window.geometry(f'{width}x{height}');window.minsize(min(width,ui.px(window,720)),min(height,ui.px(window,480)));window.transient(parent)
    window.columnconfigure(0,weight=1)
    margin=ui.px(window,24)
    ui.label(window,'Parar mineração','h1').grid(row=0,column=0,sticky='ew',padx=margin,pady=(ui.px(window,20),ui.px(window,4)))
    ui.label(window,'Escolha as placas ou pare todas. A parada desativa a retomada automática e restaura núcleo, memória, potência e ventoinhas para os padrões do driver.','body',tone='secondary',wraplength=width-2*margin).grid(row=1,column=0,sticky='ew',padx=margin,pady=(0,ui.px(window,8)))
    rows=tk.Frame(window,bg=SURFACE);rows.grid(row=2,column=0,sticky='nsew',padx=margin,pady=ui.px(window,4));rows.columnconfigure(0,weight=1);window.rowconfigure(2,weight=1)
    known=[d for d in devices if isinstance(d,dict) and d.get('uuid')][:6]
    if not known:
        registry=read(STATE_DIR/'rig-manager.json')
        known=[{'uuid':key,**entry} for key,entry in registry.get('devices',{}).items()][:6]
    choices={};labels={};requests={}
    window.gpu_choices=choices
    for row,device in enumerate(known):
        identifier=device_key(device['uuid']);variable=tk.BooleanVar(master=window,value=True);choices[identifier]=variable
        frame=ui.Card(rows,padding=10);frame.grid(row=row,column=0,sticky='ew',pady=ui.px(window,3));frame.columnconfigure(0,weight=1)
        ui.check(frame,f"GPU {device.get('index','?')} · {device.get('name','NVIDIA')} · {identifier[-8:]}",variable,bg=frame.bg,font=ui.font('label')).grid(row=0,column=0,sticky='ew')
        label=ui.label(frame,'Selecione para parar e restaurar','small',tone='secondary',bg=frame.bg,wraplength=width-2*margin-ui.px(window,48))
        label.grid(row=1,column=0,sticky='ew');labels[identifier]=label
    note=ui.label(window,'Com o serviço atualizado ativo, a parada usa a autorização já concedida. Com o serviço parado ou antigo, o Windows pede UAC para restaurar o driver. Uma falha aparece por componente; 0% de fan não é imposto manualmente.','small',tone='secondary',wraplength=width-2*margin)
    note.grid(row=3,column=0,sticky='ew',padx=margin,pady=ui.px(window,8))
    def stop(all_devices=False):
        targets=list(choices) if all_devices else [identifier for identifier,var in choices.items() if var.get()]
        if not targets:note.configure(text='Selecione pelo menos uma GPU NVIDIA.',fg=WARNING);return
        try:
            result=request_stop(targets,STATE_DIR,LOG_DIR,SETTINGS_PATH)
            requests.update({identifier:result.get('requested_at_by_gpu',{}).get(identifier,result['requested_at']) for identifier in targets})
            note.configure(text='Parada solicitada. '+('Aprove o UAC do Windows para concluir a restauração.' if result['requires_uac'] else 'Acompanhe a confirmação por placa abaixo.'),fg=TEXT_SECONDARY)
        except (OSError,ValueError,RuntimeError) as error:note.configure(text=str(error),fg=WARNING)
    ui.Separator(window).grid(row=4,column=0,sticky='ew')
    actions=tk.Frame(window,bg=SURFACE);actions.grid(row=5,column=0,sticky='ew',padx=margin,pady=ui.px(window,16))
    ui.Button(actions,'Parar TODAS e restaurar',lambda:stop(True),variant='danger').pack(side='left')
    ui.Button(actions,'Parar selecionadas e restaurar',stop,variant='danger_ghost').pack(side='left',padx=ui.px(window,8))
    ui.Button(actions,'Fechar',window.destroy,variant='ghost').pack(side='right')
    def refresh():
        if not window.winfo_exists():return
        for identifier,requested_at in requests.items():
            value=read(status_path(LOG_DIR,identifier))
            if value.get('at',0)>=requested_at:
                labels[identifier].configure(text=describe(value),fg=ACCENT_TEXT if value.get('state')=='restored' else WARNING)
        window.after(500,refresh)
    window.stop_all=lambda:stop(True)
    refresh()
    if immediate_all:window.after(50,lambda:stop(True))
    return window

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--denied',action='store_true');parser.add_argument('--uuids',required=True)
    args=parser.parse_args()
    if args.denied:
        for identifier in args.uuids.split(','):
            atomic_json(status_path(LOG_DIR,device_key(identifier)),{'state':'failed','at':time.time(),
                'error':'UAC cancelado: parada solicitada, mas padrões do driver ainda não foram restaurados'})

if __name__=='__main__':main()
