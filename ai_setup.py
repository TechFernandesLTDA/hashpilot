"""Provider setup, explicit cloud opt-in and Windows-protected credentials."""
from __future__ import annotations
import json
from pathlib import Path
import queue
import threading
import tkinter as tk
from tkinter import ttk, messagebox
import webbrowser
from ai_advisor import PROVIDERS, configuration, credential, RoutedAdvisor
from config import SETTINGS_PATH
from fsutil import atomic_json
from monitor_theme import SURFACE
import ui_kit as ui

DESCRIPTIONS={
 'openrouter':'Recomendado para rigs com CPU simples. Usa modelos na nuvem: openrouter/free ou :free, confirmados no catálogo com preço zero. Exige conta e chave API. Há cotas e indisponibilidade; sem fallback pago.',
 'opencode':'Modelos Cloud Free pelo cliente oficial OpenCode. Instale o cliente e faça login conforme a documentação. O HashPilot solicita somente uma sugestão JSON, com ferramentas bloqueadas. Modelos gratuitos podem ser promocionais.',
 'ollama':'Inferência local na CPU: sem API paga e sem enviar telemetria à nuvem. Exige CPU/RAM disponíveis. Ollama Cloud possui créditos iniciais e preços por uso; não é oferecido aqui como gratuito contínuo.',
}
MODELS={'openrouter':['openrouter/free'],'opencode':['opencode/glm-5.3-flash-free',
        'opencode/deepseek-v4.1-flash-free','opencode/mimo-v2.6-flash-free'],'ollama':['qwen2.5:0.5b']}


def prepare_ai(settings,enabled,provider,model,consent):
    if not isinstance(settings,dict):raise ValueError('Configuração precisa ser um objeto')
    if not isinstance(enabled,bool) or not isinstance(consent,bool):raise ValueError('Ativação e consentimento precisam ser booleanos')
    if not isinstance(model,str):raise ValueError('Modelo precisa ser texto')
    settings=dict(settings)
    settings.update(ai_enabled=bool(enabled),ai={'provider':provider,'model':model.strip(),
                    'cloud_consent':bool(consent),'free_only':True})
    config=configuration(settings)
    if config['enabled'] and provider!='ollama' and not config['cloud_consent']:
        raise ValueError('Marque a autorização de telemetria resumida para usar Cloud Free')
    return settings


def save_ai(path,enabled,provider,model,consent,key=''):
    try:settings=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError:settings={}
    settings=prepare_ai(settings,enabled,provider,model,consent)
    if key.strip():
        if provider!='openrouter':raise ValueError('Esta chave é somente para OpenRouter; OpenCode usa seu próprio login')
        credential(provider,key.strip())
    # Secrets are deliberately absent from the settings file.
    atomic_json(path,settings,durable=True)
    return settings


def referral(provider):
    path=Path(__file__).parent/'data/ai-referrals.json'
    try:value=json.loads(path.read_text(encoding='utf-8')).get(provider)
    except (OSError,ValueError):return None
    from urllib.parse import urlparse
    if not isinstance(value,str):return None
    parsed=urlparse(value)
    return value if not any(ord(c)<33 for c in value) and parsed.scheme=='https' and parsed.hostname and not parsed.username and not parsed.password else None


