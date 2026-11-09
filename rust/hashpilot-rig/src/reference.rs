//! `reference_profile.py` e `gpu_profiles.py`: o perfil opt-in da RTX 3070 de
//! referência (UUID fixo, presets medidos) e os perfis de objetivo térmico.

use std::path::Path;

use hashpilot_core::text::py_str;
use hashpilot_history::pyjson::py_eq;
use serde_json::{Value, json};

pub const REFERENCE_RTX3070_UUID: &str = "GPU-bafb0ad5-dd9e-ee1d-a0c0-221c0dfbfc75";

fn supports(gpu: &Value, clock: &Value) -> bool {
    gpu.get("supported_graphics_clocks_mhz")
        .and_then(Value::as_array)
        .is_some_and(|clocks| clocks.iter().any(|item| py_eq(item, clock)))
}

/// `reference_device_confirmed(gpu)`.
pub fn reference_device_confirmed(gpu: &Value) -> bool {
    let name = gpu.get("name").map_or_else(String::new, py_str);
    gpu.get("uuid").and_then(Value::as_str) == Some(REFERENCE_RTX3070_UUID)
        && name.ends_with("RTX 3070")
        && gpu
            .get("power_default_w")
            .is_some_and(|value| py_eq(value, &json!(240)))
        && supports(gpu, &json!(1560))
}

fn miner_version(miner_path: &str) -> Option<(u128, u128, u128)> {
    let folder = Path::new(miner_path).parent()?.file_name()?.to_str()?;
    let rest = folder.strip_prefix("SRBMiner-Multi-")?;
    let parts: Vec<&str> = rest.split('-').collect();
    let [major, minor, patch] = parts.as_slice() else {
        return None;
    };
    let number = |text: &str| {
        (!text.is_empty() && text.chars().all(|c| c.is_ascii_digit()))
            .then(|| text.parse::<u128>().unwrap_or(u128::MAX))
    };
    Some((number(major)?, number(minor)?, number(patch)?))
}

/// `reference_memory_clock(miner_path, settings)`: 405 MHz só com opt-in e SRBMiner ≥ 3.8.4.
pub fn reference_memory_clock(miner_path: &str, settings: &Value) -> i64 {
    let chosen = settings
        .get("reference_memory_mhz")
        .is_some_and(|value| py_eq(value, &json!(405)));
    if chosen && miner_version(miner_path).is_some_and(|version| version >= (3, 8, 4)) {
        405
    } else {
        5001
    }
}

/// `reference_start_clock(gpu, settings)`: com temperatura-alvo a partida é sempre 1560.
pub fn reference_start_clock(gpu: &Value, settings: &Value) -> i64 {
    if settings
        .get("target_temperature_c")
        .is_some_and(Value::is_number)
    {
        return 1560;
    }
    let Some(chosen) = settings.get("reference_start_core_mhz") else {
        return 1560;
    };
    let preset = py_eq(chosen, &json!(1710)) || py_eq(chosen, &json!(1725));
    if preset && supports(gpu, chosen) {
        chosen.as_f64().map_or(1560, |value| value as i64)
    } else {
        1560
    }
}

/// `gpu_profiles.profile(valor)`.
pub fn profile(value: &Value) -> Result<Value, String> {
    let (label, objective, margin) = match value.as_str() {
        Some("performance") => ("Máximo desempenho", "performance", 0),
        Some("balanced") => ("Equilibrado", "efficiency", 3),
        Some("cool") => ("Menos calor / ruído", "efficiency", 7),
        _ => return Err("Perfil de GPU inválido".into()),
    };
    Ok(json!({"label": label, "objective": objective, "thermal_margin": margin}))
}
