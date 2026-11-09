//! `hashpilot` — CLI somente leitura do port em Rust do HashPilot.
//!
//! Lê sensores NVIDIA, logs do SRBMiner, o estado financeiro e o histórico SQLite,
//! e imprime JSON. Nunca inicia ou para mineração, nunca altera clocks, potência ou
//! ventoinhas, e não grava nada nos dados do aplicativo.

mod history;
mod logs;
mod nvidia;
mod release;

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
  hashpilot release inspect <versão> --archive-sha256 <hex> [--md5 <hex>] [--file <zip>]
                                         Dados de catálogo de um SRBMiner novo (não extrai nem executa)
  hashpilot history <consulta> [--db <arquivo>] [--wallet <carteira>] [--since <epoch>]
                                         Histórico SQLite, aberto somente para leitura:
      stats              linhas e intervalo de cada tabela da carteira
      restore            séries do painel (hashrate, temperatura, potência, ventoinha)
      gpu <UUID>         séries de uma GPU do rig (hashrate medido até 180 s antes)
      gpus [<UUID>]      linhas e intervalo por GPU
      finance            pontos de saldo e a fonte financeira mais recente
      (padrões: banco da pasta de estado, carteira do settings.json, --since 0)
  hashpilot finance model [--state <arquivo>] [--hashrate <TH/s>] [--now <epoch>] [--wallet <carteira>]
                                         Modelo financeiro (créditos, projeção, pagamento) do finance-cache.json
  hashpilot finance fetch [--wallet <carteira>] [--replay <arquivo>] [--now <epoch>]
                                         Consulta pública Kryptex/CoinGecko agora, sem gravar cache nem histórico
                                         (--replay: respostas gravadas {url: json}, sem rede)
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
        Some("release") => release_command(&arguments[1..]),
        Some("history") => history_command(&arguments[1..]),
        Some("finance") => finance_command(&arguments[1..]),
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

fn read_json(path: &Path) -> Result<Value, String> {
    let text =
        std::fs::read_to_string(path).map_err(|error| format!("{}: {error}", path.display()))?;
    serde_json::from_str(text.trim_start_matches('\u{feff}'))
        .map_err(|error| format!("{}: JSON inválido: {error}", path.display()))
}

/// `hashpilot finance`: nada é gravado (nem cache, nem histórico).
fn finance_command(arguments: &[String]) -> Result<Value, Failure> {
    let options = Options::parse(
        arguments,
        &["state", "hashrate", "now", "wallet", "replay"],
        &[],
    )?;
    let [action] = options.positional.as_slice() else {
        return Err(Failure::Usage(
            "finance exige uma ação: model ou fetch".into(),
        ));
    };
    let settings: Value =
        match hashpilot_core::config::load_settings(&state_dir()?.join("settings.json")) {
            Ok(settings) => serde_json::to_value(settings).unwrap_or(Value::Null),
            Err(error) => return Err(Failure::Runtime(error)),
        };
    let wallet = match options.text("wallet") {
        Some(wallet) => wallet.to_string(),
        None => history::settings_wallet(&state_dir()?)?,
    };
    let now = options.number("now")?.unwrap_or_else(nvidia::now_epoch);
    match action.as_str() {
        "model" => {
            let path = match options.text("state") {
                Some(path) => PathBuf::from(path),
                None => state_dir()?.join(hashpilot_finance::collector::CACHE_FILE),
            };
            let state = read_json(&path)?;
            let hashrate = options.number("hashrate")?;
            let model =
                hashpilot_finance::financial_model(&state, hashrate, now, &wallet, &settings);
            Ok(json!({"state_path": path, "now": now, "wallet": wallet, "model": model}))
        }
        "fetch" => {
            let (fetcher, source): (hashpilot_finance::Fetcher, Value) =
                match options.text("replay") {
                    Some(path) => {
                        let responses = read_json(Path::new(path))?;
                        let fetcher: hashpilot_finance::Fetcher =
                            std::sync::Arc::new(move |url: &str| {
                                responses
                                    .get(url)
                                    .cloned()
                                    .ok_or_else(|| format!("sem resposta gravada para {url}"))
                            });
                        (fetcher, json!(path))
                    }
                    None => (hashpilot_finance::network_fetcher(), json!("rede")),
                };
            let clock: hashpilot_history::Clock = match options.number("now")? {
                Some(fixed) => std::sync::Arc::new(move || fixed),
                None => std::sync::Arc::new(nvidia::now_epoch),
            };
            let mut collector =
                hashpilot_finance::FinanceCollector::new(None, None, &wallet, fetcher, clock);
            let state = collector.sample()?;
            Ok(json!({"source": source, "wallet": wallet, "state": state}))
        }
        other => Err(Failure::Usage(format!(
            "ação de finance desconhecida: {other}"
        ))),
    }
}

fn history_command(arguments: &[String]) -> Result<Value, Failure> {
    let options = Options::parse(arguments, &["db", "wallet", "since"], &[])?;
    let (query, rest) = options
        .positional
        .split_first()
        .ok_or_else(|| Failure::Usage("history exige uma consulta".into()))?;
    let database = match options.text("db") {
        Some(path) => PathBuf::from(path),
        None => hashpilot_history::default_path(&state_dir()?),
    };
    let wallet = match options.text("wallet") {
        Some(wallet) => wallet.to_string(),
        None => history::settings_wallet(&state_dir()?)?,
    };
    let since = options.number("since")?.unwrap_or(0.0);
    let query = history::Query::parse(query, rest).map_err(Failure::Usage)?;
    Ok(history::run(&database, &wallet, since, &query)?)
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

fn release_command(arguments: &[String]) -> Result<Value, Failure> {
    let Some((action, rest)) = arguments.split_first() else {
        return Err(Failure::Usage("release exige uma ação: inspect".into()));
    };
    if action != "inspect" {
        return Err(Failure::Usage(format!(
            "ação desconhecida: release {action}"
        )));
    }
    let options = Options::parse(rest, &["archive-sha256", "md5", "file"], &[])?;
    let [version] = options.positional.as_slice() else {
        return Err(Failure::Usage(
            "release inspect exige a versão (X.Y.Z)".into(),
        ));
    };
    let digest = options.text("archive-sha256").ok_or_else(|| {
        Failure::Usage("--archive-sha256 é obrigatório (digest exibido pelo GitHub)".into())
    })?;
    release::validate(version, digest, options.text("md5"))?;
    let data = match options.text("file") {
        Some(path) => release::read_file(Path::new(path))?,
        None => release::download(&release::archive_url(version))?,
    };
    Ok(release::inspect(
        version,
        &data,
        digest,
        options.text("md5"),
    )?)
}
