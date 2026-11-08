"""Política térmica pura: decide clocks, sem acessar GPU, processos ou arquivos."""
from __future__ import annotations

from dataclasses import dataclass
import math

# Limites do produto em °C. No serviço do rig o minerador desliga a GPU em
# MINER_OFF_TEMPERATURE_C (`--gpu-off-temperature` em rig.py); a política entra em
# emergência antes ou no mesmo ponto, e o agente nunca mira acima do corte
# (test_thermal confere). O fluxo histórico da RTX 3070 de referência mantém o
# corte medido de 84 °C como reserva acima da emergência do seu controlador
# (test_reference_scripts confere esse teto).
EMERGENCY_C = 83
FAST_COOLING_C = 80
COOLING_C = 78
ANTICIPATE_C = 77
RECOVERY_C = 75
MINER_OFF_TEMPERATURE_C = 83


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

    def __init__(self, min_clock: int = 1500, max_clock: int = 1800, initial_clock: int = 1740) -> None:
        for value in (min_clock, max_clock, initial_clock):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError("Clocks precisam ser inteiros positivos em MHz")
        if min_clock > max_clock:
            raise ValueError("Clock mínimo não pode exceder o máximo")
        self.min_clock, self.max_clock = min_clock, max_clock
        self.clock_mhz = max(min_clock, min(max_clock, initial_clock))
        self.last_sample: float | None = None
        self.last_temperature: float | None = None
        self.last_adjustment: float | None = None
        self.cold_since: float | None = None

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
        if self.last_sample is not None and self.last_temperature is not None and now > self.last_sample:
            slope = (temp-self.last_temperature)/(now-self.last_sample)
        self.last_sample, self.last_temperature = now, temp
        if temp <= RECOVERY_C:
            if self.cold_since is None:
                self.cold_since = now
        else:
            self.cold_since = None

        elapsed = math.inf if self.last_adjustment is None else now-self.last_adjustment
        delta, interval, mode, reason = 0, 0, "hold", "Temperatura na banda estável; mantendo clock"
        if temp >= EMERGENCY_C:
            if self.clock_mhz != self.min_clock:
                self.clock_mhz = self.min_clock
                self.last_adjustment = now
            return Decision(self.clock_mhz, f"Emergência térmica a partir de {EMERGENCY_C} °C: clock mínimo", "emergency")
        if temp >= FAST_COOLING_C:
            delta, interval, mode = -60, 4, "cooling"
            reason = f"A partir de {FAST_COOLING_C} °C: reduzir 60 MHz a cada 4 s"
        elif temp >= COOLING_C:
            delta, interval, mode = -15, 6, "cooling"
            reason = f"A partir de {COOLING_C} °C: reduzir 15 MHz a cada 6 s"
        elif temp >= ANTICIPATE_C and slope >= .4:
            delta, interval, mode = -30, 6, "anticipating"
            reason = f"Aquecimento rápido ({slope:.2f} °C/s): antecipar redução de 30 MHz"
        elif self.cold_since is not None:
            stable_for = now-self.cold_since
            if stable_for < 60:
                reason = f"Resfriando: {stable_for:.0f}/60 s estáveis até {RECOVERY_C} °C"
            else:
                delta, interval, mode = 15, 20, "recovering"
                reason = f"Até {RECOVERY_C} °C por 60 s: recuperar 15 MHz a cada 20 s"

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