def show_ai_setup(parent=None,path=SETTINGS_PATH,container=None):
    from ui_host import open_window,present,embedded
    try:settings=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError:settings={}
    config=configuration(settings)
    window=open_window(parent,container,'Assistente de IA · HashPilot')
    ui.apply_styles(window)
    window.configure(bg=SURFACE)
    width=min(ui.px(window,800),window.winfo_screenwidth()-60);height=min(ui.px(window,680),window.winfo_screenheight()-80)
    window.geometry(f'{width}x{height}');window.columnconfigure(0,weight=1)
    margin=ui.px(window,32 if embedded(window) else 24);gap=ui.px(window,4)
    enabled=tk.BooleanVar(value=config['enabled']);consent=tk.BooleanVar(value=config['cloud_consent'])
    provider=tk.StringVar(value=PROVIDERS[config['provider']][0]);model=tk.StringVar(value=config['model'])
    key=tk.StringVar();description=tk.StringVar();result=tk.StringVar()
    wrapped=[]
    def label(text,row,tone='secondary',role='small'):
        item=ui.label(window,text,role,tone=tone,wraplength=width-2*margin)
        item.grid(row=row,column=0,sticky='ew',padx=margin,pady=gap);wrapped.append((item,0));return item
    label('IA opcional · escolha onde processar',0,'primary','h1').grid_configure(pady=(ui.px(window,24 if embedded(window) else 16),gap))
    ui.check(window,'Ativar sugestões de IA',enabled).grid(row=1,column=0,sticky='w',padx=margin,pady=gap)
    choices=ttk.Combobox(window,textvariable=provider,values=[v[0] for v in PROVIDERS.values()],state='readonly')
    choices.grid(row=2,column=0,sticky='ew',padx=margin,pady=gap)
    card=ui.Card(window,padding='md')
    card.grid(row=3,column=0,sticky='ew',padx=margin,pady=gap)
    card_text=ui.label(card,'','body',tone='secondary',bg=card.bg,textvariable=description,wraplength=width-2*margin-ui.px(window,36))
    card_text.pack(fill='x');wrapped.append((card_text,ui.px(window,36)))
    def fit(event):
        if event.widget is window:
            for item,extra in wrapped:item.configure(wraplength=max(ui.px(window,240),event.width-2*margin-extra))
    if embedded(window):window.bind('<Configure>',fit,add='+')
    label('Modelo (a disponibilidade gratuita é verificada antes da consulta)',4)
    models=ttk.Combobox(window,textvariable=model);models.grid(row=5,column=0,sticky='ew',padx=margin,pady=gap)
    key_label=label('Chave OpenRouter · deixe vazio para manter a chave salva no cofre do Windows',6)
    entry=ui.TextField(window,key,show='●')
    entry.grid(row=7,column=0,sticky='ew',padx=margin,ipady=ui.px(window,1))
    cloud=ui.check(window,'Autorizo enviar à nuvem somente sensores e resumo do benchmark',consent)
    cloud.grid(row=8,column=0,sticky='w',padx=margin,pady=gap)
    label('Sem carteira, UUID, serial, logs ou chaves nos prompts. Até 40 consultas cloud/dia por rig. Sem IA ou sem conexão, benchmark e proteção térmica continuam ativos.',9)
    links=tk.Frame(window,bg=SURFACE);links.grid(row=10,column=0,sticky='w',padx=margin-ui.px(window,15),pady=gap)
    def selected():return next(k for k,v in PROVIDERS.items() if v[0]==provider.get())
    ui.Button(links,'Conta / login / documentação oficial',lambda:webbrowser.open(PROVIDERS[selected()][1]),variant='ghost').pack(side='left')
    invite=ui.Button(links,'Indicação do autor · opcional',lambda:webbrowser.open(referral(selected())),variant='ghost')
    install=ui.Button(links,'Instalar Ollama local',lambda:__import__('onboarding').launch_ai_setup(),variant='secondary')
    status=label('',11,'accent','body');status.configure(textvariable=result,anchor='nw');status.grid_configure(sticky='new')
    def tint(*_):
        text=result.get()
        status.configure(fg=ui.TONES['secondary'] if not text or text.startswith('Consultando') else ui.TONES['accent'] if text.startswith('Conexão validada') else ui.TONES['warning'])
    result.trace_add('write',tint)
    window.rowconfigure(11,weight=0 if embedded(window) else 1)
    busy=False;responses=queue.Queue();revision=0;timer=None
    def change(event=None):
        nonlocal revision
        p=selected();description.set(DESCRIPTIONS[p]);models.configure(values=MODELS[p])
        if event is not None:
            revision+=1;result.set('');model.set(MODELS[p][0]);key.set('');consent.set(False)
        entry.configure(state='normal' if p=='openrouter' else 'disabled')
        if p=='openrouter':key_label.grid();entry.grid()
        else:key_label.grid_remove();entry.grid_remove()
        cloud.configure(state='disabled' if p=='ollama' else 'normal')
        invite.pack_forget();install.pack_forget()
        if referral(p):invite.pack(side='left',padx=ui.px(window,8))
        if p=='ollama':install.pack(side='left',padx=ui.px(window,8))
    choices.bind('<<ComboboxSelected>>',change);change()
    if not embedded(window):ui.Separator(window).grid(row=12,column=0,sticky='ew')
    buttons=tk.Frame(window,bg=SURFACE);buttons.grid(row=13,column=0,sticky='ew',padx=margin,pady=ui.px(window,12))
    def save(close=True):
        try:save_ai(path,enabled.get(),selected(),model.get(),consent.get(),key.get());key.set('')
        except (ValueError,OSError) as error:
            messagebox.showerror('Configurar IA',str(error),parent=window);return False
        if close:window.destroy()
        return True
    def test():
        nonlocal busy
        if busy:return
        try:
            proposed=prepare_ai(settings,enabled.get(),selected(),model.get(),consent.get())
            if not proposed['ai_enabled']:raise ValueError('Ative as sugestões de IA para testar a conexão')
        except ValueError as error:
            result.set(str(error));return
        test_key=key.get().strip() or None
        test_revision=revision
        busy=True;result.set('Consultando em segundo plano…')
        def work():
            try:
                answer=RoutedAdvisor(Path(path).parent,path,config_override=proposed,key_override=test_key).advise({'sample':{},'benchmark':{}})
                responses.put((test_revision,'Conexão validada · '+answer['reason']))
            except ValueError as error:responses.put((test_revision,str(error)))
            except (OSError,KeyError,TypeError,RuntimeError):
                responses.put((test_revision,'Consulta indisponível. Confira chave/login e cota gratuita.'))
        threading.Thread(target=work,daemon=True).start()
    def poll():
        nonlocal busy,timer
        if not window.winfo_exists():return
        try:
            response_revision,message=responses.get_nowait();busy=False
            if response_revision==revision:result.set(message)
        except queue.Empty:pass
        timer=window.after(200,poll)
    def cleanup(event):
        if event.widget is window and timer is not None:
            try:window.after_cancel(timer)
            except tk.TclError:pass
    window.bind('<Destroy>',cleanup,add='+')
    ui.Button(buttons,'Testar conexão',test,variant='secondary').pack(side='left')
    ui.Button(buttons,'Salvar IA',save,variant='primary').pack(side='right')
    ui.Button(buttons,'Descartar' if embedded(window) else 'Cancelar',window.destroy,variant='ghost').pack(side='right',padx=(0,ui.px(window,8)))
    poll()
    present(parent,window)
    return window

