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
from fsutil import durable_text
from monitor_theme import SURFACE, TEXT_TERTIARY
import ui_kit as ui

ROOT = Path(__file__).resolve().parent


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
    durable_text(temporary, json.dumps(data, ensure_ascii=False, indent=2)+'\n')
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


def rig_launch_failure(log_dir, since: float) -> str | None:
    """Última falha que o Iniciar-Rig.ps1 registrou depois de `since`, ou None.

    O script roda elevado e oculto: sem esta leitura, a falha dele não chega ao painel.
    """
    path = Path(log_dir) / 'diagnostics' / 'iniciar-rig.log'
    try:
        if path.stat().st_mtime < since - 1:
            return None
        lines = [line.strip() for line in path.read_text(encoding='utf-8-sig', errors='replace').splitlines() if line.strip()]
    except OSError:
        return None
    if not lines:
        return None
    _stamp, _space, message = lines[-1].partition(' ')
    return (message or lines[-1])[:300]


def confirm_author_wallet(parent, settings, identifiers):
    """Confirm an explicit UI action that sends mining work to the author."""
    from contribution import AUTHOR_WALLET
    overrides = settings.get('gpu_settings', {})
    if not isinstance(overrides, dict):
        raise ValueError('Configuração das GPUs inválida')
    default = settings.get('wallet', DEFAULT_WALLET)
    author_targets = []
    for identifier in identifiers:
        entry = overrides.get(identifier, {})
        if not isinstance(entry, dict):
            raise ValueError('Configuração da GPU inválida')
        if (entry.get('wallet') or default) == AUTHOR_WALLET:
            author_targets.append(identifier)
    if not author_targets:
        return True
    return messagebox.askyesno(
        'Confirmar carteira do autor',
        'A mineração das GPUs abaixo será direcionada à carteira do autor, não à sua: \n\n'
        + '\n'.join(author_targets) + '\n\n' + AUTHOR_WALLET
        + '\n\nOs pagamentos dessas GPUs irão para o autor. Continuar?', parent=parent, default='no')


def confirm_setup_mining(parent, wallet, settings, devices):
    """Review the enabled GPUs the setup will launch with the proposed global wallet."""
    from rig_config import resolve_devices
    planned = dict(settings)
    planned['wallet'] = wallet.strip()
    resolved = resolve_devices(devices, planned, LOG_DIR)
    if not resolved:
        raise ValueError('Nenhuma GPU NVIDIA detectada para verificar as carteiras antes de iniciar.')
    targets = [device['uuid'] for device in resolved if device['enabled']]
    return confirm_author_wallet(parent, planned, targets)

