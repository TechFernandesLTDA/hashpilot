//! `thermal_target.py`: controle por temperatura-alvo definida pelo usuário.
//!
//! Política pura, sem hardware: recebe amostras a cada ~2 s e devolve a ação de clock.
//! [`TargetTemperatureController`] decide subir, descer ou manter o clock do núcleo e
//! aprende tetos (clock que estourou o alvo ou que não rendeu hashrate).
//! [`MemoryExplorer`] mede cada clock de memória candidato e trava no melhor.
//!
//! Os textos de motivo e os arredondamentos são os do Python: `round()` e `:.0f`
//! arredondam empates para o par, e a ventoinha aparece como `str(float)`.

use std::collections::{BTreeSet, HashMap};

use crate::text::py_float_repr;

pub const FAN_SATURATED_PCT: f64 = 95.0;
pub const FORCE_DOWN_C: f64 = 2.0;
pub const HEADROOM_C: f64 = 2.0;
pub const DEADBAND_C: f64 = 0.5;
pub const SUSTAIN_UP_S: f64 = 20.0;
pub const DOWN_INTERVAL_S: f64 = 6.0;
pub const FAST_DOWN_INTERVAL_S: f64 = 3.0;
pub const UP_INTERVAL_S: f64 = 30.0;
pub const OVERSHOOT_WINDOW_S: f64 = 90.0;
pub const CEILING_TTL_S: f64 = 600.0;
pub const GAIN_SETTLE_S: f64 = 45.0;
pub const MIN_GAIN: f64 = 0.003;
pub const MIN_TARGET_C: f64 = 50.0;
/// Partida sem memória: clock em que a RTX 3070 de referência minerou estável (~74 °C).
pub const SAFE_START_CLOCK_MHZ: f64 = 1635.0;
pub const MEMORY_SETTLE_S: f64 = 480.0;
pub const MEMORY_MEASURE_S: f64 = 180.0;
pub const MEMORY_REEXPLORE_S: f64 = 6.0 * 3600.0;
pub const MEMORY_MIN_SAMPLES: usize = 3;
pub const MEMORY_TIE_FRACTION: f64 = 0.005;

/// `f"{value:.0f}"` do Python (empate para o par, sem `-0`).
fn whole(value: f64) -> String {
    let text = format!("{value:.0}");
    if text == "-0" { "0".into() } else { text }
}

/// `round(float(clock))` do Python.
fn py_round(value: f64) -> i64 {
    value.round_ties_even() as i64
}

/// `clamp_target(value, hard_limit)`: alvo limitado a `[50, limite rígido - 3]`;
/// `None` para valor ausente ou NaN.
pub fn clamp_target(value: Option<f64>, hard_limit: f64) -> Option<f64> {
    let value = value?;
    if value.is_nan() {
        return None;
    }
    Some(MIN_TARGET_C.max((hard_limit - 3.0).min(value)))
}

#[derive(Clone, Debug, PartialEq)]
pub struct TargetDecision {
    /// `"clock_down"`, `"clock_up"` ou `"hold"`.
    pub action: &'static str,
    pub reason: String,
    pub ceiling_mhz: Option<f64>,
}

impl TargetDecision {
    fn new(action: &'static str, reason: impl Into<String>, ceiling_mhz: Option<f64>) -> Self {
        Self {
            action,
            reason: reason.into(),
            ceiling_mhz,
        }
    }
}

/// `{"from", "to", "at", "rate_before"}` de uma subida ainda em avaliação.
#[derive(Clone, Debug, PartialEq)]
pub struct PendingUp {
    pub from: Option<f64>,
    pub to: Option<f64>,
    pub at: f64,
    pub rate_before: Option<f64>,
}

#[derive(Clone, Debug)]
pub struct TargetTemperatureController {
    pub target_c: f64,
    pub last_down_at: f64,
    pub last_up_at: f64,
    pub headroom_since: Option<f64>,
    pub ceiling_mhz: Option<f64>,
    pub ceiling_until: f64,
    pub pending_up: Option<PendingUp>,
    /// Clock arredondado → média móvel exponencial do hashrate.
    pub rates: HashMap<i64, f64>,
}

