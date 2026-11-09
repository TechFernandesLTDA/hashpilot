//! `hashpilot rig`: o serviço do rig em Rust, com os mesmos argumentos do `rig.py`.
//!
//! - `--request <ação>` só grava um pedido em `rig-requests/`, como `rig.write_request`.
//! - `--serve` liga o `RigManager` aos sensores (`nvidia-smi`), aos logs e ao
//!   minerador reais. Inicia e para mineração de verdade: exige administrador.
//! - `--serve --check` confere tudo o que o `--serve` precisa (administrador, scripts
//!   Python, executável do minerador pelo SHA-256, GPUs e sensores) **sem** criar o
//!   gerenciador, sem gravar nada e sem iniciar nenhum processo.

use std::path::{Path, PathBuf};
use std::sync::atomic::Ordering;
use std::time::{Duration, Instant};

use hashpilot_rig::manager::{Paths, RigManager, ServeOptions, write_request};
use hashpilot_rig::nvidia::{CapabilityCache, detect_nvidia_gpus, read_gpu_samples, smi_command};
use hashpilot_rig::snapshots::{StampZone, collect_snapshots};
use serde_json::{Map, Value, json};

use super::{Failure, Options};
use crate::logs::{TAIL_LIMIT, read_log_tail};
use crate::nvidia::now_epoch;

const REQUESTS: [&str; 8] = [
    "reconcile",
    "start",
    "stop",
    "stop_all",
    "benchmark",
    "shutdown",
    "restart",
    "profile",
];
const WITH_VALUE: [&str; 11] = [
    "request",
    "profile",
    "gpu-uuid",
    "objective",
    "state-dir",
    "log-dir",
    "settings",
    "miner-path",
    "python",
    "project-dir",
    "legacy-uuid",
];
const FLAGS: [&str; 5] = [
    "serve",
    "adopt-legacy",
    "migrate-legacy",
    "reference-rtx3070",
    "check",
];
/// Scripts que o rig ainda inicia em Python (controlador e proxy de contribuição).
const SCRIPTS: [&str; 2] = ["gpu_agent.py", "contribution.py"];

fn choice<'a>(
    options: &'a Options,
    name: &str,
    allowed: &[&str],
    default: &'a str,
) -> Result<&'a str, Failure> {
    let value = options.text(name).unwrap_or(default);
    if allowed.contains(&value) {
        Ok(value)
    } else {
        Err(Failure::Usage(format!(
            "--{name}: escolha inválida: '{value}' (opções: {})",
            allowed.join(", ")
        )))
    }
}

/// O que o `--serve` vai usar, já validado.
struct Serve {
    paths: Paths,
    options: ServeOptions,
}

fn parse_serve(options: &Options, state_dir: &Path) -> Result<Serve, Failure> {
    if !options.flag("serve") {
        return Err(Failure::Usage(
            "Use --serve para iniciar o gerenciamento explícito de mineração".into(),
        ));
    }
    let (Some(log_dir), Some(settings), Some(miner)) = (
        options.text("log-dir"),
        options.text("settings"),
        options.text("miner-path"),
    ) else {
        return Err(Failure::Usage(
            "--serve exige --log-dir, --settings e --miner-path".into(),
        ));
    };
    let python = options.text("python").ok_or_else(|| {
        Failure::Usage(
            "--serve exige --python (o Python que roda gpu_agent.py e contribution.py)".into(),
        )
    })?;
    let legacy = options.text("legacy-uuid").map(str::to_owned);
    if options.flag("migrate-legacy") && legacy.is_none() {
        return Err(Failure::Usage(
            "--migrate-legacy exige --legacy-uuid explícito".into(),
        ));
    }
    if options.flag("reference-rtx3070") && legacy.is_none() {
        return Err(Failure::Usage(
            "Reference profile requires explicit --legacy-uuid".into(),
        ));
    }
    let project_dir = match options.text("project-dir") {
        Some(path) => PathBuf::from(path),
        None => std::env::current_dir().map_err(|error| Failure::Runtime(error.to_string()))?,
    };
    Ok(Serve {
        paths: Paths {
            state_dir: state_dir.to_path_buf(),
            log_dir: PathBuf::from(log_dir),
            settings_path: PathBuf::from(settings),
            miner_path: PathBuf::from(miner),
            python_path: PathBuf::from(python),
            project_dir,
        },
        options: ServeOptions {
            adopt_legacy: options.flag("adopt-legacy"),
            migrate_legacy_uuid: if options.flag("migrate-legacy") {
                legacy.clone()
            } else {
                None
            },
            reference_rtx3070: options.flag("reference-rtx3070"),
            reference_uuid: legacy,
        },
    })
}