def run_gpu_setup(parent=None,selected_uuid=None,devices=None,path=SETTINGS_PATH,container=None):
    """Até seis carteiras/interruptores, sempre associados ao UUID físico."""
    from ui_host import open_window,present,embedded
    from gpu_agent import detect_nvidia_gpus
    from rig_config import resolve_devices,update_gpu_settings
    settings=read_settings(path)
    if devices is None:
        devices=((getattr(parent,'last_data',None) or {}).get('rig') or {}).get('devices')
    if devices is None: devices=detect_nvidia_gpus(max_devices=6)
    resolved=resolve_devices(devices,settings,LOG_DIR)
    window=open_window(parent,container,'GPUs e carteiras — HashPilot')
    ui.apply_styles(window)
    window.configure(bg=SURFACE)
    width=min(ui.px(window,1100),window.winfo_screenwidth()-60)
    height=min(ui.px(window,650),window.winfo_screenheight()-80)
    window.geometry(f'{width}x{height}')
    window.minsize(min(width,ui.px(window,980)),min(height,ui.px(window,560)))
    window.columnconfigure(0,weight=1)
    margin=ui.px(window,32 if embedded(window) else 24)
    ui.label(window,'Seu rig NVIDIA · até 6 GPUs','h1').grid(row=0,column=0,sticky='ew',padx=margin,pady=(ui.px(window,24 if embedded(window) else 20),ui.px(window,4)))
    wrapped=[]
    intro=ui.label(window,'Todas são ativadas por padrão. Cada placa pode usar a carteira global ou um endereço próprio. Desativar encerra somente o minerador daquela GPU.','body',tone='secondary',wraplength=width-2*margin)
    intro.grid(row=1,column=0,sticky='ew',padx=margin,pady=(0,ui.px(window,12)));wrapped.append((intro,0))
    holder=tk.Frame(window,bg=SURFACE)
    holder.grid(row=2,column=0,sticky='nsew',padx=margin)
    holder.columnconfigure(0,weight=1)
    window.rowconfigure(2,weight=0 if embedded(window) else 1)
    rows=[]
    for index,device in enumerate(resolved):
        card=ui.Card(holder,padding='md')
        card.set_selected(device['uuid']==selected_uuid)
        card.grid(row=index,column=0,sticky='ew',pady=ui.px(window,4))
        card.columnconfigure(2,weight=1)
        enabled=tk.BooleanVar(value=device['enabled'])
        inherit=tk.BooleanVar(value=device['uses_default_wallet'])
        address=tk.StringVar(value=device['wallet'])
        title=device['name'].removeprefix('NVIDIA ').removeprefix('GeForce ')
        title=title if len(title)<=26 else title[:23]+'…'
        ui.check(card,f"GPU {device['index']} · {title}",enabled,bg=card.bg,font=ui.font('label'),width=29).grid(row=0,column=0,sticky='w')
        uuid_label=ui.label(card,device['uuid'],'caption',tone='tertiary',bg=card.bg)
        uuid_label.configure(font=ui.font('caption',mono=True))
        uuid_label.grid(row=1,column=0,columnspan=2,sticky='w')
        inherit_box=ui.check(card,'Carteira global',inherit,bg=card.bg)
        inherit_box.grid(row=0,column=1,padx=ui.px(window,8))
        entry=ui.TextField(card,address,mono=True)
        entry.grid(row=0,column=2,rowspan=2,sticky='ew',ipady=ui.px(window,1))
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
        ui.label(holder,'Nenhuma GPU NVIDIA detectada. Confira o driver e nvidia-smi.','body',tone='secondary').pack(pady=ui.px(window,30))
    support=tk.BooleanVar(value=settings.get('support_enabled') is True)
    ui.check(window,'Ajuda opcional: até 5% do tempo conectado para a carteira do autor',support).grid(row=3,column=0,sticky='ew',padx=margin,pady=(ui.px(window,10),ui.px(window,2)))
    help_note=ui.label(window,'Somente para carteiras diferentes da do autor. Pode ser desativada. Divide tempo conectado, sem garantir 5% das moedas nem retirar saldo. O serviço reinicia GPUs cuja carteira mudou; um rig parado só inicia pelo botão de mineração.','small',tone='secondary',wraplength=width-2*margin)
    help_note.grid(row=4,column=0,sticky='ew',padx=margin,pady=(0,ui.px(window,8)));wrapped.append((help_note,0))
    def fit(event):
        if event.widget is window:
            for item,extra in wrapped:item.configure(wraplength=max(ui.px(window,240),event.width-2*margin-extra))
    if embedded(window):window.bind('<Configure>',fit,add='+')
    saved=False
    def finish():
        nonlocal saved
        try:
            updated=read_settings(path)
            for identifier,enabled,inherit,address in rows:
                updated=update_gpu_settings(updated,identifier,enabled=enabled.get(),wallet_override=None if inherit.get() else address.get().strip(),change_wallet=True)
            updated['support_enabled']=support.get()
            targets=[identifier for identifier,enabled,inherit,address in rows if enabled.get()]
            if not confirm_author_wallet(window,updated,targets): return
            path.parent.mkdir(parents=True,exist_ok=True)
            temporary=path.with_name(path.name+'.gpu-setup.tmp')
            durable_text(temporary,json.dumps(updated,ensure_ascii=False,indent=2)+'\n')
            temporary.replace(path)
            from rig import write_request
            write_request(path.parent,'reconcile')
        except (OSError,ValueError,RuntimeError) as error:
            messagebox.showerror('Configurar GPUs',str(error),parent=window);return
        saved=True;window.destroy()
    if not embedded(window): ui.Separator(window).grid(row=5,column=0,sticky='ew')
    buttons=tk.Frame(window,bg=SURFACE)
    buttons.grid(row=6,column=0,sticky='ew',padx=margin,pady=ui.px(window,16))
    ui.Button(buttons,'Salvar GPUs e carteiras',finish,variant='primary').pack(side='right')
    ui.Button(buttons,'Descartar' if embedded(window) else 'Cancelar',window.destroy,variant='ghost').pack(side='right',padx=(0,ui.px(window,8)))
    if not embedded(window):
        ui.Button(buttons,'Segurança do minerador',lambda:__import__('defender_setup').show_defender_setup(window),variant='ghost').pack(side='left')
        ui.Button(buttons,'Atualizações',lambda:__import__('app_update').show_update_setup(window),variant='ghost').pack(side='left',padx=(ui.px(window,8),0))
    if embedded(window):return window
    # Com seis GPUs a lista não cabe na altura padrão (não há rolagem): a janela cresce até o limite da tela.
    window.update_idletasks()
    needed=min(window.winfo_reqheight(),window.winfo_screenheight()-80)
    if needed>height: window.geometry(f'{width}x{needed}')
    present(parent,window)
    return saved



