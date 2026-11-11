//! `rig.RigManager`: até seis mineradores NVIDIA independentes por UUID.
//!
//! Port linha a linha do `rig.py`. O registro (`rig-manager.json`), os pedidos
//! (`rig-requests/`), o `settings.json` e o `operations.sqlite3` são os mesmos do
//! Python, então os dois serviços leem o estado um do outro. Os processos passam
//! pela interface [`Backend`]; os testes usam um backend falso, igual ao do
//! `test_rig.py`, e nenhum deles inicia o minerador de verdade.
//!
//! O laço `serve`, a ação `miner_update` e a validação com reversão automática da
//! atualização estão aqui; o backend real restaura a GPU pelo NVML (`gpu_defaults`).

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use hashpilot_core::numbers::py_float;
use hashpilot_core::rig_config::{MAX_GPUS, device_key, resolve_devices, update_gpu_settings};
use hashpilot_core::text::py_str;
use hashpilot_core::thermal::MINER_OFF_TEMPERATURE_C;
use hashpilot_core::wallet::{AUTHOR_WALLET, valid_wallet};
use hashpilot_history::pyjson::{encode_spaced, py_eq, repr_str, truthy, type_name};
use serde_json::{Map, Value, json};

use crate::continuity::{ContinuityWindow, IdleSleepGuard, safe_sensor};
use crate::fsutil::atomic_json;
use crate::health::{OperationalStore, finite};
use crate::lease::Lease;
use crate::miner_install::Catalog;
use crate::process::normalized_path;
use crate::reference::{
    REFERENCE_RTX3070_UUID, profile, reference_device_confirmed, reference_memory_clock,
    reference_start_clock,
};
use crate::successor::{match_successor, option};

pub const MARKER: &str = "HashPilotRig-v1";
const APP_VERSION_SOURCE: &str = include_str!("../../../app_version.py");
const ACTIONS: [&str; 9] = [
    "reconcile",
    "start",
    "stop",
    "stop_all",
    "benchmark",
    "shutdown",
    "restart",
    "profile",
    "miner_update",
];

type Obj = Map<String, Value>;
static NULL: Value = Value::Null;

fn field<'a>(object: &'a Value, key: &str) -> &'a Value {
    object.get(key).unwrap_or(&NULL)
}

fn truncate(text: &str, limit: usize) -> String {
    text.chars().take(limit).collect()
}

fn now_wall() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0.0, |elapsed| elapsed.as_secs_f64())
}

pub fn random_hex(length: usize) -> String {
    use std::hash::{BuildHasher, Hasher};
    let mut out = String::new();
    while out.len() < length {
        let mut hasher = std::collections::hash_map::RandomState::new().build_hasher();
        hasher.write_u128(
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map_or(0, |d| d.as_nanos()),
        );
        out.push_str(&format!("{:016x}", hasher.finish()));
    }
    out.truncate(length);
    out
}

/// `str(Path)`: o texto que o Python passa ao backend.
fn path_text(path: &Path) -> String {
    path.to_string_lossy().into_owned()
}

/// Operações de processo do rig (`process_backend.ProcessBackend` ou um falso).
pub trait Backend {
    fn verify_miner(&mut self, miner_path: &Path) -> Result<(), String>;
    fn assert_gpu_available(&mut self, miner_path: &Path, index: &Value) -> Result<(), String>;
    fn start(
        &mut self,
        arguments: &[String],
        working_dir: &Path,
        log_path: Option<&Path>,
    ) -> Result<Value, String>;
    fn inspect(&mut self, pid: &Value) -> Option<Value>;
    fn is_owned(&mut self, identity: &Value) -> bool;
    fn stop(&mut self, identity: &Value) -> bool;
    fn wait(&mut self, identity: &Value, timeout: f64) -> bool;
    fn legacy_candidates(&mut self, miner_path: &Path) -> Result<Vec<Value>, String>;
    fn miner_device_map(&mut self, miner_path: &Path, devices: &[Value]) -> Result<Obj, String>;
    fn restore_defaults(&mut self, gpu: &Value, directory: &Path) -> Result<Value, String>;
}

/// O backend real. `verify_miner` confere o executável selecionado pelo SHA-256 do
/// catálogo; `restore_defaults` devolve a GPU aos padrões pelo NVML, com a trava de
/// controle da GPU (`gpu_defaults`).
impl Backend for crate::process::ProcessBackend {
    fn verify_miner(&mut self, miner_path: &Path) -> Result<(), String> {
        let base = miner_path
            .parent()
            .and_then(Path::parent)
            .unwrap_or(Path::new(""));
        let target = Catalog::default().active_executable(base, true)?;
        if normalized_path(&path_text(&target)) != normalized_path(&path_text(miner_path)) {
            return Err("Minerador selecionado difere do executável solicitado".into());
        }
        Ok(())
    }
    fn assert_gpu_available(&mut self, _miner_path: &Path, index: &Value) -> Result<(), String> {
        crate::process::ProcessBackend::assert_gpu_available(self, &py_str(index))
    }
    fn start(
        &mut self,
        arguments: &[String],
        working_dir: &Path,
        log_path: Option<&Path>,
    ) -> Result<Value, String> {
        crate::process::ProcessBackend::start(self, arguments, working_dir, log_path)
    }
    fn inspect(&mut self, pid: &Value) -> Option<Value> {
        crate::process::ProcessBackend::inspect(self, pid, false)
    }
    fn is_owned(&mut self, identity: &Value) -> bool {
        identity.is_object() && crate::process::ProcessBackend::is_owned(self, identity)
    }
    fn stop(&mut self, identity: &Value) -> bool {
        crate::process::ProcessBackend::stop(self, identity)
    }
    fn wait(&mut self, identity: &Value, timeout: f64) -> bool {
        crate::process::ProcessBackend::wait(
            self,
            identity,
            std::time::Duration::from_secs_f64(timeout.max(0.0)),
        )
    }
    fn legacy_candidates(&mut self, miner_path: &Path) -> Result<Vec<Value>, String> {
        crate::process::ProcessBackend::legacy_candidates(self, miner_path)
    }
    fn miner_device_map(&mut self, miner_path: &Path, devices: &[Value]) -> Result<Obj, String> {
        let map = crate::process::ProcessBackend::miner_device_map(self, miner_path, devices)?;
        Ok(devices
            .iter()
            .filter_map(|gpu| gpu.get("uuid").and_then(Value::as_str))
            .filter_map(|uuid| map.get(uuid).map(|index| (uuid.to_owned(), json!(index))))
            .collect())
    }
    fn restore_defaults(&mut self, gpu: &Value, directory: &Path) -> Result<Value, String> {
        crate::gpu_defaults::restore_defaults(gpu, directory, &mut open_nvml)
    }
}

/// Abre o NVML real da GPU (só no Windows).
fn open_nvml(uuid: &str) -> Result<Box<dyn crate::nvml::Nvml>, String> {
    #[cfg(windows)]
    {
        crate::nvml::Library::open(uuid)
            .map(|library| Box::new(library) as Box<dyn crate::nvml::Nvml>)
    }
    #[cfg(not(windows))]
    {
        crate::nvml::Library::open(uuid).map(|_| unreachable!())
    }
}

/// `contribution.support_configuration(settings, wallet)`.
pub fn support_configuration(settings: &Value, wallet: &Value) -> Value {
    let is_dict = settings.is_object();
    let valid = is_dict && valid_wallet(wallet);
    let configured = if is_dict {
        settings.get("wallet").unwrap_or(wallet)
    } else {
        &NULL
    };
    let same_wallet = py_eq(configured, wallet);
    let requested = is_dict && settings.get("support_enabled") == Some(&Value::Bool(true));
    let author = json!(AUTHOR_WALLET);
    let enabled = valid && same_wallet && requested && !py_eq(wallet, &author);
    let reason = if enabled {
        "Contribuição opcional habilitada: 57 min para sua carteira / 3 min para o autor"
    } else if py_eq(wallet, &author) {
        "Carteira do autor configurada; não existe contribuição adicional"
    } else if !valid || !same_wallet {
        "Configuração inválida ou carteira alterada; contribuição desligada"
    } else {
        "Contribuição opcional desligada"
    };
    json!({"enabled": enabled, "primary_wallet": wallet, "support_wallet": AUTHOR_WALLET, "percent": 5,
        "method": "mining_time", "reason": reason, "configuration_valid": valid && same_wallet})
}

/// `component_status.identity(root)`: versão do manifesto da release ou do código.
pub fn software_identity(root: &Path) -> Value {
    let manifest = root.join("RELEASE-MANIFEST.json");
    if let Ok(text) = std::fs::read_to_string(&manifest)
        && let Ok(value) = serde_json::from_str::<Value>(text.trim_start_matches('\u{feff}'))
    {
        let version = value.get("version").and_then(Value::as_str);
        let commit = value.get("commit").map_or(Some(""), Value::as_str);
        let version_ok = version.is_some_and(|text| {
            let rest = text.strip_prefix('v').unwrap_or("");
            let parts: Vec<&str> = rest.split('.').collect();
            text.starts_with('v')
                && parts.len() == 3
                && parts
                    .iter()
                    .all(|p| !p.is_empty() && p.chars().all(|c| c.is_ascii_digit()))
        });
        let commit_ok = commit.is_some_and(|c| {
            c.len() == 40 && c.chars().all(|ch| matches!(ch, 'a'..='f' | '0'..='9'))
        });
        if py_eq(value.get("schema").unwrap_or(&NULL), &json!(1)) && version_ok && commit_ok {
            return json!({"version": version, "revision": commit, "source": "release"});
        }
    }
    let source = APP_VERSION_SOURCE
        .lines()
        .find_map(|line| {
            line.strip_prefix("VERSION = ")
                .map(|value| value.trim().trim_matches('\'').trim_matches('"').to_owned())
        })
        .unwrap_or_default();
    if manifest.exists() {
        json!({"version": "não verificada", "revision": null, "source": "invalid"})
    } else {
        json!({"version": source, "revision": null, "source": "source"})
    }
}

/// `rig.write_request(state_dir, action, gpu_uuid, **fields)`.
pub fn write_request(
    state_dir: &Path,
    action: &str,
    gpu_uuid: Option<&str>,
    fields: &Obj,
) -> Result<Value, String> {
    if !ACTIONS.contains(&action) {
        return Err("Ação do rig inválida".into());
    }
    if let Some(uuid) = gpu_uuid {
        device_key(&json!(uuid))?;
    }
    let mut payload = fields.clone();
    let id = random_hex(32);
    payload.insert("id".into(), json!(id));
    payload.insert("action".into(), json!(action));
    payload.insert("gpu_uuid".into(), json!(gpu_uuid));
    payload.insert("at".into(), json!(now_wall()));
    let payload = Value::Object(payload);
    atomic_json(
        &state_dir.join("rig-requests").join(format!("{id}.json")),
        &payload,
        false,
        false,
    )?;
    Ok(payload)
}

