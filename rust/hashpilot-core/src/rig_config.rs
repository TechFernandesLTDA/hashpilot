//! Configuração de até seis GPUs NVIDIA por UUID — porte de `rig_config.py`.
//! Sem acesso ao hardware: entra a detecção do driver e o `settings.json`, sai a
//! lista de GPUs com caminhos isolados, carteira efetiva e perfil.

use std::path::{Path, PathBuf};
use std::sync::LazyLock;

use regex::Regex;
use serde_json::{Map, Value, json};

use crate::wallet::{AUTHOR_WALLET, valid_wallet};

pub const MAX_GPUS: usize = 6;
/// Perfis de `gpu_profiles.py` (objetivos e margens térmicas, nunca clocks de outro modelo).
pub const PROFILES: [&str; 3] = ["performance", "balanced", "cool"];

static UUID: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^GPU-[A-Za-z0-9-]{1,100}$").expect("padrão válido"));

/// `rig_config.device_key`: UUID NVIDIA validado, ou erro.
pub fn device_key(gpu_uuid: &Value) -> Result<String, String> {
    match gpu_uuid.as_str() {
        Some(text) if UUID.is_match(text) => Ok(text.to_string()),
        _ => Err("UUID NVIDIA inválido".into()),
    }
}

/// `Path.resolve()` do Python: absoluto, com links simbólicos resolvidos quando existe.
pub fn resolve_path(path: &Path) -> PathBuf {
    match std::fs::canonicalize(path) {
        Ok(real) => strip_verbatim(real),
        Err(_) => std::path::absolute(path).unwrap_or_else(|_| path.to_path_buf()),
    }
}

/// No Windows, `canonicalize` devolve `\\?\C:\...`; o Python devolve `C:\...`.
fn strip_verbatim(path: PathBuf) -> PathBuf {
    let text = path.to_string_lossy();
    match text.strip_prefix(r"\\?\") {
        Some(rest) if !rest.starts_with("UNC\\") => PathBuf::from(rest),
        _ => path,
    }
}

fn path_text(path: &Path) -> Value {
    Value::String(path.to_string_lossy().into_owned())
}

/// `rig_config.gpu_paths`: log, estado e arquivos de controle isolados por UUID.
pub fn gpu_paths(log_root: &Path, gpu_uuid: &str) -> Result<Map<String, Value>, String> {
    let root = resolve_path(log_root)
        .join("gpus")
        .join(device_key(&Value::String(gpu_uuid.into()))?);
    let mut map = Map::new();
    map.insert("log_dir".into(), path_text(&root));
    map.insert("state_dir".into(), path_text(&root));
    map.insert("latest_path".into(), path_text(&root.join("latest.json")));
    map.insert(
        "controller_status_path".into(),
        path_text(&root.join("gpu-agent-control.json")),
    );
    map.insert(
        "proxy_settings_path".into(),
        path_text(&root.join("proxy-settings.json")),
    );
    map.insert(
        "benchmark_request_path".into(),
        path_text(&root.join("gpu-agent-request.json")),
    );
    Ok(map)
}

fn json_int(value: Option<&Value>) -> Option<i64> {
    match value {
        Some(Value::Number(number)) if number.is_i64() || number.is_u64() => number.as_i64(),
        _ => None,
    }
}

