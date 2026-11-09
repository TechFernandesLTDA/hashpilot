//! `mining_continuity.py`: sensor seguro, janela de inventário e bloqueio de
//! suspensão por inatividade (só a automática; tela e suspensão manual continuam).

use std::collections::HashMap;

use hashpilot_history::pyjson::{key_error, truthy};
use serde_json::{Value, json};

use crate::health::finite;

/// `safe_sensor(sensor, now, limit=70)`: leitura atual (até 15 s) abaixo do limite.
pub fn safe_sensor(sensor: &Value, now: f64, limit: f64) -> bool {
    let temperature = sensor.get("temperature.gpu").unwrap_or(&Value::Null);
    let stamp = sensor.get("sampled_at").unwrap_or(&Value::Null);
    finite(temperature)
        && (0.0..limit).contains(&temperature.as_f64().unwrap_or(f64::NAN))
        && !truthy(sensor.get("error").unwrap_or(&Value::Null))
        && finite(stamp)
        && (0.0..=15.0).contains(&(now - stamp.as_f64().unwrap_or(f64::NAN)))
}

/// `ContinuityWindow`: um inventário vazio ou parcial do driver não derruba
/// mineradores saudáveis por até 90 s, e uma volta de suspensão abre 60 s de tolerância.
#[derive(Debug, Default)]
pub struct ContinuityWindow {
    /// GPUs conhecidas, na ordem do último inventário (a ordem do `dict` do Python).
    pub known: Vec<(String, Value)>,
    missing_since: HashMap<String, f64>,
    last_tick: Option<f64>,
    grace_until: Option<f64>,
}

impl ContinuityWindow {
    pub fn new() -> Self {
        Self::default()
    }

    /// `grace_until` como o Python publica: o inteiro `0` até a primeira tolerância.
    pub fn grace_until(&self) -> Value {
        self.grace_until.map_or(json!(0), |value| json!(value))
    }

    fn grace(&self) -> f64 {
        self.grace_until.unwrap_or(0.0)
    }

    pub fn inventory(&mut self, devices: &[Value], now: f64) -> Result<Vec<Value>, String> {
        let mut result: Vec<(String, Value)> = Vec::new();
        for gpu in devices {
            let identifier = gpu
                .get("uuid")
                .ok_or_else(|| key_error("uuid"))?
                .as_str()
                .map(str::to_owned)
                .unwrap_or_else(|| hashpilot_history::pyjson::encode(&gpu["uuid"]));
            match result.iter_mut().find(|(key, _)| *key == identifier) {
                Some(slot) => slot.1 = gpu.clone(),
                None => result.push((identifier, gpu.clone())),
            }
        }
        for (identifier, _) in &result {
            self.missing_since.remove(identifier);
        }
        for (identifier, gpu) in &self.known {
            if result.iter().any(|(key, _)| key == identifier) {
                self.missing_since.remove(identifier);
                continue;
            }
            let since = *self.missing_since.entry(identifier.clone()).or_insert(now);
            if (0.0..90.0).contains(&(now - since)) {
                result.push((identifier.clone(), gpu.clone()));
            }
        }
        self.known = result.clone();
        Ok(result.into_iter().map(|(_, gpu)| gpu).collect())
    }

    /// `tick`: salto de mais de 30 s (ou relógio para trás) abre 60 s de tolerância.
    pub fn tick(&mut self, now: f64) -> bool {
        if let Some(last) = self.last_tick
            && (now - last > 30.0 || now < last)
        {
            self.grace_until = Some(now + 60.0);
        }
        self.last_tick = Some(now);
        now < self.grace()
    }

    /// `now < grace_until`, sem avançar o relógio.
    pub fn in_grace(&self, now: f64) -> bool {
        now < self.grace()
    }
}

type SetState = Box<dyn FnMut(u32) -> u32 + Send>;

/// `IdleSleepGuard`: `SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)`.
pub struct IdleSleepGuard {
    pub active: bool,
    pub error: Option<String>,
    set_state: Option<SetState>,
}

impl IdleSleepGuard {
    /// Com a API do Windows (fora dele, não faz nada, como no Python).
    pub fn new() -> Self {
        let set_state: Option<SetState> = if cfg!(windows) {
            Some(Box::new(crate::win32::set_thread_execution_state))
        } else {
            None
        };
        Self {
            active: false,
            error: None,
            set_state,
        }
    }

    /// Com uma função de estado falsa, para testes.
    pub fn with(set_state: SetState) -> Self {
        Self {
            active: false,
            error: None,
            set_state: Some(set_state),
        }
    }

    pub fn update(&mut self, active: bool) {
        if active == self.active {
            return;
        }
        let Some(set_state) = self.set_state.as_mut() else {
            return;
        };
        if set_state(if active { 0x8000_0001 } else { 0x8000_0000 }) != 0 {
            self.active = active;
            self.error = None;
        } else {
            self.error =
                Some("Windows não confirmou o bloqueio de suspensão por inatividade".into());
        }
    }
}

impl Default for IdleSleepGuard {
    fn default() -> Self {
        Self::new()
    }
}
