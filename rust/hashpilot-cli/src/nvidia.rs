//! Consulta somente leitura ao `nvidia-smi` — porte de `nvidia_smi.py` e de
//! `gpu_telemetry.read_gpu_samples`. Nunca escreve clocks, potência ou ventoinhas.

use std::time::{SystemTime, UNIX_EPOCH};

use hashpilot_core::numbers::numeric_str;
use serde_json::{Map, Value, json};

/// Campos consultados em lote, na mesma ordem do Python.
pub const FIELDS: [&str; 15] = [
    "uuid",
    "index",
    "name",
    "driver_version",
    "temperature.gpu",
    "power.draw",
    "power.limit",
    "clocks.gr",
    "clocks.mem",
    "utilization.gpu",
    "utilization.memory",
    "memory.used",
    "memory.total",
    "fan.speed",
    "pstate",
];
const NUMERIC_FIELDS: std::ops::Range<usize> = 4..14;

pub use hashpilot_rig::nvidia::run_smi;

pub fn now_epoch() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0.0, |elapsed| elapsed.as_secs_f64())
}

/// Converte uma linha CSV do `nvidia-smi` numa amostra. Valores `[N/A]` viram
/// ausência explícita; temperatura fora de 0–120 °C marca a amostra como inválida.
pub fn parse_row(line: &str, sampled_at: f64) -> Option<Map<String, Value>> {
    let values: Vec<&str> = line.split(',').map(str::trim).collect();
    if values.len() != FIELDS.len() || !values[0].starts_with("GPU-") {
        return None;
    }
    let index: i64 = values[1].parse().ok()?;
    let mut sample = Map::new();
    for (position, (field, value)) in FIELDS.iter().zip(&values).enumerate() {
        let entry = if position == 1 {
            json!(index)
        } else if NUMERIC_FIELDS.contains(&position) {
            numeric_str(value).map_or(Value::Null, |number| json!(number))
        } else {
            json!(value)
        };
        sample.insert((*field).to_string(), entry);
    }
    let temperature = sample.get("temperature.gpu").and_then(Value::as_f64);
    let error = match temperature {
        Some(value) if (0.0..=120.0).contains(&value) => Value::Null,
        _ => json!("Temperatura NVIDIA indisponível ou inválida"),
    };
    sample.insert("sampled_at".into(), json!(sampled_at));
    sample.insert("error".into(), error);
    Some(sample)
}

/// Uma consulta CSV para todas as placas.
pub fn query_gpus() -> Result<Vec<Map<String, Value>>, String> {
    let query = format!("--query-gpu={}", FIELDS.join(","));
    let output = run_smi(&[&query, "--format=csv,noheader,nounits"])?;
    let sampled_at = now_epoch();
    Ok(output
        .lines()
        .filter_map(|line| parse_row(line, sampled_at))
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_a_real_shaped_row_and_marks_missing_values() {
        let row = "GPU-bafb0ad5-dd9e-ee1d-a0c0-221c0dfbfc75, 0, NVIDIA GeForce RTX 3070, 617.14, 74, 152.21, 240.00, \
                   1740, 5001, 100, 3, 1024, 8192, 100, P2";
        let sample = parse_row(row, 10.0).unwrap();
        assert_eq!(sample["index"], json!(0));
        assert_eq!(sample["temperature.gpu"], json!(74.0));
        assert_eq!(sample["pstate"], json!("P2"));
        assert_eq!(sample["error"], Value::Null);
        let missing = row.replace(", 74,", ", [N/A],");
        let sample = parse_row(&missing, 10.0).unwrap();
        assert_eq!(sample["temperature.gpu"], Value::Null);
        assert_eq!(
            sample["error"],
            json!("Temperatura NVIDIA indisponível ou inválida")
        );
        assert!(parse_row("lixo", 0.0).is_none());
        assert!(parse_row(&row.replace(", 0,", ", x,"), 0.0).is_none());
    }
}
