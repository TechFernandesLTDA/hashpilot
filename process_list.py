"""Lista processos pelo Toolhelp32 do Windows, sem abrir `tasklist` a cada ciclo de coleta."""
from __future__ import annotations
import ctypes
from ctypes import wintypes
import os

TH32CS_SNAPPROCESS=0x2
INVALID_HANDLE_VALUE=ctypes.c_void_p(-1).value


class PROCESSENTRY32W(ctypes.Structure):
    _fields_=[("dwSize",wintypes.DWORD),("cntUsage",wintypes.DWORD),("th32ProcessID",wintypes.DWORD),
              ("th32DefaultHeapID",ctypes.c_size_t),("th32ModuleID",wintypes.DWORD),("cntThreads",wintypes.DWORD),
              ("th32ParentProcessID",wintypes.DWORD),("pcPriClassBase",ctypes.c_long),("dwFlags",wintypes.DWORD),
              ("szExeFile",wintypes.WCHAR*260)]


_KERNEL=None


def _kernel():
    global _KERNEL
    if os.name!="nt":raise OSError("Lista de processos disponível somente no Windows")
    if _KERNEL is None:
        kernel=ctypes.WinDLL("kernel32",use_last_error=True)
        kernel.CreateToolhelp32Snapshot.argtypes=[wintypes.DWORD,wintypes.DWORD]
        kernel.CreateToolhelp32Snapshot.restype=wintypes.HANDLE
        kernel.Process32FirstW.argtypes=[wintypes.HANDLE,ctypes.POINTER(PROCESSENTRY32W)]
        kernel.Process32FirstW.restype=wintypes.BOOL
        kernel.Process32NextW.argtypes=[wintypes.HANDLE,ctypes.POINTER(PROCESSENTRY32W)]
        kernel.Process32NextW.restype=wintypes.BOOL
        kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        _KERNEL=kernel
    return _KERNEL


def processes():
    """Pares (PID, nome do executável) de todos os processos visíveis; OSError se o snapshot falhar."""
    kernel=_kernel()
    snapshot=kernel.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS,0)
    if not snapshot or snapshot==INVALID_HANDLE_VALUE:
        raise OSError(ctypes.get_last_error(),"CreateToolhelp32Snapshot falhou")
    try:
        entry=PROCESSENTRY32W();entry.dwSize=ctypes.sizeof(PROCESSENTRY32W)
        found=[]
        ok=kernel.Process32FirstW(snapshot,ctypes.byref(entry))
        while ok:
            found.append((int(entry.th32ProcessID),entry.szExeFile))
            ok=kernel.Process32NextW(snapshot,ctypes.byref(entry))
        if not found:raise OSError("Nenhum processo listado")
        return found
    finally:kernel.CloseHandle(snapshot)


def process_ids(image_name):
    name=image_name.casefold()
    return {pid for pid,executable in processes() if executable.casefold()==name}


def pid_running(pid):
    return any(current==pid for current,_ in processes())
