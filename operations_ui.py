"""Central operacional com saúde, perfis, auditoria e manutenção local."""
from __future__ import annotations
import datetime as dt
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import tkinter as tk
from contextlib import closing
from tkinter import ttk,filedialog,messagebox
from config import STATE_DIR,LOG_DIR
from component_status import identity,miner_version
from gpu_profiles import PROFILES
from mining_health import health
from contribution import atomic_json,read_support_status,read_settings
from rig import write_request,ProcessBackend
from contribution_audit import audit,audit_text

def read_json(path):
    try:
        value=json.loads(Path(path).read_text(encoding='utf-8-sig'))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError): return {}

def stamp(value):
    try:return dt.datetime.fromtimestamp(value).strftime('%d/%m %H:%M:%S') if value else 'não disponível'
    except (ValueError,TypeError,OSError):return 'não disponível'

def trial_rows(directory,identifier,page=0):
    path=Path(directory)/'gpu-agent.sqlite3'
    if not path.is_file():return []
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=2)) as db:
        has_adjustments=db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='adjustments'").fetchone()
        query=('SELECT stamp,state,payload_json FROM trials WHERE gpu_uuid=? UNION ALL SELECT stamp,\'applied\',payload_json FROM adjustments WHERE gpu_uuid=? ORDER BY stamp DESC LIMIT 8 OFFSET ?') if has_adjustments else 'SELECT stamp,state,payload_json FROM trials WHERE gpu_uuid=? ORDER BY stamp DESC LIMIT 8 OFFSET ?'
        rows=db.execute(query,(identifier,identifier,page*8) if has_adjustments else (identifier,page*8)).fetchall()
    return [{'at':at,'state':state,**json.loads(payload)} for at,state,payload in rows]

