//! `gpu_defaults`: devolve uma GPU, pelo UUID, aos padrões do driver.
//!
//! Ventoinhas em política automática (nunca uma porcentagem fixa), offsets de clock em
//! zero em todos os P-states, clocks destravados e potência de fábrica. Cada etapa é
//! conferida por leitura, e a falha de uma não impede as outras. Antes de qualquer
//! escrita, a restauração toma a mesma trava por GPU que o controlador
//! (`gpu_agent.NvidiaActuator`, arquivo `gpu-control-owner.lock`).

use std::fs::{File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};

use hashpilot_core::rig_config::device_key;
use hashpilot_history::pyjson::{encode_spaced, py_eq};
use serde_json::{Map, Value, json};

use crate::nvml::{Nvml, NvmlError};

pub const METHOD: &str =
    "NVML por UUID; política automática, offsets zero, clocks destravados e potência de fábrica";

fn now() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0.0, |elapsed| elapsed.as_secs_f64())
}

fn nvml(error: NvmlError) -> String {
    error.to_string()
}

/// `NvidiaDefaults.states()`: P-states válidos (0–15), ordenados e sem repetição.
pub fn states(api: &mut dyn Nvml) -> Result<Vec<i32>, String> {
    let mut found: Vec<i32> = api
        .get_supported_performance_states()
        .map_err(nvml)?
        .into_iter()
        .filter(|value| (0..16).contains(value))
        .collect();
    found.sort_unstable();
    found.dedup();
    if found.is_empty() {
        return Err("Driver não informou P-states suportados".into());
    }
    Ok(found)
}

/// `NvidiaDefaults.reset_offsets()`: domínios gráfico (0) e memória (2) em cada P-state.
pub fn reset_offsets(api: &mut dyn Nvml) -> Result<Value, String> {
    let mut checked = Vec::new();
    for state in states(api)? {
        for domain in [0, 2] {
            let mut value = api.get_clock_offsets(domain, state).map_err(nvml)?;
            if value.offset != 0 {
                if !(value.minimum <= 0 && 0 <= value.maximum) {
                    return Err("Offset zero fora dos limites do driver".into());
                }
                value.offset = 0;
                api.set_clock_offsets(&value).map_err(nvml)?;
            }
            if api.get_clock_offsets(domain, state).map_err(nvml)?.offset != 0 {
                return Err("Offset não voltou a zero".into());
            }
            checked.push(json!({"pstate": state, "domain": domain, "offset_mhz": 0}));
        }
    }
    Ok(Value::Array(checked))
}

fn fans(api: &mut dyn Nvml) -> Result<Value, String> {
    let count = api.get_num_fans().map_err(nvml)?;
    if count > 16 {
        return Err("Quantidade de ventoinhas inválida".into());
    }
    let mut policies = Vec::new();
    for index in 0..count {
        api.set_default_fan_speed(index).map_err(nvml)?;
        if api.get_fan_control_policy(index).map_err(nvml)? != 0 {
            return Err(format!("Ventoinha {index} ainda não está em automático"));
        }
        policies.push(json!({"fan": index, "policy": "automatic"}));
    }
    Ok(Value::Array(policies))
}

fn power(api: &mut dyn Nvml) -> Result<Value, String> {
    let default = api.get_power_default_limit().map_err(nvml)?;
    if !(1000..=2_000_000).contains(&default) {
        return Err("Limite padrão inválido".into());
    }
    api.set_power_limit(default).map_err(nvml)?;
    let current = api.get_power_limit().map_err(nvml)?;
    if current != default {
        return Err("Potência não confirmou o valor de fábrica".into());
    }
    Ok(json!({"default_w": f64::from(default) / 1000.0, "actual_w": f64::from(current) / 1000.0}))
}