impl TargetTemperatureController {
    pub fn new(target_c: f64) -> Self {
        Self {
            target_c,
            last_down_at: f64::NEG_INFINITY,
            last_up_at: f64::NEG_INFINITY,
            headroom_since: None,
            ceiling_mhz: None,
            ceiling_until: f64::NEG_INFINITY,
            pending_up: None,
            rates: HashMap::new(),
        }
    }

    fn ceiling(&mut self, now: f64) -> Option<f64> {
        if self.ceiling_mhz.is_some() && now >= self.ceiling_until {
            self.ceiling_mhz = None;
        }
        self.ceiling_mhz
    }

    fn learn_ceiling(&mut self, clock: Option<f64>, now: f64) {
        let Some(clock) = clock else { return };
        self.ceiling_mhz = Some(self.ceiling_mhz.map_or(clock, |ceiling| ceiling.min(clock)));
        self.ceiling_until = now + CEILING_TTL_S;
    }

    pub fn record_hashrate(&mut self, clock: Option<f64>, rate: Option<f64>) {
        let (Some(clock), Some(rate)) = (clock, rate) else {
            return;
        };
        if rate <= 0.0 {
            return;
        }
        let key = py_round(clock);
        let value = self
            .rates
            .get(&key)
            .map_or(rate, |old| old * 0.8 + rate * 0.2);
        self.rates.insert(key, value);
    }

    pub fn notify_applied(
        &mut self,
        action: &str,
        before_clock: Option<f64>,
        after_clock: Option<f64>,
        now: f64,
        rate_before: Option<f64>,
    ) {
        if action == "clock_up" {
            self.last_up_at = now;
            self.pending_up = Some(PendingUp {
                from: before_clock,
                to: after_clock,
                at: now,
                rate_before,
            });
        } else if action == "clock_down" {
            self.last_down_at = now;
        }
        self.headroom_since = None;
    }

    fn rate_of(&self, clock: Option<f64>) -> Option<f64> {
        clock.and_then(|clock| self.rates.get(&py_round(clock)).copied())
    }

