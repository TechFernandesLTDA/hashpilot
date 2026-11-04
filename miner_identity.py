"""Match a restarted SRBMiner to its recorded GPU session without trusting its PID."""
import os


def option(argv,name):
    if argv.count(name)!=1:return None
    index=argv.index(name)
    return argv[index+1] if index+1<len(argv) else None


def path_key(value):return os.path.normcase(os.path.abspath(str(value)))


def match_successor(candidate,previous,device,miner_path):
    argv=candidate.get('argv') or []
    old=previous.get('miner') or {}
    if not argv or candidate.get('pid')==old.get('pid'):return False
    try:
        if int(candidate['created_at'])<=int(old['created_at']):return False
    except (KeyError,TypeError,ValueError):return False
    if path_key(candidate.get('executable',''))!=path_key(miner_path) or path_key(argv[0])!=path_key(miner_path):return False
    log=option(argv,'--log-file')
    if not log or not previous.get('miner_log') or path_key(log)!=path_key(previous['miner_log']):return False
    return (option(argv,'--gpu-id')==str(device.get('miner_index',device['index']))
            and option(argv,'--wallet')==previous.get('wallet')
            and option(argv,'--pool')==previous.get('pool')
            and option(argv,'--worker')==device.get('worker_name'))