/// `NvidiaDefaults.restore()`: o relatório por componente, como o Python.
pub fn restore(api: &mut dyn Nvml, at: f64) -> Value {
    let mut results = Map::new();
    let mut errors = Vec::new();
    let mut step = |name: &str, outcome: Result<Value, String>| {
        let entry = match outcome {
            Ok(details) => json!({"ok": true, "details": details}),
            Err(error) => {
                errors.push(json!(name));
                json!({"ok": false, "error": error})
            }
        };
        results.insert(name.to_owned(), entry);
    };
    step("fans", fans(api));
    step("offsets", reset_offsets(api));
    step(
        "graphics_clock",
        api.reset_gpu_locked_clocks()
            .map(|()| Value::Null)
            .map_err(nvml),
    );
    step(
        "memory_clock",
        api.reset_memory_locked_clocks()
            .map(|()| Value::Null)
            .map_err(nvml),
    );
    step("power", power(api));
    json!({"state": if errors.is_empty() { "restored" } else { "partial" }, "at": at, "components": results,
        "failed_components": errors, "method": METHOD})
}

/// `gpu_agent.process_state(pid)`: `"alive"`, `"dead"` ou `"unknown"` (acesso negado
/// nunca prova que o processo morreu).
pub fn process_state(pid: &Value) -> &'static str {
    match pid.as_u64().filter(|pid| (1..=0xffff_ffff).contains(pid)) {
        Some(pid) => crate::win32::process_state(pid as u32),
        None => "unknown",
    }
}

/// `gpu_agent.NvidiaActuator` só na parte de posse: a trava do primeiro byte de
/// `gpu-control-owner.lock` e o `gpu-control-owner.json` com o token desta sessão.
pub struct ControlLease {
    path: PathBuf,
    lock_path: PathBuf,
    token: String,
    owns: bool,
    handle: Option<File>,
}

impl ControlLease {
    pub fn new(state_dir: &Path) -> Result<Self, String> {
        std::fs::create_dir_all(state_dir).map_err(|error| error.to_string())?;
        Ok(Self {
            path: state_dir.join("gpu-control-owner.json"),
            lock_path: state_dir.join("gpu-control-owner.lock"),
            token: crate::manager::random_hex(32),
            owns: false,
            handle: None,
        })
    }

    fn lock(&mut self) -> Result<(), String> {
        let mut file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(&self.lock_path)
            .map_err(|error| error.to_string())?;
        if file.metadata().map_err(|error| error.to_string())?.len() == 0 {
            file.write_all(b" ").map_err(|error| error.to_string())?;
        }
        if !crate::win32::lock_first_byte(&file) {
            return Err("Outra sessão detém o controle da GPU".into());
        }
        self.handle = Some(file);
        Ok(())
    }

    fn unlock(&mut self) {
        if let Some(file) = self.handle.take() {
            crate::win32::unlock_first_byte(&file);
        }
    }

    fn read_owner(&self) -> Option<Value> {
        std::fs::read_to_string(&self.path)
            .ok()
            .and_then(|text| serde_json::from_str(&text).ok())
    }

    /// O token desta sessão, gravado no arquivo de posse.
    pub fn token(&self) -> &str {
        &self.token
    }

    /// `acquire(gpu_uuid)` com a escrita de hardware habilitada.
    pub fn acquire(&mut self, gpu_uuid: &str) -> Result<(), String> {
        if self.owns || self.handle.is_some() {
            return Err("Controle da GPU já adquirido".into());
        }
        self.lock()?;
        let result = self.claim(gpu_uuid);
        if result.is_err() {
            self.unlock();
        }
        result
    }

