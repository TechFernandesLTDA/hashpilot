//! `hashpilot` — CLI somente leitura do port em Rust do HashPilot.
//!
//! Lê sensores NVIDIA, logs do SRBMiner, o estado financeiro e o histórico SQLite,
//! e imprime JSON. Nunca inicia ou para mineração, nunca altera clocks, potência ou
//! ventoinhas, e não grava nada nos dados do aplicativo.

mod history;
mod logs;
mod nvidia;
mod release;
mod rig_cli;

use std::path::{Path, PathBuf};
use std::process::ExitCode;

use hashpilot_core::config::{
    self, current_platform, load_settings, process_env, resolve_state_dir,
};
use hashpilot_core::miner_log::{StampZone, parse_gpu_log};
use hashpilot_core::payout::payout_model;
use serde_json::{Map, Value, json};

const USAGE: &str = "\
hashpilot — HashPilot em Rust (só lê, exceto `rig --serve`, que minera e restaura a GPU)

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
  hashpilot history retention [--apply] [--days N] [--state-dir D] [--log-dir L] [--db F] [--now EPOCH]
                                         Retenção: telemetria com mais de N dias (padrão 30) vira uma
                                         média por minuto; decisões `hold` sem escrita mais velhas que
                                         isso saem dos gpu-agent.sqlite3. Sem --apply só conta (somente
                                         leitura); com --apply grava em transações curtas, sem VACUUM
  hashpilot finance model [--state <arquivo>] [--hashrate <TH/s>] [--now <epoch>] [--wallet <carteira>]
                                         Modelo financeiro (créditos, projeção, pagamento) do finance-cache.json
  hashpilot finance fetch [--wallet <carteira>] [--replay <arquivo>] [--now <epoch>]
                                         Consulta pública Kryptex/CoinGecko agora, sem gravar cache nem histórico
                                         (--replay: respostas gravadas {url: json}, sem rede)
  hashpilot process inspect <pid> [--command-line]
                                         Identidade de um processo (PID, criação, executável, argv)
  hashpilot process miners               SRBMiner em execução e as GPUs (--gpu-id) que ocupam
  hashpilot process owned [--manager <rig-manager.json>]
                                         Confere se os processos do rig-manager.json ainda são os mesmos
                                         (nunca encerra nada)
  hashpilot recovery --gpu <uuid> [--operations <operations.sqlite3>] [--now EPOCH]
                                         Orçamento de recuperação e incidentes recentes da GPU
  hashpilot miner [--base <pasta>]      Versão selecionada do SRBMiner e conferência do SHA-256
                                         do executável pelo catálogo (só lê; não executa nada)
  hashpilot rig --request AÇÃO --state-dir D [--gpu-uuid U] [--profile P] [--objective O]
  hashpilot rig --serve --state-dir D --log-dir L --settings S --miner-path M --python PY
                [--project-dir P] [--adopt-legacy] [--migrate-legacy] [--legacy-uuid U]
                [--reference-rtx3070] [--check]
  hashpilot contribution --wallet W --settings S --state-dir D [--port N]
                                         Proxy Stratum local da contribuição opcional (como
                                         contribution.py); roda até Ctrl+C
  hashpilot gpu-agent --control --miner-pid PID --uuid U [--index N] [--miner-index N]
                [--duration S] [--objective O] [--profile P] [--state-dir D]
                [--monitor-state-dir D] [--log-dir D] [--miner-log L] [--project-dir P]
                [--samples N] [--ollama]
                                         Supervisor NVIDIA (como gpu_agent.py --control):
                                         único escritor de clock e potência enquanto o
                                         minerador vive; exige administrador; sai com 1
                                         quando o gerenciador deve parar o minerador
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
        Some("process") => process_command(&arguments[1..]),
        Some("recovery") => recovery_command(&arguments[1..]),
        Some("miner") => miner_command(&arguments[1..]),
        Some("nvml") => nvml_command(&arguments[1..]),
        Some("rig") => rig_cli::rig_command(&arguments[1..]),
        Some("contribution") => contribution_command(&arguments[1..]),
        Some("gpu-agent") => gpu_agent_command(&arguments[1..]),
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