def show_operations(parent):
    window=tk.Toplevel(parent)
    window.title('HashPilot · Central de operação')
    window.geometry('980x620')
    window.minsize(760,500)
    window.transient(parent)
    window.configure(bg='#101821')
    root=parent._root()
    style=ttk.Style(window)
    style.configure('Ops.TFrame',background='#101821')
    style.configure('Ops.TLabel',background='#101821',foreground='#e7edf3',font=('Segoe UI',10))
    style.configure('Ops.TNotebook',background='#101821')
    style.configure('Ops.TNotebook.Tab',padding=(12,8))
    style.configure('Ops.Treeview',background='#192633',fieldbackground='#192633',foreground='#e7edf3',rowheight=25)
    book=ttk.Notebook(window);book.pack(fill='both',expand=True,padx=12,pady=12)
    pages={name:ttk.Frame(book,padding=12) for name in ('Saúde e versões','Perfis e ajustes','Ajuda de 5%','Backup e diagnóstico')}
    for name,page in pages.items():book.add(page,text=name)
    selected=tk.StringVar();identifiers=[]
    window.selected_gpu_variable=selected
    selector=ttk.Combobox(pages['Saúde e versões'],textvariable=selected,state='readonly')
    selector.pack(fill='x')
    info=ttk.Label(pages['Saúde e versões'],justify='left',anchor='nw',wraplength=880)
    info.pack(fill='both',expand=True,pady=12)
    buttons=ttk.Frame(pages['Saúde e versões']);buttons.pack(fill='x')
    def error_call(action):
        try: action()
        except Exception as error:messagebox.showerror('HashPilot',str(error),parent=window)
    def update_service():
        script=Path(__file__).resolve().parent/'mining/windows/Atualizar-Rig.ps1'
        subprocess.Popen(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script),'-Python',sys.executable],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    ttk.Button(buttons,text='Atualizar painel',command=lambda:__import__('app_update').show_update_setup(window)).pack(side='left')
    ttk.Button(buttons,text='Atualizar serviço (UAC)',command=update_service).pack(side='left',padx=8)
    ttk.Button(buttons,text='Atualizar minerador',command=lambda:__import__('miner_update_ui').show_miner_update(window)).pack(side='left')
    def gpu_id():
        return identifiers[selector.current()] if 0<=selector.current()<len(identifiers) else None
    def reconnect():
        identifier=gpu_id()
        if identifier and messagebox.askyesno('Reconectar GPU','Parar e iniciar somente esta GPU? O orçamento de 3 tentativas/hora será respeitado.',parent=window):
            write_request(STATE_DIR,'restart',identifier)
    ttk.Button(buttons,text='Reconectar esta GPU',command=lambda:error_call(reconnect)).pack(side='right')
    page=pages['Perfis e ajustes']
    ttk.Label(page,text='Aplica à GPU selecionada em Saúde e versões. Cada placa recebe benchmark próprio de 5 min.',wraplength=850).pack(anchor='w')
    chosen=tk.StringVar(value=PROFILES['performance']['label'])
    ttk.Combobox(page,textvariable=chosen,values=[v['label'] for v in PROFILES.values()],state='readonly').pack(fill='x',pady=8)
    def apply_profile():
        identifier=gpu_id()
        if not identifier:raise ValueError('Selecione uma GPU')
        key=next(key for key,value in PROFILES.items() if value['label']==chosen.get())
        write_request(STATE_DIR,'profile',identifier,profile=key)
        messagebox.showinfo('Perfil','Perfil salvo na fila do serviço. A validação será individual; confira o perfil ativo em Saúde e versões.',parent=window)
    ttk.Button(page,text='Aplicar perfil e medir novamente',command=lambda:error_call(apply_profile)).pack(anchor='w')
    ttk.Label(page,text='Equilibrado prioriza eficiência e antecipa alívio térmico em 3 °C; Menos calor em 7 °C. Não promete ruído menor: a ventoinha mantém a política do driver/minerador. Limites do driver e proteção térmica continuam ativos.',wraplength=850).pack(fill='x',pady=8)
    tree=ttk.Treeview(page,columns=('at','state','before','after','hash'),show='headings',height=8)
    for key,label in zip(tree['columns'],('Quando','Resultado','Antes MHz / W / °C','Depois MHz / W / °C','TH/s antes → depois')):
        tree.heading(key,text=label);tree.column(key,width=150,stretch=True)
    tree.pack(fill='both',expand=True)
    detail=ttk.Label(page,text='Selecione um ajuste para ver o motivo.',wraplength=850);detail.pack(fill='x',pady=5)
    pager=ttk.Frame(page);pager.pack(fill='x')
    page_index=[0];last_rows={}
    def change_page(delta):page_index[0]=max(0,page_index[0]+delta);refresh_trials()
    ttk.Button(pager,text='Mais recentes',command=lambda:change_page(-1)).pack(side='left')
    ttk.Button(pager,text='Mais antigos',command=lambda:change_page(1)).pack(side='right')
    def refresh_trials():
        identifier=gpu_id()
        if not identifier:return
        registry=read_json(STATE_DIR/'rig-manager.json');entry=registry.get('devices',{}).get(identifier,{})
        directory=entry.get('log_dir')
        if not directory:return
        try:rows=trial_rows(directory,identifier,page_index[0])
        except (sqlite3.Error,ValueError):rows=[]
        tree.delete(*tree.get_children());last_rows.clear()
        def metrics(sample):return ' / '.join(str(round(sample[k],1)) if isinstance(sample.get(k),(int,float)) else '—' for k in ('clock_mhz','power_w','temperature_c'))
        for index,row in enumerate(rows):
            key=str(index);last_rows[key]=row
            tree.insert('', 'end',iid=key,values=(stamp(row['at']),row['state'],metrics(row.get('before',{})),metrics(row.get('after',{})),f"{row.get('baseline_hashrate_ths','—')} → {row.get('measured_hashrate_ths','—')}"))
    def show_trial(_=None):
        selection=tree.selection()
        if selection:
            row=last_rows.get(selection[0],{})
            detail.configure(text=str(row.get('result_reason') or row.get('interruption_reason') or (row.get('plan') or {}).get('reason') or row.get('state','')))
    tree.bind('<<TreeviewSelect>>',show_trial)
    book.bind('<<NotebookTabChanged>>',lambda _: refresh_trials())
    selector.bind('<<ComboboxSelected>>',lambda _: (page_index.__setitem__(0,0),refresh_trials()))
    support_label=ttk.Label(pages['Ajuda de 5%'],justify='left',anchor='nw',wraplength=850)
    support_label.pack(fill='both',expand=True)
    page=pages['Backup e diagnóstico']
    ttk.Label(page,text='Backups privados incluem carteiras, configurações e históricos SQLite consistentes. Chaves de IA e reservas da contribuição ficam separadas. Não publique o backup.',wraplength=850).pack(fill='x')
    backup_status=ttk.Label(page,text='');backup_status.pack(fill='x',pady=8)
    busy=[False]
    def backup_now():
        if busy[0]:return
        filename=filedialog.asksaveasfilename(parent=window,defaultextension='.zip',initialfile='HashPilot-backup-'+time.strftime('%Y%m%d-%H%M%S')+'.zip')
        if not filename:return
        busy[0]=True;backup_status.configure(text='Criando snapshot consistente…')
        result=[]
        def work():
            try:
                from backup import create_backup
                value=create_backup(filename,STATE_DIR,LOG_DIR)
                result.append(f"Backup concluído: {len(value['files'])} arquivos. Credenciais excluídas.")
            except Exception as error:result.append(str(error))
        threading.Thread(target=work,daemon=True).start()
        def poll():
            if not window.winfo_exists():return
            if result:busy[0]=False;backup_status.configure(text=result[0])
            else:window.after(200,poll)
        poll()
    ttk.Button(page,text='Criar backup',command=backup_now).pack(anchor='w')
    def restore_now():
        if getattr(root,'update_install_busy',False):raise ValueError('Aguarde a atualização terminar')
        if busy[0]:raise ValueError('Aguarde o backup terminar')
        filename=filedialog.askopenfilename(parent=window,filetypes=[('Backup HashPilot','*.zip')])
        if not filename:return
        from backup import validate_backup
        with tempfile.TemporaryDirectory(prefix='hashpilot-preview-') as temporary:
            manifest=validate_backup(filename,temporary)
        if not messagebox.askyesno('Restaurar backup',f"Backup de {stamp(manifest['created_at'])}, {len(manifest['files'])} arquivos.\n\nO painel e o rig serão encerrados para restaurar os históricos. Um backup de segurança será criado antes. Credenciais e pasta local serão preservadas. Depois, confira as carteiras e inicie o rig. Continuar?",parent=window):return
        registry=read_json(STATE_DIR/'rig-manager.json');backend=ProcessBackend()
        manager=backend.inspect(registry.get('pid'))
        children=[entry[role] for entry in registry.get('devices',{}).values() for role in ('miner','controller','proxy') if entry.get(role)]
        if registry.get('ready') and (not manager or not 0<=time.time()-registry.get('sampled_at',0)<=15): raise ValueError('Serviço sem identidade atual; restauração bloqueada')
        if not hasattr(root,'close'):raise ValueError('Abra a central pelo painel principal')
        request={'archive':filename,'state_dir':str(STATE_DIR),'log_dir':str(LOG_DIR),'gui':backend.inspect(__import__('os').getpid()),'manager':manager,'children':children}
        if not request['gui']:raise ValueError('Painel não identificado')
        path=STATE_DIR/'restore-request.json';atomic_json(path,request)
        if manager:write_request(STATE_DIR,'shutdown')
        subprocess.Popen([sys.executable,str(Path(__file__).with_name('restore_helper.py')),'--request',str(path)],creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        root.close()
    ttk.Button(page,text='Validar e restaurar backup…',command=lambda:error_call(restore_now)).pack(anchor='w',pady=8)
    ttk.Separator(page).pack(fill='x',pady=8)
    ttk.Label(page,text='Diagnóstico compartilhável: apenas modelo, driver, sensores e contadores. Revise antes de salvar. Não envia dados pela internet.',wraplength=850).pack(fill='x')
    preview=tk.Text(page,height=8,wrap='word',bg='#192633',fg='#e7edf3',insertbackground='#e7edf3',font=('Consolas',10));preview.pack(fill='both',expand=True,pady=8)
    diagnostic=[{}]
    def prepare_diagnostic():
        from tools.validation_capture import capture
        diagnostic[0]=capture(STATE_DIR)
        diagnostic[0]['app_version']=identity()['version']
        preview.configure(state='normal')
        preview.delete('1.0','end');preview.insert('1.0',json.dumps(diagnostic[0],ensure_ascii=False,indent=2))
        preview.configure(state='disabled')
    def save_diagnostic():
        filename=filedialog.asksaveasfilename(parent=window,defaultextension='.json',initialfile='HashPilot-diagnostico.json')
        if filename:atomic_json(Path(filename),diagnostic[0])
    line=ttk.Frame(page);line.pack(fill='x')
    ttk.Button(line,text='Preparar prévia',command=prepare_diagnostic).pack(side='left')
    ttk.Button(line,text='Salvar diagnóstico revisado',command=lambda:error_call(save_diagnostic)).pack(side='right')
    def refresh():
        if not window.winfo_exists():return
        registry=read_json(STATE_DIR/'rig-manager.json');devices=registry.get('devices',{})
        ids=list(devices)
        if ids!=identifiers:
            identifiers[:]=ids;selector.configure(values=[f"GPU {devices[i].get('index')} · {devices[i].get('name')}" for i in ids])
            if ids:selector.current(0);refresh_trials()
        identifier=gpu_id();entry=devices.get(identifier,{})
        snapshot=read_json(Path(entry.get('log_dir',LOG_DIR))/'latest.json')
        controller=read_json(Path(entry.get('log_dir',LOG_DIR))/'gpu-agent-control.json')
        status=entry.get('health') or health(snapshot.get('miner',{}),snapshot.get('miner_running'))
        recovery=entry.get('recovery') or {}
        software=registry.get('software') or {}
        service_fresh=0<=time.time()-registry.get('sampled_at',0)<=15 and registry.get('ready')
        miner=snapshot.get('miner') or {}
        import re
        version=re.search(r'Miner version:\s*([^\r\n]+)',miner.get('recent_logs',''))
        pending=software.get('version')!=identity()['version']
        reason=entry.get('error') or ('Último registro: '+str(recovery['last_reason']) if recovery.get('last_reason') else 'sem falha registrada')
        info.configure(text=f"Painel: {identity()['version']}\nServiço: {software.get('version','não informada pela versão antiga')} · {'atual' if service_fresh else 'sem heartbeat atual'}{' · atualização pendente' if pending else ''}\nControlador: {(controller.get('software') or {}).get('version','não informada')}\nMinerador: {miner.get('version') or miner_version(entry.get('miner_log')) or (version[1] if version else 'versão não disponível no log')} · pacote selecionado: {__import__('miner_update').selected_version()}\n\n{status['label']}{' · informação antiga' if not service_fresh else ''}\nConexão: {'sim' if status.get('connected') else 'não confirmada'} · GPU calculando: {'sim' if status.get('hashing') else 'não confirmado'}\nÚltima share aceita: {stamp(status.get('last_accepted_at'))}\nAlerta após {status.get('warning_after_seconds',900)/60:.0f} min · {status.get('basis','')}\n\nRecuperações: {recovery.get('attempts_last_hour',0)} / 3 na última hora\nPróxima tentativa permitida: {stamp(recovery.get('next_allowed_at'))}\nMotivo: {reason}\nPerfil ativo: {PROFILES.get(controller.get('profile'),{}).get('label','não informado')}\nCarteira configurada: {entry.get('wallet','não disponível')}\n\nACK confirma uma share aceita pela pool; crédito e pagamento são etapas distintas.")
        support=audit(Path(entry.get('log_dir',LOG_DIR)),entry.get('wallet',''),read_settings(STATE_DIR/'settings.json')) if entry else {}
        shares=support.get('share_responses') or {}
        support_label.configure(text=audit_text(entry,support))
        window.after(2000,refresh)
    def theme(widget):
        for child in widget.winfo_children():
            kind=child.winfo_class()
            mapping={'TFrame':'Ops.TFrame','TLabel':'Ops.TLabel','TNotebook':'Ops.TNotebook','Treeview':'Ops.Treeview'}
            if kind in mapping:child.configure(style=mapping[kind])
            theme(child)
    theme(window)
    refresh()
    prepare_diagnostic()
    return window
