"""Gerenciador Windows de até seis mineradores NVIDIA independentes por UUID.

Só inicia mineração quando --serve é chamado explicitamente. Registro persistente
e identidade de processo impedem que um PID reutilizado seja encerrado por engano.
"""
from __future__ import annotations
import argparse
import ctypes
from ctypes import wintypes
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

from contribution import ProxyLease,atomic_json,support_configuration
from gpu_agent import detect_nvidia_gpus
from rig_config import MAX_GPUS,device_key,resolve_devices,update_gpu_settings

MARKER="HashPilotRig-v1"
REFERENCE_RTX3070_UUID="GPU-bafb0ad5-dd9e-ee1d-a0c0-221c0dfbfc75"
NO_WINDOW=getattr(subprocess,"CREATE_NO_WINDOW",0)


def reference_device_confirmed(gpu):
    return (gpu.get("uuid")==REFERENCE_RTX3070_UUID and str(gpu.get("name","")).endswith("RTX 3070")
        and gpu.get("power_default_w")==240 and 1740 in gpu.get("supported_graphics_clocks_mhz",[]))


def parse_miner_device_map(text,devices):
    """Match SRBMiner IDs to NVIDIA UUIDs by unique PCI bus, never model name."""
    buses={}
    for line in text.splitlines():
        index=re.search(r"\bGPU\s*(\d+)\s*[:\[]",line,re.I)
        bus=re.search(r"\bBUS\s*:\s*([0-9a-f]{1,2})\b",line,re.I)
        if index and bus and "nvidia" in line.lower():
            buses.setdefault(int(bus[1],16),set()).add(int(index[1]))
    result={}
    seen_buses=set()
    for gpu in devices:
        pci=re.fullmatch(r"[0-9a-fA-F]{4,8}:([0-9a-fA-F]{2}):[0-9a-fA-F]{2}\.[0-7]",str(gpu.get("pci_bus_id","")))
        if not pci: raise RuntimeError("Endereço PCI da NVIDIA ausente; seleção do minerador não confirmada")
        bus=int(pci[1],16)
        candidates=buses.get(bus,set())
        if bus in seen_buses or len(candidates)!=1:
            raise RuntimeError("Mapeamento PCI/ID do SRBMiner ambíguo ou ausente; mineração não iniciada")
        seen_buses.add(bus)
        result[gpu["uuid"]]=next(iter(candidates))
    if len(set(result.values()))!=len(result): raise RuntimeError("Um ID do minerador correspondeu a mais de uma GPU")
    return result


def normalized_path(value):
    return os.path.normcase(os.path.abspath(str(value)))


