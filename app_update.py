"""Atualização opt-in da interface, sem alterar dados ou o serviço de mineração."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox
from urllib.request import Request, urlopen
import webbrowser

from app_version import VERSION, RELEASE_CHANNEL

ROOT=Path(__file__).resolve().parent
REPOSITORY='TechFernandesLTDA/hashpilot'
API_URL=f'https://api.github.com/repos/{REPOSITORY}/releases?per_page=100'
RELEASES_URL=f'https://github.com/{REPOSITORY}/releases'
CHECK_INTERVAL=6*60*60


def version_tuple(value):
    if not isinstance(value,str) or not re.fullmatch(r'v\d{1,5}\.\d{1,5}\.\d{1,5}',value):
        raise ValueError('Versão de release inválida.')
    return tuple(map(int,value[1:].split('.')))


def current_version(root=ROOT):
    manifest=Path(root)/'RELEASE-MANIFEST.json'
    if manifest.exists():
        data=json.loads(manifest.read_text(encoding='utf-8-sig'))
        if not isinstance(data,dict) or data.get('schema')!=1 or not re.fullmatch('[a-f0-9]{40}',str(data.get('commit',''))):
            raise ValueError('Manifesto da instalação inválido.')
        version_tuple(data.get('version'))
        return data['version'],'manifesto da instalação'
    version_tuple(VERSION)
    return VERSION,'código-fonte / desenvolvimento'


def build_identity(root=ROOT):
    """Identify the code actually loaded, never the activated shortcut target."""
    try:
        version,source=current_version(root)
        manifest=Path(root)/'RELEASE-MANIFEST.json'
        revision=json.loads(manifest.read_text(encoding='utf-8-sig'))['commit'] if manifest.exists() else None
        return {'version':version,'revision':revision,'source':source}
    except (OSError,ValueError,KeyError,TypeError):
        return {'version':'não verificada','revision':None,'source':'manifesto inválido ou indisponível'}


def state_directory():
    return Path(os.environ['LOCALAPPDATA'])/'PearlMonitor'


def read_preferences(state=None):
    try:
        data=json.loads(((state or state_directory())/'update-preferences.json').read_text(encoding='utf-8-sig'))
        return {'include_prereleases':data.get('include_prereleases',RELEASE_CHANNEL=='prerelease') is True}
    except FileNotFoundError:return {'include_prereleases':RELEASE_CHANNEL=='prerelease'}
    except (OSError,ValueError,AttributeError):return {'include_prereleases':False}


def save_preferences(include_prereleases,state=None):
    folder=state or state_directory();folder.mkdir(parents=True,exist_ok=True)
    path=folder/'update-preferences.json'
    temporary=path.with_suffix('.tmp')
    temporary.write_text(json.dumps({'include_prereleases':include_prereleases is True}),encoding='utf-8')
    temporary.replace(path)


def select_release(payload,current,include_prereleases=False):
    installed=version_tuple(current)
    if not isinstance(payload,list):raise ValueError('Resposta de releases inválida.')
    candidates=[]
    for release in payload[:100]:
        if not isinstance(release,dict) or release.get('draft') is not False:continue
        if release.get('prerelease') is not False and not (include_prereleases and release.get('prerelease') is True):continue
        try:version=version_tuple(release.get('tag_name'))
        except ValueError:continue
        if version<=installed:continue
        tag=release['tag_name'];base=f'https://github.com/{REPOSITORY}/releases/download/{tag}/'
        assets=release.get('assets')
        if not isinstance(assets,list):continue
        required=(f'hashpilot-{tag}.zip','SHA256SUMS')
        if not all(sum(1 for asset in assets if isinstance(asset,dict) and asset.get('name')==name and
                       asset.get('state')=='uploaded' and asset.get('browser_download_url')==base+name)==1 for name in required):continue
        candidates.append((version,{'version':tag,'prerelease':release['prerelease'],
            'url':f'https://github.com/{REPOSITORY}/releases/tag/{tag}'}))
    return max(candidates,key=lambda item:item[0])[1] if candidates else None


def check_release(current,include_prereleases=False,opener=urlopen):
    request=Request(API_URL,headers={'Accept':'application/vnd.github+json','User-Agent':'HashPilot-update-check',
        'X-GitHub-Api-Version':'2022-11-28'})
    with opener(request,timeout=12) as response:
        content=response.read(2*1024*1024+1)
    if len(content)>2*1024*1024:raise ValueError('Resposta de releases excede o limite.')
    return select_release(json.loads(content),current,include_prereleases)


class UpdateChecker:
    def __init__(self,state=None):
        self.state=state or state_directory()
        self.results=queue.Queue();self.busy=False;self.next_check=0;self.offered=set()
    def start(self,force=False):
        if self.busy or (not force and time.monotonic()<self.next_check):return False
        self.busy=True;self.next_check=time.monotonic()+CHECK_INTERVAL
        def work():
            channel=read_preferences(self.state)['include_prereleases']
            try:
                version,_=current_version()
                result={'release':check_release(version,channel),'error':None}
            except (OSError,ValueError,TypeError,RuntimeError) as error:result={'release':None,'error':str(error)[:200]}
            finally:self.busy=False
            result['include_prereleases']=channel
            self.results.put(result)
        threading.Thread(target=work,daemon=True,name='release-check').start()
        return True


def installation_root(root=ROOT):
    root=Path(root).resolve()
    if (root/'RELEASE-MANIFEST.json').is_file() and root.parent.name=='versions':return root.parent.parent
    return Path(os.environ['LOCALAPPDATA'])/'Programs/HashPilot'


@contextmanager
def installation_lock(state):
    import msvcrt
    with (state/'update-install.lock').open('a+b') as handle:
        if handle.tell()==0:handle.write(b' ');handle.flush()
        handle.seek(0)
        try:msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
        except OSError as error:raise RuntimeError('Outra atualização já está em andamento.') from error
        try:yield
        finally:handle.seek(0);msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)


def install_release(release,install_root=None,runner=subprocess.run):
    version_tuple(release)
    destination=Path(install_root or installation_root())
    script=ROOT/'install.ps1'
    if not script.is_file():raise RuntimeError('Instalador local ausente. Use a página oficial de releases.')
    state=state_directory();state.mkdir(parents=True,exist_ok=True)
    with installation_lock(state),(state/'update-install.log').open('ab') as log:
        result=runner(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script),
            '-Release',release,'-InstallRoot',str(destination),'-NoLaunch'],cwd=ROOT,
            stdin=subprocess.DEVNULL,stdout=log,stderr=log,timeout=600,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise RuntimeError('Atualização não concluída. Consulte update-install.log; a interface atual continua aberta.')
    metadata=json.loads((destination/'installation.json').read_text(encoding='utf-8-sig'))
    if not isinstance(metadata,dict) or not isinstance(metadata.get('app'),str) or not metadata['app']:
        raise ValueError('Metadados da instalação inválidos; a interface atual continua aberta.')
    app=Path(metadata['app']).resolve();versions=(destination/'versions').resolve()
    if versions not in app.parents or metadata.get('release')!=release:raise ValueError('Ativação de versão não confirmada.')
    version,_=current_version(app)
    if version!=release:raise ValueError('Manifesto da versão instalada diverge da solicitação.')
    return app


def verify_monitor(app):
    app=Path(app).resolve()
    manifest=json.loads((app/'RELEASE-MANIFEST.json').read_text(encoding='utf-8-sig'))
    current_version(app)
    expected=manifest['files']['monitor.py']['sha256']
    if hashlib.sha256((app/'monitor.py').read_bytes()).hexdigest()!=expected:raise ValueError('Interface instalada diverge do manifesto.')
    return app/'monitor.py'


def wait_for_parent(pid,timeout=60):
    if os.name!='nt':raise RuntimeError('A reabertura requer Windows.')
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.OpenProcess(0x100000|0x1000,False,pid)
    if not handle:
        if ctypes.get_last_error()==87:return
        raise RuntimeError('Não foi possível confirmar o encerramento da interface anterior.')
    try:
        if kernel.WaitForSingleObject(handle,int(timeout*1000))!=0:raise RuntimeError('A interface anterior ainda está aberta. Abra o novo atalho após sair normalmente.')
    finally:kernel.CloseHandle(handle)


def reopen_after_close(parent,app):
    verify_monitor(app)
    pythonw=Path(sys.executable).with_name('pythonw.exe')
    subprocess.Popen([str(pythonw),str(Path(__file__).resolve()),'--reopen',str(app),'--wait-pid',str(os.getpid())],
        cwd=ROOT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    parent.restart_requested=False
    parent.close()


def show_update_setup(parent):
    window=tk.Toplevel(parent);window.title('Atualizações — HashPilot');window.configure(bg='#0A1628')
    width=min(720,window.winfo_screenwidth()-60);height=min(460,window.winfo_screenheight()-80)
    window.geometry(f'{width}x{height}');window.minsize(width,height);window.columnconfigure(0,weight=1);window.rowconfigure(4,weight=1)
    def label(text,row,color='#B9C7DC',size=14):
        widget=tk.Label(window,text=text,bg='#0A1628',fg=color,font=('Segoe UI',-size),wraplength=width-40,justify='left',anchor='w')
        widget.grid(row=row,column=0,sticky='ew',padx=20,pady=8);return widget
    label('Atualizar a interface preservando seus dados',0,'#12A98E',22)
    try:version,source=current_version();version_text=f'Em execução: {version} · {source}'
    except (OSError,ValueError) as error:version_text=str(error)
    label(version_text+'\nCanal desta versão: '+('pré-lançamento' if RELEASE_CHANNEL=='prerelease' else 'estável'),1)
    label('Carteiras, preferências, bancos e logs permanecem nos diretórios atuais. A atualização baixa código verificado e mantém a versão anterior. O serviço e os mineradores não são reiniciados; continuam com seu código atual. Voltar à pasta anterior não desfaz migrações de banco.',2)
    checker=UpdateChecker();channel=tk.BooleanVar(value=read_preferences()['include_prereleases'])
    def channel_changed():
        try:save_preferences(channel.get());checker.next_check=0;check()
        except OSError as error:status.configure(text=str(error))
    tk.Checkbutton(window,text='Incluir pré-lançamentos (versões experimentais)',variable=channel,command=channel_changed,
        bg='#0A1628',fg='white',selectcolor='#10213B',activebackground='#0A1628',activeforeground='white',font=('Segoe UI',-14)).grid(row=3,column=0,sticky='w',padx=20)
    status=label('Consultando releases oficiais…',4)
    actions=tk.Frame(window,bg='#0A1628');actions.grid(row=5,column=0,sticky='ew',padx=20,pady=16)
    selected=None;installed=None;result_queue=queue.Queue();installing=False
    def check():
        nonlocal selected
        if installing:return
        selected=None;install.configure(state='disabled');status.configure(text='Consultando releases oficiais…')
        checker.start(force=True)
    def update():
        nonlocal installing
        if not selected or installing:return
        release=selected.copy()
        if not messagebox.askyesno('Atualizar HashPilot',f"Instalar {release['version']}?\n\nSerá baixado o pacote oficial, com verificação de SHA-256 e inventário. Seus dados permanecem no local atual. Depois você poderá reabrir a interface. A mineração não será reiniciada.",parent=window):return
        installing=True;parent._root().update_install_busy=True
        install.configure(state='disabled');refresh.configure(state='disabled');status.configure(text='Baixando, verificando e instalando… mantenha esta janela aberta.')
        def work():
            try:result_queue.put({'app':install_release(release['version']),'error':None})
            except (OSError,ValueError,RuntimeError,KeyError,TypeError,subprocess.SubprocessError) as error:result_queue.put({'app':None,'error':str(error)[:300]})
        threading.Thread(target=work,daemon=True,name='release-install').start()
    def restart():
        if installed:
            root=parent._root()
            if not callable(getattr(root,'close',None)):
                messagebox.showinfo('Versão instalada','Conclua ou feche o assistente e abra o novo atalho do HashPilot.',parent=window);return
            if not messagebox.askyesno('Reabrir HashPilot','Salvar e fechar a interface atual para abrir a nova versão? Alterações ainda não salvas em assistentes serão descartadas. A mineração não será reiniciada.',parent=window):return
            try:reopen_after_close(root,installed);window.destroy()
            except (OSError,ValueError,KeyError,TypeError) as error:messagebox.showerror('Reabrir HashPilot',str(error),parent=window)
    refresh=tk.Button(actions,text='Verificar agora',command=check);refresh.pack(side='left')
    install=tk.Button(actions,text='Instalar atualização',state='disabled',command=update);install.pack(side='left',padx=8)
    reopen=tk.Button(actions,text='Reabrir versão instalada',state='disabled',command=restart);reopen.pack(side='left')
    tk.Button(actions,text='Releases',command=lambda:webbrowser.open(RELEASES_URL)).pack(side='right')
    def poll():
        nonlocal selected,installed,installing
        if not window.winfo_exists():return
        try:
            result=checker.results.get_nowait();selected=result['release']
            if result['include_prereleases']!=channel.get():
                checker.next_check=0;check();window.after(250,poll);return
            status.configure(text=('Não foi possível verificar: '+result['error']) if result['error'] else
                f"Disponível: {selected['version']}"+(' · pré-lançamento' if selected['prerelease'] else ' · estável') if selected else
                'Nenhuma versão mais nova neste canal. Pré-lançamentos são ignorados quando a opção acima está desmarcada.')
            install.configure(state='normal' if selected and not installing else 'disabled')
        except queue.Empty:pass
        try:
            result=result_queue.get_nowait();installed=result['app'];installing=False
            parent._root().update_install_busy=False
            status.configure(text=result['error'] or 'Instalação verificada. Clique em Reabrir versão instalada para salvar e fechar esta interface normalmente.')
            refresh.configure(state='normal');reopen.configure(state='normal' if installed else 'disabled')
        except queue.Empty:pass
        window.after(250,poll)
    def close_window():
        if installing:messagebox.showinfo('Atualização em andamento','Aguarde a conclusão antes de fechar. A mineração continua independente.',parent=window)
        else:window.destroy()
    window.protocol('WM_DELETE_WINDOW',close_window)
    checker.start(force=True);window.after(250,poll)
    window.transient(parent);window.grab_set();parent.wait_window(window)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--reopen',type=Path,required=True);parser.add_argument('--wait-pid',type=int,required=True)
    args=parser.parse_args()
    try:
        if args.wait_pid<=0:raise ValueError('PID inválido.')
        wait_for_parent(args.wait_pid)
        monitor=verify_monitor(args.reopen)
        subprocess.Popen([sys.executable,str(monitor),'--restart'],cwd=args.reopen,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    except (OSError,ValueError,KeyError,TypeError,RuntimeError) as error:
        root=tk.Tk();root.withdraw();messagebox.showerror('Atualização HashPilot',str(error),parent=root);root.destroy()


if __name__=='__main__':main()
