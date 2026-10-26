"""Visão compacta de até seis GPUs; identidade sempre por UUID."""
from __future__ import annotations
import json
import os
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox
import time
from valuation import numeric

BG,PANEL,BORDER,TEXT,MUTED,TEAL,ORANGE='#0A1628','#10213B','#23446D','#FFFFFF','#E8EDF5','#12A98E','#ffbc66'

def fresh_hash(device,now=None):
    now=time.time() if now is None else now
    miner=device.get('miner') or {}
    stamp=miner.get('hashrate_at')
    stamp=numeric(stamp)
    rate=numeric(miner.get('hashrate'))
    return rate if rate is not None and rate>=0 and device.get('enabled') and device.get('miner_running') and not miner.get('paused') and stamp is not None and -5<=now-stamp<=180 else None

def project_selected(data,identifier):
    devices=(data.get('rig') or {}).get('devices') or []
    selected=next((d for d in devices if d['uuid']==identifier),devices[0] if devices else None)
    if not selected: return dict(data)
    result=dict(data)
    for key in ('gpu','miner','miner_running','log_path','log_error','thermal_control','support_contribution','health','recovery'):
        result[key]=selected.get(key)
    result['gpu_error']=(selected.get('gpu') or {}).get('error')
    result['process_error']=selected.get('process_error') or data.get('process_error')
    result['miner_state']=selected.get('state')
    result['selected_gpu_uuid']=selected['uuid']
    return result

def totals(devices,now=None):
    rates=[fresh_hash(d,now) for d in devices]
    powers=[numeric((d.get('gpu') or {}).get('power.draw')) for d in devices]
    powers=[v if v is not None and v>=0 else None for v in powers]
    temperatures=[numeric((d.get('gpu') or {}).get('temperature.gpu')) for d in devices]
    temperatures=[v if v is not None and 0<=v<=120 else None for v in temperatures]
    return {'hashrate_ths':sum(v for v in rates if isinstance(v,(int,float))) if any(v is not None for v in rates) else None,
      'power_w':sum(v for v in powers if isinstance(v,(int,float))) if any(v is not None for v in powers) else None,
      'max_temperature_c':max((v for v in temperatures if isinstance(v,(int,float))),default=None),
      'enabled_count':sum(bool(d.get('enabled')) for d in devices),'mining_count':sum(v is not None for v in rates),
      'detected_count':len(devices),'power_partial':any(v is None for v in powers)}

def save_enabled(settings_path,identifier,enabled):
    from rig_config import update_gpu_settings
    path=Path(settings_path)
    settings=json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
    updated=update_gpu_settings(settings,identifier,enabled=enabled)
    temporary=path.with_name(path.name+'.rig-ui.tmp')
    with temporary.open('w',encoding='utf-8') as handle:
        json.dump(updated,handle,ensure_ascii=False,indent=2)
        handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary,path)
    return updated

