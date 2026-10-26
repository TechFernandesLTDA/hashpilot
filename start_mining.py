"""Início explícito: habilita as GPUs escolhidas e solicita mineração real."""
import json
from pathlib import Path
import time
from contribution import atomic_json
from rig_config import device_key,update_gpu_settings
from stop_mining import read,status_path

def request_start(identifiers,state_dir,log_dir,settings_path,*,now=None):
    from rig import write_request
    stamp=time.time() if now is None else now
    identifiers=list(dict.fromkeys(map(device_key,identifiers)))
    if not identifiers or len(identifiers)>6:raise ValueError('Selecione entre uma e seis GPUs NVIDIA')
    # Do not race a helper that is still returning this GPU to factory settings.
    for identifier in identifiers:
        pending=read(status_path(log_dir,identifier))
        if pending.get('state') in ('pending','restoring') and 0<=stamp-pending.get('at',0)<120:
            raise ValueError('Uma parada/restauração ainda está em andamento. Aguarde a conclusão antes de iniciar.')
    settings_path=Path(settings_path)
    settings=json.loads(settings_path.read_text(encoding='utf-8-sig'))
    for identifier in identifiers:settings=update_gpu_settings(settings,identifier,enabled=True)
    atomic_json(settings_path,settings)
    for identifier in identifiers:
        atomic_json(status_path(log_dir,identifier),{'state':'start_requested','at':stamp,'gpu_uuid':identifier})
        write_request(state_dir,'start',identifier)
    return {'requested_at':stamp,'targets':identifiers}