fn read_json_file(path: &Path) -> Result<Value, std::io::Error> {
    let text = std::fs::read_to_string(path)?;
    serde_json::from_str(text.trim_start_matches('\u{feff}'))
        .map_err(|error| std::io::Error::new(std::io::ErrorKind::InvalidData, error))
}

/// Caminhos e executáveis do serviço.
pub struct Paths {
    pub state_dir: PathBuf,
    pub log_dir: PathBuf,
    pub settings_path: PathBuf,
    pub miner_path: PathBuf,
    pub python_path: PathBuf,
    /// Pasta dos scripts Python que o rig ainda inicia (`gpu_agent.py`, `contribution.py`).
    pub project_dir: PathBuf,
}

pub type Detector = Box<dyn FnMut() -> Result<Vec<Value>, String>>;
pub type Clock = Box<dyn Fn() -> f64>;
pub type ProxyReady = Box<dyn FnMut(&Value, &Value) -> Result<Value, String>>;

pub struct RigManager {
    pub state_dir: PathBuf,
    pub log_dir: PathBuf,
    pub settings_path: PathBuf,
    pub miner_path: PathBuf,
    pub python_path: PathBuf,
    pub project_dir: PathBuf,
    pub backend: Box<dyn Backend>,
    pub detector: Detector,
    clock: Clock,
    proxy_ready: Option<ProxyReady>,
    pub registry_path: PathBuf,
    pub devices: Obj,
    pub registry_valid: bool,
    pub error: Value,
    pub ignored: Vec<Value>,
    pub processed_ids: Vec<Value>,
    pub shutting_down: bool,
    pub reference_uuid: Option<String>,
    miner_map: Obj,
    mapping_signature: Option<Vec<String>>,
    pub require_recovery_sensors: bool,
    pub miner_update_pending: Value,
    pub miner_base: PathBuf,
    pub catalog: Catalog,
    pub continuity: ContinuityWindow,
    pub sleep_guard: IdleSleepGuard,
    pub operations: OperationalStore,
    /// Início da linha de comando do proxy de contribuição. `None` usa o
    /// `contribution.py` (o mesmo do `rig.py`); o serviço em Rust usa o próprio
    /// executável (`hashpilot contribution`).
    pub proxy_command: Option<Vec<String>>,
    /// Início da linha de comando do supervisor NVIDIA. `None` usa o `gpu_agent.py`; o
    /// serviço em Rust usa `hashpilot gpu-agent`, que recebe também `--project-dir`.
    pub controller_command: Option<Vec<String>>,
}

impl RigManager {
    pub fn new(
        paths: Paths,
        backend: Box<dyn Backend>,
        detector: Detector,
        clock: Clock,
        proxy_ready: Option<ProxyReady>,
        sleep_guard: IdleSleepGuard,
    ) -> Result<Self, String> {
        let operations = OperationalStore::new(&paths.state_dir.join("operations.sqlite3"))?;
        let miner_base = paths
            .miner_path
            .parent()
            .and_then(Path::parent)
            .map(Path::to_path_buf)
            .unwrap_or_default();
        let mut manager = Self {
            registry_path: paths.state_dir.join("rig-manager.json"),
            state_dir: paths.state_dir,
            log_dir: paths.log_dir,
            settings_path: paths.settings_path,
            miner_path: paths.miner_path,
            python_path: paths.python_path,
            project_dir: paths.project_dir,
            backend,
            detector,
            clock,
            proxy_ready,
            devices: Obj::new(),
            registry_valid: true,
            error: Value::Null,
            ignored: Vec::new(),
            processed_ids: Vec::new(),
            shutting_down: false,
            reference_uuid: None,
            miner_map: Obj::new(),
            mapping_signature: None,
            require_recovery_sensors: false,
            miner_update_pending: Value::Null,
            miner_base,
            catalog: Catalog::default(),
            continuity: ContinuityWindow::new(),
            sleep_guard,
            operations,
            proxy_command: None,
            controller_command: None,
        };
        manager.load_registry();
        manager.continuity.known = manager
            .devices
            .iter()
            .filter(|(_, entry)| entry.get("identity").is_some_and(Value::is_object))
            .map(|(key, entry)| (key.clone(), entry["identity"].clone()))
            .collect();
        let pending =
            read_json_file(&manager.state_dir.join("miner-update.json")).unwrap_or(Value::Null);
        if pending.is_object() && pending.get("state") == Some(&json!("validating")) {
            manager.miner_update_pending = pending;
        }
        Ok(manager)
    }

    pub fn clock(&self) -> f64 {
        (self.clock)()
    }

    fn load_registry(&mut self) {
        let text = match std::fs::read_to_string(&self.registry_path) {
            Ok(text) => text,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return,
            Err(error) => {
                self.invalid_registry(&error.to_string());
                return;
            }
        };
        if let Err(error) = self.parse_registry(text.trim_start_matches('\u{feff}')) {
            self.invalid_registry(&error);
        }
    }

    fn invalid_registry(&mut self, detail: &str) {
        self.devices = Obj::new();
        self.registry_valid = false;
        self.error = json!(format!(
            "Registro inválido: nenhuma ação em processos existentes foi autorizada. {detail}"
        ));
    }

    fn parse_registry(&mut self, text: &str) -> Result<(), String> {
        let payload: Value = serde_json::from_str(text).map_err(|error| error.to_string())?;
        let Value::Object(payload) = payload else {
            return Err(format!(
                "'{}' object has no attribute 'get'",
                type_name(&payload)
            ));
        };
        let invalid = "Registro de rig inválido".to_owned();
        if !payload
            .get("schema_version")
            .is_some_and(|value| py_eq(value, &json!(1)))
            || payload.get("marker") != Some(&json!(MARKER))
        {
            return Err(invalid);
        }
        let Some(Value::Object(devices)) = payload.get("devices") else {
            return Err(invalid);
        };
        if devices.len() > MAX_GPUS {
            return Err(invalid);
        }
        for (identifier, entry) in devices {
            device_key(&json!(identifier))?;
            if !entry.is_object() || entry.get("gpu_uuid") != Some(&json!(identifier)) {
                return Err("Identidade do registro de rig inválida".into());
            }
            for role in ["miner", "controller", "proxy"] {
                let identity = field(entry, role);
                if identity.is_null() {
                    continue;
                }
                let pid_ok = identity
                    .get("pid")
                    .is_some_and(|pid| pid.is_i64() || pid.is_u64() || pid.is_boolean());
                let created_ok = identity
                    .get("created_at")
                    .and_then(Value::as_str)
                    .is_some_and(|text| {
                        !text.is_empty() && text.chars().all(|c| c.is_ascii_digit())
                    });
                let executable_ok = identity.get("executable").is_some_and(Value::is_string);
                let argv_ok = identity
                    .get("argv")
                    .and_then(Value::as_array)
                    .is_some_and(|argv| !argv.is_empty());
                if !identity.is_object()
                    || !pid_ok
                    || !created_ok
                    || !executable_ok
                    || !argv_ok
                    || !truthy(field(identity, "launch_id"))
                {
                    return Err("Metadados de processo incompletos no registro".into());
                }
            }
            self.devices.insert(identifier.clone(), entry.clone());
        }
        let processed = match payload.get("processed_request_ids") {
            None => Vec::new(),
            Some(Value::Array(items)) => items.clone(),
            Some(Value::Object(map)) => map.keys().map(|key| json!(key)).collect(),
            Some(Value::String(text)) => text.chars().map(|c| json!(c.to_string())).collect(),
            Some(other) => return Err(format!("'{}' object is not iterable", type_name(other))),
        };
        self.processed_ids = processed[processed.len().saturating_sub(128)..].to_vec();
        if payload.get("reference_uuid") == Some(&json!(REFERENCE_RTX3070_UUID)) {
            self.reference_uuid = Some(REFERENCE_RTX3070_UUID.into());
        }
        Ok(())
    }

    /// `settings()`: o `settings.json` atual (um objeto JSON).
    pub fn settings(&self) -> Result<Value, String> {
        let text = std::fs::read_to_string(&self.settings_path).map_err(|error| {
            if error.kind() == std::io::ErrorKind::NotFound {
                format!(
                    "[Errno 2] No such file or directory: {}",
                    repr_str(&path_text(&self.settings_path))
                )
            } else {
                error.to_string()
            }
        })?;
        let payload: Value = serde_json::from_str(text.trim_start_matches('\u{feff}'))
            .map_err(|error| error.to_string())?;
        if !payload.is_object() {
            return Err("settings.json inválido".into());
        }
        Ok(payload)
    }

    pub fn publish(&mut self) -> Result<(), String> {
        if !self.registry_valid {
            return Ok(());
        }
        let payload = json!({
            "schema_version": 1,
            "marker": MARKER,
            "pid": std::process::id(),
            "sampled_at": self.clock(),
            "ready": !self.shutting_down,
            "max_gpus": MAX_GPUS,
            "devices": self.devices,
            "ignored_devices": self.ignored,
            "error": self.error,
            "processed_request_ids": self.processed_ids[self.processed_ids.len().saturating_sub(128)..],
            "reference_uuid": self.reference_uuid,
            "software": software_identity(&self.project_dir),
            "capabilities": ["stop_restore_v1", "miner_update_v1", "continuity_v1"],
            "continuity": {
                "idle_sleep_prevented": self.sleep_guard.active,
                "sleep_guard_error": self.sleep_guard.error,
                "resume_grace_until": self.continuity.grace_until(),
            },
        });
        atomic_json(&self.registry_path, &payload, false, false)
    }

    fn entry(&mut self, identifier: &str) -> &mut Obj {
        self.devices
            .get_mut(identifier)
            .and_then(Value::as_object_mut)
            .expect("entrada do registro presente")
    }

    fn set(&mut self, identifier: &str, pairs: Vec<(&str, Value)>) {
        let entry = self.entry(identifier);
        for (key, value) in pairs {
            entry.insert(key.to_owned(), value);
        }
    }

    fn pop(&mut self, identifier: &str, keys: &[&str]) {
        let entry = self.entry(identifier);
        for key in keys {
            entry.shift_remove(*key);
        }
    }

    fn get(&self, identifier: &str, key: &str) -> Value {
        self.devices
            .get(identifier)
            .map_or(Value::Null, |entry| field(entry, key).clone())
    }

    fn owned(&mut self, identity: &Value) -> bool {
        truthy(identity) && self.backend.is_owned(identity)
    }

    fn event(&self, identifier: &str, kind: &str, message: &str) -> Result<(), String> {
        self.operations
            .event(identifier, kind, message, self.clock())
    }

