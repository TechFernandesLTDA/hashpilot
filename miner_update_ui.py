"""Explicit SRBMiner updates, separate from HashPilot application updates."""
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox
import webbrowser
from config import STATE_DIR
import miner_update as update


def show_miner_update(parent):
    window=tk.Toplevel(parent);window.title('Atualizar minerador · HashPilot')
    window.configure(bg='#0A1628');width=min(760,window.winfo_screenwidth()-60);height=min(560,window.winfo_screenheight()-90)
    window.geometry(f'{width}x{height}');window.columnconfigure(0,weight=1);window.rowconfigure(4,weight=1)
    def label(text,row,color='#B9C7DC',size=14):
        widget=tk.Label(window,text=text,bg='#0A1628',fg=color,font=('Segoe UI',-size),wraplength=width-40,justify='left',anchor='nw')
        widget.grid(row=row,column=0,sticky='ew',padx=20,pady=8);return widget
    label('Atualizações do SRBMiner',0,'#12A98E',22)
    version=label('',1)
    label('Consulta o repositório oficial. A instalação verifica SHA-256 do ZIP e do executável antes de interromper a mineração. Mantém a versão anterior, carteiras, GPUs desativadas e históricos.',2)
    notes=label('Consultando a versão oficial…',3)
    status=label('A ativação reinicia as GPUs gerenciadas. Se não houver hashrate válido em até 3 minutos, o serviço tenta voltar à versão anterior. Não altera o Defender.',4)
    actions=tk.Frame(window,bg='#0A1628');actions.grid(row=5,column=0,sticky='ew',padx=20,pady=16)
    tk.Button(window,text='Revisar bloqueio no Defender…',command=lambda:__import__('defender_setup').show_defender_setup(window)).grid(row=6,column=0,sticky='w',padx=20,pady=(0,12))
    results=queue.Queue();selected=[None];busy=[False];requested=[None];request_at=[0]
    def current():
        registry=update.read(STATE_DIR/'rig-manager.json')
        versions=set()
        for entry in registry.get('devices',{}).values():
            path=entry.get('miner_log')
            if path:
                from component_status import miner_version
                value=miner_version(path)
                if value:versions.add(value)
        chosen=update.selected_version()
        version.configure(text=f"Selecionado: {chosen} · Em execução: {', '.join(sorted(versions)) or 'nenhum confirmado'}")
        return chosen
    def work(function):
        def run():
            try:results.put(('ok',function()))
            except Exception as error:results.put(('error',str(error)[:400]))
        threading.Thread(target=run,daemon=True,name='miner-update').start()
    def check():
        if busy[0]:return
        busy[0]=True;install.configure(state='disabled');refresh.configure(state='disabled')
        value=current();work(lambda:('check',update.latest(value)))
    def activate(target):
        if busy[0] or not target:return
        if not messagebox.askyesno('Atualizar minerador',f'Instalar e ativar SRBMiner {target}?\n\nHaverá uma breve interrupção nas GPUs gerenciadas. As GPUs desativadas continuarão desativadas. Carteiras e históricos permanecem intactos. Nenhuma exceção no Defender será criada.',parent=window):return
        busy[0]=True;install.configure(state='disabled');refresh.configure(state='disabled');rollback.configure(state='disabled')
        status.configure(text='Baixando e verificando o pacote oficial. A mineração atual continua durante esta etapa…')
        def prepare():
            update.stage(target)
            return ('activate',update.request_activation(target,STATE_DIR))
        work(prepare)
    refresh=tk.Button(actions,text='Verificar agora',command=check);refresh.pack(side='left')
    install=tk.Button(actions,text='Atualizar minerador',state='disabled',command=lambda:activate(selected[0]));install.pack(side='left',padx=8)
    def previous():
        record=update.read(update.base_directory()/'miner-install.json');target=record.get('previous_version')
        if target and target!=update.selected_version():activate(target)
        else:messagebox.showinfo('Versão anterior','Não há outra versão anteriormente selecionada.',parent=window)
    rollback=tk.Button(actions,text='Voltar à anterior',command=previous);rollback.pack(side='left')
    tk.Button(actions,text='Notas oficiais',command=lambda:webbrowser.open(update.RELEASES_URL)).pack(side='right')
    def poll():
        if not window.winfo_exists():return
        try:
            kind,payload=results.get_nowait()
            if kind=='error':
                busy[0]=False;status.configure(text=payload);refresh.configure(state='normal');rollback.configure(state='normal')
            elif payload[0]=='check':
                data=payload[1];busy[0]=False;refresh.configure(state='normal')
                selected[0]=data['version'] if data['newer'] and data['installable'] else None
                notes.configure(text=f"Última versão oficial: {data['version']}\n"+('Atualização disponível com hashes fixados. Isso não garante segurança do binário.' if selected[0] else 'Versão nova ainda não catalogada: atualize o HashPilot para habilitar a instalação.' if data['newer'] else 'O minerador selecionado está atualizado.'))
                install.configure(state='normal' if selected[0] else 'disabled')
            else:
                requested[0]=payload[1];request_at[0]=time.time();status.configure(text='Pacote verificado. Aguardando ativação e validação pelo serviço…')
        except queue.Empty:pass
        if requested[0]:
            state=update.read(STATE_DIR/'miner-update.json')
            if state.get('request_id')==requested[0]:
                labels={'validating':'Minerador iniciado; validando hashrate por GPU…','installed':'Atualização validada pelo serviço.','rolled_back':'A versão nova não passou na validação. Versão anterior selecionada; confira a retomada em Saúde.','failed':'Atualização não concluída.'}
                status.configure(text=labels.get(state.get('state'),'Aguardando serviço…')+(' '+state['error'] if state.get('error') else ''))
                if state.get('state') in ('installed','rolled_back','failed'):
                    requested[0]=None;busy[0]=False;refresh.configure(state='normal');rollback.configure(state='normal');current()
                elif time.time()-request_at[0]>240:
                    requested[0]=None;busy[0]=False;refresh.configure(state='normal');rollback.configure(state='normal')
                    status.configure(text='Validação sem conclusão. Confira o serviço em Saúde e versões; o histórico foi preservado.')
            elif time.time()-request_at[0]>150:
                requested[0]=None;busy[0]=False;refresh.configure(state='normal');rollback.configure(state='normal')
                status.configure(text='Serviço não confirmou o pedido. Confira Saúde e versões antes de tentar novamente.')
        window.after(500,poll)
    def close():
        if busy[0]:messagebox.showinfo('Atualização em andamento','Aguarde a conclusão e a validação pelo serviço.',parent=window)
        else:window.destroy()
    window.protocol('WM_DELETE_WINDOW',close);window.transient(parent)
    current();check();window.after(500,poll)
    return window