/// `hashpilot recovery`: `OperationalStore.budget` e `recent_events`, só leitura (as
/// tabelas são criadas se faltarem, como no Python, mas nada é gravado).
fn recovery_command(arguments: &[String]) -> Result<Value, Failure> {
    let options = Options::parse(arguments, &["gpu", "operations", "now"], &[])?;
    let gpu = options
        .text("gpu")
        .ok_or_else(|| Failure::Usage("recovery exige --gpu <uuid>".into()))?;
    let path = match options.text("operations") {
        Some(path) => PathBuf::from(path),
        None => state_dir()?.join("operations.sqlite3"),
    };
    let now = match options.text("now") {
        Some(text) => text
            .parse()
            .map_err(|_| Failure::Usage("--now precisa ser número".into()))?,
        None => std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0.0, |elapsed| elapsed.as_secs_f64()),
    };
    let store = hashpilot_rig::health::OperationalStore::new(&path).map_err(Failure::Runtime)?;
    Ok(json!({
        "budget": store.budget(gpu, now).map_err(Failure::Runtime)?,
        "incidents": store.recent_events(gpu, 5).map_err(Failure::Runtime)?,
    }))
}

/// `hashpilot miner`: `miner_update.selected_version` e `active_executable`. Só lê o
/// executável para conferir o SHA-256; nunca o executa.
fn miner_command(arguments: &[String]) -> Result<Value, Failure> {
    let options = Options::parse(arguments, &["base"], &[])?;
    let base = match options.text("base") {
        Some(path) => PathBuf::from(path),
        None => {
            let local = std::env::var_os("LOCALAPPDATA")
                .ok_or_else(|| Failure::Runtime("LOCALAPPDATA não definido".into()))?;
            PathBuf::from(local).join("Mining").join("Pearl")
        }
    };
    let catalog = hashpilot_rig::miner_install::Catalog::default();
    let version = catalog.selected_version(&base).map_err(Failure::Runtime)?;
    let path = catalog
        .executable(&json!(version), &base, false)
        .map_err(Failure::Runtime)?;
    let verified = catalog.executable(&json!(version), &base, true);
    Ok(json!({
        "base": base.to_string_lossy(),
        "version": version,
        "executable": path.to_string_lossy(),
        "verified": verified.is_ok(),
        "error": verified.err(),
    }))
}

/// `hashpilot nvml <uuid>`: só leitura pelo NVML (ventoinhas, P-states, offsets e
/// potência). Nunca chama uma função `set_*` ou `reset_*`.
/// `contribution.py main()`: o proxy roda até Ctrl+C (ou até o rig encerrar o processo).
fn contribution_command(arguments: &[String]) -> Result<Value, Failure> {
    use std::sync::atomic::Ordering;
    let options = Options::parse(arguments, &["wallet", "settings", "state-dir", "port"], &[])?;
    let required = |name: &str| {
        options
            .text(name)
            .ok_or_else(|| Failure::Usage(format!("--{name} é obrigatório")))
    };
    let port = match options.text("port") {
        None => 0,
        Some(text) => text
            .parse::<u16>()
            .map_err(|_| Failure::Usage("--port precisa ser um inteiro de 0 a 65535".into()))?,
    };
    let mut proxy = hashpilot_rig::contribution::Proxy::new(
        required("wallet")?,
        std::path::Path::new(required("settings")?),
        std::path::Path::new(required("state-dir")?),
        port,
        None,
        None,
    )
    .map_err(Failure::Usage)?;
    if hashpilot_rig::win32::install_ctrl_handler() {
        let stop = proxy.stop.clone();
        std::thread::spawn(move || {
            while !hashpilot_rig::win32::CTRL_STOP.load(Ordering::SeqCst) {
                std::thread::sleep(std::time::Duration::from_millis(100));
            }
            stop.store(true, Ordering::SeqCst);
        });
    }
    proxy.run().map_err(Failure::Runtime)?;
    Ok(json!({"stopped": true, "status": proxy.status}))
}