    /// `_wait_proxy`: espera o `support-mining.json` do proxy anunciar a porta.
    fn wait_proxy(
        &mut self,
        entry: &Value,
        identity: &Value,
        timeout: f64,
    ) -> Result<Value, String> {
        let deadline = std::time::Instant::now() + std::time::Duration::from_secs_f64(timeout);
        let path = PathBuf::from(py_str(field(entry, "log_dir"))).join("support-mining.json");
        while std::time::Instant::now() < deadline {
            if !self.backend.is_owned(identity) {
                return Err("Proxy encerrou antes de ficar pronto".into());
            }
            if let Ok(status) = read_json_file(&path)
                && status.get("ready") == Some(&Value::Bool(true))
                && py_eq(field(&status, "pid"), field(identity, "pid"))
                && let Some(sampled) = py_float(status.get("sampled_at").unwrap_or(&json!(0)))
                && (0.0..=10.0).contains(&(self.clock() - sampled))
                && let Some(port) = status.get("port").and_then(Value::as_i64)
                && (1..=65535).contains(&port)
            {
                return Ok(json!(port));
            }
            std::thread::sleep(std::time::Duration::from_millis(100));
        }
        Err("Proxy não ficou pronto no prazo".into())
    }

    fn sync_proxy_settings(
        &self,
        entry: &Value,
        settings: &Value,
    ) -> Result<(Value, PathBuf), String> {
        let payload = json!({"wallet": field(entry, "wallet"), "support_enabled": settings.get("support_enabled") == Some(&Value::Bool(true))});
        let path = PathBuf::from(py_str(field(entry, "log_dir"))).join("proxy-settings.json");
        let current = read_json_file(&path).ok();
        if !current.is_some_and(|current| py_eq(&current, &payload)) {
            atomic_json(&path, &payload, false, false)?;
        }
        Ok((payload, path))
    }

    fn miner_index_of(device: &Value) -> Value {
        device
            .get("miner_index")
            .or_else(|| device.get("index"))
            .cloned()
            .unwrap_or(Value::Null)
    }

    /// `start_gpu(device, settings)`. `Err` só para o que o Python não trata (o que
    /// ele trata vira estado `error`/`start_blocked` e `Ok(false)`).
    pub fn start_gpu(&mut self, device: &Value, settings: &Value) -> Result<bool, String> {
        let identifier = py_str(field(device, "uuid"));
        let mut previous = self
            .devices
            .get(&identifier)
            .cloned()
            .unwrap_or_else(|| json!({}));
        if truthy(field(&previous, "external_readonly"))
            || truthy(field(&previous, "ownership_unverified"))
        {
            return Ok(false);
        }
        for role in ["miner", "controller", "proxy"] {
            let identity = field(&previous, role).clone();
            if self.owned(&identity) {
                return Ok(false);
            }
        }
        let miner_index = Self::miner_index_of(device);
        let mut preflight_binary = true;
        let preflight = self
            .backend
            .verify_miner(&self.miner_path.clone())
            .and_then(|()| {
                preflight_binary = false;
                self.backend
                    .assert_gpu_available(&self.miner_path.clone(), &miner_index)
            });
        if let Err(error) = preflight {
            let clock = self.clock();
            let object = previous.as_object_mut().expect("objeto");
            for (key, value) in [
                ("gpu_uuid", json!(identifier)),
                ("identity", device.clone()),
                ("enabled", field(device, "enabled").clone()),
                ("state", json!("start_blocked")),
                ("error", json!(truncate(&error, 300))),
                ("retry_after", json!(clock + 30.0)),
                ("miner_running", json!(false)),
            ] {
                object.insert(key.into(), value);
            }
            for (key, value) in [
                ("wallet", field(device, "wallet").clone()),
                ("log_dir", field(device, "log_dir").clone()),
                ("index", field(device, "index").clone()),
                ("miner_index", miner_index.clone()),
                ("name", field(device, "name").clone()),
                ("managed", json!(true)),
            ] {
                object.entry(key).or_insert(value);
            }
            if preflight_binary {
                object.insert("binary_blocked".into(), json!(true));
            }
            let message = py_str(&object["error"]);
            self.devices.insert(identifier.clone(), previous);
            self.event(&identifier, "preflight_blocked", &message)?;
            self.publish()?;
            return Ok(false);
        }
        let clock = self.clock();
        let mut entry = json!({
            "gpu_uuid": identifier, "identity": device, "index": field(device, "index"), "name": field(device, "name"),
            "wallet": field(device, "wallet"), "desired_wallet": field(device, "wallet"), "enabled": field(device, "enabled"),
            "log_dir": field(device, "log_dir"), "state": "starting", "managed": true, "external_readonly": false,
            "miner": null, "controller": null, "proxy": null, "miner_pid": null, "controller_pid": null, "proxy_pid": null,
            "error": null, "started_at": clock, "pool": "prl.kryptex.network:7048",
        });
        entry["miner_index"] = miner_index;
        entry["profile"] = device
            .get("profile")
            .cloned()
            .unwrap_or_else(|| json!("performance"));
        let directory = PathBuf::from(py_str(field(device, "log_dir")));
        self.devices.insert(identifier.clone(), entry);
        std::fs::create_dir_all(&directory).map_err(|error| error.to_string())?;
        atomic_json(
            &directory.join("gpu-defaults.json"),
            &json!({"state": "mining_session", "at": clock, "gpu_uuid": identifier}),
            false,
            false,
        )?;
        let stamp = chrono::Local::now().format("%Y%m%d-%H%M%S");
        let log = directory.join(format!("mining-{stamp}-{}.log", random_hex(6)));
        self.set(&identifier, vec![("miner_log", json!(path_text(&log)))]);
        let mut stage = "binary";
        match self.launch(&identifier, device, settings, &directory, &mut stage) {
            Ok(()) => Ok(true),
            Err(error) => {
                self.stop_gpu(&identifier, false)?;
                let clock = self.clock();
                self.set(
                    &identifier,
                    vec![
                        ("state", json!("error")),
                        ("error", json!(truncate(&error, 300))),
                        ("retry_after", json!(clock + 60.0)),
                    ],
                );
                if stage == "binary" {
                    self.set(
                        &identifier,
                        vec![
                            ("state", json!("blocked_binary")),
                            ("binary_blocked", json!(true)),
                        ],
                    );
                }
                let message = py_str(&self.get(&identifier, "error"));
                self.event(&identifier, "start_failed", &message)?;
                self.publish()?;
                Ok(false)
            }
        }
    }

    fn launch(
        &mut self,
        identifier: &str,
        device: &Value,
        settings: &Value,
        directory: &Path,
        stage: &mut &str,
    ) -> Result<(), String> {
        let miner_path = self.miner_path.clone();
        self.backend.verify_miner(&miner_path)?;
        *stage = "process";
        let miner_index = self.get(identifier, "miner_index");
        self.backend
            .assert_gpu_available(&miner_path, &miner_index)?;
        let entry = Value::Object(self.entry(identifier).clone());
        let (proxy_settings, proxy_path) = self.sync_proxy_settings(&entry, settings)?;
        let wallet = field(&entry, "wallet").clone();
        if support_configuration(&proxy_settings, &wallet)["enabled"] == json!(true) {
            let mut arguments = self.proxy_command.clone().unwrap_or_else(|| {
                vec![
                    path_text(&self.python_path),
                    path_text(&self.project_dir.join("contribution.py")),
                ]
            });
            arguments.extend([
                "--wallet".into(),
                py_str(&wallet),
                "--settings".into(),
                path_text(&proxy_path),
                "--state-dir".into(),
                path_text(directory),
                "--port".into(),
                "0".into(),
            ]);
            let identity = self.backend.start(
                &arguments,
                &self.project_dir.clone(),
                Some(&directory.join("proxy-console.log")),
            )?;
            self.set(
                identifier,
                vec![
                    ("proxy", identity.clone()),
                    ("proxy_pid", field(&identity, "pid").clone()),
                ],
            );
            let entry = Value::Object(self.entry(identifier).clone());
            let port = match self.proxy_ready.as_mut() {
                Some(ready) => ready(&entry, &identity)?,
                None => self.wait_proxy(&entry, &identity, 15.0)?,
            };
            self.set(
                identifier,
                vec![("pool", json!(format!("127.0.0.1:{}", py_str(&port))))],
            );
        }
        let entry = Value::Object(self.entry(identifier).clone());
        let mut arguments: Vec<String> = vec![
            path_text(&miner_path),
            "--disable-cpu".into(),
            "--disable-msr-tweaks".into(),
            "--disable-worker-watchdog".into(),
            "--disable-gpu-amd".into(),
            "--disable-gpu-intel".into(),
            "--algorithm-gpu".into(),
            "pearlhash".into(),
            "--gpu-id".into(),
            py_str(field(&entry, "miner_index")),
            "--pool".into(),
            py_str(field(&entry, "pool")),
            "--wallet".into(),
            py_str(field(&entry, "wallet")),
            "--worker".into(),
            py_str(
                device
                    .get("worker_name")
                    .ok_or_else(|| "'worker_name'".to_owned())?,
            ),
            "--gpu-off-temperature".into(),
            MINER_OFF_TEMPERATURE_C.to_string(),
            "--log-file".into(),
            py_str(field(&entry, "miner_log")),
        ];
        if self.reference_uuid.as_deref() == Some(identifier) {
            if !reference_device_confirmed(device) {
                return Err("Unidade ou limites atuais da RTX 3070 de referência não confirmados; perfil não aplicado".into());
            }
            let memory = reference_memory_clock(&path_text(&miner_path), settings);
            let core = reference_start_clock(device, settings);
            self.set(
                identifier,
                vec![
                    ("reference_memory_mhz", json!(memory)),
                    ("reference_core_mhz", json!(core)),
                ],
            );
            for item in [
                "--gpu-cclock0",
                &core.to_string(),
                "--gpu-coffset0",
                "150",
                "--gpu-mclock0",
                &memory.to_string(),
                "--gpu-fan0",
                "100",
                "--gpu-plimit0",
                "240",
            ] {
                arguments.push(item.to_owned());
            }
            self.set(identifier, vec![(
                "startup_profile",
                json!(format!("Opt-in da RTX 3070 de referência: core{core}/offset150/mem{memory}/fan100/PL240; ajuste posterior pelo controlador")),
            )]);
        }
        let working = miner_path
            .parent()
            .map(Path::to_path_buf)
            .unwrap_or_default();
        let identity = self.backend.start(
            &arguments,
            &working,
            Some(&directory.join("miner-console.log")),
        )?;
        self.set(
            identifier,
            vec![
                ("miner", identity.clone()),
                ("miner_pid", field(&identity, "pid").clone()),
            ],
        );
        let identity = self.start_controller(identifier, settings)?;
        self.set(
            identifier,
            vec![
                ("controller", identity.clone()),
                ("controller_pid", field(&identity, "pid").clone()),
                ("state", json!("running")),
                ("miner_running", json!(true)),
            ],
        );
        self.event(identifier, "started", "Minerador e controlador iniciados")?;
        self.publish()
    }

