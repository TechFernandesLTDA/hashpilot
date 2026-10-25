"""Primeira abertura e configuração da carteira, sem acesso a chaves privadas."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, messagebox

from config import DEFAULT_WALLET, SETTINGS_PATH, LOG_DIR

ROOT = Path(__file__).resolve().parent
BG, PANEL, TEXT, MUTED, TEAL = '#0A1628', '#10213B', '#FFFFFF', '#B9C7DC', '#12A98E'


def read_settings(path: Path = SETTINGS_PATH) -> dict:
    try:
        data = json.loads(path.read_text(encoding='utf-8-sig'))
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError('A configuração precisa ser um objeto JSON.')
    return data


def save_settings(wallet: str, log_dir: str, path: Path = SETTINGS_PATH, *, support_enabled=None, ai_enabled=None) -> dict:
    wallet = wallet.strip()
    if not re.fullmatch(r'prl1[ac-hj-np-z02-9]{30,100}', wallet):
        raise ValueError('Informe um endereço público Pearl válido, começando com prl1.')
    data = read_settings(path)
    data.update(wallet=wallet, log_dir=log_dir.strip(), onboarding_complete=True)
    if support_enabled is not None:
        if not isinstance(support_enabled,bool): raise ValueError('A opção de ajuda precisa ser ativada ou desativada.')
        data['support_enabled']=support_enabled
    if ai_enabled is not None:
        data['ai_enabled']=bool(ai_enabled)
    data.setdefault('pool', 'prl.kryptex.network:7048')
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    temporary.replace(path)
    return data


def launch_mining_setup(wallet: str) -> None:
    if os.name != 'nt':
        raise RuntimeError('A instalação do minerador está disponível apenas no Windows.')
    subprocess.Popen(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
        str(ROOT / 'mining/windows/Completar-Instalacao.ps1'), '-Wallet', wallet,
        '-Python', sys.executable], cwd=ROOT, creationflags=subprocess.CREATE_NEW_CONSOLE)


def launch_ai_setup():
    subprocess.Popen(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',
        str(ROOT/'mining/windows/Instalar-IA.ps1')],cwd=ROOT,creationflags=subprocess.CREATE_NEW_CONSOLE)


def launch_rig_service():
    """Explicit user action; the Windows elevation prompt belongs to the OS."""
    subprocess.Popen(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',
        str(ROOT/'mining/windows/Iniciar-Rig.ps1'),'-Python',sys.executable],cwd=ROOT,
        creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))


def run_gpu_setup(parent=None,selected_uuid=None,devices=None,path=SETTINGS_PATH):
    """Até seis carteiras/interruptores, sempre associados ao UUID físico."""
    from gpu_agent import detect_nvidia_gpus
    from rig_config import resolve_devices,update_gpu_settings
    settings=read_settings(path)
    if devices is None:
        devices=((getattr(parent,'last_data',None) or {}).get('rig') or {}).get('devices')
    if devices is None: devices=detect_nvidia_gpus(max_devices=6)
    resolved=resolve_devices(devices,settings,LOG_DIR)
    window=tk.Toplevel(parent) if parent else tk.Tk()
    window.title('GPUs e carteiras — HashPilot')
    window.configure(bg=BG)
    width=min(1100,window.winfo_screenwidth()-60)
    height=min(650,window.winfo_screenheight()-80)
    window.geometry(f'{width}x{height}')
    window.minsize(min(width,980),min(height,560))
    window.columnconfigure(0,weight=1)
    tk.Label(window,text='Seu rig NVIDIA · até 6 GPUs',bg=BG,fg=TEAL,font=('Segoe UI',-24,'bold'),anchor='w').grid(row=0,column=0,sticky='ew',padx=20,pady=(14,5))
    tk.Label(window,text='Todas são ativadas por padrão. Cada placa pode usar a carteira global ou um endereço próprio. Desativar encerra somente o minerador daquela GPU.',bg=BG,fg=MUTED,font=('Segoe UI',-13),wraplength=width-40,justify='left',anchor='w').grid(row=1,column=0,sticky='ew',padx=20,pady=(0,8))
    holder=tk.Frame(window,bg=BG)
    holder.grid(row=2,column=0,sticky='nsew',padx=20)
    holder.columnconfigure(0,weight=1)
    window.rowconfigure(2,weight=1)
    rows=[]
    for index,device in enumerate(resolved):
        frame=tk.Frame(holder,bg=PANEL,highlightbackground=TEAL if device['uuid']==selected_uuid else '#23446D',highlightthickness=1,padx=8,pady=5)
        frame.grid(row=index,column=0,sticky='ew',pady=3)
        frame.columnconfigure(2,weight=1)
        enabled=tk.BooleanVar(value=device['enabled'])
        inherit=tk.BooleanVar(value=device['uses_default_wallet'])
        address=tk.StringVar(value=device['wallet'])
        title=device['name'].removeprefix('NVIDIA ').removeprefix('GeForce ')
        title=title if len(title)<=26 else title[:23]+'…'
        tk.Checkbutton(frame,text=f"GPU {device['index']} · {title}",variable=enabled,bg=PANEL,fg=TEXT,selectcolor=BG,activebackground=PANEL,activeforeground=TEXT,font=('Segoe UI',-12,'bold'),anchor='w',width=32).grid(row=0,column=0,sticky='w')
        tk.Label(frame,text=device['uuid'],bg=PANEL,fg=MUTED,font=('Consolas',-10),anchor='w').grid(row=1,column=0,columnspan=2,sticky='w')
        inherit_box=tk.Checkbutton(frame,text='Carteira global',variable=inherit,bg=PANEL,fg=TEXT,selectcolor=BG,activebackground=PANEL,activeforeground=TEXT,font=('Segoe UI',-12))
        inherit_box.grid(row=0,column=1,padx=8)
        entry=tk.Entry(frame,textvariable=address,bg=BG,fg=TEXT,readonlybackground=PANEL,insertbackground=TEXT,relief='flat',font=('Consolas',-11))
        entry.grid(row=0,column=2,rowspan=2,sticky='ew',ipady=9)
        def mode(inherit=inherit,address=address,entry=entry,device=device):
            if inherit.get():
                address.set(settings.get('wallet',DEFAULT_WALLET));entry.configure(state='readonly')
            else:
                if address.get()==settings.get('wallet',DEFAULT_WALLET): address.set(device.get('wallet_override') or '')
                entry.configure(state='normal');entry.focus_set()
        inherit_box.configure(command=mode)
        if inherit.get(): entry.configure(state='readonly')
        rows.append((device['uuid'],enabled,inherit,address))
    if not resolved:
        tk.Label(holder,text='Nenhuma GPU NVIDIA detectada. Confira o driver e nvidia-smi.',bg=BG,fg=MUTED,font=('Segoe UI',-16)).pack(pady=30)
    support=tk.BooleanVar(value=settings.get('support_enabled') is True)
    tk.Checkbutton(window,text='Ajuda opcional pela ferramenta: 5% do tempo para a carteira do autor',variable=support,bg=BG,fg=TEXT,selectcolor=PANEL,activebackground=BG,activeforeground=TEXT,font=('Segoe UI',-13),anchor='w').grid(row=3,column=0,sticky='ew',padx=20,pady=(7,3))
    tk.Label(window,text='Aplica-se somente a carteiras diferentes da do autor. Pode ser desativada. O serviço reinicia apenas GPUs cuja carteira mudou; um rig parado só inicia pelo botão de mineração.',bg=BG,fg=MUTED,font=('Segoe UI',-12),wraplength=width-40,justify='left',anchor='w').grid(row=4,column=0,sticky='ew',padx=20,pady=3)
    saved=False
    def finish():
        nonlocal saved
        try:
            updated=read_settings(path)
            for identifier,enabled,inherit,address in rows:
                updated=update_gpu_settings(updated,identifier,enabled=enabled.get(),wallet_override=None if inherit.get() else address.get().strip(),change_wallet=True)
            updated['support_enabled']=support.get()
            path.parent.mkdir(parents=True,exist_ok=True)
            temporary=path.with_name(path.name+'.gpu-setup.tmp')
            temporary.write_text(json.dumps(updated,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            temporary.replace(path)
            from rig import write_request
            write_request(path.parent,'reconcile')
        except (OSError,ValueError,RuntimeError) as error:
            messagebox.showerror('Configurar GPUs',str(error),parent=window);return
        saved=True;window.destroy()
    buttons=tk.Frame(window,bg=BG)
    buttons.grid(row=5,column=0,sticky='e',padx=20,pady=12)
    tk.Button(buttons,text='Cancelar',command=window.destroy,padx=14,pady=7).pack(side='left',padx=6)
    tk.Button(buttons,text='Salvar GPUs e carteiras',command=finish,bg=TEAL,fg=TEXT,relief='flat',padx=16,pady=8).pack(side='left')
    if parent:
        window.transient(parent);window.grab_set();parent.wait_window(window)
    else: window.mainloop()
    return saved


def run_setup(parent=None,selected_uuid=None) -> bool:
    if selected_uuid is not None: return run_gpu_setup(parent,selected_uuid)
    settings = read_settings()
    window = tk.Toplevel(parent) if parent else tk.Tk()
    window.title('Configurar HashPilot — Tech Fernandes')
    window.configure(bg=BG)
    width = min(760, window.winfo_screenwidth()-60)
    height = min(640, window.winfo_screenheight()-80)
    window.geometry(f'{width}x{height}')
    window.minsize(min(660,width), min(500,height))
    window.columnconfigure(0, weight=1)
    try:
        window.iconbitmap(str(ROOT / 'assets/brand/hashpilot.ico'))
    except tk.TclError:
        pass
    saved = False
    wallet = tk.StringVar(value=settings.get('wallet', DEFAULT_WALLET))
    logs = tk.StringVar(value=settings.get('log_dir') or str(LOG_DIR))
    mine = tk.BooleanVar(value=False)
    ai = tk.BooleanVar(value=bool(settings.get('ai_enabled')))
    support = tk.BooleanVar(value=settings.get('support_enabled',True) is True)

    def label(text, row, color=TEXT, size=11):
        widget = tk.Label(window, text=text, bg=BG, fg=color, font=('Segoe UI',-int(size*1.3)), anchor='w', justify='left', wraplength=width-48)
        widget.grid(row=row,column=0,sticky='ew',padx=24,pady=5)
        return widget

    label('Bem-vindo ao HashPilot', 0, TEAL, 20)
    label('Acompanhe sua GPU NVIDIA, a mineração e os recebimentos em PRL.',1)
    label('Carteira global · padrão para todas as GPUs sem carteira própria',2,TEAL)
    entry = tk.Entry(window,textvariable=wallet,font=('Consolas',-14),bg=PANEL,fg=TEXT,insertbackground=TEXT,relief='flat')
    entry.grid(row=3,column=0,sticky='ew',padx=24,ipady=9)
    label('O endereço preenchido por padrão pertence ao autor. Mantê-lo direciona a mineração iniciada por este assistente para o autor. Você pode substituí-lo pelo seu endereço público. Nunca informe seed ou chave privada.',4,MUTED,10)
    support_frame=tk.Frame(window,bg=PANEL,padx=8,pady=5)
    support_frame.grid(row=5,column=0,sticky='ew',padx=24,pady=4)
    tk.Checkbutton(support_frame,text='Contribuir com 5% da mineração para ajudar pela ferramenta',variable=support,bg=PANEL,fg=TEXT,selectcolor=BG,activebackground=PANEL,activeforeground=TEXT,anchor='w',font=('Segoe UI',-14)).pack(anchor='w')
    tk.Label(support_frame,text='Ajuda opcional ao autor: 57 min para sua carteira e 3 min para a carteira padrão por hora de mineração. Desmarque para desativar. Não é uma cobrança em dinheiro.',bg=PANEL,fg=MUTED,justify='left',anchor='w',wraplength=width-72,font=('Segoe UI',-13)).pack(fill='x')
    target=tk.Entry(support_frame,textvariable=tk.StringVar(value=DEFAULT_WALLET),state='readonly',readonlybackground=PANEL,fg=MUTED,relief='flat',font=('Consolas',-11))
    target.pack(fill='x',pady=(2,0))
    def show_support(*_):
        if wallet.get().strip() and wallet.get().strip()!=DEFAULT_WALLET:
            support_frame.grid()
        else:
            support_frame.grid_remove()
    wallet.trace_add('write',show_support)
    show_support()
    label('Pasta dos logs do SRBMiner',6,TEAL)
    logrow=tk.Frame(window,bg=BG)
    logrow.grid(row=7,column=0,sticky='ew',padx=24)
    logrow.columnconfigure(0,weight=1)
    tk.Entry(logrow,textvariable=logs,bg=PANEL,fg=TEXT,insertbackground=TEXT,relief='flat').grid(row=0,column=0,sticky='ew',ipady=7)
    def browse():
        chosen=filedialog.askdirectory(parent=window,title='Pasta dos logs')
        if chosen: logs.set(chosen)
    tk.Button(logrow,text='Escolher',command=browse).grid(row=0,column=1,padx=(8,0))
    check=tk.Checkbutton(window,text='Instalar e iniciar o minerador NVIDIA',variable=mine,bg=BG,fg=TEXT,selectcolor=PANEL,activebackground=BG,activeforeground=TEXT,anchor='w',font=('Segoe UI',-14))
    check.grid(row=8,column=0,sticky='ew',padx=20,pady=(10,3))
    if os.name != 'nt': check.configure(state='disabled')
    label('O SRBMiner tem SHA256 conferido. O controle NVIDIA solicita elevação do Windows. A carteira de um minerador ativo só muda ao reiniciá-lo.',9,MUTED,10)
    label('Histórico permanente por carteira • você pode voltar em Configurar a qualquer momento.',10,MUTED,10)
    ai_check=tk.Checkbutton(window,text='Ativar sugestões de IA',variable=ai,bg=BG,fg=TEXT,selectcolor=PANEL,activebackground=BG,activeforeground=TEXT,anchor='w',font=('Segoe UI',-14))
    ai_check.grid(row=11,column=0,sticky='w',padx=20,pady=4)
    def choose_ai():
        __import__('ai_setup').show_ai_setup(window)
        ai.set(read_settings().get('ai_enabled') is True)
    tk.Button(window,text='Escolher provedor de IA',command=choose_ai).grid(row=11,column=0,sticky='e',padx=24)
    label('Escolha OpenRouter, OpenCode ou Ollama local no assistente de IA. Cloud Free poupa CPU/RAM do rig.',12,MUTED,10)
    window.rowconfigure(13,weight=1)
    buttons=tk.Frame(window,bg=BG)
    buttons.grid(row=14,column=0,sticky='e',padx=24,pady=12)
    def finish():
        nonlocal saved
        try:
            save_settings(wallet.get(),logs.get(),support_enabled=support.get(),ai_enabled=ai.get())
            if ai.get() and not read_settings().get("ai"):
                __import__("ai_setup").show_ai_setup(window)
            if mine.get(): launch_mining_setup(wallet.get().strip())
        except (ValueError,OSError,RuntimeError) as error:
            messagebox.showerror('Configuração',str(error),parent=window)
            return
        saved=True
        window.destroy()
    tk.Button(buttons,text='Cancelar',command=window.destroy,padx=12,pady=7).pack(side='left',padx=6)
    tk.Button(buttons,text='Salvar e abrir painel',command=finish,bg=TEAL,fg=TEXT,relief='flat',padx=16,pady=8).pack(side='left')
    entry.focus_set()
    if parent:
        window.transient(parent)
        window.grab_set()
        parent.wait_window(window)
    else:
        window.mainloop()
    return saved