def windows_argv(command_line):
    shell=ctypes.WinDLL("shell32",use_last_error=True)
    shell.CommandLineToArgvW.argtypes=[wintypes.LPCWSTR,ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype=ctypes.POINTER(wintypes.LPWSTR)
    count=ctypes.c_int()
    pointer=shell.CommandLineToArgvW(command_line,ctypes.byref(count))
    if not pointer: return []
    try: return [pointer[index] for index in range(count.value)]
    finally:
        kernel=ctypes.WinDLL("kernel32")
        kernel.LocalFree.argtypes=[ctypes.c_void_p]
        kernel.LocalFree(pointer)


class ProcessBackend:
    def __init__(self):
        self.children={}

    @staticmethod
    def _kernel():
        if os.name!="nt": raise RuntimeError("O gerenciador de mineração requer Windows")
        kernel=ctypes.WinDLL("kernel32",use_last_error=True)
        kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        kernel.OpenProcess.restype=wintypes.HANDLE
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        kernel.GetProcessTimes.argtypes=[wintypes.HANDLE,*([ctypes.POINTER(wintypes.FILETIME)]*4)]
        kernel.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
        kernel.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
        kernel.TerminateProcess.argtypes=[wintypes.HANDLE,wintypes.UINT]
        return kernel

    @staticmethod
    def _metadata(kernel,handle,pid):
        code=wintypes.DWORD()
        if not kernel.GetExitCodeProcess(handle,ctypes.byref(code)) or code.value!=259: return None
        creation,exit_time,kernel_time,user_time=(wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(handle,*map(ctypes.byref,(creation,exit_time,kernel_time,user_time))): return None
        size=wintypes.DWORD(32768)
        image=ctypes.create_unicode_buffer(size.value)
        if not kernel.QueryFullProcessImageNameW(handle,0,image,ctypes.byref(size)): return None
        return {"pid":pid,"created_at":str((creation.dwHighDateTime<<32)|creation.dwLowDateTime),"executable":image.value}

    def inspect(self,pid,*,command_line=False):
        if isinstance(pid,bool) or not isinstance(pid,int) or pid<=0: return None
        kernel=self._kernel()
        handle=kernel.OpenProcess(0x1000,False,pid)
        if not handle: return None
        try: metadata=self._metadata(kernel,handle,pid)
        finally: kernel.CloseHandle(handle)
        if metadata and command_line:
            command=f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}'; if($p){{$p.CommandLine}}"
            command="[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); "+command
            result=subprocess.run(["powershell.exe","-NoProfile","-Command",command],capture_output=True,text=True,
                encoding="utf-8",errors="replace",timeout=8,creationflags=NO_WINDOW)
            if result.returncode: return None
            metadata["argv"]=windows_argv(result.stdout.strip())
        return metadata

    def start(self,arguments,working_dir,log_path=None):
        args=list(map(str,arguments))
        output=None
        try:
            if log_path:
                Path(log_path).parent.mkdir(parents=True,exist_ok=True)
                output=open(log_path,"ab")
            child=subprocess.Popen(args,cwd=str(working_dir),stdin=subprocess.DEVNULL,
                stdout=output or subprocess.DEVNULL,stderr=output or subprocess.DEVNULL,creationflags=NO_WINDOW)
        finally:
            if output: output.close()
        metadata=self.inspect(child.pid)
        if not metadata:
            if child.poll() is None: child.terminate()
            raise RuntimeError("Processo encerrou durante a inicialização")
        metadata.update(argv=args,launch_id=uuid.uuid4().hex)
        self.children[child.pid]=(child,metadata)
        return metadata

    def is_owned(self,identity):
        if not isinstance(identity,dict) or not isinstance(identity.get("argv"),list) or not identity.get("launch_id"):
            return False
        pid=identity.get("pid")
        metadata=self.inspect(pid)
        if not metadata or metadata["created_at"]!=identity.get("created_at") or normalized_path(metadata["executable"])!=normalized_path(identity.get("executable","")):
            return False
        launched=self.children.get(pid)
        if launched and launched[1].get("launch_id")==identity["launch_id"]:
            return launched[0].poll() is None
        actual=self.inspect(pid,command_line=True)
        if not actual: return False
        expected=list(map(str,identity["argv"]))
        observed=actual.get("argv",[])
        return bool(observed and expected and normalized_path(observed[0])==normalized_path(expected[0]) and observed[1:]==expected[1:])

    def stop(self,identity):
        if not self.is_owned(identity): return False
        launched=self.children.get(identity["pid"])
        if launched and launched[1].get("launch_id")==identity.get("launch_id"):
            launched[0].terminate()
            return True
        kernel=self._kernel()
        handle=kernel.OpenProcess(0x1001,False,identity["pid"])
        if not handle: return False
        try:
            current=self._metadata(kernel,handle,identity["pid"])
            if not current or current["created_at"]!=identity.get("created_at") or normalized_path(current["executable"])!=normalized_path(identity["executable"]):
                return False
            return bool(kernel.TerminateProcess(handle,1))
        finally: kernel.CloseHandle(handle)

    def wait(self,identity,timeout=12):
        deadline=time.monotonic()+timeout
        while True:
            current=self.inspect(identity.get("pid"))
            if not current or current.get("created_at")!=identity.get("created_at") or normalized_path(current.get("executable",""))!=normalized_path(identity.get("executable","")):
                return True
            if time.monotonic()>=deadline: return False
            time.sleep(.1)

    def legacy_candidates(self,miner_path):
        name=Path(miner_path).name.replace("'","''")
        command=f"@(Get-CimInstance Win32_Process -Filter \"Name = '{name}' OR Name = 'python.exe' OR Name = 'pythonw.exe' OR Name = 'powershell.exe' OR Name = 'pwsh.exe'\" | Select-Object ProcessId,CommandLine) | ConvertTo-Json -Compress"
        command="[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); "+command
        result=subprocess.run(["powershell.exe","-NoProfile","-Command",command],capture_output=True,text=True,
            encoding="utf-8",errors="replace",timeout=10,creationflags=NO_WINDOW)
        if result.returncode: raise RuntimeError("Não foi possível identificar com segurança os processos legados")
        payload=json.loads(result.stdout or "[]")
        if isinstance(payload,dict): payload=[payload]
        found=[]
        for item in payload:
            metadata=self.inspect(item.get("ProcessId"))
            if metadata:
                metadata.update(argv=windows_argv(item.get("CommandLine") or ""),launch_id="legacy-"+uuid.uuid4().hex)
                found.append(metadata)
        return found

    def miner_device_map(self,miner_path,devices):
        result=subprocess.run([str(miner_path),"--list-devices","--disable-gpu-amd","--disable-gpu-intel"],
            cwd=str(Path(miner_path).parent),capture_output=True,text=True,encoding="utf-8",errors="replace",
            timeout=30,creationflags=NO_WINDOW)
        if result.returncode: raise RuntimeError("SRBMiner não confirmou sua lista de dispositivos; mineração não iniciada")
        return parse_miner_device_map(result.stdout+"\n"+result.stderr,devices)


def write_request(state_dir,action,gpu_uuid=None,**fields):
    if action not in ("reconcile","start","stop","benchmark","shutdown"):
        raise ValueError("Ação do rig inválida")
    if gpu_uuid is not None: device_key(gpu_uuid)
    payload={**fields,"id":uuid.uuid4().hex,"action":action,"gpu_uuid":gpu_uuid,"at":time.time()}
    destination=Path(state_dir)/"rig-requests"/(payload["id"]+".json")
    atomic_json(destination,payload)
    return payload


class RigManager:
    def __init__(self,state_dir,log_dir,settings_path,miner_path,python_path=None,*,backend=None,detector=None,clock=time.time,proxy_ready=None):
        self.state_dir,self.log_dir=Path(state_dir),Path(log_dir)
        self.settings_path,self.miner_path=Path(settings_path),Path(miner_path)
        self.python_path=Path(python_path or sys.executable)
        self.project_dir=Path(__file__).resolve().parent
        self.backend=backend or ProcessBackend()
        self.detector=detector or detect_nvidia_gpus
        self.clock=clock
        self.proxy_ready=proxy_ready or self._wait_proxy
        self.registry_path=self.state_dir/"rig-manager.json"
        self.devices={}
        self.registry_valid=True
        self.error=None
        self.ignored=[]
        self.processed_ids=[]
        self.shutting_down=False
        self.reference_uuid=None
        self.miner_map={}
        self.mapping_signature=None
        self._load_registry()

    def _load_registry(self):
        try:
            payload=json.loads(self.registry_path.read_text(encoding="utf-8-sig"))
            if payload.get("schema_version")!=1 or payload.get("marker")!=MARKER or not isinstance(payload.get("devices"),dict) or len(payload["devices"])>MAX_GPUS:
                raise ValueError("Registro de rig inválido")
            for identifier,entry in payload["devices"].items():
                device_key(identifier)
                if not isinstance(entry,dict) or entry.get("gpu_uuid")!=identifier:
                    raise ValueError("Identidade do registro de rig inválida")
                for role in ("miner","controller","proxy"):
                    identity=entry.get(role)
                    if identity is not None and (not isinstance(identity,dict) or not isinstance(identity.get("pid"),int)
                        or not isinstance(identity.get("created_at"),str) or not identity["created_at"].isdigit()
                        or not isinstance(identity.get("executable"),str) or not isinstance(identity.get("argv"),list)
                        or not identity["argv"] or not identity.get("launch_id")):
                        raise ValueError("Metadados de processo incompletos no registro")
                self.devices[identifier]=entry
            self.processed_ids=list(payload.get("processed_request_ids",[]))[-128:]
            if payload.get("reference_uuid")==REFERENCE_RTX3070_UUID:
                self.reference_uuid=REFERENCE_RTX3070_UUID
        except FileNotFoundError: pass
        except (OSError,ValueError,TypeError,AttributeError) as error:
            self.devices={}
            self.registry_valid=False
            self.error="Registro inválido: nenhuma ação em processos existentes foi autorizada. "+str(error)

    def settings(self):
        payload=json.loads(self.settings_path.read_text(encoding="utf-8-sig"))
        if not isinstance(payload,dict): raise ValueError("settings.json inválido")
        return payload

    def publish(self):
        if not self.registry_valid: return
        atomic_json(self.registry_path,{"schema_version":1,"marker":MARKER,"pid":os.getpid(),"sampled_at":self.clock(),
            "ready":not self.shutting_down,"max_gpus":MAX_GPUS,"devices":self.devices,"ignored_devices":self.ignored,
            "error":self.error,"processed_request_ids":self.processed_ids[-128:],"reference_uuid":self.reference_uuid})

    def _wait_proxy(self,entry,identity,timeout=15):
        deadline=time.monotonic()+timeout
        path=Path(entry["log_dir"])/"support-mining.json"
        while time.monotonic()<deadline:
            if not self.backend.is_owned(identity): raise RuntimeError("Proxy encerrou antes de ficar pronto")
            try:
                status=json.loads(path.read_text(encoding="utf-8-sig"))
                if status.get("ready") is True and status.get("pid")==identity["pid"] and 0<=self.clock()-float(status.get("sampled_at",0))<=10:
                    port=status.get("port")
                    if isinstance(port,int) and 1<=port<=65535: return port
            except (OSError,ValueError,TypeError): pass
            time.sleep(.1)
        raise RuntimeError("Proxy não ficou pronto no prazo")

    def _sync_proxy_settings(self,entry,settings):
        payload={"wallet":entry["wallet"],"support_enabled":settings.get("support_enabled") is True}
        path=Path(entry["log_dir"])/"proxy-settings.json"
        try: current=json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError,ValueError): current=None
        if current!=payload: atomic_json(path,payload)
        return payload,path

    def start_gpu(self,device,settings):
        identifier=device["uuid"]
        previous=self.devices.get(identifier,{})
        if previous.get("external_readonly") or previous.get("ownership_unverified"):
            return False
        if previous.get("miner") and self.backend.is_owned(previous["miner"]):
            return False
        entry={"gpu_uuid":identifier,"identity":dict(device),"index":device["index"],"name":device["name"],
            "wallet":device["wallet"],"desired_wallet":device["wallet"],"enabled":device["enabled"],
            "log_dir":device["log_dir"],"state":"starting","managed":True,"external_readonly":False,
            "miner":None,"controller":None,"proxy":None,"miner_pid":None,"controller_pid":None,"proxy_pid":None,
            "error":None,"started_at":self.clock(),"pool":"prl.kryptex.network:7048"}
        entry["miner_index"]=device.get("miner_index",device["index"])
        self.devices[identifier]=entry
        directory=Path(entry["log_dir"])
        directory.mkdir(parents=True,exist_ok=True)
        entry["miner_log"]=str(directory/("mining-"+time.strftime("%Y%m%d-%H%M%S")+"-"+uuid.uuid4().hex[:6]+".log"))
        try:
            proxy_settings,proxy_path=self._sync_proxy_settings(entry,settings)
            if support_configuration(proxy_settings,entry["wallet"])["enabled"]:
                identity=self.backend.start([self.python_path,self.project_dir/"contribution.py","--wallet",entry["wallet"],
                    "--settings",proxy_path,"--state-dir",directory,"--port","0"],self.project_dir,directory/"proxy-console.log")
                entry.update(proxy=identity,proxy_pid=identity["pid"])
                port=self.proxy_ready(entry,identity)
                entry["pool"]=f"127.0.0.1:{port}"
            miner_args=[self.miner_path,"--disable-cpu","--disable-msr-tweaks","--disable-gpu-amd","--disable-gpu-intel","--algorithm-gpu","pearlhash",
                "--gpu-id",str(entry["miner_index"]),"--pool",entry["pool"],"--wallet",entry["wallet"],
                "--worker",device["worker_name"],"--gpu-off-temperature","83","--log-file",entry["miner_log"]]
            if identifier==self.reference_uuid:
                if not reference_device_confirmed(device):
                    raise RuntimeError("Unidade ou limites atuais da RTX 3070 de referência não confirmados; perfil não aplicado")
                miner_args.extend(["--gpu-cclock0","1740","--gpu-coffset0","150","--gpu-mclock0","5001",
                    "--gpu-fan0","100","--gpu-plimit0","240"])
                entry["startup_profile"]="Opt-in da RTX 3070 de referência: core1740/offset150/mem5001/fan100/PL240"
            identity=self.backend.start(miner_args,self.miner_path.parent,directory/"miner-console.log")
            entry.update(miner=identity,miner_pid=identity["pid"])
            controller_args=[self.python_path,self.project_dir/"gpu_agent.py","--control","--miner-pid",str(identity["pid"]),
                "--uuid",identifier,"--index",str(device["index"]),"--duration","300","--state-dir",directory,
                "--monitor-state-dir",directory,"--log-dir",directory,"--miner-log",entry["miner_log"],"--miner-index",str(entry["miner_index"])]
            if settings.get("ai_enabled") is True: controller_args.append("--ollama")
            identity=self.backend.start(controller_args,self.project_dir,directory/"controller-console.log")
            entry.update(controller=identity,controller_pid=identity["pid"],state="running",miner_running=True)
            self.publish()
            return True
        except (OSError,RuntimeError,ValueError,subprocess.SubprocessError) as error:
            self.stop_gpu(identifier)
            entry.update(state="error",error=str(error)[:300],retry_after=self.clock()+60)
            self.publish()
            return False

    def stop_gpu(self,identifier):
        entry=self.devices.get(identifier)
        if not entry: return True
        if entry.get("external_readonly"):
            entry["error"]="Mineração externa preservada; migração explícita necessária antes de controlar esta GPU"
            return False
        for role in ("miner","controller","proxy"):
            identity=entry.get(role)
            if identity and not self.backend.is_owned(identity):
                current=self.backend.inspect(identity.get("pid"))
                if current and current.get("created_at")==identity.get("created_at"):
                    entry.update(ownership_unverified=True,state="ownership_unverified",
                        error="Processo vivo com propriedade não confirmada; ação bloqueada para evitar duplicação ou encerramento indevido")
                    return False
        controller=entry.get("controller")
        if controller and self.backend.is_owned(controller):
            atomic_json(Path(entry["log_dir"])/"gpu-agent-stop.json",{"gpu_uuid":identifier,"pid":controller["pid"],
                "at":self.clock(),"reason":"rig_stop"})
        miner=entry.get("miner")
        if miner and self.backend.is_owned(miner): self.backend.stop(miner)
        if controller and self.backend.is_owned(controller):
            if not self.backend.wait(controller,timeout=12):
                self.backend.stop(controller)
                entry["restore_warning"]="Supervisor não confirmou a restauração no prazo; verificar clocks no driver"
        proxy=entry.get("proxy")
        if proxy and self.backend.is_owned(proxy): self.backend.stop(proxy)
        entry.update(state="stopped",miner_running=False,miner=None,controller=None,proxy=None,
            miner_pid=None,controller_pid=None,proxy_pid=None,stopped_at=self.clock())
        return True

    def reconcile(self,detected=None,settings=None):
        if not self.registry_valid:
            raise RuntimeError(self.error)
        settings=self.settings() if settings is None else settings
        detected=list(self.detector() if detected is None else detected)
        desired=resolve_devices(detected,settings,self.log_dir)
        signature=tuple(sorted((gpu["uuid"],gpu["index"],gpu.get("pci_bus_id")) for gpu in desired))
        if signature!=self.mapping_signature:
            # One NVIDIA, no integrated/other vendors enabled: SRBMiner GPU0 is
            # the known existing single-board route. Rigs require explicit PCI proof.
            if not desired:
                self.miner_map={}
            elif len(desired)==1 and desired[0]["index"]==0:
                self.miner_map={desired[0]["uuid"]:0}
            else:
                self.miner_map=self.backend.miner_device_map(self.miner_path,desired)
            self.mapping_signature=signature
        for device in desired:
            if device["uuid"] not in self.miner_map: raise RuntimeError("ID do SRBMiner não confirmado para esta GPU")
            device["miner_index"]=self.miner_map[device["uuid"]]
        wanted={device["uuid"]:device for device in desired}
        self.ignored=[{"uuid":gpu.get("uuid"),"name":gpu.get("name"),"reason":"Limite de seis GPUs NVIDIA"}
            for gpu in detected if gpu.get("vendor")=="NVIDIA" and gpu.get("uuid") not in wanted]
        for identifier,entry in list(self.devices.items()):
            device=wanted.get(identifier)
            if not device:
                if entry.get("external_readonly") and not self.backend.is_owned(entry.get("miner")):
                    entry.update(external_readonly=False,managed=True)
                if self.stop_gpu(identifier):
                    entry["state"]="disconnected"
                    self.state_dir.mkdir(parents=True,exist_ok=True)
                    with (self.state_dir/"rig-device-history.jsonl").open("a",encoding="utf-8") as history:
                        history.write(json.dumps({"at":self.clock(),"event":"gpu_disconnected","device":entry},ensure_ascii=False)+"\n")
                    del self.devices[identifier]
                continue
            entry.update(enabled=device["enabled"],desired_wallet=device["wallet"],identity=dict(device))
            if entry.get("external_readonly"):
                entry["miner_running"]=bool(entry.get("miner") and self.backend.is_owned(entry["miner"]))
                if entry["miner_running"]:
                    entry["state"]="external_readonly"
                    continue
                entry.update(external_readonly=False,managed=True,state="stopped",miner=None,controller=None)
            changed=entry.get("wallet")!=device["wallet"] or entry.get("index")!=device["index"] or entry.get("miner_index",entry.get("index"))!=device["miner_index"]
            healthy=bool(entry.get("miner") and entry.get("controller") and self.backend.is_owned(entry["miner"]) and self.backend.is_owned(entry["controller"])
                and (not entry.get("proxy") or self.backend.is_owned(entry["proxy"])))
            if not device["enabled"] or changed or entry.get("state")=="running" and not healthy:
                if not self.stop_gpu(identifier): continue
                if device["enabled"] and not changed and not healthy:
                    entry.update(state="error",error="Um processo desta GPU encerrou; demais GPUs preservadas",retry_after=self.clock()+60)
            self._sync_proxy_settings(entry,settings)
            entry["support_pending_restart"]=settings.get("support_enabled") is True and not entry.get("proxy") and support_configuration({"wallet":device["wallet"],"support_enabled":True},device["wallet"])["enabled"]
        for device in desired:
            entry=self.devices.get(device["uuid"],{})
            if not entry and len(self.devices)>=MAX_GPUS:
                self.ignored.append({"uuid":device["uuid"],"name":device["name"],"reason":"Seis registros ocupados; uma sessão externa ou não confirmada foi preservada"})
                continue
            if device["enabled"] and not entry.get("external_readonly") and not entry.get("ownership_unverified") and entry.get("state")!="running" and self.clock()>=entry.get("retry_after",0):
                self.start_gpu(device,settings)
            elif not entry:
                self.devices[device["uuid"]]={"gpu_uuid":device["uuid"],"identity":device,"index":device["index"],"name":device["name"],
                    "wallet":device["wallet"],"enabled":False,"state":"disabled","managed":True,"log_dir":device["log_dir"],"miner_running":False}
        self.publish()
        return desired

    def adopt_legacy(self,detected=None):
        devices=resolve_devices(self.detector() if detected is None else detected,self.settings(),self.log_dir)
        device=next((item for item in devices if item["index"]==0),None)
        if not device: return False
        previous=self.devices.get(device["uuid"],{})
        if previous.get("miner") and self.backend.is_owned(previous["miner"]): return False
        candidates=self.backend.legacy_candidates(self.miner_path)
        miner=None
        for candidate in candidates:
            argv=candidate.get("argv",[])
            if normalized_path(candidate.get("executable",""))==normalized_path(self.miner_path) and "--gpu-id" in argv:
                position=argv.index("--gpu-id")
                if position+1<len(argv) and argv[position+1]=="0": miner=candidate;break
        if not miner: return False
        argv=miner["argv"]
        wallet=argv[argv.index("--wallet")+1] if "--wallet" in argv else device["wallet"]
        log=argv[argv.index("--log-file")+1] if "--log-file" in argv else None
        controller=next((item for item in candidates if any(normalized_path(arg)==normalized_path(self.log_dir.parent/"thermal_controller.py") for arg in item.get("argv",[]))
            and "--miner-pid" in item["argv"] and str(miner["pid"]) in item["argv"]),None)
        supervisor=next((item for item in candidates if any(normalized_path(arg)==normalized_path(self.log_dir.parent/"Iniciar-Pearl.ps1") for arg in item.get("argv",[]))
            and Path(item.get("executable","")).name.lower() in ("powershell.exe","pwsh.exe")),None)
        self.devices[device["uuid"]]={"gpu_uuid":device["uuid"],"identity":device,"index":0,"name":device["name"],"wallet":wallet,
            "desired_wallet":device["wallet"],"enabled":device["enabled"],"log_dir":device["log_dir"],"miner_log":log,
            "state":"external_readonly","managed":False,"external_readonly":True,"miner":miner,"miner_pid":miner["pid"],
            "controller":controller,"controller_pid":controller["pid"] if controller else None,"proxy":None,"proxy_pid":None,
            "miner_running":True,"error":None,"legacy_log_dir":str(self.log_dir),"legacy_supervisor":supervisor}
        self.publish()
        return True

    def migrate_legacy(self,expected_uuid,*,reference_rtx3070=False):
        """Explicit, verified migration only; never called by ordinary monitoring."""
        device_key(expected_uuid)
        detected=list(self.detector())
        current=next((gpu for gpu in detected if gpu.get("uuid")==expected_uuid and gpu.get("index")==0),None)
        if not current: raise RuntimeError("UUID esperado não corresponde à GPU 0 atual; migração bloqueada")
        entry=self.devices.get(expected_uuid)
        if not entry or not entry.get("external_readonly"):
            self.adopt_legacy(detected)
            entry=self.devices.get(expected_uuid)
        if not entry or not entry.get("external_readonly"):
            raise RuntimeError("Mineração legada correspondente não foi identificada")
        miner,controller,supervisor=(entry.get(key) for key in ("miner","controller","legacy_supervisor"))
        if not miner or not controller or not supervisor:
            raise RuntimeError("Minerador, controlador e supervisor legado precisam ser identificados antes da migração")
        if any(not self.backend.is_owned(identity) for identity in (miner,controller,supervisor)):
            raise RuntimeError("Identidade dos processos legados mudou; nenhuma migração executada")
        if normalized_path(miner["executable"])!=normalized_path(self.miner_path):
            raise RuntimeError("Executável legado fora do caminho autorizado")
        if reference_rtx3070 and not reference_device_confirmed(current):
            raise RuntimeError("Perfil de referência só pode ser usado na unidade RTX 3070 explicitamente validada")
        entry.update(state="migrating_legacy",migration_started_at=self.clock())
        self.publish()
        if not self.backend.stop(miner): raise RuntimeError("Minerador legado não pôde ser encerrado com segurança")
        if not self.backend.wait(controller,timeout=15):
            entry.update(state="migration_failed",error="Controlador legado ainda ativo; novo minerador não iniciado")
            self.publish()
            raise RuntimeError(entry["error"])
        # The old PowerShell finally block may run an 8-second GPU reset process.
        # Waiting only for Python would allow that reset to affect the new miner.
        if not self.backend.wait(supervisor,timeout=35):
            entry.update(state="migration_failed",error="Restauração do supervisor legado não confirmou término; novo minerador não iniciado")
            self.publish()
            raise RuntimeError(entry["error"])
        if reference_rtx3070: self.reference_uuid=expected_uuid
        entry.update(state="stopped",external_readonly=False,managed=True,miner=None,controller=None,legacy_supervisor=None,
            miner_pid=None,controller_pid=None,miner_running=False,migration_completed_at=self.clock(),error=None)
        self.publish()
        return True

    def process_requests(self):
        queue_dir=self.state_dir/"rig-requests"
        paths=list(queue_dir.glob("*.json"))
        compatibility=self.state_dir/"rig-request.json"
        if compatibility.is_file(): paths.append(compatibility)
        def queued_order(path):
            try:
                payload=json.loads(path.read_text(encoding="utf-8-sig"))
                stamp=float(payload.get("at",0))
                return (stamp if math.isfinite(stamp) else math.inf,path.stat().st_mtime_ns,path.name)
            except (OSError,ValueError,TypeError,AttributeError):
                return (math.inf,0,path.name)
        paths.sort(key=queued_order)
        for path in paths:
            try:
                request=json.loads(path.read_text(encoding="utf-8-sig"))
                identifier=request.get("id")
                if identifier in self.processed_ids: continue
                if not isinstance(identifier,str) or not 0<=self.clock()-float(request.get("at",0))<=120:
                    raise ValueError("Pedido de rig antigo ou inválido")
                action,gpu_uuid=request.get("action"),request.get("gpu_uuid")
                if gpu_uuid is not None: device_key(gpu_uuid)
                if action in ("start","stop"):
                    if gpu_uuid is None: raise ValueError("A ação exige UUID")
                    settings=update_gpu_settings(self.settings(),gpu_uuid,enabled=action=="start")
                    atomic_json(self.settings_path,settings)
                    if action=="stop": self.stop_gpu(gpu_uuid)
                elif action=="benchmark":
                    targets=[gpu_uuid] if gpu_uuid else list(self.devices)
                    for target in targets:
                        entry=self.devices.get(target)
                        if entry and not entry.get("external_readonly"):
                            atomic_json(Path(entry["log_dir"])/"gpu-agent-request.json",{"action":"benchmark","gpu_uuid":target,
                                "objective":request.get("objective","performance"),"duration_seconds":300,"at":self.clock()})
                elif action=="shutdown": self.shutting_down=True
                elif action!="reconcile": raise ValueError("Ação desconhecida")
                self.processed_ids.append(identifier)
            except (OSError,ValueError,TypeError) as error:
                self.error=str(error)[:300]
            finally:
                if path.parent==queue_dir and path.is_file():
                    archive=queue_dir/"processed"/path.name
                    archive.parent.mkdir(parents=True,exist_ok=True)
                    path.replace(archive)

    def serve(self,adopt_legacy=False,*,migrate_legacy_uuid=None,reference_rtx3070=False,reference_uuid=None):
        lease=ProxyLease(self.state_dir/"rig-manager-owner.json")
        try:
            if reference_rtx3070 and not migrate_legacy_uuid:
                current=next((gpu for gpu in self.detector() if gpu.get("uuid")==reference_uuid),None)
                if not current or not reference_device_confirmed(current):
                    raise RuntimeError("UUID ou limites da GPU de referência não confirmados")
                self.reference_uuid=reference_uuid
            if adopt_legacy: self.adopt_legacy()
            if migrate_legacy_uuid:
                self.migrate_legacy(migrate_legacy_uuid,reference_rtx3070=reference_rtx3070)
            detected=None
            last_detection=0
            while not self.shutting_down:
                started=time.monotonic()
                try:
                    self.process_requests()
                    if self.shutting_down: break
                    if detected is None or time.monotonic()-last_detection>=30:
                        detected=list(self.detector())
                        last_detection=time.monotonic()
                    devices=self.reconcile(detected=detected)
                    from rig_controller import collect_snapshots
                    collect_snapshots(devices,self.devices)
                except (OSError,RuntimeError,ValueError,subprocess.SubprocessError) as error:
                    self.error=str(error)[:300]
                    self.publish()
                time.sleep(max(.1,2-(time.monotonic()-started)))
        finally:
            self.shutting_down=True
            try:
                for identifier in list(self.devices):
                    try: self.stop_gpu(identifier)
                    except (OSError,RuntimeError,subprocess.SubprocessError) as error:
                        self.devices[identifier]["error"]=str(error)[:300]
                self.publish()
            finally: lease.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serve",action="store_true")
    parser.add_argument("--request",choices=("reconcile","start","stop","benchmark","shutdown"))
    parser.add_argument("--gpu-uuid")
    parser.add_argument("--objective",choices=("performance","efficiency"),default="performance")
    parser.add_argument("--state-dir",type=Path,required=True)
    parser.add_argument("--log-dir",type=Path)
    parser.add_argument("--settings",type=Path)
    parser.add_argument("--miner-path",type=Path)
    parser.add_argument("--python",type=Path,default=Path(sys.executable))
    parser.add_argument("--adopt-legacy",action="store_true")
    parser.add_argument("--migrate-legacy",action="store_true")
    parser.add_argument("--legacy-uuid")
    parser.add_argument("--reference-rtx3070",action="store_true")
    args=parser.parse_args()
    if args.request:
        if args.request in ("start","stop") and not args.gpu_uuid:
            parser.error("--request start/stop exige --gpu-uuid")
        print(json.dumps(write_request(args.state_dir,args.request,args.gpu_uuid,objective=args.objective)))
        return
    if not args.serve: parser.error("Use --serve para iniciar o gerenciamento explícito de mineração")
    if None in (args.log_dir,args.settings,args.miner_path): parser.error("--serve exige --log-dir, --settings e --miner-path")
    if args.migrate_legacy and not args.legacy_uuid: parser.error("--migrate-legacy exige --legacy-uuid explícito")
    if args.reference_rtx3070 and not args.legacy_uuid: parser.error("Reference profile requires explicit --legacy-uuid")
    if os.name!="nt" or not ctypes.WinDLL("shell32").IsUserAnAdmin():
        parser.error("O serviço de mineração precisa ser iniciado como administrador no Windows")
    RigManager(args.state_dir,args.log_dir,args.settings,args.miner_path,args.python).serve(args.adopt_legacy,
        migrate_legacy_uuid=args.legacy_uuid if args.migrate_legacy else None,reference_rtx3070=args.reference_rtx3070,reference_uuid=args.legacy_uuid)


if __name__=="__main__": main()
