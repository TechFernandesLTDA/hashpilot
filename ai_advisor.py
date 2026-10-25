"""Optional advice providers. No hardware access, paid fallback or secret logging."""
from __future__ import annotations
import ctypes
from ctypes import wintypes
from contextlib import closing
import datetime as dt
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler

PROVIDERS = {
    'openrouter': ('OpenRouter · Cloud Free', 'https://openrouter.ai/settings/keys'),
    'opencode': ('OpenCode · Cloud Free pelo cliente', 'https://opencode.ai/docs/'),
    'ollama': ('Ollama local · usa CPU e RAM', 'https://ollama.com/download/windows'),
}
SYSTEM = ('You advise NVIDIA mining. Return only JSON with exactly action and reason. '
          'action must be one of hold,clock_up,clock_down,power_up,power_down,restore_best. '
          'Never return code, commands, numeric settings or tools. Prioritize thermal safety '
          'and accepted shares. Explain briefly in Portuguese, at most 400 characters.')


def configuration(settings):
    if not isinstance(settings,dict):raise ValueError('Configuração de IA precisa ser um objeto')
    raw = settings.get('ai', {})
    if not isinstance(raw,dict):raise ValueError('A seção ai precisa ser um objeto')
    provider = raw.get('provider', 'ollama' if settings.get('ai_enabled') else 'openrouter')
    if not isinstance(provider,str) or provider not in PROVIDERS:
        raise ValueError('Provedor de IA inválido')
    if 'model' in raw and raw['model'] is not None and not isinstance(raw['model'],str):raise ValueError('Modelo precisa ser texto')
    model = raw.get('model') or {'ollama':'qwen2.5:0.5b', 'openrouter':'openrouter/free',
                                    'opencode':'opencode/glm-5.3-flash-free'}[provider]
    if not isinstance(model,str):raise ValueError('Modelo precisa ser texto')
    if not re.fullmatch(r'[A-Za-z0-9._:/-]{1,160}', model):
        raise ValueError('Identificador de modelo inválido')
    if provider == 'openrouter' and model != 'openrouter/free' and not model.endswith(':free'):
        raise ValueError('Selecione openrouter/free ou um modelo :free')
    if provider == 'opencode' and not re.fullmatch(r'opencode/[a-z0-9.-]+-free', model):
        raise ValueError('OpenCode exige um modelo opencode/...-free')
    return {'provider':provider, 'model':model, 'cloud_consent':raw.get('cloud_consent') is True,
            'enabled':settings.get('ai_enabled') is True, 'free_only':True}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Redirecionamento do provedor recusado')


def request_json(url, payload=None, key=None):
    if key is not None and (not isinstance(key,str) or not key or any(c.isspace() or not 33<=ord(c)<=126 for c in key)):
        raise ValueError('Chave inválida')
    headers={'Content-Type':'application/json','User-Agent':'HashPilot/0.3'}
    if key: headers['Authorization']='Bearer '+key
    request=Request(url, json.dumps(payload).encode() if payload is not None else None, headers)
    try:
        with build_opener(ProxyHandler({}),_NoRedirect()).open(request,timeout=20) as response:
            body=response.read(2_000_001)
        if len(body)>2_000_000: raise ValueError('Resposta do provedor excedeu o limite')
        try:return json.loads(body)
        except (ValueError,UnicodeError):raise ValueError('Resposta JSON inválida do provedor') from None
    except HTTPError as error:
        # Do not include a response body, URL parameters, headers or credential.
        error.close()
        raise ValueError({401:'Autenticação recusada; confira sua chave',
                          402:'Créditos/cota indisponíveis; sem fallback pago',
                          429:'Limite gratuito atingido; controle adaptativo permanece ativo'}.get(
                              error.code, f'Provedor indisponível (HTTP {error.code})')) from None
    except (URLError,TimeoutError,OSError):
        raise ValueError('Provedor sem conexão ou tempo de resposta excedido') from None


def telemetry_context(context):
    """Only numeric measurements and summary fields may leave the computer."""
    allowed_sample=('temperature_c','power_w','power_limit_w','clock_mhz','utilization_pct',
                    'temperature.gpu','power.draw','power.limit','clocks.gr','utilization.gpu')
    allowed_bench=('mean_hashrate_ths','mean_power_w','efficiency_ths_per_w',
                   'independent_hash_samples','invalid_samples','validated','elapsed_seconds')
    if not isinstance(context,dict):raise ValueError('Contexto de IA inválido')
    result={}
    for section,keys in [('sample',allowed_sample),('benchmark',allowed_bench)]:
        source=context.get(section) or {}
        if not isinstance(source,dict):source={}
        result[section]={}
        for key in keys:
            value=source.get(key)
            if section=='benchmark' and key=='validated' and isinstance(value,bool):
                result[section][key]=value;continue
            if isinstance(value,bool) or not isinstance(value,(int,float)):continue
            try:valid=math.isfinite(value) and value>=0
            except OverflowError:valid=False
            if valid:result[section][key]=value
    return result


