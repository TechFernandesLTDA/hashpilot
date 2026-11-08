//! `hashpilot` — CLI somente leitura do port em Rust do HashPilot.
//!
//! Lê sensores NVIDIA, logs do SRBMiner e o estado financeiro, e imprime JSON.
//! Nunca inicia ou para mineração, nunca altera clocks, potência ou ventoinhas,
//! e não grava nada nos dados do aplicativo.

mod logs;
mod nvidia;

use std::path::{Path, PathBuf};
use std::process::ExitCode;

use hashpilot_core::config::{
    self, current_platform, load_settings, process_env, resolve_state_dir,
};
use hashpilot_core::miner_log::{StampZone, parse_gpu_log};
use hashpilot_core::payout::payout_model;
use serde_json::{Map, Value, json};

const USAGE: &str = "\
hashpilot — HashPilot em Rust (somente leitura: não minera nem altera a GPU)

Uso:
  hashpilot gpus                         Sensores NVIDIA de todas as placas (nvidia-smi)
  hashpilot parse-log <arquivo> [opções] Estado de uma GPU a partir de um log do SRBMiner
      --index <n>        índice NVIDIA da GPU (padrão 0)
      --shared           log compartilhado: não assume GPU0 isolada
      --now <epoch>      instante de referência (padrão: agora)
      --utc              carimbos do log em UTC (padrão: hora local, como o SRBMiner grava)
  hashpilot payout [--state <arquivo>] [--now <epoch>] [--price-brl <x>]
                                         Previsão de pagamento a partir do finance-cache.json
  hashpilot snapshot [--log-dir <pasta>] Sensores + log mais recente de cada GPU
  hashpilot --version | --help
";

fn main() -> ExitCode {
    let arguments: Vec<String> = std::env::args().skip(1).collect();
    let result = match arguments.first().map(String::as_str) {
        None | Some("-h" | "--help" | "help") => {
            print!("{USAGE}");
            return ExitCode::SUCCESS;
        }
        Some("-V" | "--version") => {
            println!("hashpilot {}", env!("CARGO_PKG_VERSION"));
            return ExitCode::SUCCESS;
        }
        Some("gpus") => nvidia::query_gpus()
            .map(|gpus| json!(gpus))
            .map_err(Failure::Runtime),
        Some("parse-log") => parse_log_command(&arguments[1..]),
        Some("payout") => payout_command(&arguments[1..]),
        Some("snapshot") => snapshot_command(&arguments[1..]),
        Some(other) => Err(Failure::Usage(format!("comando desconhecido: {other}"))),
    };
    match result {
        Ok(value) => {
            println!(
                "{}",
                serde_json::to_string_pretty(&value).expect("JSON serializável")
            );
            ExitCode::SUCCESS
        }
        Err(Failure::Usage(message)) => {
            eprintln!("erro: {message}\n\n{USAGE}");
            ExitCode::from(2)
        }
        Err(Failure::Runtime(message)) => {
            eprintln!("erro: {message}");
            ExitCode::from(1)
        }
    }
}

enum Failure {
    Usage(String),
    Runtime(String),
}

impl From<String> for Failure {
    fn from(message: String) -> Self {
        Failure::Runtime(message)
    }
}

/// Opções `--nome valor` e sinalizadores `--nome`; o resto são posicionais.
struct Options {
    positional: Vec<String>,
    values: Map<String, Value>,
}

impl Options {
    fn parse(arguments: &[String], with_value: &[&str], flags: &[&str]) -> Result<Self, Failure> {
        let mut positional = Vec::new();
        let mut values = Map::new();
        let mut iterator = arguments.iter();
        while let Some(argument) = iterator.next() {
            if let Some(name) = argument.strip_prefix("--") {
                if with_value.contains(&name) {
                    let value = iterator
                        .next()
                        .ok_or_else(|| Failure::Usage(format!("--{name} exige um valor")))?;
                    values.insert(name.into(), Value::String(value.clone()));
                } else if flags.contains(&name) {
                    values.insert(name.into(), Value::Bool(true));
                } else {
                    return Err(Failure::Usage(format!("opção desconhecida: {argument}")));
                }
            } else {
                positional.push(argument.clone());
            }
        }
        Ok(Self { positional, values })
    }

    fn text(&self, name: &str) -> Option<&str> {
        self.values.get(name).and_then(Value::as_str)
    }

    fn flag(&self, name: &str) -> bool {
        self.values.get(name) == Some(&Value::Bool(true))
    }

    fn number(&self, name: &str) -> Result<Option<f64>, Failure> {
        self.text(name)
            .map(|text| {
                hashpilot_core::numbers::numeric_str(text)
                    .ok_or_else(|| Failure::Usage(format!("--{name} precisa ser um número finito")))
            })
            .transpose()
    }
}

