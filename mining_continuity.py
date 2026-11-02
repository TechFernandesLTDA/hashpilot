"""Bounded continuity helpers; no antivirus changes or persistent power-plan edits."""
from __future__ import annotations
import ctypes
import os


def safe_sensor(sensor,now,limit=70):
    from mining_health import finite
    temperature=sensor.get('temperature.gpu');stamp=sensor.get('sampled_at')
    return (finite(temperature) and 0<=temperature<limit and not sensor.get('error')
            and finite(stamp) and 0<=now-stamp<=15)


class IdleSleepGuard:
    """Prevent automatic system idle sleep only; allow display-off and explicit sleep."""
    def __init__(self,set_state=None):
        self.active=False;self.error=None
        self.set_state=set_state
        if set_state is None and os.name=='nt':
            self.set_state=ctypes.WinDLL('kernel32',use_last_error=True).SetThreadExecutionState
            self.set_state.argtypes=[ctypes.c_uint32];self.set_state.restype=ctypes.c_uint32

    def update(self,active):
        active=bool(active)
        if active==self.active:return
        if self.set_state is None:return
        if self.set_state(0x80000001 if active else 0x80000000):
            self.active=active;self.error=None
        else:self.error='Windows não confirmou o bloqueio de suspensão por inatividade'


class ContinuityWindow:
    def __init__(self):
        self.known={};self.missing_since={};self.last_tick=None;self.grace_until=0

    def inventory(self,devices,now):
        """A transient empty/partial driver inventory must not terminate healthy miners."""
        result={gpu['uuid']:dict(gpu) for gpu in devices}
        for identifier in result:self.missing_since.pop(identifier,None)
        for identifier,gpu in self.known.items():
            if identifier not in result:
                since=self.missing_since.setdefault(identifier,now)
                if 0<=now-since<90:result[identifier]=gpu
            else:self.missing_since.pop(identifier,None)
        self.known=result.copy()
        return list(result.values())

    def tick(self,now):
        if self.last_tick is not None and (now-self.last_tick>30 or now<self.last_tick):
            self.grace_until=now+60
        self.last_tick=now
        return now<self.grace_until