def credential(provider, value=None):
    """Windows Credential Manager; key never enters settings/history/exports."""
    if provider not in PROVIDERS: raise ValueError('Provedor inválido')
    if os.name!='nt': raise ValueError('Credenciais exigem o cofre do Windows')
    class CREDENTIAL(ctypes.Structure):
        _fields_=[('Flags',wintypes.DWORD),('Type',wintypes.DWORD),('TargetName',wintypes.LPWSTR),
                  ('Comment',wintypes.LPWSTR),('LastWritten',wintypes.FILETIME),
                  ('CredentialBlobSize',wintypes.DWORD),('CredentialBlob',ctypes.POINTER(ctypes.c_ubyte)),
                  ('Persist',wintypes.DWORD),('AttributeCount',wintypes.DWORD),('Attributes',ctypes.c_void_p),
                  ('TargetAlias',wintypes.LPWSTR),('UserName',wintypes.LPWSTR)]
    api=ctypes.WinDLL('advapi32',use_last_error=True)
    target='HashPilot/AI/'+provider
    if value is not None:
        if not value or len(value)>2048 or any(c.isspace() for c in value):
            raise ValueError('Chave inválida')
        blob=value.encode('utf-8')
        if len(blob)>2560:raise ValueError('Chave excede o limite do cofre do Windows')
        buffer=(ctypes.c_ubyte*len(blob)).from_buffer_copy(blob)
        item=CREDENTIAL(Type=1,TargetName=target,CredentialBlobSize=len(blob),CredentialBlob=buffer,
                        Persist=2,UserName='HashPilot')
        api.CredWriteW.argtypes=[ctypes.POINTER(CREDENTIAL),wintypes.DWORD]
        api.CredWriteW.restype=wintypes.BOOL
        if not api.CredWriteW(ctypes.byref(item),0):raise ValueError('Não foi possível salvar no cofre do Windows')
        return None
    pointer=ctypes.POINTER(CREDENTIAL)()
    api.CredReadW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.POINTER(ctypes.POINTER(CREDENTIAL))]
    api.CredReadW.restype=wintypes.BOOL
    api.CredFree.argtypes=[ctypes.c_void_p]
    if not api.CredReadW(target,1,0,ctypes.byref(pointer)):
        if ctypes.get_last_error()==1168:return None
        raise ValueError('Cofre do Windows indisponível ou acesso negado')
    try:return ctypes.string_at(pointer.contents.CredentialBlob,pointer.contents.CredentialBlobSize).decode('utf-8')
    finally:api.CredFree(pointer)


class RequestBudget:
    """Shared across all six controllers and UI, including failed attempts."""
    def __init__(self, state_dir): self.path=Path(state_dir)/'ai-requests.sqlite3'
    def reserve(self, now=None):
        now=time.time() if now is None else now
        if isinstance(now,bool) or not isinstance(now,(int,float)) or not math.isfinite(now) or not 0<=now<=253402300799:
            raise ValueError('Horário inválido para a cota de IA')
        day=dt.datetime.fromtimestamp(now,dt.timezone.utc).strftime('%Y-%m-%d')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(self.path,timeout=2)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS requests(day TEXT,stamp REAL)')
            db.execute('BEGIN IMMEDIATE')
            count=db.execute('SELECT count(*) FROM requests WHERE day=?',(day,)).fetchone()[0]
            last=db.execute('SELECT max(stamp) FROM requests').fetchone()[0]
            if count>=40:raise ValueError('Limite local de 40 consultas cloud/dia atingido')
            if last is not None and now-last<60:raise ValueError('Intervalo global da IA: aguarde 60 segundos')
            db.execute('INSERT INTO requests VALUES(?,?)',(day,now))


