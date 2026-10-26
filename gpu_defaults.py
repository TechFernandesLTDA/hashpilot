"""Restaura somente o UUID solicitado através da NVML instalada pelo driver.

Não fixa porcentagem de fan: devolve a política automática de cada ventoinha.
ABI: NVIDIA/go-nvml pkg/nvml/nvml.h e referência NVIDIA NVML Device Commands.
"""
from __future__ import annotations
import ctypes as c
import os
from pathlib import Path
import time
from rig_config import device_key

U,I,H=c.c_uint,c.c_int,c.c_void_p

class ClockOffset(c.Structure):
    _fields_=[('version',U),('type',I),('pstate',I),('offset',I),('minimum',I),('maximum',I)]

class NvmlError(RuntimeError):
    def __init__(self,name,code):
        self.code=code
        super().__init__(f'{name}: NVML {code}')

class NvidiaDefaults:
    def __init__(self,identifier,library=None):
        device_key(identifier)
        self.lib=library or c.WinDLL(str(Path(os.environ['WINDIR'])/'System32/nvml.dll'))
        self.handle=H()
        self.call('nvmlInit_v2',[])
        try:
            self.call('nvmlDeviceGetHandleByUUID',[c.c_char_p,c.POINTER(H)],identifier.encode('ascii'),c.byref(self.handle))
            if not self.handle.value:raise RuntimeError('Driver não confirmou a identidade da GPU')
        except Exception:
            self.close();raise

    def call(self,name,types,*arguments):
        try:function=getattr(self.lib,name)
        except AttributeError:raise NvmlError(name,'API indisponível')
        function.argtypes=types;function.restype=I
        code=function(*arguments)
        if code!=0:raise NvmlError(name,code)

    def close(self):
        self.call('nvmlShutdown',[])

    def value(self,name):
        value=U()
        self.call(name,[H,c.POINTER(U)],self.handle,c.byref(value))
        return value.value

    def fan_policy(self,index):
        value=U()
        self.call('nvmlDeviceGetFanControlPolicy_v2',[H,U,c.POINTER(U)],self.handle,index,c.byref(value))
        return value.value

    def states(self):
        values=(I*16)(*([32]*16))
        self.call('nvmlDeviceGetSupportedPerformanceStates',[H,c.POINTER(I),U],self.handle,values,c.sizeof(values))
        result=sorted({value for value in values if 0<=value<16})
        if not result:raise RuntimeError('Driver não informou P-states suportados')
        return result

    def offset(self,domain,state):
        value=ClockOffset(c.sizeof(ClockOffset)|(1<<24),domain,state,0,0,0)
        self.call('nvmlDeviceGetClockOffsets',[H,c.POINTER(ClockOffset)],self.handle,c.byref(value))
        return value

    def reset_offsets(self):
        checked=[]
        for state in self.states():
            for domain in (0,2): # NVML graphics and memory
                value=self.offset(domain,state)
                if value.offset:
                    if not value.minimum<=0<=value.maximum:raise RuntimeError('Offset zero fora dos limites do driver')
                    value.offset=0
                    self.call('nvmlDeviceSetClockOffsets',[H,c.POINTER(ClockOffset)],self.handle,c.byref(value))
                if self.offset(domain,state).offset!=0:raise RuntimeError('Offset não voltou a zero')
                checked.append({'pstate':state,'domain':domain,'offset_mhz':0})
        return checked

    def restore(self):
        results={};errors=[]
        def step(name,work):
            try:results[name]={'ok':True,'details':work()}
            except (OSError,RuntimeError,ValueError) as error:
                results[name]={'ok':False,'error':str(error)};errors.append(name)
        def fans():
            count=self.value('nvmlDeviceGetNumFans')
            if count>16:raise RuntimeError('Quantidade de ventoinhas inválida')
            policies=[]
            for index in range(count):
                self.call('nvmlDeviceSetDefaultFanSpeed_v2',[H,U],self.handle,index)
                policy=self.fan_policy(index)
                if policy!=0:raise RuntimeError(f'Ventoinha {index} ainda não está em automático')
                policies.append({'fan':index,'policy':'automatic'})
            return policies
        def power():
            default=self.value('nvmlDeviceGetPowerManagementDefaultLimit')
            if not 1000<=default<=2000000:raise RuntimeError('Limite padrão inválido')
            self.call('nvmlDeviceSetPowerManagementLimit',[H,U],self.handle,default)
            current=self.value('nvmlDeviceGetPowerManagementLimit')
            if current!=default:raise RuntimeError('Potência não confirmou o valor de fábrica')
            return {'default_w':default/1000,'actual_w':current/1000}
        # A failure in one operation must not prevent releasing the fans or other controls.
        step('fans',fans)
        step('offsets',self.reset_offsets)
        step('graphics_clock',lambda:self.call('nvmlDeviceResetGpuLockedClocks',[H],self.handle))
        step('memory_clock',lambda:self.call('nvmlDeviceResetMemoryLockedClocks',[H],self.handle))
        step('power',power)
        return {'state':'restored' if not errors else 'partial','at':time.time(),'components':results,'failed_components':errors,
            'method':'NVML por UUID; política automática, offsets zero, clocks destravados e potência de fábrica'}

def restore_defaults(gpu,state_dir,*,backend_factory=NvidiaDefaults,actuator_factory=None):
    """Acquire the same per-GPU OS lease as the controller before any driver write."""
    from gpu_agent import NvidiaActuator
    factory=actuator_factory or NvidiaActuator
    actuator=factory(state_dir,enabled=True)
    api=None
    try:
        actuator.acquire(gpu['uuid'])
        actuator.assert_owner(gpu['uuid'])
        api=backend_factory(gpu['uuid'])
        return api.restore()
    except (OSError,RuntimeError,ValueError) as error:
        return {'state':'failed','at':time.time(),'components':{},'error':str(error),'failed_components':['driver_or_ownership']}
    finally:
        try:
            if api:api.close()
        finally:actuator.close()