class RigCards(tk.Frame):
    def __init__(self,parent,on_select,on_toggle,on_config):
        super().__init__(parent,bg=BG)
        self.on_select,self.on_toggle,self.on_config=on_select,on_toggle,on_config
        self.cards=[]; self.devices=[]; self.selected=None
        for slot in range(6):
            frame=tk.Frame(self,bg=PANEL,highlightthickness=1,highlightbackground=BORDER,padx=8,pady=4)
            title=tk.Label(frame,text='',bg=PANEL,fg=TEXT,anchor='w',font=('Segoe UI',-12,'bold'))
            title.pack(fill='x')
            values=tk.Label(frame,text='',bg=PANEL,fg=TEAL,anchor='w',font=('Segoe UI',-14,'bold'))
            values.pack(fill='x',pady=(2,0))
            note=tk.Label(frame,text='',bg=PANEL,fg=MUTED,anchor='w',font=('Segoe UI',-11))
            note.pack(fill='x')
            buttons=tk.Frame(frame,bg=PANEL);buttons.pack(fill='x',pady=(3,0))
            toggle=tk.Button(buttons,text='Desativar',command=lambda s=slot:self.toggle(s),bg=BG,fg=TEXT,relief='flat',font=('Segoe UI',-11),padx=5,pady=1)
            toggle.pack(side='left')
            config=tk.Button(buttons,text='Carteira',command=lambda s=slot:self.configure_gpu(s),bg=BG,fg=TEXT,relief='flat',font=('Segoe UI',-11),padx=5,pady=1)
            config.pack(side='right')
            for widget in (frame,title,values,note):widget.bind('<Button-1>',lambda e,s=slot:self.select(s))
            self.cards.append((frame,title,values,note,toggle,config))
    def select(self,slot):
        if slot<len(self.devices):self.on_select(self.devices[slot]['uuid'])
    def toggle(self,slot):
        if slot<len(self.devices):
            device=self.devices[slot];self.on_toggle(device['uuid'],not device.get('enabled'))
    def configure_gpu(self,slot):
        if slot<len(self.devices):self.on_config(self.devices[slot]['uuid'])
    def render(self,devices,selected=None):
        self.devices=list(devices)[:6]; self.selected=selected
        for slot,(frame,title,values,note,toggle,config) in enumerate(self.cards):
            if slot>=len(self.devices):frame.grid_remove();continue
            d=self.devices[slot];gpu=d.get('gpu') or {};rate=fresh_hash(d)
            title.configure(text=f"GPU {d['index']} · {d['name'].replace('NVIDIA GeForce ','')}")
            def f(v,unit):return 'N/D' if v is None else f'{v:.1f}{unit}'
            values.configure(text=f"{f(rate,' TH/s')}  ·  {f(gpu.get('temperature.gpu'),'°C')}  ·  {f(gpu.get('power.draw'),' W')}")
            state=('Parada pendente' if d.get('miner_running') else 'Desativada · sensores ativos') if not d.get('enabled') else 'Minerando' if rate is not None else d.get('state','Aguardando')
            note.configure(text=f"{d['uuid'][-8:]} · {'Global' if d.get('uses_default_wallet') else 'Própria'} · {'OFF' if not d.get('enabled') else 'ON' if rate is not None else 'Aguarda'}")
            toggle.configure(text='Desativar' if d.get('enabled') else 'Ativar')
            frame.configure(highlightbackground=TEAL if d['uuid']==selected else BORDER)
        self.adapt(self.winfo_width())
    def adapt(self,width):
        columns=min(max(1,len(self.devices)),6 if width>=1800 else 3 if width>=900 else 2)
        for i in range(6):self.columnconfigure(i,weight=1 if i<columns else 0,uniform='gpu' if i<columns else '')
        for slot,card in enumerate(self.cards):
            if slot<len(self.devices):
                card[0].grid(row=slot//columns,column=slot%columns,sticky='nsew',padx=3,pady=3)
                for widget,size in zip(card[1:],(11,13,10,10,10)):widget.configure(font=('Segoe UI',-size,'bold' if widget in card[1:3] else 'normal'))
                title,values,note=card[1:4];buttons=card[4].master
                for widget in (title,values,note,buttons):widget.pack_forget()
                title.grid(row=0,column=0,columnspan=2,sticky='ew')
                values.grid(row=1,column=0,columnspan=2,sticky='ew')
                note.grid(row=2,column=0,sticky='w');buttons.grid(row=2,column=1,sticky='e')
                card[0].columnconfigure(0,weight=1)
                card[0].configure(pady=2)
                for widget in (title,values,note):widget.configure(pady=0)
        self.configure(height=1)

def show_identity(parent,device):
    window=tk.Toplevel(parent);window.title('Identificação completa da GPU');window.configure(bg=BG)
    window.geometry('760x500');window.minsize(540,390)
    identity=device.get('identity') or device
    fields=[('Índice atual','index'),('Nome','name'),('UUID','uuid'),('PCI bus','pci_bus_id'),('PCI device','pci_device_id'),
            ('PCI subsystem','pci_sub_device_id'),('Serial','serial'),('VBIOS','vbios_version'),('Driver','driver_version'),
            ('VRAM MiB','memory_mib'),('Arquitetura','architecture'),('Carteira efetiva','wallet')]
    for row,(label,key) in enumerate(fields):
        tk.Label(window,text=label,bg=BG,fg=MUTED,font=('Segoe UI',-12)).grid(row=row,column=0,sticky='w',padx=12,pady=3)
        value=identity.get(key,device.get(key))
        entry=tk.Entry(window,readonlybackground=PANEL,fg=TEXT,relief='flat',font=('Consolas',-12))
        entry.insert(0,str(value) if value is not None else 'N/D');entry.configure(state='readonly');entry.grid(row=row,column=1,sticky='ew',padx=10,pady=3)
    window.columnconfigure(1,weight=1)
    tk.Button(window,text='Fechar',command=window.destroy).grid(row=len(fields),column=1,sticky='e',padx=10,pady=10)
    return window
