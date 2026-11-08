"""Controle por temperatura-alvo definida pelo usuário.

Política pura (sem hardware): recebe amostras a cada ~2 s e devolve a ação de clock.

- Acima do alvo com a ventoinha saturada (>= FAN_SATURATED_PCT): reduz um degrau.
- Bem acima do alvo (>= alvo + FORCE_DOWN_C): reduz mesmo sem ventoinha saturada (segurança).
- Abaixo do alvo com folga (<= alvo - HEADROOM_C) por SUSTAIN_UP_S: sobe um degrau.
- Aprende: um clock que levou a temperatura acima do alvo vira teto temporário; um degrau
  que não aumentou o hashrate também vira teto (melhor combinação, não só clock maior).
"""
from dataclasses import dataclass, field

FAN_SATURATED_PCT = 95.0
FORCE_DOWN_C = 2.0
HEADROOM_C = 2.0
DEADBAND_C = 0.5
SUSTAIN_UP_S = 20.0
DOWN_INTERVAL_S = 6.0
FAST_DOWN_INTERVAL_S = 3.0
UP_INTERVAL_S = 30.0
OVERSHOOT_WINDOW_S = 90.0
CEILING_TTL_S = 600.0
GAIN_SETTLE_S = 45.0
MIN_GAIN = 0.003
MIN_TARGET_C = 50.0
# Partida sem memória: clock em que a RTX 3070 de referência mineirou estável (~74 °C) em 07/11/2026.
SAFE_START_CLOCK_MHZ = 1635.0


# 8 min: tempo para o controle do núcleo aproveitar a folga térmica que uma memória mais baixa libera
# (com 90 s a memória baixa era medida no núcleo da anterior e perdia injustamente; medido em 07/11/2026).
MEMORY_SETTLE_S = 480.0
MEMORY_MEASURE_S = 180.0
MEMORY_REEXPLORE_S = 6 * 3600.0
MEMORY_MIN_SAMPLES = 3
# Medido em 07/11/2026 na RTX 3070: 6801/5001/810 MHz ficaram em 75,23–75,34 TH/s (0,15%), mas 810 gasta 12 W menos.
MEMORY_TIE_FRACTION = 0.005


