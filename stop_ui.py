"""Parada fácil no painel e na bandeja; resultado visível para cada GPU."""
import argparse
import time
import tkinter as tk
from config import STATE_DIR,LOG_DIR,SETTINGS_PATH
from stop_mining import request_stop,read,status_path
from contribution import atomic_json
from rig_config import device_key

BG,PANEL,TEXT,MUTED,RED,TEAL='#0A1628','#10213B','#FFFFFF','#B9C7DC','#9E3333','#12A98E'

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
    window=tk.Toplevel(parent);window.title('Parar mineração e restaurar padrões');window.configure(bg=BG)
    width=min(820,window.winfo_screenwidth()-60);height=min(570,window.winfo_screenheight()-90)
    window.geometry(f'{width}x{height}');window.minsize(min(width,720),min(height,480));window.transient(parent)
    window.columnconfigure(0,weight=1)
    tk.Label(window,text='Parar mineração',bg=BG,fg=TEAL,font=('Segoe UI',20,'bold'),anchor='w').grid(row=0,column=0,sticky='ew',padx=20,pady=(16,5))
    tk.Label(window,text='Escolha as placas ou pare todas. A parada desativa a retomada automática e restaura núcleo, memória, potência e ventoinhas para os padrões do driver.',bg=BG,fg=MUTED,wraplength=width-40,justify='left',anchor='w').grid(row=1,column=0,sticky='ew',padx=20,pady=5)
    rows=tk.Frame(window,bg=BG);rows.grid(row=2,column=0,sticky='nsew',padx=20,pady=10);rows.columnconfigure(0,weight=1);window.rowconfigure(2,weight=1)
    known=[d for d in devices if isinstance(d,dict) and d.get('uuid')][:6]
    if not known:
        registry=read(STATE_DIR/'rig-manager.json')
        known=[{'uuid':key,**entry} for key,entry in registry.get('devices',{}).items()][:6]
    choices={};labels={};requests={}
    window.gpu_choices=choices
    for row,device in enumerate(known):
        identifier=device_key(device['uuid']);variable=tk.BooleanVar(master=window,value=True);choices[identifier]=variable
        frame=tk.Frame(rows,bg=PANEL,padx=8,pady=5);frame.grid(row=row,column=0,sticky='ew',pady=3);frame.columnconfigure(0,weight=1)
        tk.Checkbutton(frame,text=f"GPU {device.get('index','?')} · {device.get('name','NVIDIA')} · {identifier[-8:]}",variable=variable,bg=PANEL,fg=TEXT,selectcolor=BG,activebackground=PANEL,activeforeground=TEXT,font=('Segoe UI',10,'bold'),anchor='w').grid(row=0,column=0,sticky='ew')
        label=tk.Label(frame,text='Selecione para parar e restaurar',bg=PANEL,fg=MUTED,anchor='w',justify='left',wraplength=width-72,font=('Segoe UI',9))
        label.grid(row=1,column=0,sticky='ew');labels[identifier]=label
    note=tk.Label(window,text='Com o serviço atualizado ativo, a parada usa a autorização já concedida. Com o serviço parado ou antigo, o Windows pede UAC para restaurar o driver. Uma falha aparece por componente; 0% de fan não é imposto manualmente.',bg=BG,fg=MUTED,justify='left',wraplength=width-40,anchor='w',font=('Segoe UI',9))
    note.grid(row=3,column=0,sticky='ew',padx=20,pady=8)
    def stop(all_devices=False):
        targets=list(choices) if all_devices else [identifier for identifier,var in choices.items() if var.get()]
        if not targets:note.configure(text='Selecione pelo menos uma GPU NVIDIA.');return
        try:
            result=request_stop(targets,STATE_DIR,LOG_DIR,SETTINGS_PATH)
            requests.update({identifier:result.get('requested_at_by_gpu',{}).get(identifier,result['requested_at']) for identifier in targets})
            note.configure(text='Parada solicitada. '+('Aprove o UAC do Windows para concluir a restauração.' if result['requires_uac'] else 'Acompanhe a confirmação por placa abaixo.'))
        except (OSError,ValueError,RuntimeError) as error:note.configure(text=str(error),fg='#ffbc66')
    actions=tk.Frame(window,bg=BG);actions.grid(row=4,column=0,sticky='ew',padx=20,pady=(5,16))
    tk.Button(actions,text='Parar TODAS e restaurar',command=lambda:stop(True),bg=RED,fg=TEXT,relief='flat',padx=12,pady=8).pack(side='left')
    tk.Button(actions,text='Parar selecionadas e restaurar',command=stop,bg=PANEL,fg=TEXT,relief='flat',padx=12,pady=8).pack(side='left',padx=8)
    tk.Button(actions,text='Fechar',command=window.destroy,padx=12,pady=8).pack(side='right')
    def refresh():
        if not window.winfo_exists():return
        for identifier,requested_at in requests.items():
            value=read(status_path(LOG_DIR,identifier))
            if value.get('at',0)>=requested_at:
                labels[identifier].configure(text=describe(value),fg=TEAL if value.get('state')=='restored' else '#ffbc66')
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
