"""Identidade de componentes sem importar interface nem configuração secreta."""
import json
import re
from pathlib import Path
from app_version import VERSION

_miner_versions={}

def miner_version(log_path):
    if not log_path:return None
    if str(log_path) in _miner_versions:return _miner_versions[str(log_path)]
    try:
        with Path(log_path).open('rb') as handle:data=handle.read(16384)
        encoding='utf-16' if data.startswith((b'\xff\xfe',b'\xfe\xff')) else 'utf-8-sig'
        match=re.search(r'Miner version:\s*([^\r\n]+)',data.decode(encoding,errors='replace'))
        if match:
            value=match[1].strip()[:100]
            _miner_versions[str(log_path)]=value
            return value
    except OSError:pass
    return None

def identity(root=None):
    root=Path(root or Path(__file__).resolve().parent)
    manifest=root/'RELEASE-MANIFEST.json'
    try:
        value=json.loads(manifest.read_text(encoding='utf-8-sig'))
        version=value.get('version');commit=value.get('commit','')
        if value.get('schema')==1 and isinstance(version,str) and re.fullmatch(r'v\d+\.\d+\.\d+',version) and isinstance(commit,str) and re.fullmatch('[a-f0-9]{40}',commit):
            return {'version':version,'revision':commit,'source':'release'}
    except (OSError,ValueError,TypeError,AttributeError): pass
    return {'version':'não verificada' if manifest.exists() else VERSION,'revision':None,'source':'invalid' if manifest.exists() else 'source'}