@dataclass
class MemoryExplorer:
    """Escolhe o clock de memória que dá mais hashrate respeitando o alvo térmico.

    Cada candidato: aplica, espera o controle do núcleo se acomodar (MEMORY_SETTLE_S), mede
    MEMORY_MEASURE_S. Shares rejeitadas/erros de hardware ou temperatura acima do alvo + 1 °C
    durante a medição desclassificam. Depois trava no melhor e reexplora a cada 6 h.
    """
    candidates: list
    target_c: float
    best_mhz: float | None = None
    scores: dict = field(default_factory=dict)
    queue: list = field(default_factory=list)
    current_mhz: float | None = None
    applied_at: float = float("-inf")
    samples: list = field(default_factory=list)
    errors_at_start: int | None = None
    hot: bool = False
    explored_at: float = float("-inf")
    unreachable: set = field(default_factory=set)
    # "hashrate": maior TH/s. "profit": receita do hashrate menos custo da energia (R$/dia).
    objective: str = "hashrate"
    revenue_per_ths_day: float | None = None
    price_kwh: float | None = None
    watts: list = field(default_factory=list)
    power: dict = field(default_factory=dict)  # clock -> potência média medida

    def score(self, rate, watts):
        if (self.objective == "profit" and self.revenue_per_ths_day and self.price_kwh is not None
                and watts is not None):
            return rate * self.revenue_per_ths_day - watts * 24 / 1000 * self.price_kwh
        return rate

    def __post_init__(self):
        self.candidates = sorted({float(c) for c in self.candidates if c and c > 0}, reverse=True)

    def _start_round(self, now):
        # O melhor conhecido primeiro (referência), depois os demais.
        usable = [c for c in self.candidates if c not in self.unreachable]
        order = ([self.best_mhz] if self.best_mhz in usable else []) + [c for c in usable if c != self.best_mhz]
        self.queue = order
        self.scores = {}
        self.explored_at = now

    def step(self, now, temp, hashrate, error_count, actual_mhz=None, power_w=None):
        """Devolve o clock de memória a aplicar agora, ou None para manter.

        actual_mhz: clock de memória lido da placa. Em carga CUDA (estado P2) a série 30 limita a
        memória a 6801 MHz; um candidato que o driver não aplica é pulado em vez de medido."""
        if (actual_mhz is not None and self.current_mhz is not None and self.queue is not None
                and now - self.applied_at >= 10 and abs(actual_mhz - self.current_mhz) > 50
                and self.current_mhz not in self.unreachable):
            self.unreachable.add(self.current_mhz)
            self.scores.pop(self.current_mhz, None)
            if self.best_mhz == self.current_mhz:
                self.best_mhz = None  # o melhor memorizado era um clock que o driver não aplica
            if not self.queue and self.best_mhz is None:
                self._start_round(now)
            return self._next(now)
        if len(self.candidates) < 2:
            return None
        if not self.queue and self.current_mhz is None:
            self._start_round(now)
        if self.current_mhz is None:
            return self._next(now)
        if self.queue == [] and self.current_mhz == self.best_mhz:
            if now - self.explored_at >= MEMORY_REEXPLORE_S:
                self._start_round(now)
                self.queue = [c for c in self.queue if c != self.current_mhz]
                self._begin(self.current_mhz, now, error_count)
            return None
        elapsed = now - self.applied_at
        if elapsed < MEMORY_SETTLE_S:
            return None
        if self.errors_at_start is None:
            self.errors_at_start = error_count
        if error_count is not None and self.errors_at_start is not None and error_count > self.errors_at_start:
            self.scores[self.current_mhz] = 0.0
            return self._next(now)
        if temp is not None and temp > self.target_c + 1.0:
            self.hot = True
        if hashrate is not None and hashrate > 0:
            self.samples.append(hashrate)
            if power_w is not None and power_w > 0:
                self.watts.append(power_w)
        if elapsed < MEMORY_SETTLE_S + MEMORY_MEASURE_S:
            return None
        valid = len(self.samples) >= MEMORY_MIN_SAMPLES and not self.hot
        if valid:
            rate = sum(self.samples) / len(self.samples)
            watts = sum(self.watts) / len(self.watts) if self.watts else None
            # Pontuação 0 é reservada para desclassificado; lucro negativo vira um valor mínimo positivo.
            self.scores[self.current_mhz] = max(self.score(rate, watts), 1e-6)
            if watts is not None:
                self.power[self.current_mhz] = watts
        else:
            self.scores[self.current_mhz] = 0.0
        return self._next(now)

    def _begin(self, clock, now, error_count=None):
        self.current_mhz, self.applied_at = clock, now
        self.samples, self.watts, self.hot, self.errors_at_start = [], [], False, None

    def _next(self, now):
        if self.queue:
            clock = self.queue.pop(0)
            self._begin(clock, now)
            return clock
        ranked = sorted(((score, clock) for clock, score in self.scores.items() if score > 0), reverse=True)
        best = ranked[0][1] if ranked else (self.best_mhz or self.candidates[0])
        if ranked and self.objective == "hashrate":
            # Diferença de hashrate dentro do ruído da medição (0,5%): empate; vence quem gasta menos energia.
            top = ranked[0][0]
            tied = [clock for score, clock in ranked if score >= top * (1 - MEMORY_TIE_FRACTION)]
            best = min(tied, key=lambda clock: (self.power.get(clock, float("inf")), -self.scores[clock]))
        self.best_mhz = best
        changed = best != self.current_mhz
        self._begin(best, now)
        return best if changed else None


