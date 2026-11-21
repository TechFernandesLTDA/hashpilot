"""Ajuda transparente para revisão e exceção local do minerador no Windows."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tkinter as tk
from tkinter import messagebox
import webbrowser

from monitor_theme import SURFACE
import ui_kit as ui

ROOT = Path(__file__).resolve().parent
SUBMISSION_URL = 'https://www.microsoft.com/en-us/wdsi/filesubmission'
HISTORY_URL = 'https://support.microsoft.com/en-us/windows/security/windows-security/protection-history-in-the-windows-security-app'
EXPLANATION = (
    'O Defender pode bloquear o SRBMiner, um minerador de terceiros. Uma detecção '
    'não comprova sozinha infecção nem falso positivo. SHA-256 igual ao pacote '
    'oficial confirma a origem, mas não garante segurança.\n\n'
    'Para Sabsik.FL.A!ml, a Microsoft não publica detalhes técnicos da variante. '
    'É necessária análise específica do arquivo para esclarecer a detecção. '
    'O caminho recomendado é solicitar análise antes de permitir o arquivo.\n\n'
    'A exceção é opcional e local: dispensa a verificação antivírus no caminho '
    'exato do executável, inclusive para arquivos que o substituam nesse caminho. '
    'Não é aprovação da Microsoft, não torna o minerador imune e pode ser removida. '
    'Nenhuma pasta inteira, processo Python ou proteção global será excluída.\n\n'
    'A operação não restaura arquivos em quarentena nem inicia mineração. '
    'Restaurar ou reinstalar depois pode fazer o serviço do rig retomar a mineração.'
)


def launch_exception_request(action,version=None):
    if action not in ('Add', 'Remove'):
        raise ValueError('Ação de exceção inválida.')
    if os.name != 'nt':
        raise RuntimeError('Este recurso requer Windows.')
    from miner_update import selected_version,descriptor
    version=version or selected_version();descriptor(version)
    script = ROOT / 'mining/windows/Permissao-Defender.ps1'
    if not script.is_file():
        raise RuntimeError('Assistente do Defender ausente. Reinstale o HashPilot.')
    return subprocess.Popen(
        ['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(script), '-Version', version, '-Action', action],
        cwd=ROOT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def show_defender_setup(parent,container=None):
    from ui_host import open_window,present,embedded
    window = open_window(parent,container,'Segurança do minerador — HashPilot')
    ui.apply_styles(window)
    window.configure(bg=SURFACE)
    width = min(ui.px(window,730), window.winfo_screenwidth()-60)
    height = min(ui.px(window,640), window.winfo_screenheight()-80)
    window.geometry(f'{width}x{height}')
    window.minsize(width, height)
    window.columnconfigure(0, weight=1)
    # Embutida nas Configurações, o rodapé acompanha o conteúdo em vez de descer até o fim da área visível.
    window.rowconfigure(3, weight=0 if embedded(window) else 1)
    margin = ui.px(window, 32 if embedded(window) else 24)
    wrapped=[]
    for text,role,tone,row,pady in (('Revisão do bloqueio e permissão do administrador','h1','primary',0,(ui.px(window,24 if embedded(window) else 20),ui.px(window,8))),(EXPLANATION,'body','secondary',1,0)):
        item=ui.label(window, text, role, tone=tone, wraplength=width-2*margin)
        item.grid(row=row, column=0, sticky='ew', padx=margin, pady=pady);wrapped.append((item,0))
    links=tk.Frame(window,bg=SURFACE);links.grid(row=2,column=0,sticky='w',padx=margin-ui.px(window,15),pady=ui.px(window,12))
    ui.Button(links,'Solicitar análise à Microsoft',lambda:webbrowser.open(SUBMISSION_URL),variant='ghost').pack(side='left',padx=(0,ui.px(window,8)))
    def history():
        try:os.startfile('windowsdefender://history')
        except OSError:webbrowser.open(HISTORY_URL)
    ui.Button(links,'Histórico de proteção',history,variant='ghost').pack(side='left')
    from miner_update import selected_version,CATALOG
    miner_choice=tk.StringVar(value=selected_version())
    version_menu=tk.OptionMenu(links,miner_choice,*CATALOG)
    ui.style_option_menu(version_menu)
    version_menu.pack(side='left',padx=ui.px(window,8))
    consent=tk.BooleanVar(value=False)
    risk=ui.check(window,'Entendo o risco e quero solicitar a exceção para este executável.',consent,
        command=lambda:add.configure(state='normal' if consent.get() else 'disabled'),
        wraplength=width-2*margin)
    risk.grid(row=4,column=0,sticky='w',padx=margin,pady=ui.px(window,8));wrapped.append((risk,0))
    def fit(event):
        if event.widget is window:
            for item,extra in wrapped:item.configure(wraplength=max(ui.px(window,240),event.width-2*margin-extra))
    if embedded(window):window.bind('<Configure>',fit,add='+')
    def request(action):
        if action=='Add' and not consent.get():return
        try:launch_exception_request(action,miner_choice.get())
        except (OSError,ValueError,RuntimeError) as error:messagebox.showerror('Permissão do Defender',str(error),parent=window)
    if not embedded(window):ui.Separator(window).grid(row=5,column=0,sticky='ew')
    buttons=tk.Frame(window,bg=SURFACE);buttons.grid(row=6,column=0,sticky='ew',padx=margin,pady=ui.px(window,16))
    add=ui.Button(buttons,'Solicitar exceção (UAC)',lambda:request('Add'),variant='primary',state='disabled')
    add.pack(side='left')
    ui.Button(buttons,'Remover exceção (UAC)',lambda:request('Remove'),variant='danger_ghost').pack(side='left',padx=ui.px(window,8))
    if not embedded(window):ui.Button(buttons,'Fechar',window.destroy,variant='ghost').pack(side='right')
    present(parent,window)
    return window

