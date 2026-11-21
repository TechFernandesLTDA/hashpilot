"""Visão compacta de até seis GPUs; identidade sempre por UUID."""
from __future__ import annotations
import json
import os
from pathlib import Path
import tkinter as tk
import time
from valuation import numeric
import ui_kit as ui
from monitor_theme import SURFACE, TEXT, TEXT_TERTIARY

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
    for key in ('gpu','miner','miner_running','log_path','log_error','thermal_control','support_contribution','health','recovery','restoration'):
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
        super().__init__(parent,bg=SURFACE)
        self.on_select,self.on_toggle,self.on_config=on_select,on_toggle,on_config
        self.cards=[]; self.devices=[]; self.selected=None
        self.font_scale=1.0  # px multiplicador definido por Monitor.adapt_layout (corpo da janela / 12)
        for slot in range(6):
            frame=ui.Card(self,padding='md',hover=True)
            bg=frame.bg
            title=tk.Label(frame,text='',bg=bg,fg=TEXT,anchor='w',font=ui.font('label',size=-12))
            title.pack(fill='x')
            values=tk.Label(frame,text='',bg=bg,fg=TEXT,anchor='w',font=ui.font('label',size=-14))
            values.pack(fill='x',pady=(2,0))
            note=tk.Label(frame,text='',bg=bg,fg=TEXT_TERTIARY,anchor='w',font=ui.font('caption',size=-11))
            note.pack(fill='x')
            buttons=tk.Frame(frame,bg=bg);buttons.pack(fill='x',pady=(3,0))
            toggle=ui.Button(buttons,'Parar',lambda s=slot:self.toggle(s),variant='secondary',size='sm',bg=bg)
            toggle.pack(side='left')
            config=ui.Button(buttons,'Carteira',lambda s=slot:self.configure_gpu(s),variant='ghost',size='sm',bg=bg)
            config.pack(side='right')
            for widget in (frame,title,values,note):widget.bind('<Button-1>',lambda e,s=slot:self.select(s))
            self.cards.append((frame,title,values,note,toggle,config))
    def select(self,slot):
        if slot<len(self.devices):self.on_select(self.devices[slot]['uuid'])
    def toggle(self,slot):
        if slot<len(self.devices):
            device=self.devices[slot];self.on_toggle(device['uuid'],not (device.get('enabled') or device.get('miner_running')))
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
            restoration=d.get('restoration') or {}
            stopped_note='Padrão restaurado' if restoration.get('state')=='restored' else 'Restauração incompleta' if restoration.get('state') in ('partial','failed') else 'Restaurando' if restoration.get('state')=='restoring' else 'Parada'
            note.configure(text=(stopped_note if not d.get('enabled') else f"{d['uuid'][-8:]} · {'Global' if d.get('uses_default_wallet') else 'Própria'} · {'ON' if rate is not None else 'Aguarda'}"))
            toggle.configure(text='Parar' if d.get('enabled') or d.get('miner_running') else 'Iniciar')
            frame.set_selected(d['uuid']==selected)
        self.adapt(self.winfo_width())
    def adapt(self,width):
        columns=min(max(1,len(self.devices)),6 if width>=1800 else 3 if width>=900 else 2)
        for i in range(6):self.columnconfigure(i,weight=1 if i<columns else 0,uniform='gpu' if i<columns else '')
        for slot,card in enumerate(self.cards):
            if slot<len(self.devices):
                column=slot%columns;row=slot//columns;half=ui.px(self,6);rows=-(-len(self.devices)//columns)
                card[0].grid(row=row,column=column,sticky='nsew',padx=(0 if column==0 else half,0 if column==columns-1 else half),pady=(0 if row==0 else half,0 if row==rows-1 else half))
                for widget,role,size in zip(card[1:6],('label','label','caption','label','label'),(13,15,11,12,12)):widget.configure(font=ui.font(role,size=-round(size*self.font_scale)))
                title,values,note=card[1:4];buttons=card[4].master
                for widget in (title,values,note,buttons):widget.pack_forget()
                title.grid(row=0,column=0,columnspan=2,sticky='ew')
                values.grid(row=1,column=0,columnspan=2,sticky='ew')
                note.grid(row=2,column=0,sticky='w');buttons.grid(row=2,column=1,sticky='e')
                card[0].columnconfigure(0,weight=1)
                for widget in (title,values,note):widget.configure(pady=0)
        self.configure(height=1)

def show_identity(parent,device):
    window=tk.Toplevel(parent);window.title('Identificação completa da GPU');window.configure(bg=SURFACE)
    ui.apply_styles(window)
    window.geometry(f'{ui.px(window,760)}x{ui.px(window,440)}');window.minsize(ui.px(window,540),ui.px(window,420))
    identity=device.get('identity') or device
    fields=[('Índice atual','index'),('Nome','name'),('UUID','uuid'),('PCI bus','pci_bus_id'),('PCI device','pci_device_id'),
            ('PCI subsystem','pci_sub_device_id'),('Serial','serial'),('VBIOS','vbios_version'),('Driver','driver_version'),
            ('VRAM MiB','memory_mib'),('Arquitetura','architecture'),('Carteira efetiva','wallet')]
    for row,(label,key) in enumerate(fields):
        top=(ui.px(window,20) if row==0 else ui.px(window,2),ui.px(window,2))  # respiro do topo da janela só na primeira linha
        ui.label(window,label,'small',tone='secondary').grid(row=row,column=0,sticky='w',padx=(ui.px(window,24),ui.px(window,12)),pady=top)
        value=identity.get(key,device.get(key))
        entry=ui.TextField(window,mono=True)
        entry.insert(0,str(value) if value is not None else 'N/D');entry.configure(state='readonly');entry.grid(row=row,column=1,sticky='ew',padx=(0,ui.px(window,24)),pady=top)
    window.columnconfigure(1,weight=1)
    ui.Button(window,'Fechar',window.destroy,variant='ghost').grid(row=len(fields),column=1,sticky='e',padx=ui.px(window,24),pady=ui.px(window,12))
    return window