/// `hashpilot gpu-agent --control`: o supervisor NVIDIA, com os argumentos do
/// `gpu_agent.py`. Código 1 é o sinal para o gerenciador parar o minerador desta GPU.
fn gpu_agent_command(arguments: &[String]) -> Result<Value, Failure> {
    use hashpilot_rig::controller::{ControlArgs, Hooks, MinerSource, control};
    let options = Options::parse(
        arguments,
        &[
            "miner-pid",
            "uuid",
            "index",
            "gpu",
            "miner-index",
            "duration",
            "objective",
            "profile",
            "state-dir",
            "monitor-state-dir",
            "log-dir",
            "miner-log",
            "project-dir",
            "samples",
        ],
        &["control", "ollama"],
    )?;
    if !options.flag("control") {
        return Err(Failure::Usage(
            "o port em Rust só tem o modo --control".into(),
        ));
    }
    let integer = |name: &str| -> Result<Option<i64>, Failure> {
        options
            .text(name)
            .map(|text| {
                text.parse::<i64>()
                    .map_err(|_| Failure::Usage(format!("--{name} precisa ser um inteiro")))
            })
            .transpose()
    };
    let miner_pid = integer("miner-pid")?
        .filter(|pid| *pid > 0)
        .ok_or_else(|| Failure::Usage("--control exige --miner-pid".into()))?;
    let choice = |name: &str, default: &str, allowed: &[&str]| -> Result<String, Failure> {
        let value = options.text(name).unwrap_or(default);
        if allowed.contains(&value) {
            Ok(value.to_string())
        } else {
            Err(Failure::Usage(format!("--{name} inválido: {value}")))
        }
    };
    let path = |name: &str| options.text(name).map(PathBuf::from);
    let state = match path("state-dir") {
        Some(directory) => directory,
        None => state_dir().map_err(Failure::Runtime)?,
    };
    let monitor =
        path("monitor-state-dir").unwrap_or_else(|| state_dir().unwrap_or_else(|_| state.clone()));
    let source = MinerSource {
        miner_log: path("miner-log"),
        log_dir: path("log-dir"),
        miner_index: integer("miner-index")?,
    };
    let args = ControlArgs {
        miner_pid: u32::try_from(miner_pid)
            .map_err(|_| Failure::Usage("--miner-pid fora do intervalo".into()))?,
        uuid: options.text("uuid").map(str::to_owned),
        index: integer("index")?.or(integer("gpu")?),
        duration: integer("duration")?.unwrap_or(300),
        objective: choice("objective", "performance", &["performance", "efficiency"])?,
        profile: choice(
            "profile",
            "performance",
            &["performance", "balanced", "cool"],
        )?,
        source: source.clone(),
        samples: integer("samples")?.unwrap_or(0).max(0) as u64,
    };
    let project = match path("project-dir") {
        Some(directory) => directory,
        None => std::env::current_dir().map_err(|error| Failure::Runtime(error.to_string()))?,
    };
    let software = hashpilot_rig::manager::software_identity(&project);
    if !hashpilot_rig::win32::install_ctrl_handler() {
        eprintln!(
            "aviso: tratamento de Ctrl+C indisponível; o gerenciador para pelo gpu-agent-stop.json"
        );
    }
    let mut hooks = Hooks::real(monitor.clone(), source, software, |file: &Path| {
        logs::read_log_tail(file, logs::TAIL_LIMIT)
    })
    .map_err(|error| Failure::Runtime(error.message))?;
    match control(&args, &state, &monitor, &mut hooks) {
        Ok(0) => Ok(json!({"code": 0})),
        Ok(code) => {
            let status =
                std::fs::read_to_string(state.join("gpu-agent-control.json")).unwrap_or_default();
            let error = serde_json::from_str::<Value>(&status)
                .ok()
                .and_then(|status| {
                    status
                        .get("error")
                        .and_then(Value::as_str)
                        .map(str::to_owned)
                })
                .unwrap_or_else(|| format!("controle encerrado com código {code}"));
            Err(Failure::Runtime(error))
        }
        Err(error) => Err(Failure::Runtime(error.describe())),
    }
}