/// Scripts Python ausentes na pasta do projeto.
fn missing_scripts(project_dir: &Path) -> Vec<&'static str> {
    SCRIPTS
        .into_iter()
        .filter(|script| !project_dir.join(script).is_file())
        .collect()
}

pub fn rig_command(arguments: &[String]) -> Result<Value, Failure> {
    let options = Options::parse(arguments, &WITH_VALUE, &FLAGS)?;
    if let Some(extra) = options.positional.first() {
        return Err(Failure::Usage(format!("argumento inesperado: {extra}")));
    }
    let state_dir = PathBuf::from(
        options
            .text("state-dir")
            .ok_or_else(|| Failure::Usage("--state-dir é obrigatório".into()))?,
    );
    let profile = choice(
        &options,
        "profile",
        &["performance", "balanced", "cool"],
        "performance",
    )?;
    let objective = choice(
        &options,
        "objective",
        &["performance", "efficiency"],
        "performance",
    )?;
    if let Some(request) = options.text("request") {
        if !REQUESTS.contains(&request) {
            return Err(Failure::Usage(format!(
                "--request: escolha inválida: '{request}' (opções: {})",
                REQUESTS.join(", ")
            )));
        }
        let gpu = options.text("gpu-uuid");
        if ["start", "stop", "restart", "profile"].contains(&request) && gpu.is_none() {
            return Err(Failure::Usage(
                "--request start/stop exige --gpu-uuid".into(),
            ));
        }
        let mut fields = Map::new();
        fields.insert("objective".into(), json!(objective));
        fields.insert("profile".into(), json!(profile));
        return write_request(&state_dir, request, gpu, &fields).map_err(Failure::Runtime);
    }
    let serve = parse_serve(&options, &state_dir)?;
    if options.flag("check") {
        return Ok(check(&serve));
    }
    if !hashpilot_rig::win32::is_user_an_admin() {
        return Err(Failure::Usage(
            "O serviço de mineração precisa ser iniciado como administrador no Windows".into(),
        ));
    }
    let missing = missing_scripts(&serve.paths.project_dir);
    if !missing.is_empty() {
        return Err(Failure::Runtime(format!(
            "Scripts ausentes em {}: {}",
            serve.paths.project_dir.display(),
            missing.join(", ")
        )));
    }
    run(serve)
}

