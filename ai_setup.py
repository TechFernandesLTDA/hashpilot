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
from contribution import atomic_json

DESCRIPTIONS={
 'openrouter':'Recomendado para rigs com CPU simples. Usa modelos na nuvem: openrouter/free ou :free, confirmados no catálogo com preço zero. Exige conta e chave API. Há cotas e indisponibilidade; sem fallback pago.',
 'opencode':'Modelos Cloud Free pelo cliente oficial OpenCode. Instale o cliente e faça login conforme a documentação. O HashPilot solicita somente uma sugestão JSON, com ferramentas bloqueadas. Modelos gratuitos podem ser promocionais.',
 'ollama':'Inferência local na CPU: sem API paga e sem enviar telemetria à nuvem. Exige CPU/RAM disponíveis. Ollama Cloud possui créditos iniciais e preços por uso; não é oferecido aqui como gratuito contínuo.',
}
MODELS={'openrouter':['openrouter/free'],'opencode':['opencode/glm-5.3-flash-free',
        'opencode/deepseek-v4.1-flash-free','opencode/mimo-v2.6-flash-free'],'ollama':['qwen2.5:0.5b']}


def save_ai(path,enabled,provider,model,consent,key=''):
    try:settings=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError:settings={}
    settings.update(ai_enabled=bool(enabled),ai={'provider':provider,'model':model.strip(),
                    'cloud_consent':bool(consent),'free_only':True})
    config=configuration(settings)
    if config['enabled'] and provider!='ollama' and not config['cloud_consent']:
        raise ValueError('Marque a autorização de telemetria resumida para usar Cloud Free')
    if key.strip():
        if provider!='openrouter':raise ValueError('Esta chave é somente para OpenRouter; OpenCode usa seu próprio login')
        credential(provider,key.strip())
    # Secrets are deliberately absent from the settings file.
    atomic_json(path,settings)
    return settings


def referral(provider):
    path=Path(__file__).parent/'data/ai-referrals.json'
    try:value=json.loads(path.read_text(encoding='utf-8')).get(provider)
    except (OSError,ValueError):return None
    from urllib.parse import urlparse
    if not isinstance(value,str):return None
    parsed=urlparse(value)
    return value if parsed.scheme=='https' and parsed.hostname and not parsed.username and not parsed.password else None