    fn start_controller(&mut self, identifier: &str, settings: &Value) -> Result<Value, String> {
        let entry = Value::Object(self.entry(identifier).clone());
        let directory = PathBuf::from(py_str(field(&entry, "log_dir")));
        let device = field(&entry, "identity");
        let stop_file = directory.join("gpu-agent-stop.json");
        if stop_file.is_file() {
            std::fs::remove_file(&stop_file).map_err(|error| error.to_string())?;
        }
        let chosen = entry
            .get("profile")
            .cloned()
            .unwrap_or_else(|| json!("performance"));
        let objective = profile(&chosen)?["objective"].clone();
        let dir = path_text(&directory);
        let (mut arguments, project_argument) = match &self.controller_command {
            Some(command) => (command.clone(), true),
            None => (
                vec![
                    path_text(&self.python_path),
                    path_text(&self.project_dir.join("gpu_agent.py")),
                ],
                false,
            ),
        };
        arguments.extend::<Vec<String>>(vec![
            "--control".into(),
            "--miner-pid".into(),
            py_str(field(&entry, "miner_pid")),
            "--uuid".into(),
            py_str(field(&entry, "gpu_uuid")),
            "--index".into(),
            py_str(field(device, "index")),
            "--duration".into(),
            "300".into(),
            "--state-dir".into(),
            dir.clone(),
            "--monitor-state-dir".into(),
            dir.clone(),
            "--log-dir".into(),
            dir,
            "--miner-log".into(),
            py_str(field(&entry, "miner_log")),
            "--miner-index".into(),
            py_str(field(&entry, "miner_index")),
            "--profile".into(),
            py_str(&chosen),
            "--objective".into(),
            py_str(&objective),
        ]);
        if project_argument {
            arguments.extend(["--project-dir".into(), path_text(&self.project_dir)]);
        }
        if settings.get("ai_enabled") == Some(&Value::Bool(true)) {
            arguments.push("--ollama".into());
        }
        self.backend.start(
            &arguments,
            &self.project_dir.clone(),
            Some(&directory.join("controller-console.log")),
        )
    }

    fn recover_controller(&mut self, identifier: &str, settings: &Value) -> Result<(), String> {
        let previous = self.get(identifier, "controller");
        if truthy(&previous) {
            let observed = self.backend.inspect(field(&previous, "pid"));
            if observed.is_some_and(|observed| {
                py_eq(
                    field(&observed, "created_at"),
                    field(&previous, "created_at"),
                )
            }) {
                self.set(
                    identifier,
                    vec![(
                        "controller_warning",
                        json!("Controlador vivo sem identidade confirmada; substituição bloqueada"),
                    )],
                );
                return Ok(());
            }
        }
        if !self
            .operations
            .reserve_component(identifier, "controller", self.clock(), 60.0, 3)?
        {
            self.set(
                identifier,
                vec![(
                    "controller_warning",
                    json!("Controlador aguarda intervalo de recuperação; minerador preservado"),
                )],
            );
            return Ok(());
        }
        match self.start_controller(identifier, settings) {
            Ok(identity) => {
                self.set(
                    identifier,
                    vec![
                        ("controller", identity.clone()),
                        ("controller_pid", field(&identity, "pid").clone()),
                    ],
                );
                self.pop(identifier, &["controller_warning"]);
                self.event(
                    identifier,
                    "controller_recovered",
                    "Controlador recuperado sem interromper o minerador",
                )
            }
            Err(error) => {
                let warning = truncate(&error, 300);
                self.set(identifier, vec![("controller_warning", json!(warning))]);
                self.event(identifier, "controller_failed", &warning)
            }
        }
    }

    fn reconcile_successor(&mut self, identifier: &str, device: &Value) -> Result<bool, String> {
        let entry = Value::Object(self.entry(identifier).clone());
        let own_session = truthy(field(&entry, "miner"));
        let session = if own_session {
            entry.clone()
        } else {
            entry
                .get("last_miner_session")
                .cloned()
                .unwrap_or_else(|| json!({}))
        };
        if !session.is_object() {
            return Err(format!(
                "'{}' object has no attribute 'get'",
                type_name(&session)
            ));
        }
        let old = field(&session, "miner").clone();
        let clock = self.clock();
        if !own_session
            && clock > py_float(session.get("valid_until").unwrap_or(&json!(0))).unwrap_or(f64::NAN)
        {
            return Ok(false);
        }
        if !truthy(&old)
            || truthy(field(&entry, "external_readonly"))
            || truthy(field(&entry, "ownership_unverified"))
        {
            return Ok(false);
        }
        let observed = self.backend.inspect(field(&old, "pid"));
        if observed.is_some_and(|observed| {
            py_eq(field(&observed, "created_at"), field(&old, "created_at"))
        }) {
            return Ok(false);
        }
        if clock
            < py_float(entry.get("successor_probe_after").unwrap_or(&json!(0))).unwrap_or(f64::NAN)
        {
            return Ok(false);
        }
        self.set(
            identifier,
            vec![("successor_probe_after", json!(clock + 15.0))],
        );
        let miner_path = self.miner_path.clone();
        let miner_text = path_text(&miner_path);
        let candidates = self.backend.legacy_candidates(&miner_path)?;
        let matches: Vec<&Value> = candidates
            .iter()
            .filter(|c| match_successor(c, &session, device, &miner_text))
            .collect();
        if matches.len() != 1 {
            return Ok(false);
        }
        let candidate = matches[0].clone();
        let index = py_str(field(device, "miner_index"));
        let overlaps = |item: &Value| {
            let empty = Vec::new();
            let argv = item.get("argv").and_then(Value::as_array).unwrap_or(&empty);
            let ids: Vec<String> = option(argv, "--gpu-id").map_or_else(Vec::new, |selected| {
                py_str(selected).split(',').map(str::to_owned).collect()
            });
            ids.is_empty()
                || !ids
                    .iter()
                    .all(|id| !id.is_empty() && id.chars().all(|c| c.is_ascii_digit()))
                || ids.contains(&index)
        };
        let miner_name = miner_path
            .file_name()
            .map(|name| name.to_string_lossy().to_lowercase())
            .unwrap_or_default();
        let peers = candidates
            .iter()
            .filter(|item| {
                let executable = py_str(item.get("executable").unwrap_or(&json!("")));
                Path::new(&executable)
                    .file_name()
                    .map(|name| name.to_string_lossy().to_lowercase())
                    .unwrap_or_default()
                    == miner_name
                    && overlaps(item)
            })
            .count();
        if peers != 1 {
            return Ok(false);
        }
        self.backend.verify_miner(&miner_path)?;
        let parent_confirmed = py_eq(field(&candidate, "parent_pid"), field(&old, "pid"));
        let controller = field(&entry, "controller").clone();
        if self.owned(&controller)
            && (!self.backend.stop(&controller) || !self.backend.wait(&controller, 12.0))
        {
            return Ok(false);
        }
        self.set(
            identifier,
            vec![
                ("miner", candidate.clone()),
                ("miner_pid", field(&candidate, "pid").clone()),
                ("controller", Value::Null),
                ("controller_pid", Value::Null),
                ("miner_running", json!(true)),
                ("started_at", json!(clock)),
                (
                    "state",
                    json!(if parent_confirmed {
                        "running"
                    } else {
                        "external_readonly"
                    }),
                ),
                ("external_readonly", json!(!parent_confirmed)),
                ("managed", json!(parent_confirmed)),
            ],
        );
        let log = session
            .get("miner_log")
            .cloned()
            .ok_or_else(|| "'miner_log'".to_owned())?;
        self.set(identifier, vec![("miner_log", log)]);
        self.pop(
            identifier,
            &["error", "retry_after", "successor_probe_after"],
        );
        let uuid = py_str(field(device, "uuid"));
        if parent_confirmed {
            self.operations.event(
                &uuid,
                "successor_recovered",
                "Processo sucessor confirmado pelo pai e pela sessão",
                clock,
            )?;
        } else {
            self.operations.event(
                &uuid,
                "successor_observed",
                "Sessão reencontrada somente leitura; vínculo de pai não confirmado",
                clock,
            )?;
        }
        Ok(true)
    }

    pub fn manual_start(&mut self, identifier: &str) -> Result<(), String> {
        let entry = self
            .devices
            .get(identifier)
            .cloned()
            .unwrap_or_else(|| json!({}));
        if truthy(field(&entry, "external_readonly"))
            || truthy(field(&entry, "ownership_unverified"))
        {
            return Err(
                "Sessão externa ou sem identidade confirmada; início manual bloqueado".into(),
            );
        }
        if field(&entry, "state") == "running" {
            return Ok(());
        }
        self.backend.verify_miner(&self.miner_path.clone())?;
        let sensor = entry
            .get("last_gpu_sample")
            .filter(|value| truthy(value))
            .cloned()
            .unwrap_or_else(|| json!({}));
        if self.require_recovery_sensors && !safe_sensor(&sensor, self.clock(), 70.0) {
            return Err("Início manual aguarda sensor atual abaixo de 70 °C".into());
        }
        if !self.operations.reserve_component(
            identifier,
            "manual_start",
            self.clock(),
            30.0,
            120,
        )? {
            return Err("Aguarde 30 segundos entre tentativas manuais de início".into());
        }
        if self.devices.contains_key(identifier) {
            self.pop(identifier, &["error", "retry_after", "started_at"]);
            self.set(identifier, vec![("state", json!("stopped"))]);
        }
        self.event(
            identifier,
            "manual_start",
            "Retomada manual; histórico de recuperações preservado",
        )
    }

