"""Official SRBMiner discovery and verified, side-by-side Windows packages."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import queue
import threading
import tempfile
import time
import urllib.request
import zipfile
from contribution import atomic_json

RELEASES_URL='https://github.com/doktor83/SRBMiner-Multi/releases'
API_URL='https://api.github.com/repos/doktor83/SRBMiner-Multi/releases/latest'
DEFAULT_VERSION='3.8.0'
CATALOG={
 '3.8.0':{'archive_sha256':'33eef5f8946259290013ce370a746a85f91c2bbcd81fcefa1aad904dd9a912f8','exe_sha256':'6eb4e0bc37d65fdf7644ab620737e14f4c7dfa9666acde43d5f38ab1e9094ac1'},
 '3.8.1':{'archive_sha256':'16bd455557bd3698e004d1f6b5d19290b9b59e9f9c8ef9245acafdaeb2413e79','exe_sha256':'2f7e60a17fcc65ddd3c7f5821666a6ae11ef6c1f2c95331bb7e46793c032b688'},
}

def base_directory():return Path(os.environ['LOCALAPPDATA'])/'Mining/Pearl'

def descriptor(version):
    if version not in CATALOG:raise ValueError('Versão ainda não revisada pelo HashPilot; atualize o aplicativo antes de instalar esse minerador.')
    folder='SRBMiner-Multi-'+version.replace('.','-')
    return {**CATALOG[version],'version':version,'folder':folder,'asset':folder+'-win64.zip',
        'url':f'https://github.com/doktor83/SRBMiner-Multi/releases/download/{version}/{folder}-win64.zip'}

def read(path):
    try:
        data=json.loads(Path(path).read_text(encoding='utf-8-sig'))
        return data if isinstance(data,dict) else {}
    except FileNotFoundError:return {}

def selected_version(base=None):
    record=read(Path(base or base_directory())/'miner-install.json')
    version=record.get('version',DEFAULT_VERSION);descriptor(version)
    return version

def executable(version,base=None,verify=True):
    item=descriptor(version);base=Path(base or base_directory()).resolve()
    target=base/item['folder']/'SRBMiner-MULTI.exe'
    # Never execute outside the fixed installation directory through a junction.
    if target.resolve()!=target:raise ValueError('Caminho redirecionado do minerador recusado')
    if verify:
        try:valid=target.is_file() and hashlib.sha256(target.read_bytes()).hexdigest()==item['exe_sha256']
        except OSError:valid=False
        if not valid:raise ValueError('Executável ausente, bloqueado ou SHA-256 divergente. Confira o Histórico de proteção do Windows; nenhuma exceção será criada automaticamente. A versão atual foi preservada se a ativação ainda não começou.')
    return target

def active_executable(base=None,verify=True):return executable(selected_version(base),base,verify)

def latest(current=DEFAULT_VERSION,opener=urllib.request.urlopen):
    request=urllib.request.Request(API_URL,headers={'User-Agent':'HashPilot','Accept':'application/vnd.github+json'})
    with opener(request,timeout=20) as response:
        payload=json.loads(response.read(1024*1024))
    version=str(payload.get('tag_name','')).removeprefix('v')
    if not re.fullmatch(r'\d+\.\d+\.\d+',version) or payload.get('draft') or payload.get('prerelease'):
        raise ValueError('Release estável oficial não confirmada')
    newer=tuple(map(int,version.split('.')))>tuple(map(int,current.split('.')))
    item=descriptor(version) if version in CATALOG else None
    asset=next((a for a in payload.get('assets',[]) if item and a.get('name')==item['asset']),None)
    compatible=bool(item and asset and asset.get('browser_download_url')==item['url'] and asset.get('digest')=='sha256:'+item['archive_sha256'])
    return {'version':version,'current':current,'newer':newer,'installable':compatible,
        'url':RELEASES_URL+'/tag/'+version,'notes':str(payload.get('body',''))[:6000],'checked_at':time.time()}

class MinerUpdateChecker:
    def __init__(self,state_dir):
        self.path=Path(state_dir)/'miner-release-check.json';self.results=queue.Queue();self.busy=False;self.offered=set()
        try:cached=read(self.path);stamp=float(cached.get('checked_at',0))
        except (OSError,ValueError,TypeError):stamp=0
        self.next_check=stamp+6*3600 if 0<=time.time()-stamp<=6*3600 else 0

    def start(self):
        if self.busy or time.time()<self.next_check:return False
        self.busy=True;self.next_check=time.time()+6*3600
        def work():
            try:
                result=latest(selected_version());atomic_json(self.path,result);self.results.put(result)
            except (OSError,ValueError,RuntimeError) as error:
                self.next_check=time.time()+3600
                self.results.put({'error':str(error)[:200]})
            finally:self.busy=False
        threading.Thread(target=work,daemon=True,name='miner-release-check').start()
        return True

def stage(version,base=None,opener=urllib.request.urlopen):
    from contribution import ProxyLease
    base=Path(base or base_directory());base.mkdir(parents=True,exist_ok=True)
    lease=ProxyLease(base/'miner-package-owner.json')
    try:return _stage(version,base,opener)
    finally:lease.close()

def _stage(version,base=None,opener=urllib.request.urlopen):
    """Download without stopping mining; only extract the verified executable."""
    item=descriptor(version);base=Path(base or base_directory()).resolve();base.mkdir(parents=True,exist_ok=True)
    target=executable(version,base,verify=False)
    if target.is_file():
        try:return executable(version,base)
        except ValueError:raise ValueError('Instalação existente divergente; arquivo preservado para análise')
    with tempfile.TemporaryDirectory(prefix='hashpilot-miner-') as temporary:
        archive=Path(temporary)/item['asset'];digest=hashlib.sha256();size=0
        request=urllib.request.Request(item['url'],headers={'User-Agent':'HashPilot'})
        with opener(request,timeout=90) as response,archive.open('wb') as output:
            while block:=response.read(1024*1024):
                size+=len(block)
                if size>100*1024*1024:raise ValueError('Pacote excedeu o tamanho permitido')
                digest.update(block);output.write(block)
        if digest.hexdigest()!=item['archive_sha256']:raise ValueError('SHA-256 do pacote divergente; nada instalado')
        saved_archive=base/item['asset']
        if saved_archive.resolve()!=saved_archive:raise ValueError('Arquivo redirecionado recusado')
        archive_copy=saved_archive.with_suffix('.download')
        if archive_copy.resolve()!=archive_copy:raise ValueError('Arquivo temporário redirecionado recusado')
        archive_copy.write_bytes(archive.read_bytes());archive_copy.replace(saved_archive)
        with zipfile.ZipFile(archive) as package:
            name=item['folder']+'/SRBMiner-MULTI.exe'
            members=[entry for entry in package.infolist() if entry.filename==name]
            if len(members)!=1 or members[0].file_size>100*1024*1024:raise ValueError('Executável único não confirmado no pacote')
            binary=package.read(members[0])
        if hashlib.sha256(binary).hexdigest()!=item['exe_sha256']:raise ValueError('SHA-256 do executável divergente')
        target.parent.mkdir(parents=True,exist_ok=True)
        partial=target.with_suffix('.download')
        if partial.resolve()!=partial:raise ValueError('Arquivo temporário redirecionado recusado')
        partial.write_bytes(binary);partial.replace(target)
    return executable(version,base)

def select(version,base=None):
    base=Path(base or base_directory());executable(version,base)
    old=selected_version(base)
    atomic_json(base/'miner-install.json',{'version':version,'previous_version':old,'selected_at':time.time()})

def request_activation(version,state_dir):
    from rig import write_request
    descriptor(version);registry=read(Path(state_dir)/'rig-manager.json')
    if not registry.get('ready') or not 0<=time.time()-registry.get('sampled_at',0)<=15:
        raise RuntimeError('Inicie o serviço do rig antes de ativar. O pacote já preparado será preservado.')
    if 'miner_update_v1' not in registry.get('capabilities',[]):
        raise RuntimeError('Atualize o serviço pela Central de operação antes de ativar este minerador.')
    request=write_request(state_dir,'miner_update',version=version)
    return request['id']

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--path',action='store_true');args=parser.parse_args()
    if args.path:print(active_executable())

if __name__=='__main__':main()
