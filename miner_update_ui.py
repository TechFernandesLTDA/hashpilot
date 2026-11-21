"""Explicit SRBMiner updates, separate from HashPilot application updates."""
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox
import webbrowser
from config import STATE_DIR
import miner_update as update
from monitor_theme import SURFACE
import ui_kit as ui


def show_miner_update(parent,container=None):
    from ui_host import open_window
    window=open_window(parent,container,'Atualizar minerador · HashPilot')

    ui.apply_styles(window);window.configure(bg=SURFACE);width=min(ui.px(window,760),window.winfo_screenwidth()-60);height=min(ui.px(window,600),window.winfo_screenheight()-90)
    margin=ui.px(window,32 if getattr(window,'embedded',False) else 24)
    window.geometry(f'{width}x{height}');window.columnconfigure(0,weight=1);window.rowconfigure(4,weight=0 if getattr(window,'embedded',False) else 1)
    wrapped=[]
    def label(text,row,tone='secondary',role='body'):
        widget=ui.label(window,text,role,tone=tone,wraplength=width-2*margin,anchor='nw')
        widget.grid(row=row,column=0,sticky='ew',padx=margin,pady=ui.px(window,6));wrapped.append(widget);return widget
    label('Atualizações do SRBMiner',0,'primary','h2' if getattr(window,'embedded',False) else 'h1').grid_configure(pady=(ui.px(window,20),ui.px(window,6)))
    version=label('',1)
    label('Consulta o repositório oficial. A instalação verifica SHA-256 do ZIP e do executável antes de interromper a mineração. Mantém a versão anterior, carteiras, GPUs desativadas e históricos.',2)
    notes=label('Consultando a versão oficial…',3)
    status=label('A ativação reinicia as GPUs gerenciadas. Se não houver hashrate válido em até 3 minutos, o serviço tenta voltar à versão anterior. Não altera o Defender.',4,'primary')
    status.grid_configure(sticky='new')
    def say(text,tone='primary'):status.configure(text=text,fg=ui.TONES[tone])
    if getattr(window,'embedded',False):ui.Button(window,'Revisar bloqueio no Defender →',lambda:window.navigate('Segurança'),variant='ghost').grid(row=5,column=0,sticky='w',padx=margin-ui.px(window,15),pady=(0,ui.px(window,12)))
    else:ui.Button(window,'Revisar bloqueio no Defender…',lambda:__import__('defender_setup').show_defender_setup(window),variant='ghost').grid(row=5,column=0,sticky='w',padx=margin-ui.px(window,15),pady=(0,ui.px(window,12)))
    def fit(event):
        if event.widget is window:
            for item in wrapped:item.configure(wraplength=max(ui.px(window,240),event.width-2*margin))
    if getattr(window,'embedded',False):window.bind('<Configure>',fit,add='+')
    if not getattr(window,'embedded',False):ui.Separator(window).grid(row=6,column=0,sticky='ew')
    actions=tk.Frame(window,bg=SURFACE);actions.grid(row=7,column=0,sticky='ew',padx=margin,pady=ui.px(window,16))

    results=queue.Queue();selected=[None];busy=[False];activating=[False];requested=[None];request_at=[0.0]
    def current():
        versions=set()
        try:
            registry=update.read(STATE_DIR/'rig-manager.json')
            devices=registry.get('devices',{})
            for entry in (devices.values() if isinstance(devices,dict) else ()):
                path=entry.get('miner_log') if isinstance(entry,dict) else None
                if path:
                    from component_status import miner_version
                    value=miner_version(path)
                    if value:versions.add(value)
        except (OSError,ValueError):pass
        try:chosen=update.selected_version()
        except (OSError,ValueError):chosen=update.DEFAULT_VERSION
        version.configure(text=f"Selecionado: {chosen} · Em execução: {', '.join(sorted(versions)) or 'nenhum confirmado'}")
        return chosen
    def idle():
        busy[0]=False;activating[0]=False;refresh.configure(state='normal');rollback.configure(state='normal')
    def work(function):
        def run():
            try:results.put(('ok',function()))
            except Exception as error:results.put(('error',str(error)[:400]))
        threading.Thread(target=run,daemon=True,name='miner-update').start()
    def check():
        if busy[0]:return
        busy[0]=True;install.configure(state='disabled');refresh.configure(state='disabled')
        try:value=current()
        except Exception as error:
            idle();notes.configure(text=f'Não foi possível ler a versão local: {str(error)[:200]}');return
        work(lambda:('check',update.latest(value)))
    def activate(target):
        if busy[0] or not target:return
        if not messagebox.askyesno('Atualizar minerador',f'Instalar e ativar SRBMiner {target}?\n\nHaverá uma breve interrupção nas GPUs gerenciadas. As GPUs desativadas continuarão desativadas. Carteiras e históricos permanecem intactos. Nenhuma exceção no Defender será criada.',parent=window):return
        busy[0]=True;activating[0]=True;install.configure(state='disabled');refresh.configure(state='disabled');rollback.configure(state='disabled')
        say('Baixando e verificando o pacote oficial. A mineração atual continua durante esta etapa…')
        def prepare():
            update.stage(target)
            return ('activate',update.request_activation(target,STATE_DIR))
        work(prepare)
    refresh=ui.Button(actions,'Verificar agora',check,variant='secondary');refresh.pack(side='left')
    install=ui.Button(actions,'Atualizar minerador',lambda:activate(selected[0]),variant='primary',state='disabled');install.pack(side='left',padx=ui.px(window,8))
    def previous():
        record=update.read(update.base_directory()/'miner-install.json');target=record.get('previous_version')
        if target and target!=update.selected_version():activate(target)
        else:messagebox.showinfo('Versão anterior','Não há outra versão anteriormente selecionada.',parent=window)
    rollback=ui.Button(actions,'Voltar à anterior',previous,variant='secondary');rollback.pack(side='left')
    ui.Button(actions,'Notas oficiais',lambda:webbrowser.open(update.RELEASES_URL),variant='ghost').pack(side='right')
    def poll():
        if not window.winfo_exists():return
        try:step()
        except Exception as error:
            # Um estado inesperado não pode deixar a janela presa como "ocupada".
            requested[0]=None;idle();say(f'Falha ao acompanhar a atualização: {str(error)[:200]}','warning')
        finally:
            if window.winfo_exists():window.after(500,poll)
    def step():
        try:
            kind,payload=results.get_nowait()
            if kind=='error':
                idle();say(payload,'warning')
            elif payload[0]=='check':
                data=payload[1];busy[0]=False;refresh.configure(state='normal')
                selected[0]=data['version'] if data['newer'] and data['installable'] else None
                notes.configure(text=f"Última versão oficial: {data['version']}\n"+('Atualização disponível com hashes fixados. Isso não garante segurança do binário.' if selected[0] else 'Versão nova ainda não catalogada: atualize o HashPilot para habilitar a instalação.' if data['newer'] else 'O minerador selecionado está atualizado.'))
                install.configure(state='normal' if selected[0] else 'disabled')
            else:
                requested[0]=payload[1];request_at[0]=time.time();say('Pacote verificado. Aguardando ativação e validação pelo serviço…')
        except queue.Empty:pass
        if requested[0]:
            state=update.read(STATE_DIR/'miner-update.json')
            if state.get('request_id')==requested[0]:
                labels={'validating':'Minerador iniciado; validando hashrate por GPU…','installed':'Atualização validada pelo serviço.','rolled_back':'A versão nova não passou na validação. Versão anterior selecionada; confira a retomada em Saúde.','failed':'Atualização não concluída.'}
                say(labels.get(state.get('state'),'Aguardando serviço…')+(' '+state['error'] if state.get('error') else ''),'accent' if state.get('state')=='installed' else 'warning' if state.get('state') in ('rolled_back','failed') else 'primary')
                if state.get('state') in ('installed','rolled_back','failed'):
                    requested[0]=None;idle();current()
                elif time.time()-request_at[0]>240:
                    requested[0]=None;idle()
                    say('Validação sem conclusão. Confira o serviço em Saúde e versões; o histórico foi preservado.','warning')
            elif time.time()-request_at[0]>150:
                requested[0]=None;idle()
                say('Serviço não confirmou o pedido. Confira Saúde e versões antes de tentar novamente.','warning')
    def close():
        # Só a ativação (download + troca do minerador) impede fechar; consultar a versão não.
        if activating[0]:messagebox.showinfo('Atualização em andamento','Aguarde a conclusão e a validação pelo serviço.',parent=window)
        else:window.destroy()
    window.protocol('WM_DELETE_WINDOW',close);window.transient(parent)
    current();check();window.after(500,poll)
    return window