/// `--serve --check`: só leitura. Não cria o `RigManager` (que abriria o banco e o
/// registro da pasta de estado), não grava nada e não inicia processo nenhum.
fn check(serve: &Serve) -> Value {
    let now = now_epoch();
    let catalog = hashpilot_rig::miner_install::Catalog::default();
    let base = serve
        .paths
        .miner_path
        .parent()
        .and_then(Path::parent)
        .map(Path::to_path_buf)
        .unwrap_or_default();
    let selected = catalog.active_executable(&base, true);
    let miner_matches = selected.as_ref().ok().map(|path| {
        hashpilot_rig::normalized_path(&path.to_string_lossy())
            == hashpilot_rig::normalized_path(&serve.paths.miner_path.to_string_lossy())
    });
    let mut cache = CapabilityCache::new();
    let mut command = smi_command;
    let gpus = detect_nvidia_gpus(&mut command, None, Some(&mut cache), now);
    let samples = gpus
        .as_ref()
        .ok()
        .map(|devices| read_gpu_samples(devices, &mut command, now));
    let summary = |result: &Result<Vec<Value>, String>| {
        match result {
        Ok(devices) => json!(devices
            .iter()
            .map(|gpu| json!({"uuid": gpu["uuid"], "index": gpu["index"], "name": gpu["name"],
                "catalog_match": gpu.get("catalog_match"), "supported_clocks": gpu["supported_graphics_clocks_mhz"].as_array().map(Vec::len)}))
            .collect::<Vec<_>>()),
        Err(error) => json!({"error": error}),
    }
    };
    let missing = missing_scripts(&serve.paths.project_dir);
    let admin = hashpilot_rig::win32::is_user_an_admin();
    let ready = admin
        && missing.is_empty()
        && miner_matches == Some(true)
        && gpus.as_ref().is_ok_and(|devices| !devices.is_empty());
    json!({
        "ready": ready,
        "admin": admin,
        "project_dir": serve.paths.project_dir.to_string_lossy(),
        "missing_scripts": missing,
        "python": serve.paths.python_path.to_string_lossy(),
        "python_exists": serve.paths.python_path.is_file(),
        "miner": {
            "requested": serve.paths.miner_path.to_string_lossy(),
            "verified": selected.as_ref().ok().map(|path| path.to_string_lossy().into_owned()),
            "error": selected.err(),
            "matches_selected": miner_matches,
        },
        "gpus": summary(&gpus),
        "samples": samples.map(|result| match result {
            Ok(samples) => json!(samples
                .iter()
                .map(|(uuid, sample)| json!({"uuid": uuid, "temperature_c": sample.get("temperature.gpu"), "power_w": sample.get("power.draw"), "error": sample.get("error")}))
                .collect::<Vec<_>>()),
            Err(error) => json!({"error": error}),
        }),
        "options": {
            "adopt_legacy": serve.options.adopt_legacy,
            "migrate_legacy_uuid": serve.options.migrate_legacy_uuid,
            "reference_rtx3070": serve.options.reference_rtx3070,
            "reference_uuid": serve.options.reference_uuid,
        },
        "started": false,
    })
}