    fn claim(&mut self, gpu_uuid: &str) -> Result<(), String> {
        let owner = json!({"pid": std::process::id(), "token": self.token, "gpu_uuid": gpu_uuid, "at": now(), "lease_version": 2});
        let text = encode_spaced(&owner);
        if self.path.exists() {
            let ambiguous = || "Trava legada ambígua; controle bloqueado".to_owned();
            let previous: Value = std::fs::read_to_string(&self.path)
                .map_err(|_| ambiguous())
                .and_then(|text| serde_json::from_str(&text).map_err(|_| ambiguous()))?;
            if !previous.is_object() {
                return Err(ambiguous());
            }
            let version = previous
                .get("lease_version")
                .cloned()
                .unwrap_or(Value::Null);
            if !py_eq(&version, &json!(2))
                && process_state(previous.get("pid").unwrap_or(&Value::Null)) != "dead"
            {
                return Err("Outra sessão pode deter o controle da GPU".into());
            }
            let name = self
                .path
                .file_name()
                .map(|name| name.to_string_lossy().into_owned())
                .unwrap_or_default();
            let temporary = self
                .path
                .with_file_name(format!("{name}.{}.tmp", self.token));
            let written = std::fs::write(&temporary, &text)
                .and_then(|()| std::fs::rename(&temporary, &self.path));
            let _ = std::fs::remove_file(&temporary);
            written.map_err(|error| error.to_string())?;
        } else {
            let mut file = OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&self.path)
                .map_err(|error| {
                    if error.kind() == std::io::ErrorKind::AlreadyExists {
                        "Outra sessão detém o controle da GPU".to_owned()
                    } else {
                        error.to_string()
                    }
                })?;
            file.write_all(text.as_bytes())
                .map_err(|error| error.to_string())?;
        }
        self.owns = true;
        Ok(())
    }

    /// `assert_owner(gpu_uuid)`: o arquivo ainda é desta sessão e desta GPU.
    pub fn assert_owner(&self, gpu_uuid: &str) -> Result<(), String> {
        let invalid = || "Posse da GPU inválida".to_owned();
        if !self.owns || self.handle.is_none() {
            return Err(invalid());
        }
        let owner = self
            .read_owner()
            .filter(Value::is_object)
            .ok_or_else(invalid)?;
        if owner.get("token") != Some(&json!(self.token))
            || owner.get("gpu_uuid") != Some(&json!(gpu_uuid))
        {
            return Err(invalid());
        }
        Ok(())
    }

    /// `close()`: apaga o arquivo de posse só se ainda for desta sessão e solta a trava.
    pub fn close(&mut self) {
        if self.owns
            && let Some(owner) = self.read_owner()
            && owner.get("token") == Some(&json!(self.token))
        {
            let _ = std::fs::remove_file(&self.path);
        }
        self.owns = false;
        self.unlock();
    }
}

impl Drop for ControlLease {
    fn drop(&mut self) {
        self.close();
    }
}

/// Abre o NVML da GPU (`backend_factory` do Python).
pub type Factory<'a> = &'a mut dyn FnMut(&str) -> Result<Box<dyn Nvml>, String>;

/// `restore_defaults(gpu, state_dir)`: trava por GPU, conferência da posse, restauração
/// e, nessa ordem, desligamento do NVML e liberação da trava.
pub fn restore_defaults(gpu: &Value, state_dir: &Path, factory: Factory) -> Result<Value, String> {
    let failed = |error: String| json!({"state": "failed", "at": now(), "components": {}, "error": error, "failed_components": ["driver_or_ownership"]});
    let uuid = gpu
        .get("uuid")
        .and_then(Value::as_str)
        .ok_or_else(|| "'uuid'".to_owned())?;
    let mut lease = ControlLease::new(state_dir)?;
    let mut api: Option<Box<dyn Nvml>> = None;
    let outcome = (|| {
        lease.acquire(uuid)?;
        lease.assert_owner(uuid)?;
        device_key(&json!(uuid))?;
        let opened = api.insert(factory(uuid)?);
        Ok::<Value, String>(restore(opened.as_mut(), now()))
    })();
    let report = outcome.unwrap_or_else(failed);
    let closed = api
        .as_mut()
        .map_or(Ok(()), |opened| opened.close().map_err(nvml));
    lease.close();
    closed.map(|()| report)
}
