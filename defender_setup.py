"""Ajuda transparente para revisão e exceção local do minerador no Windows."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tkinter as tk
from tkinter import messagebox
import webbrowser

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


def show_defender_setup(parent):
    window = tk.Toplevel(parent)
    window.title('Segurança do minerador — HashPilot')
    window.configure(bg='#0A1628')
    width = min(730, window.winfo_screenwidth()-60)
    height = min(610, window.winfo_screenheight()-80)
    window.geometry(f'{width}x{height}')
    window.minsize(width, height)
    window.columnconfigure(0, weight=1)
    window.rowconfigure(3, weight=1)
    tk.Label(window, text='Revisão do bloqueio e permissão do administrador', bg='#0A1628', fg='#12A98E',
             font=('Segoe UI', -21, 'bold'), wraplength=width-40, justify='left').grid(row=0, column=0, sticky='w', padx=20, pady=(16,8))
    tk.Label(window, text=EXPLANATION, bg='#0A1628', fg='#B9C7DC', font=('Segoe UI', -14),
             wraplength=width-40, justify='left', anchor='w').grid(row=1, column=0, sticky='ew', padx=20)
    links=tk.Frame(window,bg='#0A1628');links.grid(row=2,column=0,sticky='w',padx=20,pady=10)
    tk.Button(links,text='Solicitar análise à Microsoft',command=lambda:webbrowser.open(SUBMISSION_URL)).pack(side='left',padx=(0,8))
    def history():
        try:os.startfile('windowsdefender://history')
        except OSError:webbrowser.open(HISTORY_URL)
    tk.Button(links,text='Histórico de proteção',command=history).pack(side='left')
    from miner_update import selected_version,CATALOG
    miner_choice=tk.StringVar(value=selected_version())
    tk.OptionMenu(links,miner_choice,*CATALOG).pack(side='left',padx=8)
    consent=tk.BooleanVar(value=False)
    tk.Checkbutton(window,text='Entendo o risco e quero solicitar a exceção para este executável.',
        variable=consent,command=lambda:add.configure(state='normal' if consent.get() else 'disabled'),
        wraplength=width-60,justify='left',bg='#0A1628',fg='white',selectcolor='#10213B',
        activebackground='#0A1628',activeforeground='white',font=('Segoe UI',-14)).grid(row=4,column=0,sticky='w',padx=20,pady=8)
    def request(action):
        if action=='Add' and not consent.get():return
        try:launch_exception_request(action,miner_choice.get())
        except (OSError,ValueError,RuntimeError) as error:messagebox.showerror('Permissão do Defender',str(error),parent=window)
    buttons=tk.Frame(window,bg='#0A1628');buttons.grid(row=5,column=0,sticky='ew',padx=20,pady=(0,16))
    add=tk.Button(buttons,text='Solicitar exceção (UAC)',state='disabled',command=lambda:request('Add'))
    add.pack(side='left')
    tk.Button(buttons,text='Remover exceção (UAC)',command=lambda:request('Remove')).pack(side='left',padx=8)
    tk.Button(buttons,text='Fechar',command=window.destroy).pack(side='right')
    window.transient(parent);window.grab_set()
    parent.wait_window(window)
