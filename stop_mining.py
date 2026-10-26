"""Parada explícita, persistente e por UUID; restaura sem iniciar mineração."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
from contribution import atomic_json
from rig_config import device_key,update_gpu_settings,gpu_paths

def read(path):
    try:
        value=json.loads(Path(path).read_text(encoding='utf-8-sig'))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):return {}

def disable(settings_path,identifiers):
    path=Path(settings_path)
    value=json.loads(path.read_text(encoding='utf-8-sig'))
    for identifier in identifiers:value=update_gpu_settings(value,device_key(identifier),enabled=False)
    atomic_json(path,value)

def status_path(log_dir,identifier):
    return Path(gpu_paths(log_dir,identifier)['state_dir'])/'gpu-defaults.json'

def launch_admin(identifiers):
    script=Path(__file__).resolve().parent/'mining/windows/Parar-Rig.ps1'
    # Individual --uuid arguments are validated; no shell command is assembled from free text.
    args=['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script),'-Python',sys.executable,
        '-GpuUuids',','.join(map(device_key,identifiers))]
    subprocess.Popen(args,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))

def request_stop(identifiers,state_dir,log_dir,settings_path,*,launcher=launch_admin,now=None):
    from rig import write_request
    stamp=time.time() if now is None else now
    identifiers=list(dict.fromkeys(map(device_key,identifiers)))
    if not identifiers or len(identifiers)>6:raise ValueError('Selecione entre uma e seis GPUs NVIDIA')
    disable(settings_path,identifiers)
    previous={identifier:read(status_path(log_dir,identifier)) for identifier in identifiers}
    pending={identifier:value for identifier,value in previous.items() if value.get('state') in ('pending','restoring') and 0<=stamp-value.get('at',0)<120}
    new_targets=[identifier for identifier in identifiers if identifier not in pending]
    if not new_targets:
        return {'requested_at':min(value['at'] for value in pending.values()),'targets':identifiers,
            'requires_uac':any(value.get('requires_uac') for value in pending.values()),'already_pending':True}
    registry=read(Path(state_dir)/'rig-manager.json')
    fresh=registry.get('marker')=='HashPilotRig-v1' and registry.get('ready') and 0<=stamp-registry.get('sampled_at',0)<=15
    native=fresh and 'stop_restore_v1' in registry.get('capabilities',[])
    for identifier in new_targets:
        atomic_json(status_path(log_dir,identifier),{'state':'pending','at':stamp,'gpu_uuid':identifier,
            'requires_uac':not native,
            'message':'Aguardando parada e restauração pelo serviço' if native else 'Parada solicitada; aprove o UAC para restaurar padrões'})
    if native and set(new_targets)==set(registry.get('devices',{})):
        write_request(state_dir,'stop_all')
    elif fresh:
        for identifier in new_targets:write_request(state_dir,'stop',identifier)
    if not native:launcher(new_targets)
    return {'requested_at':stamp,'targets':identifiers,'requires_uac':not native,
        'requested_at_by_gpu':{identifier:pending[identifier]['at'] if identifier in pending else stamp for identifier in identifiers}}

def stop_owned(entry,backend,identifier):
    if entry.get('external_readonly') or entry.get('ownership_unverified'):
        raise RuntimeError('Processo externo não confirmado; restauração bloqueada para evitar concorrência')
    for role in ('miner','controller','proxy'):
        value=entry.get(role)
        if value and backend.inspect(value.get('pid')) and not backend.is_owned(value):
            raise RuntimeError('Propriedade do processo não confirmada; nenhuma restauração autorizada')
    controller=entry.get('controller')
    if controller and backend.is_owned(controller):
        atomic_json(Path(entry['log_dir'])/'gpu-agent-stop.json',{'gpu_uuid':identifier,'pid':controller['pid'],'at':time.time(),'reason':'restore_defaults'})
    for role in ('miner','controller','proxy'):
        value=entry.get(role)
        if not value or not backend.is_owned(value):continue
        if role!='controller' or not backend.wait(value,timeout=12):
            if not backend.stop(value) or not backend.wait(value,timeout=12):
                raise RuntimeError('Processo ainda ativo; restauração bloqueada')

def restore_offline(identifiers,state_dir,log_dir,settings_path,*,backend=None,detector=None,restorer=None):
    from rig import ProcessBackend
    from gpu_agent import detect_nvidia_gpus
    from gpu_defaults import restore_defaults
    backend=backend or ProcessBackend();detector=detector or detect_nvidia_gpus;restorer=restorer or restore_defaults
    identifiers=list(dict.fromkeys(map(device_key,identifiers)))
    if not identifiers or len(identifiers)>6:raise ValueError('Seleção de GPUs inválida')
    disable(settings_path,identifiers)
    registry=read(Path(state_dir)/'rig-manager.json')
    from rig import write_request
    fresh=registry.get('marker')=='HashPilotRig-v1' and registry.get('ready') and 0<=time.time()-registry.get('sampled_at',0)<=15
    if fresh:
        for identifier in identifiers:write_request(state_dir,'stop',identifier)
        deadline=time.monotonic()+30
        while time.monotonic()<deadline:
            registry=read(Path(state_dir)/'rig-manager.json')
            if all(not registry.get('devices',{}).get(identifier,{}).get(role) for identifier in identifiers for role in ('miner','controller','proxy')):break
            time.sleep(.2)
        else:raise RuntimeError('Serviço não confirmou a parada; restauração não executada')
    detected={gpu['uuid']:gpu for gpu in detector()}
    results={}
    for identifier in identifiers:
        path=status_path(log_dir,identifier)
        try:
            gpu=detected.get(identifier)
            if not gpu:raise RuntimeError('UUID não corresponde a uma NVIDIA presente')
            entry=registry.get('devices',{}).get(identifier,{})
            stop_owned(entry,backend,identifier)
            atomic_json(path,{'state':'restoring','at':time.time(),'gpu_uuid':identifier})
            result=restorer(gpu,path.parent)
        except (OSError,RuntimeError,ValueError) as error:
            result={'state':'failed','at':time.time(),'error':str(error)[:300]}
        result['gpu_uuid']=identifier
        atomic_json(path,result);results[identifier]=result
    return results

def main():
    import ctypes,os
    parser=argparse.ArgumentParser(description=__doc__)
    selection=parser.add_mutually_exclusive_group(required=True)
    selection.add_argument('--uuid',action='append')
    selection.add_argument('--request-all',action='store_true')
    args=parser.parse_args()
    from config import STATE_DIR,LOG_DIR,SETTINGS_PATH
    if args.request_all:
        from gpu_agent import detect_nvidia_gpus
        identifiers=[gpu['uuid'] for gpu in detect_nvidia_gpus(max_devices=6)]
        print(json.dumps(request_stop(identifiers,STATE_DIR,LOG_DIR,SETTINGS_PATH)))
        return
    if not args.uuid:parser.error('Use --uuid ou --request-all')
    if os.name!='nt' or not ctypes.WinDLL('shell32').IsUserAnAdmin():raise RuntimeError('Restauração exige aprovação do administrador no Windows')
    try:restore_offline(args.uuid,STATE_DIR,LOG_DIR,SETTINGS_PATH)
    except (OSError,RuntimeError,ValueError) as error:
        for identifier in args.uuid:atomic_json(status_path(LOG_DIR,device_key(identifier)),{'state':'failed','at':time.time(),'error':str(error)[:300]})
        raise

if __name__=='__main__':main()