    /// `stop_gpu(identifier, restore=)`: devolve se a parada foi confirmada.
    pub fn stop_gpu(&mut self, identifier: &str, restore: bool) -> Result<bool, String> {
        let Some(entry) = self.devices.get(identifier).cloned() else {
            return Ok(true);
        };
        if truthy(field(&entry, "external_readonly")) {
            self.set(identifier, vec![("error", json!("Mineração externa preservada; migração explícita necessária antes de controlar esta GPU"))]);
            return Ok(false);
        }
        let roles = ["miner", "controller", "proxy"];
        let restore_needed = restore || roles.iter().any(|role| truthy(field(&entry, role)));
        for role in roles {
            let identity = field(&entry, role).clone();
            if truthy(&identity) && !self.backend.is_owned(&identity) {
                let current = self.backend.inspect(field(&identity, "pid"));
                if current.is_some_and(|current| {
                    py_eq(
                        field(&current, "created_at"),
                        field(&identity, "created_at"),
                    )
                }) {
                    self.set(identifier, vec![
                        ("ownership_unverified", json!(true)),
                        ("state", json!("ownership_unverified")),
                        ("error", json!("Processo vivo com propriedade não confirmada; ação bloqueada para evitar duplicação ou encerramento indevido")),
                    ]);
                    return Ok(false);
                }
            }
        }
        let log_dir = PathBuf::from(py_str(field(&entry, "log_dir")));
        let controller = field(&entry, "controller").clone();
        if self.owned(&controller) {
            atomic_json(
                &log_dir.join("gpu-agent-stop.json"),
                &json!({"gpu_uuid": identifier, "pid": field(&controller, "pid"), "at": self.clock(), "reason": "rig_stop"}),
                false,
                false,
            )?;
        }
        let failed = |manager: &mut Self, message: &str| {
            manager.set(
                identifier,
                vec![("state", json!("stop_failed")), ("error", json!(message))],
            );
            Ok(false)
        };
        let miner = field(&entry, "miner").clone();
        if self.owned(&miner) {
            if !self.backend.stop(&miner) {
                return failed(
                    self,
                    "Minerador não confirmou parada; identidade preservada",
                );
            }
            if !self.backend.wait(&miner, 12.0) {
                return failed(self, "Minerador ainda ativo; restauração bloqueada");
            }
        }
        if self.owned(&controller) && !self.backend.wait(&controller, 12.0) {
            if !self.backend.stop(&controller) {
                return failed(
                    self,
                    "Supervisor não confirmou parada; identidade preservada",
                );
            }
            self.set(identifier, vec![("restore_warning", json!("Supervisor não confirmou a restauração no prazo; verificar clocks no driver"))]);
        }
        let proxy = field(&entry, "proxy").clone();
        if self.owned(&proxy) && !self.backend.stop(&proxy) {
            return failed(self, "Proxy não confirmou parada; identidade preservada");
        }
        let clock = self.clock();
        if truthy(&miner) && !restore {
            let current = Value::Object(self.entry(identifier).clone());
            let mut session: Obj = ["miner", "miner_log", "wallet", "pool"]
                .iter()
                .map(|key| ((*key).to_owned(), field(&current, key).clone()))
                .collect();
            session.insert("valid_until".into(), json!(clock + 120.0));
            self.set(
                identifier,
                vec![("last_miner_session", Value::Object(session))],
            );
        } else if restore {
            self.pop(identifier, &["last_miner_session"]);
        }
        self.set(
            identifier,
            vec![
                ("state", json!("stopped")),
                ("miner_running", json!(false)),
                ("miner", Value::Null),
                ("controller", Value::Null),
                ("proxy", Value::Null),
                ("miner_pid", Value::Null),
                ("controller_pid", Value::Null),
                ("proxy_pid", Value::Null),
                ("stopped_at", json!(clock)),
            ],
        );
        let suffix = if restore {
            " com restauração solicitada"
        } else {
            ""
        };
        self.event(identifier, "stopped", &format!("Parada confirmada{suffix}"))?;
        if restore_needed {
            self.set(
                identifier,
                vec![("restoration", json!({"state": "restoring", "at": clock}))],
            );
            self.publish()?;
            let gpu = entry
                .get("identity")
                .filter(|value| truthy(value))
                .cloned()
                .unwrap_or_else(|| json!({"uuid": identifier}));
            let result = self
                .backend
                .restore_defaults(&gpu, &log_dir)
                .unwrap_or_else(|error| json!({"state": "failed", "at": self.clock(), "error": truncate(&error, 250)}));
            self.set(identifier, vec![("restoration", result.clone())]);
            atomic_json(&log_dir.join("gpu-defaults.json"), &result, false, false)?;
            if field(&result, "state") == "restored" {
                self.pop(identifier, &["restore_warning"]);
            } else {
                self.set(identifier, vec![("restore_warning", json!("Mineração parada; restauração incompleta. Confira os componentes e tente novamente."))]);
            }
        }
        Ok(true)
    }

    fn signature(desired: &[Value]) -> Vec<String> {
        let mut items: Vec<String> = desired
            .iter()
            .map(|gpu| {
                hashpilot_history::pyjson::encode(&json!([
                    field(gpu, "uuid"),
                    field(gpu, "index"),
                    field(gpu, "pci_bus_id")
                ]))
            })
            .collect();
        items.sort();
        items
    }

    /// `reconcile(detected=, settings=)`: alinha cada GPU ao `settings.json`.
    pub fn reconcile(
        &mut self,
        detected: Option<Vec<Value>>,
        settings: Option<Value>,
    ) -> Result<Vec<Value>, String> {
        if !self.registry_valid {
            return Err(py_str(&self.error));
        }
        let settings = match settings {
            Some(settings) => settings,
            None => self.settings()?,
        };
        let detected = match detected {
            Some(detected) => detected,
            None => (self.detector)()?,
        };
        let mut desired: Vec<Value> = resolve_devices(&detected, &settings, &self.log_dir)?
            .into_iter()
            .map(Value::Object)
            .collect();
        let signature = Self::signature(&desired);
        if self.mapping_signature.as_ref() != Some(&signature) {
            self.backend.verify_miner(&self.miner_path.clone())?;
            self.miner_map = if desired.is_empty() {
                Obj::new()
            } else if desired.len() == 1 && py_eq(field(&desired[0], "index"), &json!(0)) {
                [(py_str(field(&desired[0], "uuid")), json!(0))]
                    .into_iter()
                    .collect()
            } else {
                self.backend
                    .miner_device_map(&self.miner_path.clone(), &desired)?
            };
            self.mapping_signature = Some(signature);
        }
        for device in &mut desired {
            let uuid = py_str(field(device, "uuid"));
            let Some(index) = self.miner_map.get(&uuid) else {
                return Err("ID do SRBMiner não confirmado para esta GPU".into());
            };
            device["miner_index"] = index.clone();
        }
        let wanted: HashMap<String, Value> = desired
            .iter()
            .map(|device| (py_str(field(device, "uuid")), device.clone()))
            .collect();
        self.ignored = detected
            .iter()
            .filter(|gpu| field(gpu, "vendor") == "NVIDIA" && !gpu.get("uuid").and_then(Value::as_str).is_some_and(|uuid| wanted.contains_key(uuid)))
            .map(|gpu| json!({"uuid": field(gpu, "uuid"), "name": field(gpu, "name"), "reason": "Limite de seis GPUs NVIDIA"}))
            .collect();
        let identifiers: Vec<String> = self.devices.keys().cloned().collect();
        for identifier in identifiers {
            let Some(device) = wanted.get(&identifier).filter(|device| truthy(device)) else {
                let miner = self.get(&identifier, "miner");
                if truthy(&self.get(&identifier, "external_readonly"))
                    && !(miner.is_object() && self.backend.is_owned(&miner))
                {
                    self.set(
                        &identifier,
                        vec![
                            ("external_readonly", json!(false)),
                            ("managed", json!(true)),
                        ],
                    );
                }
                if self.stop_gpu(&identifier, false)? {
                    self.set(&identifier, vec![("state", json!("disconnected"))]);
                    std::fs::create_dir_all(&self.state_dir).map_err(|error| error.to_string())?;
                    let line = json!({"at": self.clock(), "event": "gpu_disconnected", "device": self.devices[&identifier]});
                    use std::io::Write;
                    let mut history = std::fs::OpenOptions::new()
                        .create(true)
                        .append(true)
                        .open(self.state_dir.join("rig-device-history.jsonl"))
                        .map_err(|error| error.to_string())?;
                    history
                        .write_all(format!("{}\n", encode_spaced(&line)).as_bytes())
                        .map_err(|error| error.to_string())?;
                    self.devices.shift_remove(&identifier);
                }
                continue;
            };
            let enabled = truthy(field(device, "enabled"));
            self.set(
                &identifier,
                vec![
                    ("enabled", field(device, "enabled").clone()),
                    ("desired_wallet", field(device, "wallet").clone()),
                    ("identity", device.clone()),
                ],
            );
            if truthy(&self.get(&identifier, "external_readonly")) {
                let miner = self.get(&identifier, "miner");
                let running = self.owned(&miner);
                self.set(&identifier, vec![("miner_running", json!(running))]);
                if running {
                    self.set(&identifier, vec![("state", json!("external_readonly"))]);
                    continue;
                }
                self.set(
                    &identifier,
                    vec![
                        ("external_readonly", json!(false)),
                        ("managed", json!(true)),
                        ("state", json!("stopped")),
                        ("miner", Value::Null),
                        ("controller", Value::Null),
                    ],
                );
            }
            let entry = Value::Object(self.entry(&identifier).clone());
            let mut changed = !py_eq(field(&entry, "wallet"), field(device, "wallet"))
                || !py_eq(field(&entry, "index"), field(device, "index"))
                || !py_eq(
                    entry
                        .get("miner_index")
                        .unwrap_or_else(|| field(&entry, "index")),
                    field(device, "miner_index"),
                );
            if self.reference_uuid.as_deref() == Some(identifier.as_str()) {
                let memory = reference_memory_clock(&path_text(&self.miner_path), &settings);
                let core = reference_start_clock(device, &settings);
                changed = changed
                    || !py_eq(
                        entry.get("reference_memory_mhz").unwrap_or(&json!(5001)),
                        &json!(memory),
                    );
                changed = changed
                    || !py_eq(
                        entry.get("reference_core_mhz").unwrap_or(&json!(1560)),
                        &json!(core),
                    );
            }
            let mut miner_alive = self.owned(field(&entry, "miner"));
            if enabled
                && !changed
                && !miner_alive
                && self.reconcile_successor(&identifier, device)?
            {
                if truthy(&self.get(&identifier, "external_readonly")) {
                    continue;
                }
                miner_alive = true;
            }
            let entry = Value::Object(self.entry(&identifier).clone());
            let proxy = field(&entry, "proxy").clone();
            let proxy_alive = !truthy(&proxy) || self.backend.is_owned(&proxy);
            let controller = field(&entry, "controller").clone();
            let controller_alive = self.owned(&controller);
            if enabled && !changed && miner_alive && proxy_alive && !controller_alive {
                self.recover_controller(&identifier, &settings)?;
                self.set(
                    &identifier,
                    vec![("state", json!("running")), ("miner_running", json!(true))],
                );
                continue;
            }
            let miner = field(&entry, "miner").clone();
            let healthy = truthy(&miner)
                && truthy(&controller)
                && self.backend.is_owned(&miner)
                && self.backend.is_owned(&controller)
                && (!truthy(&proxy) || self.backend.is_owned(&proxy));
            let state = field(&entry, "state").clone();
            if !enabled || changed || state == "stop_failed" || (state == "running" && !healthy) {
                if !self.stop_gpu(&identifier, false)? {
                    continue;
                }
                if enabled && !changed && !healthy {
                    let clock = self.clock();
                    self.set(
                        &identifier,
                        vec![
                            ("state", json!("error")),
                            (
                                "error",
                                json!("Um processo desta GPU encerrou; demais GPUs preservadas"),
                            ),
                            ("retry_after", json!(clock + 60.0)),
                        ],
                    );
                }
            }
            let entry = Value::Object(self.entry(&identifier).clone());
            self.sync_proxy_settings(&entry, &settings)?;
            let pending = settings.get("support_enabled") == Some(&Value::Bool(true))
                && !truthy(field(&entry, "proxy"))
                && support_configuration(
                    &json!({"wallet": field(device, "wallet"), "support_enabled": true}),
                    field(device, "wallet"),
                )["enabled"]
                    == json!(true);
            self.set(
                &identifier,
                vec![("support_pending_restart", json!(pending))],
            );
        }
        for device in &desired {
            let uuid = py_str(field(device, "uuid"));
            let enabled = truthy(field(device, "enabled"));
            let exists = self.devices.contains_key(&uuid);
            if exists && truthy(&self.get(&uuid, "binary_blocked")) && enabled {
                if self.backend.verify_miner(&self.miner_path.clone()).is_err() {
                    continue;
                }
                let sensor = Some(self.get(&uuid, "last_gpu_sample"))
                    .filter(truthy)
                    .unwrap_or_else(|| json!({}));
                if self.require_recovery_sensors && !safe_sensor(&sensor, self.clock(), 70.0) {
                    continue;
                }
                self.pop(
                    &uuid,
                    &["binary_blocked", "retry_after", "error", "started_at"],
                );
                self.set(&uuid, vec![("state", json!("stopped"))]);
                self.event(
                    &uuid,
                    "binary_available",
                    "Executável voltou a passar na verificação; retomada liberada",
                )?;
            }
            if !exists && self.devices.len() >= MAX_GPUS {
                self.ignored.push(json!({"uuid": uuid, "name": field(device, "name"), "reason": "Seis registros ocupados; uma sessão externa ou não confirmada foi preservada"}));
                continue;
            }
            let entry = self
                .devices
                .get(&uuid)
                .cloned()
                .unwrap_or_else(|| json!({}));
            let retry = py_float(entry.get("retry_after").unwrap_or(&json!(0))).unwrap_or(f64::NAN);
            if enabled
                && !truthy(field(&entry, "external_readonly"))
                && !truthy(field(&entry, "ownership_unverified"))
                && field(&entry, "state") != "running"
                && self.clock() >= retry
            {
                let state = field(&entry, "state");
                let recovering = truthy(field(&entry, "error"))
                    || ["error", "recovery_wait", "stop_failed"]
                        .iter()
                        .any(|s| state == *s);
                if exists
                    && entry
                        .get("started_at")
                        .is_some_and(|value| !value.is_null())
                    && recovering
                {
                    let clock = self.clock();
                    let budget = self.operations.budget(&uuid, clock)?;
                    self.set(&uuid, vec![("recovery", budget.clone())]);
                    let sensor = Some(field(&entry, "last_gpu_sample").clone())
                        .filter(truthy)
                        .unwrap_or_else(|| json!({}));
                    let temperature = field(&sensor, "temperature.gpu");
                    let sampled =
                        py_float(sensor.get("sampled_at").unwrap_or(&json!(0))).unwrap_or(f64::NAN);
                    if self.require_recovery_sensors
                        && (temperature.is_null()
                            || temperature.as_f64().is_some_and(|t| t >= 70.0)
                            || truthy(field(&sensor, "error"))
                            || !(0.0..=15.0).contains(&(clock - sampled)))
                    {
                        self.set(
                            &uuid,
                            vec![
                                ("state", json!("recovery_wait")),
                                ("retry_after", json!(clock + 10.0)),
                                (
                                    "error",
                                    json!("Recuperação aguarda sensor atual abaixo de 70 °C"),
                                ),
                            ],
                        );
                        continue;
                    }
                    let mut preflight_binary = true;
                    let miner_index = field(device, "miner_index").clone();
                    let preflight = self
                        .backend
                        .verify_miner(&self.miner_path.clone())
                        .and_then(|()| {
                            preflight_binary = false;
                            self.backend
                                .assert_gpu_available(&self.miner_path.clone(), &miner_index)
                        });
                    if let Err(error) = preflight {
                        let message = truncate(&error, 300);
                        self.set(
                            &uuid,
                            vec![
                                ("state", json!("start_blocked")),
                                ("error", json!(message)),
                                ("retry_after", json!(clock + 30.0)),
                            ],
                        );
                        if preflight_binary {
                            self.set(&uuid, vec![("binary_blocked", json!(true))]);
                        }
                        self.event(&uuid, "preflight_blocked", &message)?;
                        continue;
                    }
                    let reason = Some(self.get(&uuid, "error"))
                        .filter(truthy)
                        .map_or_else(|| "Processo encerrado".to_owned(), |value| py_str(&value));
                    if !self.operations.reserve(&uuid, &reason, clock)? {
                        self.set(
                            &uuid,
                            vec![
                                ("state", json!("recovery_wait")),
                                ("retry_after", budget["next_allowed_at"].clone()),
                            ],
                        );
                        continue;
                    }
                }
                self.start_gpu(device, &settings)?;
            } else if !exists {
                self.devices.insert(uuid.clone(), json!({
                    "gpu_uuid": uuid, "identity": device, "index": field(device, "index"), "name": field(device, "name"),
                    "wallet": field(device, "wallet"), "enabled": false, "state": "disabled", "managed": true,
                    "log_dir": field(device, "log_dir"), "miner_running": false,
                }));
            }
        }
        self.publish()?;
        Ok(desired)
    }