fn parse_log_command(arguments: &[String]) -> Result<Value, Failure> {
    let options = Options::parse(arguments, &["index", "now"], &["shared", "utc"])?;
    let [path] = options.positional.as_slice() else {
        return Err(Failure::Usage(
            "parse-log exige exatamente um arquivo".into(),
        ));
    };
    let index = match options.text("index") {
        Some(text) => text
            .parse::<i64>()
            .map_err(|_| Failure::Usage("--index precisa ser inteiro".into()))?,
        None => 0,
    };
    let now = options.number("now")?.unwrap_or_else(nvidia::now_epoch);
    let zone = if options.flag("utc") {
        StampZone::UTC
    } else {
        StampZone::Local
    };
    let text = logs::read_log_tail(Path::new(path), logs::TAIL_LIMIT)
        .map_err(|error| format!("{path}: {error}"))?;
    let (state, future) = parse_gpu_log(&text, None, index, now, !options.flag("shared"), zone);
    let mut value = serde_json::to_value(&state).expect("estado serializável");
    value["future_line_at"] = json!(future);
    value["log_path"] = json!(path);
    Ok(value)
}

fn payout_command(arguments: &[String]) -> Result<Value, Failure> {
    let options = Options::parse(arguments, &["state", "now", "price-brl"], &[])?;
    if !options.positional.is_empty() {
        return Err(Failure::Usage(
            "payout não aceita argumentos posicionais".into(),
        ));
    }
    let path = match options.text("state") {
        Some(path) => PathBuf::from(path),
        None => state_dir()?.join("finance-cache.json"),
    };
    let text =
        std::fs::read_to_string(&path).map_err(|error| format!("{}: {error}", path.display()))?;
    let state: Value = serde_json::from_str(text.trim_start_matches('\u{feff}'))
        .map_err(|error| format!("{}: JSON inválido: {error}", path.display()))?;
    let now = options.number("now")?.unwrap_or_else(nvidia::now_epoch);
    let price = options
        .number("price-brl")?
        .map_or(Value::Null, |price| json!(price));
    let model = payout_model(&state, None, &price, now);
    Ok(json!({"state_path": path, "now": now, "payout": model}))
}

fn state_dir() -> Result<PathBuf, String> {
    resolve_state_dir(current_platform(), &process_env)
}

fn default_log_dir() -> Result<PathBuf, String> {
    let state = state_dir()?;
    let settings = load_settings(&state.join("settings.json"))?;
    Ok(config::log_dir(&state, &settings, &process_env))
}

fn snapshot_command(arguments: &[String]) -> Result<Value, Failure> {
    let options = Options::parse(arguments, &["log-dir"], &[])?;
    let log_dir = match options.text("log-dir") {
        Some(path) => PathBuf::from(path),
        None => default_log_dir()?,
    };
    let now = nvidia::now_epoch();
    let (gpus, gpu_error) = match nvidia::query_gpus() {
        Ok(gpus) => (gpus, Value::Null),
        Err(error) => (Vec::new(), json!(error)),
    };
    let devices: Vec<Value> = gpus
        .into_iter()
        .map(|gpu| device_snapshot(gpu, &log_dir, now))
        .collect();
    Ok(json!({
        "kind": "hashpilot-rust snapshot (somente leitura)",
        "collected_at": now,
        "log_dir": log_dir,
        "gpu_error": gpu_error,
        "devices": devices,
    }))
}

/// Log isolado por UUID (`LOG_DIR/gpus/<UUID>/mining-*.log`); para a GPU 0, o log
/// legado de `LOG_DIR` quando não há log por UUID.
fn device_snapshot(gpu: Map<String, Value>, log_dir: &Path, now: f64) -> Value {
    let uuid = gpu
        .get("uuid")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string();
    let index = gpu.get("index").and_then(Value::as_i64).unwrap_or(-1);
    let isolated = logs::newest_mining_log(&log_dir.join("gpus").join(&uuid));
    let path = isolated.clone().or_else(|| {
        (index == 0)
            .then(|| logs::newest_mining_log(log_dir))
            .flatten()
    });
    let (miner, log_error) = match &path {
        None => (Value::Null, json!("Nenhum log de mineração encontrado")),
        Some(path) => match logs::read_log_tail(path, logs::TAIL_LIMIT) {
            Ok(text) => {
                let (state, _) = parse_gpu_log(
                    &text,
                    Some(&uuid),
                    index,
                    now,
                    isolated.is_some() || index == 0,
                    StampZone::Local,
                );
                (
                    serde_json::to_value(state).expect("estado serializável"),
                    Value::Null,
                )
            }
            Err(error) => (Value::Null, json!(error.to_string())),
        },
    };
    json!({"gpu": gpu, "miner": miner, "log_path": path, "log_error": log_error})
}
