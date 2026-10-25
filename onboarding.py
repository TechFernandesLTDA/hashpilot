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
    support = tk.BooleanVar(value=settings.get('support_enabled',True) is True)

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
    ai_check=tk.Checkbutton(window,text='Ativar IA local grátis (Ollama + modelo na CPU)',variable=ai,bg=BG,fg=TEXT,selectcolor=PANEL,activebackground=BG,activeforeground=TEXT,anchor='w',font=('Segoe UI',-14))
    ai_check.grid(row=11,column=0,sticky='ew',padx=20,pady=4)
    if os.name != 'nt': ai_check.configure(state='disabled')
    label('Opcional: baixa Ollama e um modelo local. Sem API paga; usa memória e processamento da CPU.',12,MUTED,10)
    window.rowconfigure(13,weight=1)
    buttons=tk.Frame(window,bg=BG)
    buttons.grid(row=14,column=0,sticky='e',padx=24,pady=12)
    def finish():
        nonlocal saved
        try:
            save_settings(wallet.get(),logs.get(),support_enabled=support.get(),ai_enabled=ai.get())
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