    /// `adopt_legacy`: reconhece, só para leitura, uma sessão antiga na GPU 0.
    pub fn adopt_legacy(&mut self, detected: Option<Vec<Value>>) -> Result<bool, String> {
        let detected = match detected {
            Some(detected) => detected,
            None => (self.detector)()?,
        };
        let settings = self.settings()?;
        let devices = resolve_devices(&detected, &settings, &self.log_dir)?;
        let Some(device) = devices
            .into_iter()
            .map(Value::Object)
            .find(|gpu| py_eq(field(gpu, "index"), &json!(0)))
        else {
            return Ok(false);
        };
        let uuid = py_str(field(&device, "uuid"));
        let previous_miner = self.get(&uuid, "miner");
        if self.owned(&previous_miner) {
            return Ok(false);
        }
        let candidates = self.backend.legacy_candidates(&self.miner_path.clone())?;
        let empty = Vec::new();
        let argv_of = |item: &Value| {
            item.get("argv")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default()
        };
        let miner_key = normalized_path(&path_text(&self.miner_path));
        let Some(miner) = candidates.iter().find(|candidate| {
            let argv = argv_of(candidate);
            normalized_path(&py_str(candidate.get("executable").unwrap_or(&json!("")))) == miner_key
                && argv
                    .iter()
                    .position(|item| item == "--gpu-id")
                    .is_some_and(|position| argv.get(position + 1) == Some(&json!("0")))
        }) else {
            return Ok(false);
        };
        let argv = argv_of(miner);
        let after = |name: &str| {
            argv.iter()
                .position(|item| item == name)
                .map(|position| argv.get(position + 1).cloned().unwrap_or(Value::Null))
        };
        let wallet = after("--wallet").unwrap_or_else(|| field(&device, "wallet").clone());
        let log = after("--log-file").unwrap_or(Value::Null);
        let parent = self
            .log_dir
            .parent()
            .map(Path::to_path_buf)
            .unwrap_or_default();
        let thermal = normalized_path(&path_text(&parent.join("thermal_controller.py")));
        let launcher = normalized_path(&path_text(&parent.join("Iniciar-Pearl.ps1")));
        let pid_text = json!(py_str(field(miner, "pid")));
        let mentions = |item: &Value, target: &str| {
            argv_of(item)
                .iter()
                .any(|arg| normalized_path(&py_str(arg)) == target)
        };
        let controller = candidates
            .iter()
            .find(|item| {
                let argv = item.get("argv").and_then(Value::as_array).unwrap_or(&empty);
                mentions(item, &thermal)
                    && argv.contains(&json!("--miner-pid"))
                    && argv.iter().any(|arg| py_eq(arg, &pid_text))
            })
            .cloned()
            .unwrap_or(Value::Null);
        let supervisor = candidates
            .iter()
            .find(|item| {
                let executable = py_str(item.get("executable").unwrap_or(&json!("")));
                let name = Path::new(&executable)
                    .file_name()
                    .map(|name| name.to_string_lossy().to_lowercase())
                    .unwrap_or_default();
                mentions(item, &launcher) && (name == "powershell.exe" || name == "pwsh.exe")
            })
            .cloned()
            .unwrap_or(Value::Null);
        let controller_pid = if truthy(&controller) {
            field(&controller, "pid").clone()
        } else {
            Value::Null
        };
        self.devices.insert(uuid.clone(), json!({
            "gpu_uuid": uuid, "identity": device, "index": 0, "name": field(&device, "name"), "wallet": wallet,
            "desired_wallet": field(&device, "wallet"), "enabled": field(&device, "enabled"), "log_dir": field(&device, "log_dir"),
            "miner_log": log, "state": "external_readonly", "managed": false, "external_readonly": true, "miner": miner,
            "miner_pid": field(miner, "pid"), "controller": controller, "controller_pid": controller_pid, "proxy": null,
            "proxy_pid": null, "miner_running": true, "error": null, "legacy_log_dir": path_text(&self.log_dir),
            "legacy_supervisor": supervisor,
        }));
        self.publish()?;
        Ok(true)
    }

    /// `migrate_legacy`: migração explícita e verificada; nunca chamada pelo monitoramento.
    pub fn migrate_legacy(
        &mut self,
        expected_uuid: &str,
        reference_rtx3070: bool,
    ) -> Result<bool, String> {
        device_key(&json!(expected_uuid))?;
        let detected = (self.detector)()?;
        let Some(current) = detected
            .iter()
            .find(|gpu| {
                gpu.get("uuid") == Some(&json!(expected_uuid))
                    && py_eq(field(gpu, "index"), &json!(0))
            })
            .cloned()
        else {
            return Err("UUID esperado não corresponde à GPU 0 atual; migração bloqueada".into());
        };
        if !truthy(&self.get(expected_uuid, "external_readonly")) {
            self.adopt_legacy(Some(detected))?;
        }
        if !self.devices.contains_key(expected_uuid)
            || !truthy(&self.get(expected_uuid, "external_readonly"))
        {
            return Err("Mineração legada correspondente não foi identificada".into());
        }
        let miner = self.get(expected_uuid, "miner");
        let controller = self.get(expected_uuid, "controller");
        let supervisor = self.get(expected_uuid, "legacy_supervisor");
        if !truthy(&miner) || !truthy(&controller) || !truthy(&supervisor) {
            return Err("Minerador, controlador e supervisor legado precisam ser identificados antes da migração".into());
        }
        if [&miner, &controller, &supervisor]
            .into_iter()
            .any(|identity| !self.backend.is_owned(identity))
        {
            return Err(
                "Identidade dos processos legados mudou; nenhuma migração executada".into(),
            );
        }
        if normalized_path(&py_str(field(&miner, "executable")))
            != normalized_path(&path_text(&self.miner_path))
        {
            return Err("Executável legado fora do caminho autorizado".into());
        }
        if reference_rtx3070 && !reference_device_confirmed(&current) {
            return Err("Perfil de referência só pode ser usado na unidade RTX 3070 explicitamente validada".into());
        }
        let clock = self.clock();
        self.set(
            expected_uuid,
            vec![
                ("state", json!("migrating_legacy")),
                ("migration_started_at", json!(clock)),
            ],
        );
        self.publish()?;
        if !self.backend.stop(&miner) {
            return Err("Minerador legado não pôde ser encerrado com segurança".into());
        }
        for (identity, timeout, message) in [
            (
                &controller,
                15.0,
                "Controlador legado ainda ativo; novo minerador não iniciado",
            ),
            (
                &supervisor,
                35.0,
                "Restauração do supervisor legado não confirmou término; novo minerador não iniciado",
            ),
        ] {
            if !self.backend.wait(identity, timeout) {
                self.set(
                    expected_uuid,
                    vec![
                        ("state", json!("migration_failed")),
                        ("error", json!(message)),
                    ],
                );
                self.publish()?;
                return Err(message.into());
            }
        }
        if reference_rtx3070 {
            self.reference_uuid = Some(expected_uuid.into());
        }
        let clock = self.clock();
        self.set(
            expected_uuid,
            vec![
                ("state", json!("stopped")),
                ("external_readonly", json!(false)),
                ("managed", json!(true)),
                ("miner", Value::Null),
                ("controller", Value::Null),
                ("legacy_supervisor", Value::Null),
                ("miner_pid", Value::Null),
                ("controller_pid", Value::Null),
                ("miner_running", json!(false)),
                ("migration_completed_at", json!(clock)),
                ("error", Value::Null),
            ],
        );
        self.publish()?;
        Ok(true)
    }

