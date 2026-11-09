//! `rig_controller.collect_snapshots` e `gpu_telemetry.read_device_payload`.
//!
//! Lê o log próprio de cada GPU e junta a amostra do driver. Não escreve clocks nem
//! inicia processos; só grava o `latest.json` de cada GPU, como o Python.

use std::path::{Path, PathBuf};

pub use hashpilot_core::miner_log::StampZone;
use hashpilot_core::miner_log::parse_gpu_log;
use hashpilot_core::text::py_str;
use hashpilot_history::pyjson::truthy;
use serde_json::{Map, Value, json};

use crate::fsutil::atomic_json;

pub type LogReader<'a> = &'a mut dyn FnMut(&Path) -> std::io::Result<String>;

/// `gpu_agent.validate_device`: só GPUs NVIDIA identificadas pelo driver.
pub fn validate_device(gpu: &Value) -> Result<(), String> {
    let uuid = gpu.get("uuid").map(py_str).unwrap_or_default();
    let valid = uuid.strip_prefix("GPU-").is_some_and(|rest| {
        !rest.is_empty() && rest.chars().all(|c| c.is_ascii_alphanumeric() || c == '-')
    });
    if gpu.get("vendor") != Some(&json!("NVIDIA")) || !valid {
        return Err("O agente aceita apenas GPUs NVIDIA identificadas pelo driver".into());
    }
    Ok(())
}

fn miner_state(
    text: &str,
    uuid: &str,
    index: i64,
    now: f64,
    zone: StampZone,
    gpu_index: &Value,
    log_index: &Value,
) -> Value {
    let (state, _) = parse_gpu_log(text, Some(uuid), index, now, true, zone);
    let mut value = serde_json::to_value(state).expect("estado serializável");
    // O dicionário do Python só tem `version` depois de uma linha "Miner version:", que
    // também cria `pause_at` (nulo); fora disso `pause_at` só existe após uma pausa.
    if let Some(object) = value.as_object_mut() {
        let started = object
            .get("version")
            .is_some_and(|version| !version.is_null());
        if !started {
            object.shift_remove("version");
            if object.get("pause_at").is_some_and(Value::is_null) {
                object.shift_remove("pause_at");
            }
        }
    }
    value["gpu_index"] = gpu_index.clone();
    value["miner_index"] = log_index.clone();
    value
}

/// `read_device_payload(gpu, log_path, miner_running=, now=)`. `zone` é o fuso dos carimbos\n/// do log: [`StampZone::Local`] no serviço (o SRBMiner grava hora local).
pub fn read_device_payload(
    gpu: &Value,
    log_path: &Value,
    miner_running: &Value,
    now: f64,
    zone: StampZone,
    read_log: LogReader,
) -> Result<Value, String> {
    validate_device(gpu)?;
    let uuid = py_str(&gpu["uuid"]);
    let log_index = gpu
        .get("miner_index")
        .cloned()
        .unwrap_or_else(|| gpu["index"].clone());
    let index = log_index
        .as_i64()
        .ok_or_else(|| "Índice do minerador inválido".to_owned())?;
    let shown = if truthy(log_path) {
        json!(py_str(log_path))
    } else {
        Value::Null
    };
    let mut result = json!({"identity": gpu, "miner_running": miner_running, "log_path": shown, "log_error": null,
        "miner": miner_state("", &uuid, index, now, zone, &gpu["index"], &log_index)});
    if log_path.is_null() {
        result["log_error"] = json!("Nenhum log próprio desta GPU encontrado");
        return Ok(result);
    }
    match read_log(&PathBuf::from(py_str(log_path))) {
        Ok(text) => {
            result["miner"] = miner_state(&text, &uuid, index, now, zone, &gpu["index"], &log_index)
        }
        Err(error) => {
            result["log_error"] = json!(error.to_string().chars().take(240).collect::<String>())
        }
    }
    Ok(result)
}

/// `collect_snapshots(devices, entries)` com as amostras do driver já lidas.
pub fn collect_snapshots(
    devices: &[Value],
    entries: &Map<String, Value>,
    samples: &Map<String, Value>,
    now: f64,
    zone: StampZone,
    read_log: LogReader,
) -> Result<Map<String, Value>, String> {
    let mut snapshots = Map::new();
    let empty = json!({});
    for device in devices {
        let identifier = py_str(&device["uuid"]);
        let entry = entries.get(&identifier).unwrap_or(&empty);
        let running = entry.get("miner_running").cloned().unwrap_or(json!(false));
        let payload = read_device_payload(
            device,
            entry.get("miner_log").unwrap_or(&Value::Null),
            &running,
            now,
            zone,
            &mut *read_log,
        )?;
        let snapshot = json!({
            "collected_at": now,
            "gpu": samples.get(&identifier).cloned().unwrap_or_else(|| json!({})),
            "miner": payload["miner"],
            "miner_running": payload["miner_running"],
            "gpu_agent": {"gpu": device},
            "wallet": entry.get("wallet").or_else(|| device.get("wallet")).cloned().unwrap_or(Value::Null),
            "pool": entry.get("pool").cloned().unwrap_or_else(|| json!("prl.kryptex.network:7048")),
            "log_error": payload["log_error"],
            "rig_managed": truthy(entry.get("managed").unwrap_or(&Value::Null)),
            "gpu_uuid": identifier,
        });
        atomic_json(
            &PathBuf::from(py_str(&device["log_dir"])).join("latest.json"),
            &snapshot,
            false,
            true,
        )?;
        snapshots.insert(identifier, snapshot);
    }
    Ok(snapshots)
}