fn nvml_command(arguments: &[String]) -> Result<Value, Failure> {
    use hashpilot_rig::nvml::Nvml;
    let options = Options::parse(arguments, &[], &[])?;
    let [uuid] = options.positional.as_slice() else {
        return Err(Failure::Usage("uso: hashpilot nvml <uuid>".into()));
    };
    let mut api = hashpilot_rig::nvml::Library::open(uuid).map_err(Failure::Runtime)?;
    let read = |result: Result<Value, hashpilot_rig::nvml::NvmlError>| {
        result.unwrap_or_else(|error| json!({"error": error.to_string()}))
    };
    let fans = read(api.get_num_fans().map(|count| {
        json!(
            (0..count)
                .map(|fan| api_policy(&mut api, fan))
                .collect::<Vec<_>>()
        )
    }));
    let states = hashpilot_rig::gpu_defaults::states(&mut api);
    let offsets = match &states {
        Ok(states) => json!(states
            .iter()
            .flat_map(|state| [0, 2].map(|domain| (*state, domain)))
            .map(|(state, domain)| match api.get_clock_offsets(domain, state) {
                Ok(value) => json!({"pstate": state, "domain": domain, "offset_mhz": value.offset, "min": value.minimum, "max": value.maximum}),
                Err(error) => json!({"pstate": state, "domain": domain, "error": error.to_string()}),
            })
            .collect::<Vec<_>>()),
        Err(error) => json!({"error": error}),
    };
    let power = json!({
        "default_mw": read(api.get_power_default_limit().map(|value| json!(value))),
        "current_mw": read(api.get_power_limit().map(|value| json!(value))),
    });
    let closed = api.close().map_err(|error| error.to_string());
    Ok(
        json!({"uuid": uuid, "fans": fans, "pstates": states.ok(), "offsets": offsets, "power": power, "shutdown_error": closed.err()}),
    )
}

fn api_policy(api: &mut hashpilot_rig::nvml::Library, fan: u32) -> Value {
    use hashpilot_rig::nvml::Nvml;
    match api.get_fan_control_policy(fan) {
        Ok(policy) => json!({"fan": fan, "policy": policy}),
        Err(error) => json!({"fan": fan, "error": error.to_string()}),
    }
}

/// `hashpilot process`: só leitura; nenhum processo é criado nem encerrado.
fn process_command(arguments: &[String]) -> Result<Value, Failure> {
    let options = Options::parse(arguments, &["manager"], &["command-line"])?;
    let mut backend = hashpilot_rig::ProcessBackend::new();
    match options.positional.as_slice() {
        [action, pid] if action == "inspect" => {
            let pid: u64 = pid
                .parse()
                .map_err(|_| Failure::Usage("o PID precisa ser inteiro positivo".into()))?;
            Ok(backend
                .inspect(&json!(pid), options.flag("command-line"))
                .unwrap_or(Value::Null))
        }
        [action] if action == "miners" => {
            let miners = hashpilot_rig::process::running_miners()?;
            Ok(json!(miners))
        }
        [action] if action == "owned" => {
            let path = match options.text("manager") {
                Some(path) => PathBuf::from(path),
                None => state_dir()?.join("rig-manager.json"),
            };
            let manager = read_json(&path)?;
            let mut result = Map::new();
            if let Some(Value::Object(devices)) = manager.get("devices") {
                for (uuid, device) in devices {
                    for role in ["miner", "controller"] {
                        if let Some(identity) = device.get(role).filter(|value| value.is_object()) {
                            result.insert(
                                format!("{uuid}:{role}"),
                                json!({"pid": identity["pid"], "owned": backend.is_owned(identity)}),
                            );
                        }
                    }
                }
            }
            Ok(json!({"manager": path, "processes": result}))
        }
        _ => Err(Failure::Usage(
            "process exige: inspect <pid>, miners ou owned".into(),
        )),
    }
}