/// O serviço real. Ctrl+C (ou fechar o console) grava um pedido `shutdown`: o ciclo
/// seguinte para todas as GPUs, restaura os padrões e publica o estado, como o painel.
fn run(serve: Serve) -> Result<Value, Failure> {
    let state_dir = serve.paths.state_dir.clone();
    let mut cache = CapabilityCache::new();
    let detector = Box::new(move || {
        let mut command = smi_command;
        detect_nvidia_gpus(&mut command, None, Some(&mut cache), now_epoch())
    });
    let mut manager = RigManager::new(
        serve.paths,
        Box::new(hashpilot_rig::ProcessBackend::new()),
        detector,
        Box::new(now_epoch),
        None,
        hashpilot_rig::continuity::IdleSleepGuard::new(),
    )
    .map_err(Failure::Runtime)?;
    if !hashpilot_rig::win32::install_ctrl_handler() {
        return Err(Failure::Runtime(
            "Não foi possível instalar o tratamento de Ctrl+C".into(),
        ));
    }
    let mut collect = |devices: &[Value], entries: &Map<String, Value>| {
        let now = now_epoch();
        let mut command = smi_command;
        let samples = read_gpu_samples(devices, &mut command, now)?;
        collect_snapshots(
            devices,
            entries,
            &samples,
            now,
            StampZone::Local,
            &mut |path: &Path| read_log_tail(path, TAIL_LIMIT),
        )
    };
    let mut requested = false;
    let mut sleep = |duration: Duration| {
        let end = Instant::now() + duration;
        loop {
            if hashpilot_rig::win32::CTRL_STOP.load(Ordering::SeqCst) && !requested {
                requested = true;
                if let Err(error) = write_request(&state_dir, "shutdown", None, &Map::new()) {
                    eprintln!("erro ao pedir o desligamento: {error}");
                }
            }
            let now = Instant::now();
            if now >= end {
                break;
            }
            std::thread::sleep((end - now).min(Duration::from_millis(50)));
        }
    };
    manager
        .serve(&serve.options, &mut collect, &mut sleep)
        .map_err(Failure::Runtime)?;
    Ok(json!({"stopped": true, "error": manager.error}))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn arguments(text: &str) -> Vec<String> {
        text.split_whitespace().map(str::to_owned).collect()
    }

    fn usage(text: &str) -> String {
        match rig_command(&arguments(text)) {
            Err(Failure::Usage(message)) => message,
            Err(Failure::Runtime(message)) => panic!("{text}: erro de execução {message}"),
            Ok(value) => panic!("{text}: aceitou {value}"),
        }
    }

    #[test]
    fn invalid_arguments_are_refused_before_anything_runs() {
        assert_eq!(usage("--serve"), "--state-dir é obrigatório");
        assert_eq!(
            usage("--state-dir s"),
            "Use --serve para iniciar o gerenciamento explícito de mineração"
        );
        assert_eq!(
            usage("--state-dir s --serve --log-dir l --settings c"),
            "--serve exige --log-dir, --settings e --miner-path"
        );
        assert!(
            usage("--state-dir s --serve --log-dir l --settings c --miner-path m")
                .starts_with("--serve exige --python")
        );
        let base = "--state-dir s --serve --log-dir l --settings c --miner-path m --python p";
        assert_eq!(
            usage(&format!("{base} --migrate-legacy")),
            "--migrate-legacy exige --legacy-uuid explícito"
        );
        assert_eq!(
            usage(&format!("{base} --reference-rtx3070")),
            "Reference profile requires explicit --legacy-uuid"
        );
        assert!(usage("--state-dir s --request fly").starts_with("--request: escolha inválida"));
        assert!(
            usage("--state-dir s --request miner_update")
                .starts_with("--request: escolha inválida")
        );
        assert!(
            usage("--state-dir s --request stop_all --profile turbo")
                .starts_with("--profile: escolha inválida")
        );
        assert!(
            usage("--state-dir s --request stop_all --objective lucro")
                .starts_with("--objective: escolha inválida")
        );
        for action in ["start", "stop", "restart", "profile"] {
            assert_eq!(
                usage(&format!("--state-dir s --request {action}")),
                "--request start/stop exige --gpu-uuid"
            );
        }
        assert_eq!(usage("--state-dir s extra"), "argumento inesperado: extra");
        assert!(usage("--state-dir s --desconhecida").starts_with("opção desconhecida"));
    }

    #[test]
    fn request_writes_the_same_payload_as_python() {
        let folder = std::env::temp_dir().join(format!("hashpilot-rig-cli-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&folder);
        let state = folder.to_string_lossy().into_owned();
        let Ok(payload) = rig_command(&arguments(&format!(
            "--state-dir {state} --request stop --gpu-uuid GPU-qa --profile cool"
        ))) else {
            panic!("pedido recusado");
        };
        assert_eq!(payload["action"], "stop");
        assert_eq!(payload["gpu_uuid"], "GPU-qa");
        assert_eq!(payload["objective"], "performance");
        assert_eq!(payload["profile"], "cool");
        assert!(payload["at"].is_f64());
        let id = payload["id"].as_str().unwrap();
        assert_eq!(id.len(), 32);
        let written: Value = serde_json::from_str(
            &std::fs::read_to_string(folder.join("rig-requests").join(format!("{id}.json")))
                .unwrap(),
        )
        .unwrap();
        assert_eq!(written, payload);
        let keys: Vec<&String> = written.as_object().unwrap().keys().collect();
        assert_eq!(
            keys,
            ["objective", "profile", "id", "action", "gpu_uuid", "at"]
        );
        let _ = std::fs::remove_dir_all(&folder);
    }

    #[test]
    fn serve_options_follow_the_python_flags() {
        let options = Options::parse(
            &arguments("--serve --log-dir l --settings c --miner-path m --python p --legacy-uuid GPU-x --reference-rtx3070 --adopt-legacy --project-dir proj"),
            &WITH_VALUE,
            &FLAGS,
        )
        .ok()
        .unwrap();
        let serve = parse_serve(&options, Path::new("s")).ok().unwrap();
        assert!(serve.options.adopt_legacy && serve.options.reference_rtx3070);
        assert_eq!(serve.options.migrate_legacy_uuid, None);
        assert_eq!(serve.options.reference_uuid.as_deref(), Some("GPU-x"));
        assert_eq!(serve.paths.project_dir, PathBuf::from("proj"));
        let migrate = Options::parse(&arguments("--serve --log-dir l --settings c --miner-path m --python p --legacy-uuid GPU-x --migrate-legacy"), &WITH_VALUE, &FLAGS).ok().unwrap();
        assert_eq!(
            parse_serve(&migrate, Path::new("s"))
                .ok()
                .unwrap()
                .options
                .migrate_legacy_uuid
                .as_deref(),
            Some("GPU-x")
        );
    }
}
