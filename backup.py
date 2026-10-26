"""Backup privado consistente; credenciais e ledger da contribuição nunca são exportados."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sqlite3
import tempfile
import time
import zipfile
from contextlib import closing
from contribution import atomic_json, valid_wallet
from rig_config import device_key
from gpu_profiles import profile

SETTINGS_KEYS=('wallet','pool','log_dir','support_enabled','ai_enabled','gpu_settings')
MAX_BYTES=1024*1024*1024

def public_settings(settings):
    result={key:settings[key] for key in SETTINGS_KEYS if key in settings and key!='gpu_settings'}
    if 'gpu_settings' in settings:
        result['gpu_settings']={device_key(key):{field:value for field,value in entry.items() if field in ('wallet','enabled','profile')}
            for key,entry in settings['gpu_settings'].items()}
    validate_settings(result)
    return result

def validate_settings(settings):
    if not isinstance(settings,dict) or set(settings)-set(SETTINGS_KEYS): raise ValueError('Configuração não permitida no backup')
    if 'wallet' in settings and not valid_wallet(settings['wallet']): raise ValueError('Carteira inválida')
    for flag in ('support_enabled','ai_enabled'):
        if flag in settings and not isinstance(settings[flag],bool): raise ValueError('Opção inválida')
    if 'log_dir' in settings and (not isinstance(settings['log_dir'],str) or not settings['log_dir']): raise ValueError('Pasta de logs inválida')
    if 'pool' in settings and (not isinstance(settings['pool'],str) or not settings['pool']): raise ValueError('Pool inválida')
    overrides=settings.get('gpu_settings',{})
    if not isinstance(overrides,dict) or len(overrides)>128: raise ValueError('GPUs inválidas')
    for identifier,entry in overrides.items():
        device_key(identifier)
        if not isinstance(entry,dict) or set(entry)-{'wallet','enabled','profile'}: raise ValueError('Configuração da GPU inválida')
        if entry.get('wallet') not in (None,'') and not valid_wallet(entry['wallet']): raise ValueError('Carteira da GPU inválida')
        if 'enabled' in entry and not isinstance(entry['enabled'],bool): raise ValueError('Ativação da GPU inválida')
        if 'profile' in entry: profile(entry['profile'])

def database_backup(source, destination):
    with closing(sqlite3.connect(Path(source).resolve().as_uri()+'?mode=ro',uri=True,timeout=5)) as src, closing(sqlite3.connect(destination)) as dst:
        src.backup(dst,pages=256,sleep=.05)
        if dst.execute('PRAGMA quick_check').fetchone()[0]!='ok': raise ValueError('Banco inconsistente')

def allowed_name(name):
    return name in ('settings.json','history.sqlite3','gpu-agent.sqlite3') or bool(re.fullmatch(r'gpus/GPU-[A-Za-z0-9-]{1,100}/gpu-agent.sqlite3',name))

def create_backup(destination, state_dir, log_dir):
    state_dir,log_dir=Path(state_dir),Path(log_dir)
    settings=public_settings(json.loads((state_dir/'settings.json').read_text(encoding='utf-8-sig')))
    files={}
    with tempfile.TemporaryDirectory(prefix='hashpilot-backup-') as temporary:
        stage=Path(temporary)
        (stage/'settings.json').write_text(json.dumps(settings,ensure_ascii=False),encoding='utf-8')
        files['settings.json']=stage/'settings.json'
        sources=[('history.sqlite3',state_dir/'history.sqlite3'),('gpu-agent.sqlite3',state_dir/'gpu-agent.sqlite3')]
        sources.extend((f'gpus/{p.parent.name}/gpu-agent.sqlite3',p) for p in (log_dir/'gpus').glob('GPU-*/gpu-agent.sqlite3'))
        for index,(name,source) in enumerate(sources):
            if source.is_file() and allowed_name(name):
                target=stage/f'{index}.sqlite3'
                database_backup(source,target)
                files[name]=target
        manifest={'schema':1,'created_at':time.time(),'credentials_included':False,
            'contribution_ledger_included':False,'files':{name:{'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'size':path.stat().st_size} for name,path in files.items()}}
        destination=Path(destination)
        if destination.exists(): raise FileExistsError('Escolha um novo nome; backup existente preservado')
        temporary_zip=destination.with_name(destination.name+'.'+os.urandom(6).hex()+'.tmp')
        try:
            with zipfile.ZipFile(temporary_zip,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=1) as archive:
                archive.writestr('manifest.json',json.dumps(manifest))
                for name,path in files.items(): archive.write(path,name)
            os.replace(temporary_zip,destination)
        finally:
            temporary_zip.unlink(missing_ok=True)
    return manifest

def validate_backup(source, stage):
    stage=Path(stage)
    stage.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(source) as archive:
        infos=archive.infolist()
        names=[i.filename for i in infos]
        if len(names)!=len(set(names)) or len(names)>132 or sum(i.file_size for i in infos)>MAX_BYTES: raise ValueError('Backup duplicado ou acima do limite')
        if 'manifest.json' not in names or archive.getinfo('manifest.json').file_size>128*1024: raise ValueError('Manifesto ausente ou inválido')
        manifest=json.loads(archive.read('manifest.json'))
        if manifest.get('schema')!=1 or not isinstance(manifest.get('files'),dict) or set(names)!={'manifest.json',*manifest['files']}: raise ValueError('Inventário inválido')
        if 'settings.json' not in manifest['files']: raise ValueError('Configuração ausente')
        for name,metadata in manifest['files'].items():
            if not allowed_name(name) or archive.getinfo(name).is_dir(): raise ValueError('Caminho não permitido')
            data=archive.read(name)
            if len(data)!=metadata['size'] or hashlib.sha256(data).hexdigest()!=metadata['sha256']: raise ValueError('Integridade do backup inválida')
            target=stage.joinpath(*PurePosixPath(name).parts)
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(data)
            if name.endswith('.sqlite3'):
                with closing(sqlite3.connect(target.resolve().as_uri()+'?mode=ro',uri=True)) as db:
                    if db.execute('PRAGMA quick_check').fetchone()[0]!='ok': raise ValueError('Banco do backup inválido')
        validate_settings(json.loads((stage/'settings.json').read_text(encoding='utf-8')))
    return manifest

def restore_backup(source,state_dir,log_dir):
    """Requires all app/controller writers closed. Caller owns process verification."""
    state_dir,log_dir=Path(state_dir),Path(log_dir)
    rollback=state_dir/'backups'/('before-restore-'+str(time.time_ns())+'.zip')
    rollback.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='hashpilot-restore-') as temporary:
        stage=Path(temporary)
        manifest=validate_backup(source,stage)
        create_backup(rollback,state_dir,log_dir)
        current=json.loads((state_dir/'settings.json').read_text(encoding='utf-8-sig'))
        incoming=json.loads((stage/'settings.json').read_text(encoding='utf-8'))
        # Preserve the local paths and credentials; portable wallets/profiles are imported.
        incoming.pop('log_dir',None)
        current.update(incoming)
        prepared=[]
        for name in manifest['files']:
            if name=='settings.json': continue
            target=(log_dir if name.startswith('gpus/') else state_dir).joinpath(*PurePosixPath(name).parts)
            target.parent.mkdir(parents=True,exist_ok=True)
            if Path(str(target)+'-wal').exists() or Path(str(target)+'-shm').exists():
                # Checkpoint after process shutdown, then use SQLite backup to overwrite safely.
                with closing(sqlite3.connect(target)) as db: db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            prepared.append((stage.joinpath(*PurePosixPath(name).parts),target))
        for src,target in prepared: database_backup(src,target)
        atomic_json(state_dir/'settings.json',current)
    return {'rollback':str(rollback),'databases':len(prepared)}