def run_setup(parent=None,selected_uuid=None) -> bool:
    if selected_uuid is not None: return run_gpu_setup(parent,selected_uuid)
    settings = read_settings()
    window = tk.Toplevel(parent) if parent else tk.Tk()
    window.title('Configurar HashPilot — Tech Fernandes')
    ui.apply_styles(window)
    window.configure(bg=SURFACE)
    width = min(ui.px(window,760), window.winfo_screenwidth()-60)
    height = min(ui.px(window,680), window.winfo_screenheight()-80)
    window.geometry(f'{width}x{height}')
    window.minsize(min(ui.px(window,660),width), min(ui.px(window,500),height))
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
    margin = ui.px(window,24)
    wrap = width-2*margin
    gap = ui.px(window,4)

    def label(text, row, tone='primary', role='body', *, pady=None):
        widget = ui.label(window, text, role, tone=tone, wraplength=wrap)
        widget.grid(row=row,column=0,sticky='ew',padx=margin,pady=pady if pady is not None else gap)
        return widget

    head = tk.Frame(window,bg=SURFACE)
    head.grid(row=0,column=0,sticky='ew',padx=margin,pady=(ui.px(window,20),ui.px(window,4)))
    ui.brand_icon(head,32,bg=SURFACE).pack(side='left',padx=(0,ui.px(window,12)))
    ui.label(head,'Bem-vindo ao HashPilot','h1').pack(side='left')
    label('Acompanhe sua GPU NVIDIA, a mineração e os recebimentos em PRL.',1,'secondary',pady=(0,ui.px(window,8)))
    label('Carteira global · padrão para todas as GPUs sem carteira própria',2,'primary','label',pady=(ui.px(window,8),gap))
    entry = ui.TextField(window,wallet,mono=True)
    entry.grid(row=3,column=0,sticky='ew',padx=margin,ipady=ui.px(window,1))
    label('O endereço preenchido por padrão pertence ao autor. Mantê-lo direciona a mineração iniciada por este assistente para o autor. Você pode substituí-lo pelo seu endereço público. Nunca informe seed ou chave privada.',4,'secondary','small')
    support_frame=ui.Card(window,padding='md')
    support_frame.grid(row=5,column=0,sticky='ew',padx=margin,pady=gap)
    ui.check(support_frame,'Contribuir até 5% do tempo conectado para ajudar pela ferramenta',support,bg=support_frame.bg).pack(anchor='w')
    ui.label(support_frame,'Após 57 min autorizados para sua carteira, até 3 min para o autor. Não retira saldo e não garante 5% das moedas; pausas e shares rejeitadas alteram os créditos. Desmarque para desativar.','small',tone='secondary',bg=support_frame.bg,wraplength=wrap-ui.px(window,36)).pack(fill='x',pady=(gap,ui.px(window,6)))
    target=ui.TextField(support_frame,tk.StringVar(value=DEFAULT_WALLET),mono=True,readonly=True)
    target.configure(fg=TEXT_TERTIARY)
    target.pack(fill='x')
    def show_support(*_):
        if wallet.get().strip() and wallet.get().strip()!=DEFAULT_WALLET:
            support_frame.grid()
        else:
            support_frame.grid_remove()
    wallet.trace_add('write',show_support)
    show_support()
    label('Pasta dos logs do SRBMiner',6,'primary','label',pady=(ui.px(window,8),gap))
    logrow=tk.Frame(window,bg=SURFACE)
    logrow.grid(row=7,column=0,sticky='ew',padx=margin)
    logrow.columnconfigure(0,weight=1)
    ui.TextField(logrow,logs).grid(row=0,column=0,sticky='ew',ipady=ui.px(window,1))
    def browse():
        chosen=filedialog.askdirectory(parent=window,title='Pasta dos logs')
        if chosen: logs.set(chosen)
    ui.Button(logrow,'Escolher',browse,variant='secondary').grid(row=0,column=1,padx=(ui.px(window,8),0))
    check=ui.check(window,'Instalar e iniciar o minerador NVIDIA',mine)
    check.grid(row=8,column=0,sticky='ew',padx=margin,pady=(ui.px(window,12),gap))
    if os.name != 'nt': check.configure(state='disabled')
    label('O hash oficial confirma a origem, não garante segurança. Uma detecção do Defender precisa de análise. Exceção opcional somente com decisão do administrador.',9,'secondary','small')
    links=tk.Frame(window,bg=SURFACE)
    links.grid(row=10,column=0,sticky='ew',padx=margin-ui.px(window,15),pady=gap)
    ui.Button(links,'Segurança do minerador / permissão do Defender',lambda:__import__('defender_setup').show_defender_setup(window),variant='ghost').pack(side='left')
    ui.Button(links,'Atualizações',lambda:__import__('app_update').show_update_setup(window),variant='ghost').pack(side='right')
    ai_row=tk.Frame(window,bg=SURFACE)
    ai_row.grid(row=11,column=0,sticky='ew',padx=(margin,margin-ui.px(window,15)),pady=gap)
    ai_check=ui.check(ai_row,'Ativar sugestões de IA',ai)
    ai_check.pack(side='left')
    def choose_ai():
        __import__('ai_setup').show_ai_setup(window)
        ai.set(read_settings().get('ai_enabled') is True)
    ui.Button(ai_row,'Escolher provedor de IA',choose_ai,variant='ghost').pack(side='right')
    label('Escolha OpenRouter, OpenCode ou Ollama local no assistente de IA. Cloud Free poupa CPU/RAM do rig.',12,'secondary','small')
    window.rowconfigure(13,weight=1)
    ui.Button(window,'Central de operação · saúde, perfis, backups e diagnóstico',lambda:__import__('operations_ui').show_operations(window),variant='ghost').grid(row=13,column=0,sticky='nw',padx=margin-ui.px(window,15),pady=gap)
    def finish():
        nonlocal saved
        try:
            if mine.get():
                from gpu_agent import detect_nvidia_gpus
                if not confirm_setup_mining(window,wallet.get(),read_settings(),detect_nvidia_gpus(max_devices=6)): return
            save_settings(wallet.get(),logs.get(),support_enabled=support.get(),ai_enabled=ai.get())
            if ai.get() and not read_settings().get("ai"):
                __import__("ai_setup").show_ai_setup(window)
            if mine.get(): launch_mining_setup(wallet.get().strip())
        except (ValueError,OSError,RuntimeError) as error:
            messagebox.showerror('Configuração',str(error),parent=window)
            return
        saved=True
        window.destroy()
    ui.Separator(window).grid(row=14,column=0,sticky='ew')
    buttons=tk.Frame(window,bg=SURFACE)
    buttons.grid(row=15,column=0,sticky='ew',padx=margin,pady=ui.px(window,16))
    ui.Button(buttons,'Salvar e abrir painel',finish,variant='primary').pack(side='right')
    ui.Button(buttons,'Cancelar',window.destroy,variant='ghost').pack(side='right',padx=(0,ui.px(window,8)))
    entry.focus_set()
    if parent:
        window.transient(parent)
        window.grab_set()
        parent.wait_window(window)
    else:
        window.mainloop()
    return saved