    /// `process_requests`: pedidos do painel, em ordem de horário, cada um arquivado.
    pub fn process_requests(&mut self) {
        let queue_dir = self.state_dir.join("rig-requests");
        let mut paths: Vec<PathBuf> = std::fs::read_dir(&queue_dir)
            .map(|entries| {
                entries
                    .filter_map(Result::ok)
                    .map(|entry| entry.path())
                    .filter(|path| {
                        path.extension().is_some_and(|ext| ext == "json") && path.is_file()
                    })
                    .collect()
            })
            .unwrap_or_default();
        let compatibility = self.state_dir.join("rig-request.json");
        if compatibility.is_file() {
            paths.push(compatibility);
        }
        let order = |path: &PathBuf| {
            let name = path
                .file_name()
                .map(|name| name.to_string_lossy().into_owned())
                .unwrap_or_default();
            let stamp = read_json_file(path)
                .ok()
                .filter(Value::is_object)
                .and_then(|payload| py_float(payload.get("at").unwrap_or(&json!(0))))
                .map(|stamp| {
                    if stamp.is_finite() {
                        stamp
                    } else {
                        f64::INFINITY
                    }
                });
            let modified = std::fs::metadata(path)
                .and_then(|meta| meta.modified())
                .ok()
                .and_then(|time| time.duration_since(std::time::UNIX_EPOCH).ok())
                .map_or(0, |elapsed| elapsed.as_nanos());
            match stamp {
                Some(stamp) => (stamp, modified, name),
                None => (f64::INFINITY, 0, name),
            }
        };
        let mut keyed: Vec<((f64, u128, String), PathBuf)> =
            paths.into_iter().map(|path| (order(&path), path)).collect();
        keyed.sort_by(|a, b| {
            a.0.0
                .total_cmp(&b.0.0)
                .then(a.0.1.cmp(&b.0.1))
                .then(a.0.2.cmp(&b.0.2))
        });
        for (_, path) in keyed {
            match self.handle_request(&path) {
                Ok(Some(identifier)) => {
                    self.processed_ids.push(identifier);
                    self.error = Value::Null;
                }
                Ok(None) => {}
                Err(error) => self.error = json!(truncate(&error, 300)),
            }
            if path.parent() == Some(queue_dir.as_path()) && path.is_file() {
                let archive = queue_dir
                    .join("processed")
                    .join(path.file_name().unwrap_or_default());
                let _ = std::fs::create_dir_all(queue_dir.join("processed"));
                let _ = std::fs::rename(&path, archive);
            }
        }
    }

    /// Um pedido: `Ok(Some(id))` processado, `Ok(None)` já visto, `Err` falhou.
    fn handle_request(&mut self, path: &Path) -> Result<Option<Value>, String> {
        let request = read_json_file(path).map_err(|error| error.to_string())?;
        if !request.is_object() {
            return Err(format!(
                "'{}' object has no attribute 'get'",
                type_name(&request)
            ));
        }
        let identifier = field(&request, "id").clone();
        if self
            .processed_ids
            .iter()
            .any(|seen| py_eq(seen, &identifier))
        {
            return Ok(None);
        }
        let at = py_float(request.get("at").unwrap_or(&json!(0)));
        if !identifier.is_string()
            || !at.is_some_and(|at| (0.0..=120.0).contains(&(self.clock() - at)))
        {
            return Err("Pedido de rig antigo ou inválido".into());
        }
        let action = field(&request, "action").clone();
        let gpu_uuid = field(&request, "gpu_uuid").clone();
        if !gpu_uuid.is_null() {
            device_key(&gpu_uuid)?;
        }
        let uuid = py_str(&gpu_uuid);
        match action.as_str() {
            Some(action @ ("start" | "stop")) => {
                if gpu_uuid.is_null() {
                    return Err("A ação exige UUID".into());
                }
                if action == "start" {
                    self.manual_start(&uuid)?;
                }
                let settings = update_gpu_settings(
                    &self.settings()?,
                    &gpu_uuid,
                    Some(&json!(action == "start")),
                    None,
                    false,
                )?;
                atomic_json(&self.settings_path, &settings, false, false)?;
                if action == "stop" {
                    self.stop_gpu(&uuid, true)?;
                }
            }
            Some("stop_all") => {
                let mut settings = self.settings()?;
                let targets: Vec<String> = self.devices.keys().cloned().collect();
                for target in &targets {
                    settings = update_gpu_settings(
                        &settings,
                        &json!(target),
                        Some(&json!(false)),
                        None,
                        false,
                    )?;
                }
                atomic_json(&self.settings_path, &settings, false, false)?;
                for target in &targets {
                    self.set(target, vec![("enabled", json!(false))]);
                    self.stop_gpu(target, true)?;
                }
            }
            Some("restart") => {
                let entry = self
                    .devices
                    .get(&uuid)
                    .cloned()
                    .unwrap_or_else(|| json!({}));
                if entry.as_object().is_none_or(Map::is_empty)
                    || !truthy(field(&entry, "managed"))
                    || truthy(field(&entry, "external_readonly"))
                    || truthy(field(&entry, "ownership_unverified"))
                    || !truthy(field(&entry, "enabled"))
                {
                    return Err("GPU precisa estar habilitada e sob controle do rig".into());
                }
                let budget = self.operations.budget(&uuid, self.clock())?;
                if budget["blocked"] == json!(true)
                    || self.clock() < budget["next_allowed_at"].as_f64().unwrap_or(f64::NAN)
                {
                    return Err("Reconexão adiada pelo orçamento de recuperação; mineração atual preservada".into());
                }
                if !self.stop_gpu(&uuid, false)? {
                    return Err("Parada não confirmada".into());
                }
                self.set(
                    &uuid,
                    vec![("error", json!("Reconexão solicitada pelo usuário"))],
                );
            }
            Some("profile") => {
                let chosen = field(&request, "profile").clone();
                let selected = profile(&chosen)?;
                let mut settings =
                    update_gpu_settings(&self.settings()?, &gpu_uuid, None, None, false)?;
                settings["gpu_settings"][uuid.as_str()]["profile"] = chosen.clone();
                atomic_json(&self.settings_path, &settings, false, false)?;
                let entry = self.devices.get(&uuid).cloned().unwrap_or(Value::Null);
                if truthy(&entry) && !truthy(field(&entry, "external_readonly")) {
                    atomic_json(
                        &PathBuf::from(py_str(field(&entry, "log_dir")))
                            .join("gpu-agent-request.json"),
                        &json!({"action": "benchmark", "gpu_uuid": uuid, "objective": selected["objective"], "profile": chosen,
                            "duration_seconds": 300, "at": self.clock()}),
                        false,
                        false,
                    )?;
                }
            }
            Some("benchmark") => {
                let targets: Vec<String> = if truthy(&gpu_uuid) {
                    vec![uuid.clone()]
                } else {
                    self.devices.keys().cloned().collect()
                };
                for target in targets {
                    let entry = self.devices.get(&target).cloned().unwrap_or(Value::Null);
                    if truthy(&entry) && !truthy(field(&entry, "external_readonly")) {
                        atomic_json(
                            &PathBuf::from(py_str(field(&entry, "log_dir")))
                                .join("gpu-agent-request.json"),
                            &json!({"action": "benchmark", "gpu_uuid": target, "objective": request.get("objective").cloned().unwrap_or_else(|| json!("performance")),
                                "duration_seconds": 300, "at": self.clock()}),
                            false,
                            false,
                        )?;
                    }
                }
            }
            Some("shutdown") => self.shutting_down = true,
            Some("miner_update") => {
                let version = field(&request, "version").clone();
                if let Err(error) = self.activate_miner(&version, &identifier) {
                    if !truthy(&self.miner_update_pending) {
                        atomic_json(
                            &self.state_dir.join("miner-update.json"),
                            &json!({"state": "failed", "request_id": identifier, "at": self.clock(), "error": truncate(&error, 300)}),
                            false,
                            false,
                        )?;
                    }
                    return Err(error);
                }
            }
            Some("reconcile") => {}
            _ => return Err("Ação desconhecida".into()),
        }
        Ok(Some(identifier))
    }

    /// `observe_health(snapshots)`: reinício automático só com sensor seguro e
    /// travamento confirmado três vezes em pelo menos 10 s.
    pub fn observe_health(&mut self, snapshots: &Obj) -> Result<(), String> {
        for (identifier, snapshot) in snapshots {
            if !self.devices.contains_key(identifier) {
                return Err(repr_str(identifier));
            }
            let clock = self.clock();
            let started = self.get(identifier, "started_at");
            let status = self.operations.assess(
                identifier,
                field(snapshot, "miner"),
                field(snapshot, "miner_running"),
                clock,
                &started,
            )?;
            let budget = self.operations.budget(identifier, clock)?;
            let incidents = self.operations.recent_events(identifier, 5)?;
            let sensor = Some(field(snapshot, "gpu").clone())
                .filter(truthy)
                .unwrap_or_else(|| json!({}));
            let sample: Obj = ["temperature.gpu", "sampled_at", "error"]
                .iter()
                .map(|key| ((*key).to_owned(), field(&sensor, key).clone()))
                .collect();
            self.set(
                identifier,
                vec![
                    ("health", status.clone()),
                    ("recovery", budget.clone()),
                    ("incidents", incidents),
                    ("last_gpu_sample", Value::Object(sample)),
                ],
            );
            let safe = safe_sensor(&sensor, clock, 70.0);
            let entry = Value::Object(self.entry(identifier).clone());
            let begun = entry
                .get("started_at")
                .map_or(clock, |value| value.as_f64().unwrap_or(f64::NAN));
            let age = clock - begun;
            let state = field(&status, "state");
            let without = field(&status, "seconds_without_share").as_f64();
            let limit = field(&status, "warning_after_seconds")
                .as_f64()
                .unwrap_or(f64::NAN);
            let mut stalled = ((state == "connecting" || state == "no_hashrate") && age > 300.0)
                || (truthy(field(&status, "warning"))
                    && without.is_some_and(|value| value > 2.0 * limit));
            stalled = stalled
                && !truthy(field(snapshot, "log_error"))
                && !self.continuity.in_grace(clock);
            if stalled {
                if !entry
                    .as_object()
                    .is_some_and(|object| object.contains_key("stall_first_at"))
                {
                    self.set(identifier, vec![("stall_first_at", json!(clock))]);
                }
                let observations = field(&entry, "stall_observations").as_i64().unwrap_or(0) + 1;
                self.set(
                    identifier,
                    vec![("stall_observations", json!(observations))],
                );
            } else {
                self.pop(identifier, &["stall_first_at", "stall_observations"]);
            }
            let entry = Value::Object(self.entry(identifier).clone());
            let first = entry
                .get("stall_first_at")
                .map_or(clock, |value| value.as_f64().unwrap_or(f64::NAN));
            stalled = stalled
                && field(&entry, "stall_observations").as_i64().unwrap_or(0) >= 3
                && clock - first >= 10.0;
            if safe
                && stalled
                && truthy(field(&entry, "enabled"))
                && truthy(field(&entry, "managed"))
                && !truthy(field(&entry, "external_readonly"))
                && !truthy(field(field(snapshot, "miner"), "paused"))
                && budget["blocked"] != json!(true)
                && clock >= budget["next_allowed_at"].as_f64().unwrap_or(f64::NAN)
                && self.stop_gpu(identifier, false)?
            {
                let label = py_str(field(&status, "label"));
                self.event(identifier, "health_restart", &label)?;
                self.set(
                    identifier,
                    vec![
                        ("state", json!("error")),
                        ("error", json!(format!("Recuperação automática: {label}"))),
                        ("retry_after", json!(clock + 2.0)),
                    ],
                );
            }
        }
        Ok(())
    }
    fn clear_retry(&mut self) {
        for entry in self.devices.values_mut() {
            if let Some(entry) = entry.as_object_mut() {
                entry.shift_remove("retry_after");
                entry.shift_remove("error");
            }
        }
    }

