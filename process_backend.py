"""Processos do Windows para o serviço do rig: criação, identidade e encerramento seguro.

Só encerra processos criados por este gerenciador; a identidade (PID, horário de
criação, executável) protege contra PID reutilizado e processos alheios.
"""
from __future__ import annotations
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

from miner_log import parse_miner_device_map

NO_WINDOW=getattr(subprocess,"CREATE_NO_WINDOW",0)


def normalized_path(value):
    return os.path.normcase(os.path.abspath(str(value)))


def windows_argv(command_line):
    # CommandLineToArgvW("") returns this Python executable, not an empty list.
    if not isinstance(command_line,str) or not command_line.strip(): return []
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
        # A process command line is fixed at creation; (pid, creation time, image) identifies it.
        self.command_lines={}

    def restore_defaults(self,gpu,directory):
        from gpu_defaults import restore_defaults
        return restore_defaults(gpu,directory)

    def verify_miner(self,path):
        from miner_update import active_executable
        target=active_executable(Path(path).parent.parent)
        if normalized_path(target)!=normalized_path(path):raise ValueError('Minerador selecionado difere do executável solicitado')

    def assert_gpu_available(self,miner_path,index):
        """Fail closed when an orphan or an unreadable miner may own this GPU."""
        command="[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); @(Get-CimInstance Win32_Process -Filter \"Name='SRBMiner-MULTI.exe'\" -ErrorAction Stop | Select-Object ProcessId,CommandLine) | ConvertTo-Json -Compress"
        result=subprocess.run(['powershell.exe','-NoProfile','-Command',command],capture_output=True,
            text=True,encoding='utf-8',errors='replace',timeout=8,creationflags=NO_WINDOW)
        if result.returncode: raise RuntimeError('Não foi possível verificar mineradores existentes; início bloqueado')
        rows=json.loads(result.stdout or '[]')
        if isinstance(rows,dict): rows=[rows]
        for row in rows:
            args=windows_argv(row.get('CommandLine'))
            if not args: raise RuntimeError('Minerador existente sem identidade legível; início bloqueado para evitar duplicação')
            try:
                selected=args[args.index('--gpu-id')+1].split(',') if '--gpu-id' in args else None
                if selected is not None and not all(x.isdigit() for x in selected): selected=None
            except IndexError: selected=None
            if selected is None or str(index) in selected:
                raise RuntimeError(f'Minerador já existente para GPU {index} (PID {row["ProcessId"]}); início bloqueado para evitar duplicação')

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
            key=(pid,metadata["created_at"],normalized_path(metadata["executable"]))
            if key not in self.command_lines:
                command=f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}'; if($p){{$p.CommandLine}}"
                command="[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); "+command
                result=subprocess.run(["powershell.exe","-NoProfile","-Command",command],capture_output=True,text=True,
                    encoding="utf-8",errors="replace",timeout=8,creationflags=NO_WINDOW)
                if result.returncode: return None
                argv=windows_argv(result.stdout.strip())
                if not argv: return {**metadata,"argv":argv}
                if len(self.command_lines)>=64: self.command_lines.pop(next(iter(self.command_lines)))
                self.command_lines[key]=argv
            metadata["argv"]=list(self.command_lines[key])
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
        command=f"@(Get-CimInstance Win32_Process -Filter \"Name = '{name}' OR Name = 'python.exe' OR Name = 'pythonw.exe' OR Name = 'powershell.exe' OR Name = 'pwsh.exe'\" | Select-Object ProcessId,ParentProcessId,CommandLine) | ConvertTo-Json -Compress"
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
                metadata.update(argv=windows_argv(item.get("CommandLine") or ""),launch_id="legacy-"+uuid.uuid4().hex,parent_pid=item.get('ParentProcessId'))
                found.append(metadata)
        return found

    def miner_device_map(self,miner_path,devices):
        result=subprocess.run([str(miner_path),"--list-devices","--disable-gpu-amd","--disable-gpu-intel"],
            cwd=str(Path(miner_path).parent),capture_output=True,text=True,encoding="utf-8",errors="replace",
            timeout=30,creationflags=NO_WINDOW)
        if result.returncode: raise RuntimeError("SRBMiner não confirmou sua lista de dispositivos; mineração não iniciada")
        return parse_miner_device_map(result.stdout+"\n"+result.stderr,devices)
