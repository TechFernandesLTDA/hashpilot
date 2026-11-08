//! Política térmica pura — porte de `thermal.py`. Decide o clock a partir de
//! temperatura e tempo monotônico; não acessa GPU, processos ou arquivos.

use serde::Serialize;

/// Emergência imediata: clock mínimo, independentemente do cooldown.
pub const EMERGENCY_C: i64 = 83;
pub const FAST_COOLING_C: i64 = 80;
pub const COOLING_C: i64 = 78;
pub const ANTICIPATE_C: i64 = 77;
pub const RECOVERY_C: i64 = 75;
/// Corte do minerador no serviço do rig (`--gpu-off-temperature`).
pub const MINER_OFF_TEMPERATURE_C: i64 = 83;

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct Decision {
    pub clock_mhz: i64,
    pub reason: String,
    pub mode: String,
}

#[derive(Clone, Debug)]
pub struct ThermalPolicy {
    min_clock: i64,
    max_clock: i64,
    clock_mhz: i64,
    last_sample: Option<f64>,
    last_temperature: Option<f64>,
    last_adjustment: Option<f64>,
    cold_since: Option<f64>,
}

impl ThermalPolicy {
    /// Mesmos padrões do Python: 1500–1800 MHz, partindo de 1740 MHz.
    pub fn default_reference() -> Self {
        Self::new(1500, 1800, 1740).expect("padrões válidos")
    }

    pub fn new(min_clock: i64, max_clock: i64, initial_clock: i64) -> Result<Self, String> {
        if [min_clock, max_clock, initial_clock]
            .iter()
            .any(|value| *value <= 0)
        {
            return Err("Clocks precisam ser inteiros positivos em MHz".into());
        }
        if min_clock > max_clock {
            return Err("Clock mínimo não pode exceder o máximo".into());
        }
        Ok(Self {
            min_clock,
            max_clock,
            clock_mhz: initial_clock.clamp(min_clock, max_clock),
            last_sample: None,
            last_temperature: None,
            last_adjustment: None,
            cold_since: None,
        })
    }

    pub fn clock_mhz(&self) -> i64 {
        self.clock_mhz
    }

    /// Um passo da política (chamado normalmente a cada 2 s).
    pub fn step(&mut self, temp: f64, now: f64) -> Result<Decision, String> {
        if !temp.is_finite() || !(0.0..=150.0).contains(&temp) {
            return Err("Temperatura inválida ou fora da faixa de sensor".into());
        }
        if !now.is_finite() || now < 0.0 || self.last_sample.is_some_and(|last| now < last) {
            return Err("Timestamp monotônico inválido".into());
        }
        let mut slope = 0.0;
        if let (Some(last), Some(last_temperature)) = (self.last_sample, self.last_temperature) {
            if now > last {
                slope = (temp - last_temperature) / (now - last);
            }
        }
        self.last_sample = Some(now);
        self.last_temperature = Some(temp);
        if temp <= RECOVERY_C as f64 {
            if self.cold_since.is_none() {
                self.cold_since = Some(now);
            }
        } else {
            self.cold_since = None;
        }

        let elapsed = self
            .last_adjustment
            .map_or(f64::INFINITY, |last| now - last);
        let (mut delta, mut interval, mut mode) = (0i64, 0i64, "hold");
        let mut reason = String::from("Temperatura na banda estável; mantendo clock");
        if temp >= EMERGENCY_C as f64 {
            if self.clock_mhz != self.min_clock {
                self.clock_mhz = self.min_clock;
                self.last_adjustment = Some(now);
            }
            return Ok(Decision {
                clock_mhz: self.clock_mhz,
                reason: format!("Emergência térmica a partir de {EMERGENCY_C} °C: clock mínimo"),
                mode: "emergency".into(),
            });
        }
        if temp >= FAST_COOLING_C as f64 {
            (delta, interval, mode) = (-60, 4, "cooling");
            reason = format!("A partir de {FAST_COOLING_C} °C: reduzir 60 MHz a cada 4 s");
        } else if temp >= COOLING_C as f64 {
            (delta, interval, mode) = (-15, 6, "cooling");
            reason = format!("A partir de {COOLING_C} °C: reduzir 15 MHz a cada 6 s");
        } else if temp >= ANTICIPATE_C as f64 && slope >= 0.4 {
            (delta, interval, mode) = (-30, 6, "anticipating");
            reason = format!("Aquecimento rápido ({slope:.2} °C/s): antecipar redução de 30 MHz");
        } else if let Some(cold_since) = self.cold_since {
            let stable_for = now - cold_since;
            if stable_for < 60.0 {
                reason = format!("Resfriando: {stable_for:.0}/60 s estáveis até {RECOVERY_C} °C");
            } else {
                (delta, interval, mode) = (15, 20, "recovering");
                reason = format!("Até {RECOVERY_C} °C por 60 s: recuperar 15 MHz a cada 20 s");
            }
        }

        if delta != 0 {
            if elapsed < interval as f64 {
                reason += &format!(
                    "; aguardando {:.0} s de cooldown",
                    interval as f64 - elapsed
                );
            } else {
                let target = (self.clock_mhz + delta).clamp(self.min_clock, self.max_clock);
                if target != self.clock_mhz {
                    self.clock_mhz = target;
                    self.last_adjustment = Some(now);
                } else {
                    reason += "; limite de clock atingido";
                }
            }
        }
        Ok(Decision {
            clock_mhz: self.clock_mhz,
            reason,
            mode: mode.into(),
        })
    }
}

// Coerência dos limites verificada na compilação: a política entra em emergência
// antes ou no mesmo ponto do corte do minerador, e as faixas são crescentes.
const _: () = assert!(EMERGENCY_C <= MINER_OFF_TEMPERATURE_C);
const _: () = assert!(RECOVERY_C < ANTICIPATE_C && ANTICIPATE_C < COOLING_C);
const _: () = assert!(COOLING_C < FAST_COOLING_C && FAST_COOLING_C < EMERGENCY_C);
