"""Política térmica pura: decide clocks, sem acessar GPU, processos ou arquivos."""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Decision:
    clock_mhz: int
    reason: str
    mode: str


class ThermalPolicy:
    """Timestamp monotônico em segundos; chamadas normalmente a cada 2 segundos.

    A recuperação exige 60 s consecutivos a até 75 °C. Ajustes para cima são
    espaçados por 20 s; reduções têm os intervalos da faixa térmica. 83 °C
    é uma exceção de emergência imediata, independentemente do cooldown.
    """

    def __init__(self, min_clock=1500, max_clock=1800, initial_clock=1740):
        for value in (min_clock, max_clock, initial_clock):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError("Clocks precisam ser inteiros positivos em MHz")
        if min_clock > max_clock:
            raise ValueError("Clock mínimo não pode exceder o máximo")
        self.min_clock, self.max_clock = min_clock, max_clock
        self.clock_mhz = max(min_clock, min(max_clock, initial_clock))
        self.last_sample = None
        self.last_temperature = None
        self.last_adjustment = None
        self.cold_since = None

    def step(self, temp: float, now: float) -> Decision:
        if isinstance(temp, bool) or isinstance(now, bool):
            raise ValueError("Temperatura e timestamp precisam ser numéricos")
        try:
            temp, now = float(temp), float(now)
        except (TypeError, ValueError) as error:
            raise ValueError("Temperatura ou timestamp inválido") from error
        if not math.isfinite(temp) or not 0 <= temp <= 150:
            raise ValueError("Temperatura inválida ou fora da faixa de sensor")
        if not math.isfinite(now) or now < 0 or (self.last_sample is not None and now < self.last_sample):
            raise ValueError("Timestamp monotônico inválido")

        slope = 0.0
        if self.last_sample is not None and now > self.last_sample:
            slope = (temp-self.last_temperature)/(now-self.last_sample)
        self.last_sample, self.last_temperature = now, temp
        if temp <= 75:
            if self.cold_since is None:
                self.cold_since = now
        else:
            self.cold_since = None

        elapsed = math.inf if self.last_adjustment is None else now-self.last_adjustment
        delta, interval, mode, reason = 0, 0, "hold", "Temperatura na banda estável; mantendo clock"
        if temp >= 83:
            if self.clock_mhz != self.min_clock:
                self.clock_mhz = self.min_clock
                self.last_adjustment = now
            return Decision(self.clock_mhz, "Emergência térmica a partir de 83 °C: clock mínimo", "emergency")
        if temp >= 80:
            delta, interval, mode = -60, 4, "cooling"
            reason = "A partir de 80 °C: reduzir 60 MHz a cada 4 s"
        elif temp >= 78:
            delta, interval, mode = -15, 6, "cooling"
            reason = "A partir de 78 °C: reduzir 15 MHz a cada 6 s"
        elif temp >= 77 and slope >= .4:
            delta, interval, mode = -30, 6, "anticipating"
            reason = f"Aquecimento rápido ({slope:.2f} °C/s): antecipar redução de 30 MHz"
        elif self.cold_since is not None:
            stable_for = now-self.cold_since
            if stable_for < 60:
                reason = f"Resfriando: {stable_for:.0f}/60 s estáveis até 75 °C"
            else:
                delta, interval, mode = 15, 20, "recovering"
                reason = "Até 75 °C por 60 s: recuperar 15 MHz a cada 20 s"

        if delta:
            if elapsed < interval:
                reason += f"; aguardando {interval-elapsed:.0f} s de cooldown"
            else:
                target = max(self.min_clock, min(self.max_clock, self.clock_mhz+delta))
                if target != self.clock_mhz:
                    self.clock_mhz = target
                    self.last_adjustment = now
                else:
                    reason += "; limite de clock atingido"
        return Decision(self.clock_mhz, reason, mode)