def show_ai_setup(parent=None,path=SETTINGS_PATH):
    try:settings=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    except FileNotFoundError:settings={}
    config=configuration(settings)
    window=tk.Toplevel(parent) if parent else tk.Tk()
    window.title('Assistente de IA · HashPilot')
    bg,panel,fg,muted,teal='#0A1628','#10213B','#FFFFFF','#B9C7DC','#12A98E'
    window.configure(bg=bg)
    width=min(800,window.winfo_screenwidth()-60);height=min(640,window.winfo_screenheight()-100)
    window.geometry(f'{width}x{height}');window.columnconfigure(0,weight=1)
    enabled=tk.BooleanVar(value=config['enabled']);consent=tk.BooleanVar(value=config['cloud_consent'])
    provider=tk.StringVar(value=PROVIDERS[config['provider']][0]);model=tk.StringVar(value=config['model'])
    key=tk.StringVar();description=tk.StringVar();result=tk.StringVar()
    def label(text,row,color=fg):
        item=tk.Label(window,text=text,bg=bg,fg=color,anchor='w',justify='left',wraplength=width-40,font=('Segoe UI',-13))
        item.grid(row=row,column=0,sticky='ew',padx=20,pady=4);return item
    label('IA opcional · escolha onde processar',0,teal).configure(font=('Segoe UI',-23,'bold'))
    tk.Checkbutton(window,text='Ativar sugestões de IA',variable=enabled,bg=bg,fg=fg,selectcolor=panel,
                   activebackground=bg,font=('Segoe UI',-14)).grid(row=1,column=0,sticky='w',padx=16,pady=4)
    choices=ttk.Combobox(window,textvariable=provider,values=[v[0] for v in PROVIDERS.values()],state='readonly')
    choices.grid(row=2,column=0,sticky='ew',padx=20,pady=4)
    tk.Label(window,textvariable=description,bg=panel,fg=fg,anchor='w',justify='left',
             wraplength=width-64,font=('Segoe UI',-13),padx=12,pady=9).grid(row=3,column=0,sticky='ew',padx=20,pady=5)
    label('Modelo (a disponibilidade gratuita é verificada antes da consulta)',4,muted)
    models=ttk.Combobox(window,textvariable=model);models.grid(row=5,column=0,sticky='ew',padx=20,pady=4)
    key_label=label('Chave OpenRouter · deixe vazio para manter a chave salva no cofre do Windows',6,muted)
    entry=tk.Entry(window,textvariable=key,show='●',bg=panel,fg=fg,insertbackground=fg,relief='flat')
    entry.grid(row=7,column=0,sticky='ew',padx=20,ipady=7)
    cloud=tk.Checkbutton(window,text='Autorizo enviar à nuvem somente sensores e resumo do benchmark',variable=consent,
        bg=bg,fg=fg,selectcolor=panel,activebackground=bg,font=('Segoe UI',-13))
    cloud.grid(row=8,column=0,sticky='w',padx=16,pady=7)
    label('Sem carteira, UUID, serial, logs ou chaves nos prompts. Até 40 consultas cloud/dia por rig. Sem IA ou sem conexão, benchmark e proteção térmica continuam ativos.',9,muted)
    links=tk.Frame(window,bg=bg);links.grid(row=10,column=0,sticky='w',padx=20,pady=4)
    def selected():return next(k for k,v in PROVIDERS.items() if v[0]==provider.get())
    tk.Button(links,text='Conta / login / documentação oficial',command=lambda:webbrowser.open(PROVIDERS[selected()][1])).pack(side='left')
    invite=tk.Button(links,text='Indicação do autor · opcional',command=lambda:webbrowser.open(referral(selected())))
    install=tk.Button(links,text='Instalar Ollama local',command=lambda:__import__('onboarding').launch_ai_setup())
    status=label('',11,teal);status.configure(textvariable=result)
    window.rowconfigure(11,weight=1)
    busy=False;responses=queue.Queue()
    def change(event=None):
        p=selected();description.set(DESCRIPTIONS[p]);models.configure(values=MODELS[p])
        if event is not None:model.set(MODELS[p][0]);key.set('');consent.set(False)
        entry.configure(state='normal' if p=='openrouter' else 'disabled')
        if p=='openrouter':key_label.grid();entry.grid()
        else:key_label.grid_remove();entry.grid_remove()
        cloud.configure(state='disabled' if p=='ollama' else 'normal')
        invite.pack_forget();install.pack_forget()
        if referral(p):invite.pack(side='left',padx=5)
        if p=='ollama':install.pack(side='left',padx=5)
    choices.bind('<<ComboboxSelected>>',change);change()
    buttons=tk.Frame(window,bg=bg);buttons.grid(row=12,column=0,sticky='e',padx=20,pady=12)
    def save(close=True):
        try:save_ai(path,enabled.get(),selected(),model.get(),consent.get(),key.get());key.set('')
        except (ValueError,OSError) as error:
            messagebox.showerror('Configurar IA',str(error),parent=window);return False
        if close:window.destroy()
        return True
    def test():
        nonlocal busy
        if busy or not save(False):return
        busy=True;result.set('Consultando em segundo plano…')
        def work():
            try:
                answer=RoutedAdvisor(Path(path).parent,path).advise({'sample':{},'benchmark':{}})
                responses.put('Conexão validada · '+answer['reason'])
            except (OSError,ValueError,KeyError,TypeError,RuntimeError):
                responses.put('Consulta indisponível. Confira ativação, consentimento, chave/login e cota gratuita.')
        threading.Thread(target=work,daemon=True).start()
    def poll():
        nonlocal busy
        if not window.winfo_exists():return
        try:result.set(responses.get_nowait());busy=False
        except queue.Empty:pass
        window.after(200,poll)
    tk.Button(buttons,text='Testar conexão',command=test).pack(side='left',padx=4)
    tk.Button(buttons,text='Cancelar',command=window.destroy).pack(side='left',padx=4)
    tk.Button(buttons,text='Salvar IA',command=save,bg=teal,fg=fg,relief='flat',padx=16,pady=8).pack(side='left',padx=4)
    poll()
    if parent:window.transient(parent);window.grab_set();parent.wait_window(window)
    else:window.mainloop()