    fn stop_all_for_update(&mut self, message: &str) -> Result<(), String> {
        let identifiers: Vec<String> = self.devices.keys().cloned().collect();
        for identifier in identifiers {
            if !self.stop_gpu(&identifier, false)? {
                return Err(message.into());
            }
        }
        Ok(())
    }

    /// `activate_miner(version, request_id)`: para tudo, registra a validação e troca a
    /// versão selecionada. A versão anterior precisa conferir antes de qualquer parada.
    pub fn activate_miner(&mut self, version: &Value, request_id: &Value) -> Result<(), String> {
        if truthy(&self.miner_update_pending) {
            return Err("Uma atualização do minerador ainda está em validação".into());
        }
        let base = self.miner_base.clone();
        let target = self.catalog.executable(version, &base, true)?;
        let old = self.catalog.selected_version(&base)?;
        self.catalog.executable(&json!(old), &base, true)?;
        if normalized_path(&path_text(&target)) == normalized_path(&path_text(&self.miner_path)) {
            let record = json!({"state": "installed", "version": version, "request_id": request_id, "at": self.clock()});
            return atomic_json(
                &self.state_dir.join("miner-update.json"),
                &record,
                false,
                false,
            );
        }
        if self.devices.values().any(|entry| {
            truthy(field(entry, "external_readonly"))
                || truthy(field(entry, "ownership_unverified"))
        }) {
            return Err(
                "Sessão externa ou sem identidade confirmada; atualização bloqueada".into(),
            );
        }
        self.stop_all_for_update("Parada não confirmada; versão anterior preservada")?;
        let clock = self.clock();
        let pending = json!({"state": "validating", "version": version, "previous_version": old, "request_id": request_id,
            "at": clock, "deadline": clock + 180.0});
        atomic_json(
            &self.state_dir.join("miner-update.json"),
            &pending,
            false,
            false,
        )?;
        self.miner_update_pending = pending;
        self.catalog.select(version, &base, now_wall())?;
        self.miner_path = target;
        self.mapping_signature = None;
        self.clear_retry();
        Ok(())
    }

    /// `validate_miner_update(snapshots)`: confirma a versão nova com hashrate recente
    /// em toda GPU habilitada, ou volta à anterior depois do prazo.
    pub fn validate_miner_update(&mut self, snapshots: &Obj) -> Result<(), String> {
        let pending = self.miner_update_pending.clone();
        if !truthy(&pending) {
            return Ok(());
        }
        let clock = self.clock();
        let valid = self
            .devices
            .iter()
            .filter(|(_, entry)| truthy(field(entry, "enabled")))
            .all(|(key, _)| {
                snapshots.get(key).is_some_and(|snapshot| {
                    let miner = field(snapshot, "miner");
                    let stamp = field(miner, "hashrate_at");
                    snapshot.get("miner_running") == Some(&Value::Bool(true))
                        && !truthy(field(miner, "paused"))
                        && py_eq(field(miner, "version"), field(&pending, "version"))
                        && py_float(field(miner, "hashrate")).is_some_and(|rate| rate > 0.0)
                        && finite(stamp)
                        && stamp
                            .as_f64()
                            .is_some_and(|at| (0.0..=180.0).contains(&(clock - at)))
                })
            });
        let path = self.state_dir.join("miner-update.json");
        if valid {
            let mut record = pending;
            record["state"] = json!("installed");
            record["validated_at"] = json!(clock);
            atomic_json(&path, &record, false, false)?;
            self.miner_update_pending = Value::Null;
        } else if pending["deadline"]
            .as_f64()
            .is_some_and(|deadline| clock > deadline)
        {
            let base = self.miner_base.clone();
            let previous = field(&pending, "previous_version").clone();
            let old = self.catalog.executable(&previous, &base, true)?;
            self.stop_all_for_update("Rollback aguarda confirmação de parada")?;
            self.catalog.select(&previous, &base, now_wall())?;
            self.miner_path = old;
            self.mapping_signature = None;
            self.clear_retry();
            let mut record = pending;
            record["state"] = json!("rolled_back");
            record["error"] =
                json!("Sem hashrate válido por GPU em 180 s; versão anterior selecionada");
            record["at"] = json!(self.clock());
            atomic_json(&path, &record, false, false)?;
            self.miner_update_pending = Value::Null;
        }
        Ok(())
    }

    /// `serve(...)`: o laço do serviço, um ciclo a cada 2 s. Ao sair, por pedido ou por
    /// erro, para todas as GPUs e libera a trava e a proteção contra suspensão.
    pub fn serve(
        &mut self,
        options: &ServeOptions,
        collect: Collector,
        sleep: &mut dyn FnMut(std::time::Duration),
    ) -> Result<(), String> {
        self.require_recovery_sensors = true;
        let mut lease = Lease::acquire(
            &self.state_dir.join("rig-manager-owner.json"),
            "Já existe um proxy de contribuição usando este diretório de logs",
        )?;
        // Retenção diária dos bancos numa thread à parte (nunca bloqueia este laço);
        // termina junto com o serviço.
        let _retention = options.retention.then(|| {
            crate::retention_task::DailyRetention::spawn(
                self.state_dir.clone(),
                self.log_dir.clone(),
            )
        });
        let result = self.serve_loop(options, collect, sleep);
        self.shutting_down = true;
        let identifiers: Vec<String> = self.devices.keys().cloned().collect();
        for identifier in identifiers {
            if let Err(error) = self.stop_gpu(&identifier, false) {
                self.set(&identifier, vec![("error", json!(truncate(&error, 300)))]);
            }
        }
        let published = self.publish();
        self.sleep_guard.update(false);
        lease.close();
        result.and(published)
    }

    fn serve_loop(
        &mut self,
        options: &ServeOptions,
        collect: Collector,
        sleep: &mut dyn FnMut(std::time::Duration),
    ) -> Result<(), String> {
        if options.reference_rtx3070 && options.migrate_legacy_uuid.is_none() {
            let wanted = options.reference_uuid.clone();
            let current = (self.detector)()?
                .into_iter()
                .find(|gpu| gpu.get("uuid").and_then(Value::as_str) == wanted.as_deref());
            if !current.is_some_and(|gpu| reference_device_confirmed(&gpu)) {
                return Err("UUID ou limites da GPU de referência não confirmados".into());
            }
            self.reference_uuid = wanted;
        }
        if options.adopt_legacy {
            self.adopt_legacy(None)?;
        }
        if let Some(uuid) = &options.migrate_legacy_uuid {
            self.migrate_legacy(uuid, options.reference_rtx3070)?;
        }
        let mut detected: Option<Vec<Value>> = None;
        let mut last_detection: Option<std::time::Instant> = None;
        while !self.shutting_down {
            let started = std::time::Instant::now();
            match self.serve_cycle(&mut detected, &mut last_detection, &mut *collect) {
                Ok(true) => break,
                Ok(false) => {}
                Err(error) => {
                    self.error = json!(truncate(&error, 300));
                    self.publish()?;
                }
            }
            sleep(std::time::Duration::from_secs_f64(
                (2.0 - started.elapsed().as_secs_f64()).max(0.1),
            ));
        }
        Ok(())
    }

    fn serve_cycle(
        &mut self,
        detected: &mut Option<Vec<Value>>,
        last_detection: &mut Option<std::time::Instant>,
        collect: Collector,
    ) -> Result<bool, String> {
        self.process_requests();
        if self.shutting_down {
            return Ok(true);
        }
        let clock = self.clock();
        self.continuity.tick(clock);
        if detected.is_none() || last_detection.is_none_or(|at| at.elapsed().as_secs_f64() >= 30.0)
        {
            let found = (self.detector)()?;
            *detected = Some(self.continuity.inventory(&found, self.clock())?);
            *last_detection = Some(std::time::Instant::now());
        }
        let devices = self.reconcile(detected.clone(), None)?;
        let snapshots = collect(&devices, &self.devices)?;
        self.validate_miner_update(&snapshots)?;
        self.observe_health(&snapshots)?;
        let active = self.devices.values().any(|entry| {
            truthy(field(entry, "enabled"))
                && truthy(field(entry, "miner_running"))
                && truthy(field(entry, "managed"))
                && !truthy(field(entry, "external_readonly"))
        });
        self.sleep_guard.update(active);
        self.publish()?;
        Ok(false)
    }
}

/// Opções de `serve`, as mesmas da linha de comando do `rig.py`.
#[derive(Clone, Debug, Default)]
pub struct ServeOptions {
    pub adopt_legacy: bool,
    pub migrate_legacy_uuid: Option<String>,
    pub reference_rtx3070: bool,
    pub reference_uuid: Option<String>,
    /// Retenção diária de `history.sqlite3` e dos `gpu-agent.sqlite3`
    /// ([`crate::retention_task`]). O serviço real liga; os testes não.
    pub retention: bool,
}

/// Leitura dos snapshots de cada ciclo: `(GPUs desejadas, registro) -> snapshots`.
pub type Collector<'a> = &'a mut dyn FnMut(&[Value], &Obj) -> Result<Obj, String>;

/// `finite` reexportado para quem monta os snapshots.
pub fn is_finite(value: &Value) -> bool {
    finite(value)
}