    pub fn step(
        &mut self,
        temp: Option<f64>,
        fan_pct: Option<f64>,
        clock: Option<f64>,
        now: f64,
        hashrate: Option<f64>,
    ) -> TargetDecision {
        let (Some(temp), Some(clock)) = (temp, clock) else {
            return TargetDecision::new(
                "hold",
                "Sensor ausente: controle por alvo aguardando",
                None,
            );
        };
        self.record_hashrate(Some(clock), hashrate);
        let mut ceiling = self.ceiling(now);
        let target = self.target_c;
        let fan_saturated = fan_pct.is_some_and(|fan| fan >= FAN_SATURATED_PCT);
        if let Some(pending) = self.pending_up.clone() {
            if temp > target + DEADBAND_C && now - pending.at <= OVERSHOOT_WINDOW_S {
                // Subida recente que estourou o alvo: memoriza o clock como teto.
                self.learn_ceiling(pending.from, now);
                self.pending_up = None;
                ceiling = self.ceiling_mhz;
            } else if now - pending.at >= GAIN_SETTLE_S {
                // Subida estabilizada: confere se rendeu hashrate (`x or y` do Python).
                let before = self
                    .rate_of(pending.from)
                    .filter(|rate| *rate != 0.0)
                    .or(pending.rate_before);
                let after = self.rate_of(pending.to);
                if let (Some(before), Some(after)) = (
                    before.filter(|rate| *rate != 0.0),
                    after.filter(|rate| *rate != 0.0),
                ) && after < before * (1.0 + MIN_GAIN)
                {
                    self.learn_ceiling(pending.from, now);
                    self.pending_up = None;
                    return TargetDecision::new(
                        "clock_down",
                        "Clock maior não aumentou o hashrate: voltando ao melhor degrau",
                        self.ceiling_mhz,
                    );
                }
                self.pending_up = None;
            }
        }
        if temp >= target + FORCE_DOWN_C {
            self.headroom_since = None;
            if now - self.last_down_at >= FAST_DOWN_INTERVAL_S {
                return TargetDecision::new(
                    "clock_down",
                    format!(
                        "{} °C bem acima do alvo {} °C: reduzindo clock",
                        whole(temp),
                        whole(target)
                    ),
                    ceiling,
                );
            }
            return TargetDecision::new("hold", "Aguardando efeito da redução anterior", ceiling);
        }
        if temp > target + DEADBAND_C {
            self.headroom_since = None;
            if !fan_saturated {
                let fan = fan_pct.map_or_else(|| "?".to_owned(), py_float_repr);
                return TargetDecision::new(
                    "hold",
                    format!("Acima do alvo; ventoinha em {fan}% ainda pode resfriar"),
                    ceiling,
                );
            }
            if now - self.last_down_at >= DOWN_INTERVAL_S {
                return TargetDecision::new(
                    "clock_down",
                    format!(
                        "Ventoinha no máximo e {} °C acima do alvo {} °C: reduzindo levemente",
                        whole(temp),
                        whole(target)
                    ),
                    ceiling,
                );
            }
            return TargetDecision::new("hold", "Aguardando efeito da redução anterior", ceiling);
        }
        if temp <= target - HEADROOM_C {
            let since = *self.headroom_since.get_or_insert(now);
            if let Some(limit) = ceiling
                && clock >= limit
            {
                return TargetDecision::new(
                    "hold",
                    format!("No teto aprendido de {} MHz", whole(limit)),
                    ceiling,
                );
            }
            if now - since >= SUSTAIN_UP_S
                && now - self.last_up_at >= UP_INTERVAL_S
                && now - self.last_down_at >= UP_INTERVAL_S
            {
                return TargetDecision::new(
                    "clock_up",
                    format!(
                        "Folga de {} °C abaixo do alvo: subindo levemente",
                        whole(target - temp)
                    ),
                    ceiling,
                );
            }
            return TargetDecision::new(
                "hold",
                "Folga térmica confirmando antes de subir",
                ceiling,
            );
        }
        self.headroom_since = None;
        TargetDecision::new(
            "hold",
            format!("Na faixa do alvo {} °C", whole(target)),
            ceiling,
        )
    }
}

/// Chave exata de um clock (os candidatos são `float`, comparados por igualdade).
fn key(value: f64) -> u64 {
    value.to_bits()
}

#[derive(Clone, Debug)]
pub struct MemoryExplorer {
    /// Candidatos positivos, sem repetição, do maior para o menor.
    pub candidates: Vec<f64>,
    pub target_c: f64,
    pub best_mhz: Option<f64>,
    /// Clock → pontuação, na ordem em que foram medidos (0 = desclassificado).
    pub scores: Vec<(f64, f64)>,
    pub queue: Vec<f64>,
    pub current_mhz: Option<f64>,
    pub applied_at: f64,
    pub samples: Vec<f64>,
    pub errors_at_start: Option<f64>,
    pub hot: bool,
    pub explored_at: f64,
    pub unreachable: BTreeSet<u64>,
    /// `"hashrate"` (maior TH/s) ou `"profit"` (receita menos energia, R$/dia).
    pub objective: String,
    pub revenue_per_ths_day: Option<f64>,
    pub price_kwh: Option<f64>,
    pub watts: Vec<f64>,
    /// Clock → potência média medida.
    pub power: HashMap<u64, f64>,
}

impl MemoryExplorer {
    pub fn new(candidates: &[f64], target_c: f64) -> Self {
        let mut unique: Vec<f64> = Vec::new();
        for value in candidates.iter().copied().filter(|value| *value > 0.0) {
            if !unique.contains(&value) {
                unique.push(value);
            }
        }
        unique.sort_by(|a, b| b.total_cmp(a));
        Self {
            candidates: unique,
            target_c,
            best_mhz: None,
            scores: Vec::new(),
            queue: Vec::new(),
            current_mhz: None,
            applied_at: f64::NEG_INFINITY,
            samples: Vec::new(),
            errors_at_start: None,
            hot: false,
            explored_at: f64::NEG_INFINITY,
            unreachable: BTreeSet::new(),
            objective: "hashrate".into(),
            revenue_per_ths_day: None,
            price_kwh: None,
            watts: Vec::new(),
            power: HashMap::new(),
        }
    }

