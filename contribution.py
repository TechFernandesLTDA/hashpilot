"""Contribuição opcional: proxy Stratum local, com 5% aproximados do tempo conectado.

Não controla GPU, não reinicia mineradores e não importa config.py. A opção é
desligada por padrão. O único upstream de produção é a pool Pearl da Kryptex.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import select
import socket
import threading
import time

AUTHOR_WALLET = "prl1pl2km7u7uddqqfmry4px9c9c7lpfeskhtl0yvkylvgupcjkafxmaqc6cr8t"
POOL_HOST,POOL_PORT="prl.kryptex.network",7048
PERCENT=5
CYCLE_SECONDS=3600
PRIMARY_SECONDS=3420
SUPPORT_SECONDS=CYCLE_SECONDS-PRIMARY_SECONDS
MAX_LINE=4*1024*1024
MAX_ACCOUNT_INTERVAL=5.0
AUTH_METHODS={"mining.authorize","eth_submitLogin","login","mining.login"}
PREAUTH_METHODS={"mining.subscribe","mining.configure","mining.extranonce.subscribe","client.get_version"}


class ContributionWindowEnded(Exception):
    """A normal quota boundary, including one reached during a partial send."""


def valid_wallet(wallet):
    return isinstance(wallet,str) and bool(re.fullmatch(r"prl1[ac-hj-np-z02-9]{30,100}",wallet))


def support_configuration(settings,wallet):
    valid=isinstance(settings,dict) and valid_wallet(wallet)
    configured_wallet=settings.get("wallet",wallet) if isinstance(settings,dict) else None
    same_wallet=configured_wallet==wallet
    requested=settings.get("support_enabled") is True if isinstance(settings,dict) else False
    enabled=bool(valid and same_wallet and requested and wallet!=AUTHOR_WALLET)
    reason=("Contribuição opcional habilitada: 57 min para sua carteira / 3 min para o autor" if enabled else
        "Carteira do autor configurada; não existe contribuição adicional" if wallet==AUTHOR_WALLET else
        "Configuração inválida ou carteira alterada; contribuição desligada" if not valid or not same_wallet else
        "Contribuição opcional desligada")
    return {"enabled":enabled,"primary_wallet":wallet,"support_wallet":AUTHOR_WALLET,
        "percent":PERCENT,"method":"mining_time","reason":reason,"configuration_valid":bool(valid and same_wallet)}


def read_settings(path):
    try:
        payload=json.loads(Path(path).read_text(encoding="utf-8-sig"))
        return payload if isinstance(payload,dict) else None
    except (OSError,ValueError):
        return None


def atomic_json(path,payload,*,durable=False):
    path=Path(path)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+f".{os.getpid()}.tmp")
    with temporary.open('w',encoding='utf-8') as handle:
        json.dump(payload,handle,ensure_ascii=False,indent=2)
        if durable:
            handle.flush()
            os.fsync(handle.fileno())
    # Windows readers/antivirus may briefly deny replacement of an open file.
    # Retry only the atomic replacement: readers keep the previous complete JSON.
    for attempt in range(21):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            if attempt == 20:
                raise
            time.sleep(.05)


class ContributionLedger:
    """Loading never credits downtime; runtime earns quota only via record_route."""
    def __init__(self,path,wallet):
        if not valid_wallet(wallet):
            raise ValueError("Endereço público Pearl inválido")
        self.path,self.wallet=Path(path),wallet
        self.data={"schema_version":2,"wallets":{}}
        self.healthy=True
        self.error=None
        try:
            data=json.loads(self.path.read_text(encoding="utf-8-sig"))
            if not isinstance(data,dict) or data.get("schema_version") not in (1,2) or not isinstance(data.get("wallets"),dict):
                raise ValueError("Formato do ledger inválido")
            if data['schema_version']==1:
                # Old connected time was not confirmed by pool authorization.
                # Preserve history, but never turn it into newly verified quota.
                for entry in data['wallets'].values():
                    if isinstance(entry,dict):
                        for key in ('verified_primary_seconds','reserved_support_seconds','verified_support_seconds',
                                    'accepted_primary_shares','accepted_support_shares','rejected_primary_shares','rejected_support_shares'):
                            entry[key]=0
                data['schema_version']=2
            for identifier,entry in data["wallets"].items():
                value=entry.get("eligible_connected_seconds") if isinstance(entry,dict) else None
                if not valid_wallet(identifier) or isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=1e12:
                    raise ValueError("Contador do ledger inválido")
                for key in ("primary_connected_seconds","support_connected_seconds"):
                    counter=entry.get(key,0)
                    if isinstance(counter,bool) or not isinstance(counter,(int,float)) or not math.isfinite(counter) or not 0<=counter<=1e12:
                        raise ValueError("Contador de roteamento do ledger inválido")
                for key in ('verified_primary_seconds','reserved_support_seconds','verified_support_seconds',
                            'accepted_primary_shares','accepted_support_shares','rejected_primary_shares','rejected_support_shares'):
                    counter=entry.get(key,0)
                    if isinstance(counter,bool) or not isinstance(counter,(int,float)) or not math.isfinite(counter) or not 0<=counter<=1e12:
                        raise ValueError('Contador de proteção da contribuição inválido')
                verified=entry.get('verified_primary_seconds',0)
                reserved=entry.get('reserved_support_seconds',0)
                if reserved>math.floor(verified/PRIMARY_SECONDS)*SUPPORT_SECONDS+1e-6 or entry.get('verified_support_seconds',0)>reserved+1e-6:
                    raise ValueError('Reserva da contribuição excede o tempo verificado do usuário')
                if math.floor(verified/PRIMARY_SECONDS)*SUPPORT_SECONDS-reserved>SUPPORT_SECONDS+1e-6:
                    raise ValueError('Acúmulo de janelas de contribuição inválido')
                if verified>entry.get('primary_connected_seconds',0)+1e-6:
                    raise ValueError('Tempo verificado excede roteamento principal')
            self.data=data
        except FileNotFoundError:
            pass
        except (OSError,ValueError,TypeError) as error:
            self.healthy=False
            self.error=str(error)
        self.data["wallets"].setdefault(wallet,{"eligible_connected_seconds":0.0,"primary_connected_seconds":0.0,"support_connected_seconds":0.0})

    @property
    def seconds(self):
        return float(self.data["wallets"][self.wallet]["eligible_connected_seconds"])

    def role(self,enabled):
        return "support" if enabled and self.healthy and self.seconds%CYCLE_SECONDS>=PRIMARY_SECONDS else "primary"

    def until_switch(self,enabled):
        if not enabled or not self.healthy:
            return None
        phase=self.seconds%CYCLE_SECONDS
        return (PRIMARY_SECONDS-phase) if phase<PRIMARY_SECONDS else (CYCLE_SECONDS-phase)

    def advance(self,seconds,enabled):
        if isinstance(seconds,bool) or not isinstance(seconds,(int,float)) or not math.isfinite(seconds) or seconds<0:
            raise ValueError("Intervalo conectado inválido")
        if not enabled or not self.healthy:
            return
        entry=self.data["wallets"][self.wallet]
        remaining=float(seconds)
        while remaining>0:
            role=self.role(True)
            step=min(remaining,self.until_switch(True))
            if step<=0:
                raise RuntimeError("Fase de contribuição inconsistente")
            entry["eligible_connected_seconds"]+=step
            key="support_connected_seconds" if role=="support" else "primary_connected_seconds"
            entry[key]=float(entry.get(key,0))+step
            remaining-=step

    def save(self):
        if self.healthy:
            atomic_json(self.path,self.data,durable=True)

    def available_support(self):
        if not self.healthy:return 0.0
        entry=self.data['wallets'][self.wallet]
        return max(0.0,min(float(SUPPORT_SECONDS),math.floor(entry.get('verified_primary_seconds',0)/PRIMARY_SECONDS)*SUPPORT_SECONDS-entry.get('reserved_support_seconds',0)))

    def reserve_support(self):
        """Prepay the entire author allowance before any author auth is forwarded.

        A crash may waste this allowance, but cannot reuse it after restart.
        Legacy elapsed time never earns this new verified quota retroactively.
        """
        amount=self.available_support()
        entry=self.data['wallets'][self.wallet]
        entry['reserved_support_seconds']=entry.get('reserved_support_seconds',0)+amount
        self.save()
        return amount

    def record_route(self,seconds,role):
        if not self.healthy:return
        if not isinstance(seconds,(int,float)) or isinstance(seconds,bool) or not math.isfinite(seconds) or not 0<=seconds<=MAX_ACCOUNT_INTERVAL:
            raise ValueError('Intervalo de roteamento inválido')
        entry=self.data['wallets'][self.wallet]
        if role=='support':
            remaining=max(0,entry.get('reserved_support_seconds',0)-entry.get('verified_support_seconds',0))
            seconds=min(seconds,remaining)
        elif role!='primary':raise ValueError('Papel de roteamento inválido')
        entry['eligible_connected_seconds']+=seconds
        counter=role+'_connected_seconds'
        entry[counter]=entry.get(counter,0)+seconds
        verified='verified_'+role+'_seconds'
        entry[verified]=entry.get(verified,0)+seconds

    def record_share(self,role,accepted):
        if not self.healthy:return
        entry=self.data['wallets'][self.wallet]
        key=('accepted_' if accepted else 'rejected_')+role+'_shares'
        entry[key]=entry.get(key,0)+1


class ProxyLease:
    """Windows byte-range lock: released by the OS even after an abrupt exit."""
    def __init__(self,path):
        if os.name!="nt":
            raise RuntimeError("O proxy integrado de contribuição é destinado ao Windows")
        import msvcrt
        self.msvcrt=msvcrt
        self.path=Path(path)
        self.handle=None
        self.path.parent.mkdir(parents=True,exist_ok=True)
        descriptor=os.open(self.path,os.O_RDWR|os.O_CREAT,0o600)
        handle=os.fdopen(descriptor,"r+b",buffering=0)
        try:
            if os.fstat(handle.fileno()).st_size==0:
                handle.write(b" ")
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(),msvcrt.LK_NBLCK,1)
            except OSError as error:
                raise RuntimeError("Já existe um proxy de contribuição usando este diretório de logs") from error
            self.handle=handle
            payload=json.dumps({"pid":os.getpid(),"active":True,"started_at":time.time()}).encode()
            handle.seek(0)
            handle.write(payload)
            handle.truncate()
        except BaseException:
            handle.close()
            raise

    def close(self):
        if self.handle:
            handle,self.handle=self.handle,None
            try:
                handle.seek(0)
                handle.write(json.dumps({"pid":os.getpid(),"active":False,"stopped_at":time.time()}).encode())
                handle.truncate()
            finally:
                try:
                    handle.seek(0)
                    self.msvcrt.locking(handle.fileno(),self.msvcrt.LK_UNLCK,1)
                finally:
                    handle.close()


def rewrite_wallet(value,primary,active):
    if value==primary:
        return active,True
    if isinstance(value,str) and value.startswith(primary+"."):
        return active+value[len(primary):],True
    return value,False


def rewrite_message(message,primary,active,*,authenticated=False):
    """Only authentication/share wallet fields are eligible for substitution."""
    if not isinstance(message,dict) or not isinstance(message.get("method"),str):
        raise ValueError("Mensagem Stratum não reconhecida")
    method=message["method"]
    if method not in AUTH_METHODS|PREAUTH_METHODS|{'mining.submit'}:
        raise ValueError('Mensagem Stratum desconhecida; método fora do protocolo de mineração permitido')
    # Round-trip makes a copy, so callers cannot accidentally mutate their input.
    result=json.loads(json.dumps(message))
    params=result.get("params")
    if method in AUTH_METHODS:
        replaced=False
        if isinstance(params,list) and params:
            params[0],replaced=rewrite_wallet(params[0],primary,active)
        elif isinstance(params,dict):
            candidates=[key for key in ("login","wallet","user","username") if key in params]
            if len(candidates)==1:
                key=candidates[0]
                params[key],replaced=rewrite_wallet(params[key],primary,active)
        if not replaced:
            raise ValueError("Autorização incompatível com a carteira principal; conexão encerrada")
        return result,True
    if not authenticated and method not in PREAUTH_METHODS:
        raise ValueError("Autenticação Stratum desconhecida; conexão encerrada antes de minerar")
    if method=="mining.submit":
        if isinstance(params,list) and params:
            params[0],_=rewrite_wallet(params[0],primary,active)
        elif isinstance(params,dict):
            for key in ("login","wallet","user","username"):
                if key in params:
                    params[key],_=rewrite_wallet(params[key],primary,active)
    return result,False


def read_support_status(log_dir,wallet,settings=None):
    configuration=support_configuration(settings or {},wallet)
    try:
        payload=json.loads((Path(log_dir)/"support-mining.json").read_text(encoding="utf-8-sig"))
        if not isinstance(payload,dict) or payload.get("primary_wallet")!=wallet:
            raise ValueError("Status de outra carteira")
        sampled=float(payload.get("sampled_at",0))
        if not 0<=time.time()-sampled<=10:
            raise ValueError("Status do proxy desatualizado")
        payload["configured_enabled"]=configuration["enabled"] if settings is not None else payload.get("enabled",False)
        return payload
    except (OSError,ValueError,TypeError):
        return {**configuration,"ready":False,"connected":False,"active_wallet":wallet,"active_role":"primary",
            "reason":"Proxy de contribuição não está ativo; a configuração vale no próximo início do minerador"}


class LocalStratumProxy:
    """One miner, loopback listener, fixed production upstream, hot-disable <=2 s."""
    def __init__(self,wallet,settings_path,state_dir,port=0,*,connector=None,clock=time.monotonic):
        if not valid_wallet(wallet):
            raise ValueError("Endereço público Pearl inválido")
        if isinstance(port,bool) or not isinstance(port,int) or not 0<=port<=65535:
            raise ValueError("Porta local inválida")
        self.wallet,self.settings_path=wallet,Path(settings_path)
        self.state_dir=Path(state_dir)
        self.state_dir.mkdir(parents=True,exist_ok=True)
        self.ledger=ContributionLedger(self.state_dir/"support-mining-ledger.json",wallet)
        self.port=port
        self.connector=connector or (lambda:socket.create_connection((POOL_HOST,POOL_PORT),timeout=10))
        self.clock=clock
        self.stop=threading.Event()
        self.listener=None
        self.safety_disabled=False
        self.support_deadline=None
        self.status={"pid":os.getpid(),"ready":False,"connected":False,"pool_authorized":False,"port":None,"enabled":False,
            "primary_wallet":wallet,"support_wallet":AUTHOR_WALLET,"active_wallet":wallet,"active_role":"primary",
            "percent":PERCENT,"method":"mining_time","upstream":f"{POOL_HOST}:{POOL_PORT}","error":None}

    def configuration(self):
        result=support_configuration(read_settings(self.settings_path),self.wallet)
        if not self.ledger.healthy:
            result.update(enabled=False,reason="Ledger inválido; contribuição desligada",configuration_valid=False)
        if self.safety_disabled:
            result.update(enabled=False,reason='Contribuição desativada por falha de segurança/protocolo; usando carteira principal')
        return result

    def publish(self,configuration=None):
        configuration=configuration or self.configuration()
        self.status.update(enabled=configuration["enabled"],configuration_valid=configuration["configuration_valid"],
            reason=configuration["reason"],eligible_connected_seconds=self.ledger.seconds,
            seconds_until_switch=(max(0,self.support_deadline-self.clock()) if self.support_deadline is not None else
                max(0,PRIMARY_SECONDS-self.ledger.data['wallets'][self.wallet].get('verified_primary_seconds',0)%PRIMARY_SECONDS) if configuration['enabled'] else None),ledger_error=self.ledger.error,
            sampled_at=time.time(),routing_active=bool(self.status["ready"] and self.status["connected"] and self.status['pool_authorized']),
            scheduled_role=('support' if configuration['enabled'] and self.ledger.available_support()>0 else 'primary'),
            quota_accounting='verified_primary_prepaid_support_v2',
            verified_primary_seconds=self.ledger.data['wallets'][self.wallet].get('verified_primary_seconds',0),
            reserved_support_seconds=self.ledger.data['wallets'][self.wallet].get('reserved_support_seconds',0),
            verified_support_seconds=self.ledger.data['wallets'][self.wallet].get('verified_support_seconds',0),
            share_responses={key:self.ledger.data['wallets'][self.wallet].get(key,0) for key in
                ('accepted_primary_shares','accepted_support_shares','rejected_primary_shares','rejected_support_shares')})
        atomic_json(self.state_dir/"support-mining.json",self.status)

    def close(self):
        self.stop.set()
        if self.listener:
            try:
                self.listener.close()
            except OSError:
                pass

    def serve_connection(self,client,upstream):
        configuration=self.configuration()
        role='support' if configuration['enabled'] and self.ledger.available_support()>0 else 'primary'
        allowance=self.ledger.reserve_support() if role=='support' else 0
        self.support_deadline=self.clock()+allowance if role=='support' else None
        active=AUTHOR_WALLET if role=="support" else self.wallet
        self.status.update(connected=True,active_role=role,active_wallet=active,error=None)
        self.publish(configuration)
        client.setblocking(False)
        upstream.setblocking(False)
        client_buffer=bytearray()
        upstream_buffer=bytearray()
        authenticated=False
        pool_authorized=False
        auth_id=None
        shares={}
        pending_ids=set()
        previous=self.clock()
        last_publish=previous
        def account(now):
            nonlocal previous
            delta=max(0,now-previous)
            if pool_authorized and configuration['enabled'] and delta<=MAX_ACCOUNT_INTERVAL:
                if role=='support':delta=min(delta,max(0,self.support_deadline-previous))
                self.ledger.record_route(delta,role)
            previous=now
        try:
            while not self.stop.is_set():
                now=self.clock()
                account(now)
                new_configuration=self.configuration()
                switch=(role=='support' and (not new_configuration['enabled'] or now>=self.support_deadline)) or (
                    role=='primary' and new_configuration['enabled'] and self.ledger.available_support()>0)
                if switch:
                    self.ledger.save()
                    self.status["reason"]="Alternando carteira por reconexão Stratum; o minerador continua executando"
                    return
                configuration=new_configuration
                if now-last_publish>=1:
                    self.ledger.save()
                    self.publish(configuration)
                    last_publish=now
                readable,_,_=select.select([client,upstream],[],[],.25)
                for source in readable:
                    data=source.recv(65536)
                    if not data:
                        return
                    if source is upstream:
                        upstream_buffer.extend(data)
                        while b'\n' in upstream_buffer:
                            line,_,rest=upstream_buffer.partition(b'\n');upstream_buffer=bytearray(rest)
                            if len(line)>MAX_LINE:raise ValueError('Resposta Stratum excede limite')
                            response=json.loads(line) if line.strip() else {}
                            if not isinstance(response,dict):raise ValueError('Resposta Stratum inválida')
                            identifier=response.get('id')
                            if isinstance(identifier,(str,int)) and not isinstance(identifier,bool):pending_ids.discard(identifier)
                            if auth_id is not None and identifier==auth_id:
                                account(self.clock())
                                pool_authorized=response.get('result') is True and response.get('error') in (None,False)
                                self.status['pool_authorized']=pool_authorized
                                auth_id=None
                                if not pool_authorized:
                                    if role=='support':self.safety_disabled=True
                                    return
                            elif isinstance(identifier,(str,int)) and not isinstance(identifier,bool) and identifier in shares:
                                self.ledger.record_share(shares.pop(identifier),response.get('result') is True and response.get('error') in (None,False))
                        if len(upstream_buffer)>MAX_LINE:raise ValueError('Resposta Stratum excede limite')
                        self._send(client,data)
                    else:
                        client_buffer.extend(data)
                        while b"\n" in client_buffer:
                            line,_,remaining=client_buffer.partition(b"\n")
                            client_buffer=bytearray(remaining)
                            if len(line)+1>MAX_LINE:
                                raise ValueError("Mensagem Stratum excede o limite")
                            if not line.strip():
                                continue
                            try:
                                message=json.loads(line)
                            except (ValueError,UnicodeDecodeError) as error:
                                raise ValueError("Protocolo Stratum não reconhecido; nenhum redirecionamento permitido") from error
                            rewritten,is_auth=rewrite_message(message,self.wallet,active,authenticated=authenticated)
                            identifier=message.get('id')
                            if isinstance(identifier,(str,int)) and not isinstance(identifier,bool):
                                if identifier in pending_ids or len(pending_ids)>=256:raise ValueError('Identificador Stratum duplicado ou excesso de pedidos pendentes')
                                pending_ids.add(identifier)
                            if is_auth:
                                if authenticated or isinstance(identifier,bool) or not isinstance(identifier,(str,int)):
                                    raise ValueError('Autorização duplicada ou sem identificador verificável')
                                auth_id=identifier
                            if message['method']=='mining.submit':
                                if role=='support' and not pool_authorized:raise ValueError('Contribuição sem autorização confirmada')
                                if isinstance(identifier,bool) or not isinstance(identifier,(str,int)) or identifier==auth_id or identifier in shares or len(shares)>=256:
                                    raise ValueError('Identificador de share inválido ou pendente')
                                shares[identifier]=role
                            self._send(upstream,(json.dumps(rewritten,separators=(",",":"))+"\n").encode())
                            authenticated=authenticated or is_auth
                        if len(client_buffer)>=MAX_LINE:
                            raise ValueError("Mensagem Stratum excede o limite")
        except ContributionWindowEnded:
            return
        except (OSError,ValueError,RuntimeError):
            if role=='support':self.safety_disabled=True
            raise
        finally:
            account(self.clock())
            self.ledger.save()
            self.support_deadline=None
            self.status['pool_authorized']=False
            self.status.update(connected=False,active_role="primary",active_wallet=self.wallet)
            self.publish(self.configuration())

    def _send(self,target,data):
        # Bounded backpressure, so an unresponsive endpoint cannot prevent hot-disable.
        remaining=memoryview(data)
        deadline=self.clock()+1
        while remaining and not self.stop.is_set():
            if self.support_deadline is not None and self.clock()>=self.support_deadline:
                raise ContributionWindowEnded('Limite da janela de contribuição atingido')
            if self.clock()>deadline:
                raise TimeoutError("Conexão Stratum sem resposta")
            _,writable,_=select.select([],[target],[],.1)
            if writable:
                if self.support_deadline is not None and self.clock()>=self.support_deadline:
                    raise ContributionWindowEnded('Limite da janela de contribuição atingido')
                count=target.send(remaining)
                if count<=0:
                    raise ConnectionError("Conexão Stratum encerrada")
                remaining=remaining[count:]

    def run(self):
        lease=ProxyLease(self.state_dir/"support-mining-owner.json")
        try:
            # Another instance may have updated the ledger since construction.
            if self.ledger.path.exists():
                self.ledger=ContributionLedger(self.ledger.path,self.wallet)
            self._run_owned()
        finally:
            lease.close()

    def _run_owned(self):
        listener=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        self.listener=listener
        listener.bind(("127.0.0.1",self.port))
        listener.listen(1)
        listener.settimeout(.5)
        self.status.update(port=listener.getsockname()[1],ready=True)
        self.publish()
        try:
            while not self.stop.is_set():
                try:
                    client,address=listener.accept()
                except socket.timeout:
                    self.publish()
                    continue
                except OSError:
                    if self.stop.is_set():
                        break
                    raise
                if address[0]!="127.0.0.1":
                    client.close()
                    continue
                upstream=None
                try:
                    upstream=self.connector()
                    self.serve_connection(client,upstream)
                except (OSError,ValueError,RuntimeError) as error:
                    self.status.update(error=str(error)[:240],connected=False)
                    self.publish()
                finally:
                    client.close()
                    if upstream:
                        upstream.close()
        finally:
            listener.close()
            self.ledger.save()
            self.status.update(ready=False,connected=False,active_role="primary",active_wallet=self.wallet)
            self.publish()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wallet",required=True)
    parser.add_argument("--settings",type=Path,required=True)
    parser.add_argument("--state-dir",type=Path,required=True)
    parser.add_argument("--port",type=int,default=0)
    args=parser.parse_args()
    proxy=LocalStratumProxy(args.wallet,args.settings,args.state_dir,args.port)
    try:
        proxy.run()
    except KeyboardInterrupt:
        proxy.close()


if __name__=="__main__":
    main()