fn history_command(arguments: &[String]) -> Result<Value, Failure> {
    if arguments.first().map(String::as_str) == Some("retention") {
        return retention_command(&arguments[1..]);
    }
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

/// `hashpilot history retention`: a única consulta de `history` que pode gravar, e só
/// com `--apply`.
fn retention_command(arguments: &[String]) -> Result<Value, Failure> {
    use hashpilot_history::retention::{self, DEFAULT_MAX_AGE_DAYS, Policy};
    let options = Options::parse(
        arguments,
        &["db", "state-dir", "log-dir", "days", "now"],
        &["apply"],
    )?;
    if !options.positional.is_empty() {
        return Err(Failure::Usage(
            "history retention não aceita argumentos posicionais".into(),
        ));
    }
    let apply = options.flag("apply");
    let days = options.number("days")?.unwrap_or(DEFAULT_MAX_AGE_DAYS);
    if days < 1.0 {
        return Err(Failure::Usage("--days precisa ser pelo menos 1".into()));
    }
    // Um relógio inventado só serve para simular; gravar usa sempre a hora real.
    let now = match options.number("now")? {
        Some(_) if apply => {
            return Err(Failure::Usage(
                "--now só vale sem --apply (simulação)".into(),
            ));
        }
        Some(now) => now,
        None => nvidia::now_epoch(),
    };
    let state = match options.text("state-dir") {
        Some(path) => PathBuf::from(path),
        None => state_dir()?,
    };
    let database = options
        .text("db")
        .map_or_else(|| hashpilot_history::default_path(&state), PathBuf::from);
    let log_dir = match options.text("log-dir") {
        Some(path) => PathBuf::from(path),
        None => {
            let settings = load_settings(&state.join("settings.json"))?;
            config::log_dir(&state, &settings, &process_env)
        }
    };
    let policy = Policy {
        max_age_seconds: days * 86_400.0,
        ..Policy::default()
    };
    let agents = retention::agent_databases(&state, &log_dir);
    Ok(retention::run(&database, &agents, now, &policy, apply))
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

#[cfg(test)]
mod retention_tests {
    use super::*;

    fn args(items: &[&str]) -> Vec<String> {
        items.iter().map(|item| (*item).to_string()).collect()
    }

    #[test]
    fn retencao_simulada_nao_cria_banco_e_recusa_relogio_falso_ao_gravar() {
        let folder = std::env::temp_dir().join(format!("hp-cli-retention-{}", std::process::id()));
        std::fs::create_dir_all(&folder).unwrap();
        let state = folder.to_string_lossy().into_owned();
        let Ok(report) = history_command(&args(&[
            "retention",
            "--state-dir",
            &state,
            "--log-dir",
            &state,
            "--now",
            "1800000000",
        ])) else {
            panic!("retenção simulada falhou");
        };
        assert_eq!(report["applied"], false);
        assert_eq!(report["history"]["exists"], false);
        assert_eq!(report["agents"], json!([]));
        assert!(!folder.join("history.sqlite3").exists());
        for bad in [
            args(&[
                "retention",
                "--apply",
                "--now",
                "1",
                "--state-dir",
                &state,
                "--log-dir",
                &state,
            ]),
            args(&[
                "retention",
                "--days",
                "0",
                "--state-dir",
                &state,
                "--log-dir",
                &state,
            ]),
            args(&["retention", "extra"]),
        ] {
            assert!(matches!(history_command(&bad), Err(Failure::Usage(_))));
        }
        let _ = std::fs::remove_dir_all(&folder);
    }
}