    pub fn score(&self, rate: f64, watts: Option<f64>) -> f64 {
        match (
            self.objective.as_str(),
            self.revenue_per_ths_day.filter(|value| *value != 0.0),
            self.price_kwh,
            watts,
        ) {
            ("profit", Some(revenue), Some(price), Some(watts)) => {
                rate * revenue - watts * 24.0 / 1000.0 * price
            }
            _ => rate,
        }
    }

    fn is_unreachable(&self, clock: f64) -> bool {
        self.unreachable.contains(&key(clock))
    }

    fn set_score(&mut self, clock: f64, score: f64) {
        match self.scores.iter_mut().find(|(known, _)| *known == clock) {
            Some(entry) => entry.1 = score,
            None => self.scores.push((clock, score)),
        }
    }

    fn start_round(&mut self, now: f64) {
        // O melhor conhecido primeiro (referência), depois os demais.
        let usable: Vec<f64> = self
            .candidates
            .iter()
            .copied()
            .filter(|clock| !self.is_unreachable(*clock))
            .collect();
        let best = self.best_mhz.filter(|best| usable.contains(best));
        self.queue = best
            .into_iter()
            .chain(
                usable
                    .into_iter()
                    .filter(|clock| Some(*clock) != self.best_mhz),
            )
            .collect();
        self.scores.clear();
        self.explored_at = now;
    }

    /// O clock de memória a aplicar agora, ou `None` para manter. `actual_mhz` é o clock
    /// lido da placa: um candidato que o driver não aplica é pulado em vez de medido.
    pub fn step(
        &mut self,
        now: f64,
        temp: Option<f64>,
        hashrate: Option<f64>,
        error_count: Option<f64>,
        actual_mhz: Option<f64>,
        power_w: Option<f64>,
    ) -> Option<f64> {
        if let (Some(actual), Some(current)) = (actual_mhz, self.current_mhz)
            && now - self.applied_at >= 10.0
            && (actual - current).abs() > 50.0
            && !self.is_unreachable(current)
        {
            self.unreachable.insert(key(current));
            self.scores.retain(|(clock, _)| *clock != current);
            if self.best_mhz == Some(current) {
                self.best_mhz = None;
            }
            if self.queue.is_empty() && self.best_mhz.is_none() {
                self.start_round(now);
            }
            return self.next(now);
        }
        if self.candidates.len() < 2 {
            return None;
        }
        if self.queue.is_empty() && self.current_mhz.is_none() {
            self.start_round(now);
        }
        let Some(current) = self.current_mhz else {
            return self.next(now);
        };
        if self.queue.is_empty() && Some(current) == self.best_mhz {
            if now - self.explored_at >= MEMORY_REEXPLORE_S {
                self.start_round(now);
                self.queue.retain(|clock| *clock != current);
                self.begin(current, now);
            }
            return None;
        }
        let elapsed = now - self.applied_at;
        if elapsed < MEMORY_SETTLE_S {
            return None;
        }
        if self.errors_at_start.is_none() {
            self.errors_at_start = error_count;
        }
        if let (Some(count), Some(start)) = (error_count, self.errors_at_start)
            && count > start
        {
            self.set_score(current, 0.0);
            return self.next(now);
        }
        if temp.is_some_and(|temp| temp > self.target_c + 1.0) {
            self.hot = true;
        }
        if let Some(rate) = hashrate.filter(|rate| *rate > 0.0) {
            self.samples.push(rate);
            if let Some(watts) = power_w.filter(|watts| *watts > 0.0) {
                self.watts.push(watts);
            }
        }
        if elapsed < MEMORY_SETTLE_S + MEMORY_MEASURE_S {
            return None;
        }
        if self.samples.len() >= MEMORY_MIN_SAMPLES && !self.hot {
            let rate = python_mean(&self.samples);
            let watts = (!self.watts.is_empty()).then(|| python_mean(&self.watts));
            // Pontuação 0 é reservada para desclassificado; lucro negativo vira mínimo positivo.
            let score = self.score(rate, watts).max(1e-6);
            self.set_score(current, score);
            if let Some(watts) = watts {
                self.power.insert(key(current), watts);
            }
        } else {
            self.set_score(current, 0.0);
        }
        self.next(now)
    }