/// `rig_config.resolve_devices`: GPUs novas ficam habilitadas; só um gerenciador em
/// execução inicia mineração. Configuração inválida de uma GPU a desabilita com o
/// motivo explícito, sem afetar as demais.
pub fn resolve_devices(
    detected: &[Value],
    settings: &Value,
    log_root: &Path,
) -> Result<Vec<Map<String, Value>>, String> {
    let settings = settings.as_object().ok_or("Configuração do rig inválida")?;
    let author = Value::String(AUTHOR_WALLET.into());
    let global_wallet = settings.get("wallet").unwrap_or(&author);
    if !valid_wallet(global_wallet) {
        return Err("Carteira global Pearl inválida".into());
    }
    let empty = Value::Object(Map::new());
    let overrides = settings
        .get("gpu_settings")
        .unwrap_or(&empty)
        .as_object()
        .ok_or("gpu_settings precisa ser um objeto por UUID")?;
    let mut candidates: Vec<&Map<String, Value>> = detected
        .iter()
        .filter_map(Value::as_object)
        .filter(|gpu| gpu.get("vendor").and_then(Value::as_str) == Some("NVIDIA"))
        .collect();
    for gpu in &candidates {
        if !json_int(gpu.get("index")).is_some_and(|index| index >= 0) {
            return Err("Índice NVIDIA inválido".into());
        }
    }
    candidates.sort_by_key(|gpu| json_int(gpu.get("index")).unwrap_or(0));

    let mut result = Vec::new();
    let mut seen = Vec::new();
    for detected_gpu in candidates {
        let identifier = device_key(detected_gpu.get("uuid").unwrap_or(&Value::Null))?;
        if seen.contains(&identifier) {
            continue;
        }
        seen.push(identifier.clone());
        if result.len() >= MAX_GPUS {
            break;
        }
        let mut error: Option<&str> = None;
        let disabled_entry = json!({"enabled": false});
        let entry = match overrides.get(&identifier) {
            None => &empty,
            Some(value) if value.is_object() => value,
            Some(_) => {
                error = Some("Configuração desta GPU inválida; dispositivo desabilitado");
                &disabled_entry
            }
        };
        let mut enabled = match entry.get("enabled") {
            None => true,
            Some(Value::Bool(flag)) => *flag,
            Some(_) => {
                error = Some("enabled precisa ser booleano; dispositivo desabilitado");
                false
            }
        };
        let mut override_wallet = match entry.get("wallet") {
            None | Some(Value::Null) => None,
            Some(Value::String(text)) if text.is_empty() => None,
            Some(value) => Some(value.clone()),
        };
        if override_wallet
            .as_ref()
            .is_some_and(|wallet| !valid_wallet(wallet))
        {
            enabled = false;
            override_wallet = None;
            error = Some("Carteira desta GPU inválida; dispositivo desabilitado");
        }
        let wallet = override_wallet
            .clone()
            .unwrap_or_else(|| global_wallet.clone());
        // Como `entry.get('profile', 'performance') not in PROFILES`: ausente é o padrão,
        // mas um valor presente e desconhecido (inclusive `null`) desabilita a GPU.
        let profile = match entry.get("profile") {
            None => "performance",
            Some(Value::String(name)) if PROFILES.contains(&name.as_str()) => name.as_str(),
            Some(_) => {
                enabled = false;
                error = Some("Perfil inválido; dispositivo desabilitado");
                "performance"
            }
        };
        let mut gpu = detected_gpu.clone();
        gpu.extend(gpu_paths(log_root, &identifier)?);
        gpu.insert("enabled".into(), Value::Bool(enabled));
        gpu.insert("profile".into(), Value::String(profile.into()));
        gpu.insert("wallet".into(), wallet.clone());
        gpu.insert("effective_wallet".into(), wallet.clone());
        gpu.insert(
            "wallet_override".into(),
            override_wallet.clone().unwrap_or(Value::Null),
        );
        gpu.insert(
            "uses_default_wallet".into(),
            Value::Bool(override_wallet.is_none()),
        );
        gpu.insert(
            "uses_author_wallet".into(),
            Value::Bool(wallet.as_str() == Some(AUTHOR_WALLET)),
        );
        gpu.insert("device_key".into(), Value::String(identifier.clone()));
        gpu.insert(
            "worker_name".into(),
            Value::String(format!("HashPilot_{}", &identifier[4..])),
        );
        gpu.insert(
            "configuration_error".into(),
            error.map_or(Value::Null, |text| Value::String(text.into())),
        );
        result.push(gpu);
    }
    Ok(result)
}

/// `rig_config.update_gpu_settings`: devolve um novo documento de configuração;
/// quem chama continua responsável pela gravação atômica.
pub fn update_gpu_settings(
    settings: &Value,
    gpu_uuid: &Value,
    enabled: Option<&Value>,
    wallet_override: Option<&Value>,
    change_wallet: bool,
) -> Result<Value, String> {
    let identifier = device_key(gpu_uuid)?;
    let mut result = settings.as_object().ok_or("Configuração inválida")?.clone();
    let overrides = result
        .entry("gpu_settings")
        .or_insert_with(|| Value::Object(Map::new()));
    let overrides = overrides.as_object_mut().ok_or("gpu_settings inválido")?;
    let entry = overrides
        .entry(identifier)
        .or_insert_with(|| Value::Object(Map::new()));
    if !entry.is_object() {
        *entry = json!({"enabled": false});
    }
    let entry = entry.as_object_mut().expect("objeto garantido acima");
    if let Some(enabled) = enabled.filter(|value| !value.is_null()) {
        if !enabled.is_boolean() {
            return Err("enabled precisa ser booleano".into());
        }
        entry.insert("enabled".into(), enabled.clone());
    }
    if change_wallet {
        let wallet = wallet_override.unwrap_or(&Value::Null);
        let empty = wallet.is_null() || wallet.as_str() == Some("");
        if !empty && !valid_wallet(wallet) {
            return Err("Carteira Pearl inválida".into());
        }
        entry.insert(
            "wallet".into(),
            if empty { Value::Null } else { wallet.clone() },
        );
    }
    Ok(Value::Object(result))
}