def clamp_target(value, hard_limit):
    """Alvo do usuário limitado a [50, limite rígido - 3]; inválido -> None."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if value != value:  # NaN
        return None
    return max(MIN_TARGET_C, min(float(hard_limit) - 3.0, value))


@dataclass
class TargetDecision:
    action: str  # "clock_down" | "clock_up" | "hold"
    reason: str
    ceiling_mhz: float | None = None


@dataclass
class TargetTemperatureController:
    target_c: float
    last_down_at: float = float("-inf")
    last_up_at: float = float("-inf")
    headroom_since: float | None = None
    ceiling_mhz: float | None = None
    ceiling_until: float = float("-inf")
    pending_up: dict | None = None  # {"from":clock,"to":clock,"at":t,"rate_before":r}
    rates: dict = field(default_factory=dict)  # clock -> EMA de hashrate

    def _ceiling(self, now):
        if self.ceiling_mhz is not None and now >= self.ceiling_until:
            self.ceiling_mhz = None
        return self.ceiling_mhz

    def _learn_ceiling(self, clock, now):
        if clock is None:
            return
        self.ceiling_mhz = clock if self.ceiling_mhz is None else min(self.ceiling_mhz, clock)
        self.ceiling_until = now + CEILING_TTL_S

    def record_hashrate(self, clock, rate):
        if clock is None or rate is None or rate <= 0:
            return
        key = round(float(clock))
        old = self.rates.get(key)
        self.rates[key] = rate if old is None else old * 0.8 + rate * 0.2

    def notify_applied(self, action, before_clock, after_clock, now, rate_before=None):
        if action == "clock_up":
            self.last_up_at = now
            self.pending_up = {"from": before_clock, "to": after_clock, "at": now, "rate_before": rate_before}
        elif action == "clock_down":
            self.last_down_at = now
        self.headroom_since = None

    def step(self, temp, fan_pct, clock, now, hashrate=None):
        if temp is None or clock is None:
            return TargetDecision("hold", "Sensor ausente: controle por alvo aguardando")
        self.record_hashrate(clock, hashrate)
        ceiling = self._ceiling(now)
        target = self.target_c
        fan_saturated = fan_pct is not None and fan_pct >= FAN_SATURATED_PCT
        pending = self.pending_up

        # Subida recente que estourou o alvo: memoriza o clock como teto.
        if pending and temp > target + DEADBAND_C and now - pending["at"] <= OVERSHOOT_WINDOW_S:
            self._learn_ceiling(pending["from"], now)
            self.pending_up = None
            ceiling = self.ceiling_mhz
        # Subida estabilizada: confere se rendeu hashrate.
        elif pending and now - pending["at"] >= GAIN_SETTLE_S:
            before = self.rates.get(round(float(pending["from"]))) or pending.get("rate_before")
            after = self.rates.get(round(float(pending["to"])))
            if before and after and after < before * (1 + MIN_GAIN):
                self._learn_ceiling(pending["from"], now)
                ceiling = self.ceiling_mhz
                self.pending_up = None
                return TargetDecision("clock_down", "Clock maior não aumentou o hashrate: voltando ao melhor degrau", ceiling)
            self.pending_up = None

        if temp >= target + FORCE_DOWN_C:
            self.headroom_since = None
            if now - self.last_down_at >= FAST_DOWN_INTERVAL_S:
                return TargetDecision("clock_down", f"{temp:.0f} °C bem acima do alvo {target:.0f} °C: reduzindo clock", ceiling)
            return TargetDecision("hold", "Aguardando efeito da redução anterior", ceiling)
        if temp > target + DEADBAND_C:
            self.headroom_since = None
            if not fan_saturated:
                return TargetDecision("hold", f"Acima do alvo; ventoinha em {fan_pct if fan_pct is not None else '?'}% ainda pode resfriar", ceiling)
            if now - self.last_down_at >= DOWN_INTERVAL_S:
                return TargetDecision("clock_down", f"Ventoinha no máximo e {temp:.0f} °C acima do alvo {target:.0f} °C: reduzindo levemente", ceiling)
            return TargetDecision("hold", "Aguardando efeito da redução anterior", ceiling)
        if temp <= target - HEADROOM_C:
            if self.headroom_since is None:
                self.headroom_since = now
            if ceiling is not None and clock >= ceiling:
                return TargetDecision("hold", f"No teto aprendido de {ceiling:.0f} MHz", ceiling)
            if now - self.headroom_since >= SUSTAIN_UP_S and now - self.last_up_at >= UP_INTERVAL_S and now - self.last_down_at >= UP_INTERVAL_S:
                return TargetDecision("clock_up", f"Folga de {target - temp:.0f} °C abaixo do alvo: subindo levemente", ceiling)
            return TargetDecision("hold", "Folga térmica confirmando antes de subir", ceiling)
        self.headroom_since = None
        return TargetDecision("hold", f"Na faixa do alvo {target:.0f} °C", ceiling)