    fn begin(&mut self, clock: f64, now: f64) {
        self.current_mhz = Some(clock);
        self.applied_at = now;
        self.samples.clear();
        self.watts.clear();
        self.hot = false;
        self.errors_at_start = None;
    }

    fn next(&mut self, now: f64) -> Option<f64> {
        if !self.queue.is_empty() {
            let clock = self.queue.remove(0);
            self.begin(clock, now);
            return Some(clock);
        }
        // `sorted(((score, clock) ...), reverse=True)`.
        let mut ranked: Vec<(f64, f64)> = self
            .scores
            .iter()
            .filter(|(_, score)| *score > 0.0)
            .map(|(clock, score)| (*score, *clock))
            .collect();
        ranked.sort_by(|a, b| b.0.total_cmp(&a.0).then(b.1.total_cmp(&a.1)));
        let mut best = match ranked.first() {
            Some((_, clock)) => *clock,
            None => match self
                .best_mhz
                .filter(|best| *best != 0.0)
                .or_else(|| self.candidates.first().copied())
            {
                Some(clock) => clock,
                None => return None,
            },
        };
        if let Some((top, _)) = ranked.first()
            && self.objective == "hashrate"
        {
            // Diferença dentro do ruído (0,5%): empate; vence quem gasta menos energia.
            let tied = ranked
                .iter()
                .filter(|(score, _)| *score >= top * (1.0 - MEMORY_TIE_FRACTION))
                .map(|(_, clock)| *clock);
            let rank = |clock: f64| {
                (
                    self.power
                        .get(&key(clock))
                        .copied()
                        .unwrap_or(f64::INFINITY),
                    -self.score_of(clock),
                )
            };
            let mut chosen: Option<(f64, (f64, f64))> = None;
            for clock in tied {
                let value = rank(clock);
                let better = chosen.is_none_or(|(_, current)| {
                    value.0 < current.0 || (value.0 == current.0 && value.1 < current.1)
                });
                if better {
                    chosen = Some((clock, value));
                }
            }
            if let Some((clock, _)) = chosen {
                best = clock;
            }
        }
        self.best_mhz = Some(best);
        let changed = Some(best) != self.current_mhz;
        self.begin(best, now);
        changed.then_some(best)
    }

    fn score_of(&self, clock: f64) -> f64 {
        self.scores
            .iter()
            .find(|(known, _)| *known == clock)
            .map_or(0.0, |(_, score)| *score)
    }
}

/// `sum(values) / len(values)` do Python 3.12+, cuja soma de floats é compensada
/// (Neumaier), como em `Objects/bltinmodule.c`.
fn python_mean(values: &[f64]) -> f64 {
    let (mut total, mut compensation) = (0.0f64, 0.0f64);
    for value in values {
        let sum = total + value;
        if total.abs() >= value.abs() {
            compensation += (total - sum) + value;
        } else {
            compensation += (value - sum) + total;
        }
        total = sum;
    }
    if compensation != 0.0 && compensation.is_finite() {
        total += compensation;
    }
    total / values.len() as f64
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn formatting_matches_python_rounding() {
        assert_eq!(whole(74.5), "74");
        assert_eq!(whole(75.5), "76");
        assert_eq!(whole(-0.4), "0");
        assert_eq!(py_round(1702.5), 1702);
        assert_eq!(py_round(1703.5), 1704);
        assert_eq!(clamp_target(Some(f64::NAN), 93.0), None);
        assert_eq!(clamp_target(Some(99.0), 93.0), Some(90.0));
        assert_eq!(clamp_target(Some(10.0), 93.0), Some(50.0));
    }
}
