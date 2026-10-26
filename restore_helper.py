"""Restauração fora do painel, após encerramento confirmado de todos os escritores."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
from backup import restore_backup
from contribution import atomic_json
from rig import ProcessBackend

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--request',type=Path,required=True)
    args=parser.parse_args()
    request=json.loads(args.request.read_text(encoding='utf-8'))
    backend=ProcessBackend()
    root=Path(__file__).resolve().parent
    state=Path(request['state_dir'])
    result={'ok':False,'at':time.time()}
    try:
        # Identity includes creation time: PID reuse must not block/target another app.
        for key in ('gui','manager'):
            identity=request.get(key)
            if identity and not backend.wait(identity,timeout=60): raise RuntimeError('Um processo ainda está ativo; restauração cancelada')
        for identity in request.get('children',[]):
            if not backend.wait(identity,timeout=10): raise RuntimeError('Minerador ou controlador ainda ativo; restauração cancelada')
        result.update(restore_backup(request['archive'],state,request['log_dir']),ok=True)
    except Exception as error:
        result['error']=str(error)[:300]
    atomic_json(state/'restore-result.json',result)
    subprocess.Popen([sys.executable,str(root/'monitor.py'),'--restart'],cwd=root,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    # A restored profile never automatically starts mining: user reviews wallets first.

if __name__=='__main__': main()