class RoutedAdvisor:
    def __init__(self,state_dir,settings_path=None,*,config_override=None,key_override=None):
        self.state_dir=Path(state_dir)
        self.settings_path=Path(settings_path) if settings_path else self.state_dir/'settings.json'
        self.catalog=None;self.catalog_at=0
        self.label='IA opcional'
        self.config_override=config_override
        self.key_override=key_override
    def settings(self):
        if self.config_override is not None:return self.config_override
        try:
            result=json.loads(self.settings_path.read_text(encoding='utf-8-sig'))
            if not isinstance(result,dict):raise ValueError('Configuração de IA inválida')
            return result
        except FileNotFoundError:return {}
    def enabled(self):
        try:return self.settings().get('ai_enabled') is True
        except (OSError,ValueError,TypeError):return False
    def advise(self,context):
        from gpu_agent import OllamaAdvisor
        config=configuration(self.settings())
        if not config['enabled']:raise ValueError('IA desativada; política adaptativa ativa')
        provider=config['provider'];self.label=PROVIDERS[provider][0]
        safe=telemetry_context(context)
        if provider=='ollama':return OllamaAdvisor(model=config['model']).advise(safe)
        if not config['cloud_consent']:raise ValueError('Autorize o envio de telemetria resumida à nuvem')
        if provider=='opencode':return self.opencode(config,safe)
        key=self.key_override or credential('openrouter')
        if not key:raise ValueError('Configure sua chave OpenRouter no assistente de IA')
        if not self.catalog or time.time()-self.catalog_at>300:
            self.catalog=request_json('https://openrouter.ai/api/v1/models')['data'];self.catalog_at=time.time()
        selected=next((x for x in self.catalog if x.get('id')==config['model']),None)
        pricing=selected.get('pricing',{}) if selected else {}
        try:free=all(float(pricing[k])==0 for k in ('prompt','completion'))
        except (KeyError,TypeError,ValueError):free=False
        if not free:raise ValueError('Modelo não confirmado como gratuito no catálogo atual')
        RequestBudget(self.state_dir).reserve()
        body=request_json('https://openrouter.ai/api/v1/chat/completions',{
            'model':config['model'],'messages':[{'role':'system','content':SYSTEM},
                {'role':'user','content':json.dumps(safe)}],
            'temperature':0,'max_tokens':200,'provider':{'max_price':{'prompt':0,'completion':0,'request':0}},
            'response_format':{'type':'json_object'}},key)
        return OllamaAdvisor.parse(body['choices'][0]['message']['content'])
    def opencode(self,config,context):
        from gpu_agent import OllamaAdvisor
        executable=shutil.which('opencode.exe')
        if not executable:raise ValueError('Instale o OpenCode oficial e faça login; cliente não encontrado')
        catalog=request_json('https://opencode.ai/zen/v1/models')['data']
        model=config['model'].split('/',1)[1]
        if not any(item.get('id')==model for item in catalog):raise ValueError('Modelo gratuito removido do catálogo OpenCode')
        RequestBudget(self.state_dir).reserve()
        with tempfile.TemporaryDirectory(prefix='HashPilot-advice-') as directory:
            # Never execute a coding agent against the user's rig/repository.
            env=dict(os.environ)
            env.update(OPENCODE_CONFIG_DIR=directory,OPENCODE_DISABLE_CLAUDE_CODE='1',
                       OPENCODE_DISABLE_DEFAULT_PLUGINS='1',OPENCODE_DISABLE_AUTOUPDATE='1',
                       OPENCODE_DISABLE_LSP_DOWNLOAD='1',OPENCODE_PERMISSION='{"*":"deny"}')
            conf={'model':config['model'],'small_model':config['model'],'permission':{'*':'deny'},
                  'plugin':[],'mcp':{},'instructions':[], 'share':'disabled',
                  'agent':{'hashpilot':{'mode':'primary','prompt':SYSTEM,'steps':1,'permission':{'*':'deny'}}}}
            env['OPENCODE_CONFIG_CONTENT']=json.dumps(conf)
            with tempfile.TemporaryFile() as output:
                try:
                    result=subprocess.run([executable,'--pure','run','--format','json','--agent','hashpilot',
                        '--model',config['model'],'--title','HashPilot telemetry',SYSTEM+'\n'+json.dumps(context)],
                        cwd=directory,env=env,stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.DEVNULL,
                        timeout=40,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                except subprocess.TimeoutExpired:raise ValueError('OpenCode excedeu o tempo de resposta') from None
                output.seek(0);raw=output.read(65537)
            if result.returncode or len(raw)>65536:raise ValueError('OpenCode indisponível; confira login/modelo gratuito')
        parts=[]
        for line in raw.decode('utf-8').splitlines():
            event=json.loads(line)
            if not isinstance(event,dict):raise ValueError('Evento OpenCode inválido')
            if event.get('type')=='text':
                part=event.get('part')
                if not isinstance(part,dict) or not isinstance(part.get('text'),str):raise ValueError('Resposta OpenCode inválida')
                parts.append(part['text'])
        return OllamaAdvisor.parse(''.join(parts))
