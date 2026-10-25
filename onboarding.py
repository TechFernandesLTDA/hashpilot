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


def save_settings(wallet: str, log_dir: str, path: Path = SETTINGS_PATH) -> dict:
    wallet = wallet.strip()
    if not re.fullmatch(r'prl1[ac-hj-np-z02-9]{30,100}', wallet):
        raise ValueError('Informe um endereço público Pearl válido, começando com prl1.')
    data = read_settings(path)
    data.update(wallet=wallet, log_dir=log_dir.strip(), onboarding_complete=True)
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


def run_setup(parent=None) -> bool:
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

    def label(text, row, color=TEXT, size=11):
        widget = tk.Label(window, text=text, bg=BG, fg=color, font=('Segoe UI',-int(size*1.3)), anchor='w', justify='left', wraplength=width-48)
        widget.grid(row=row,column=0,sticky='ew',padx=24,pady=5)
        return widget

    label('Bem-vindo ao HashPilot', 0, TEAL, 20)
    label('Acompanhe sua GPU NVIDIA, a mineração e os recebimentos em PRL.',1)
    label('Carteira pública para acompanhar e receber Pearl',2,TEAL)
    entry = tk.Entry(window,textvariable=wallet,font=('Consolas',-14),bg=PANEL,fg=TEXT,insertbackground=TEXT,relief='flat')
    entry.grid(row=3,column=0,sticky='ew',padx=24,ipady=9)
    label('O endereço preenchido por padrão pertence ao autor. Mantê-lo direciona a mineração iniciada por este assistente para o autor. Você pode substituí-lo pelo seu endereço público. Nunca informe seed ou chave privada.',4,MUTED,10)
    label('Pasta dos logs do SRBMiner',5,TEAL)
    logrow=tk.Frame(window,bg=BG)
    logrow.grid(row=6,column=0,sticky='ew',padx=24)
    logrow.columnconfigure(0,weight=1)
    tk.Entry(logrow,textvariable=logs,bg=PANEL,fg=TEXT,insertbackground=TEXT,relief='flat').grid(row=0,column=0,sticky='ew',ipady=7)
    def browse():
        chosen=filedialog.askdirectory(parent=window,title='Pasta dos logs')
        if chosen: logs.set(chosen)
    tk.Button(logrow,text='Escolher',command=browse).grid(row=0,column=1,padx=(8,0))
    check=tk.Checkbutton(window,text='Instalar e iniciar o minerador NVIDIA',variable=mine,bg=BG,fg=TEXT,selectcolor=PANEL,activebackground=BG,activeforeground=TEXT,anchor='w',font=('Segoe UI',-14))
    check.grid(row=7,column=0,sticky='ew',padx=20,pady=(12,3))
    if os.name != 'nt': check.configure(state='disabled')
    label('A instalação confere o SHA256 do SRBMiner. O controle da GPU solicita elevação do Windows. Se já houver mineração ativa, ela será preservada; a nova carteira só valerá na próxima inicialização do minerador.',8,MUTED,10)
    label('Histórico permanente por carteira • você pode voltar em Configurar a qualquer momento.',9,MUTED,10)
    ai_check=tk.Checkbutton(window,text='Ativar IA local grátis (Ollama + modelo na CPU)',variable=ai,bg=BG,fg=TEXT,selectcolor=PANEL,activebackground=BG,activeforeground=TEXT,anchor='w',font=('Segoe UI',-14))
    ai_check.grid(row=10,column=0,sticky='ew',padx=20,pady=4)
    if os.name != 'nt': ai_check.configure(state='disabled')
    label('Opcional: baixa Ollama e um modelo local. Sem API paga; usa memória e processamento da CPU.',11,MUTED,10)
    window.rowconfigure(12,weight=1)
    buttons=tk.Frame(window,bg=BG)
    buttons.grid(row=13,column=0,sticky='e',padx=24,pady=18)
    def finish():
        nonlocal saved
        try:
            data=save_settings(wallet.get(),logs.get())
            data['ai_enabled']=ai.get()
            temporary=SETTINGS_PATH.with_suffix('.json.tmp')
            temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            temporary.replace(SETTINGS_PATH)
            if ai.get(): launch_ai_setup()
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
