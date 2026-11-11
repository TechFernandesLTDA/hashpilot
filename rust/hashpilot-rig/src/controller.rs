//! Supervisor NVIDIA explícito (`gpu_agent.py --control`): o `NvidiaActuator`, a leitura
//! do minerador e o laço `control()`.
//!
//! O laço é o único escritor de clocks e potência da GPU, e só enquanto o minerador que o
//! serviço iniciou estiver vivo. Garantias mantidas do Python:
//! - trava exclusiva (`gpu-control-owner.lock`), compatível com o `msvcrt.locking`;
//! - plano revalidado contra os limites do driver antes de cada escrita;
//! - nunca sobe clock ou potência acima do limite térmico suave;
//! - na saída devolve clock (`-rgc`), potência original e memória (`-rmc`).
//!
//! O controlador nunca encerra o minerador: devolve 1 e o gerenciador do rig para o
//! minerador que iniciou.

use std::cell::RefCell;
use std::collections::BTreeSet;
use std::fs::{File, OpenOptions};
use std::path::{Path, PathBuf};
use std::rc::Rc;
use std::sync::Arc;

use hashpilot_core::miner_log::StampZone;
use hashpilot_core::numbers::numeric;
use hashpilot_core::thermal_target::{
    MemoryExplorer, SAFE_START_CLOCK_MHZ, TargetTemperatureController, clamp_target,
};
use hashpilot_history::pyjson::truthy;
use serde_json::{Map, Value, json};

use crate::ai_advisor::{BackgroundAdvisor, RoutedAdvisor};
use crate::gpu_agent::{
    ACTIONS, Advisor, AgentError, AgentResult, GpuAgent, control_plan, external_supervisor_active,
    normalize_sample, performance_probe_gate, read_fan, read_memory_clock, read_memory_clocks,
    read_optimization, read_sample, read_target_temperature, requested_control_stop, select_device,
    snapshot_device_payload, thermal_limits, validated_plan,
};
use crate::nvidia::SmiCommand;
use crate::snapshots::LogReader;

fn runtime(message: impl Into<String>) -> AgentError {
    AgentError::runtime(message)
}

fn os_error(error: std::io::Error) -> AgentError {
    AgentError {
        kind: "OSError",
        message: error.to_string(),
    }
}

fn smi(command: SmiCommand, arguments: &[&str]) -> AgentResult<String> {
    let owned: Vec<String> = arguments.iter().map(|item| (*item).to_string()).collect();
    command(&owned).map_err(runtime)
}

fn text(value: &Value) -> String {
    hashpilot_core::text::py_str(value)
}

/// `NvidiaActuator`: dono explícito do controle. O supervisor térmico existente continua
/// sendo o único escritor.
pub struct NvidiaActuator {
    pub path: PathBuf,
    pub lock_path: PathBuf,
    pub enabled: bool,
    pub token: String,
    pub owns: bool,
    handle: Option<File>,
    pid: u32,
}

impl NvidiaActuator {
    pub fn new(state_dir: &Path, enabled: bool, token: String, pid: u32) -> AgentResult<Self> {
        std::fs::create_dir_all(state_dir).map_err(os_error)?;
        Ok(Self {
            path: state_dir.join("gpu-control-owner.json"),
            lock_path: state_dir.join("gpu-control-owner.lock"),
            enabled,
            token,
            owns: false,
            handle: None,
            pid,
        })
    }

    fn lock(&mut self) -> AgentResult<()> {
        use std::io::Write;
        let mut file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(&self.lock_path)
            .map_err(os_error)?;
        if file.metadata().map_err(os_error)?.len() == 0 {
            file.write_all(b" ").map_err(os_error)?;
        }
        if !crate::win32::lock_first_byte(&file) {
            return Err(runtime("Outra sessão detém o controle da GPU"));
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
        let raw = std::fs::read_to_string(&self.path).ok()?;
        serde_json::from_str(&raw).ok()
    }

    pub fn assert_owner(&self, gpu_uuid: &str) -> AgentResult<()> {
        let invalid = || runtime("Posse da GPU inválida");
        if !self.owns || self.handle.is_none() {
            return Err(invalid());
        }
        let owner = self.read_owner().ok_or_else(invalid)?;
        if !owner.is_object()
            || owner.get("token") != Some(&json!(self.token))
            || owner.get("gpu_uuid") != Some(&json!(gpu_uuid))
        {
            return Err(invalid());
        }
        Ok(())
    }

    pub fn acquire(
        &mut self,
        gpu_uuid: &str,
        external_controller_active: bool,
        now: f64,
    ) -> AgentResult<()> {
        if self.owns || self.handle.is_some() {
            return Err(runtime("Controle da GPU já adquirido"));
        }
        if !self.enabled {
            return Err(runtime("Escritas de hardware não foram habilitadas"));
        }
        if external_controller_active {
            return Err(runtime(
                "Já existe um supervisor térmico ativo; controle concorrente bloqueado",
            ));
        }
        self.lock()?;
        let result = self.claim(gpu_uuid, now);
        if result.is_err() {
            self.unlock();
        }
        result
    }

    fn claim(&mut self, gpu_uuid: &str, now: f64) -> AgentResult<()> {
        use std::io::Write;
        let owner = json!({"pid": self.pid, "token": self.token, "gpu_uuid": gpu_uuid, "at": now, "lease_version": 2});
        let payload = hashpilot_history::pyjson::encode_spaced(&owner);
        if self.path.exists() {
            let ambiguous = || runtime("Trava legada ambígua; controle bloqueado");
            let raw = std::fs::read_to_string(&self.path).map_err(|_| ambiguous())?;
            let previous: Value = serde_json::from_str(&raw).map_err(|_| ambiguous())?;
            if !previous.is_object() {
                return Err(ambiguous());
            }
            if previous.get("lease_version") != Some(&json!(2))
                && crate::gpu_defaults::process_state(previous.get("pid").unwrap_or(&Value::Null))
                    != "dead"
            {
                return Err(runtime("Outra sessão pode deter o controle da GPU"));
            }
            let temporary = self.path.with_file_name(format!(
                "{}.{}.tmp",
                self.path
                    .file_name()
                    .map(|name| name.to_string_lossy().into_owned())
                    .unwrap_or_default(),
                self.token
            ));
            let written = std::fs::write(&temporary, &payload)
                .and_then(|()| std::fs::rename(&temporary, &self.path));
            let _ = std::fs::remove_file(&temporary);
            written.map_err(os_error)?;
        } else {
            let mut file = match OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&self.path)
            {
                Ok(file) => file,
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                    return Err(runtime("Outra sessão detém o controle da GPU"));
                }
                Err(error) => return Err(os_error(error)),
            };
            file.write_all(payload.as_bytes()).map_err(os_error)?;
        }
        self.owns = true;
        Ok(())
    }

    /// `apply`: revalida o plano e escreve um único ajuste (clock travado ou potência).
    pub fn apply(
        &self,
        gpu: &Value,
        sample: &Value,
        plan: &Value,
        external_controller_active: bool,
        now: f64,
        command: SmiCommand,
    ) -> AgentResult<Value> {
        crate::nvidia::validate_device(gpu).map_err(AgentError::value)?;
        if !self.enabled || !self.owns || external_controller_active {
            return Err(runtime("Hardware somente leitura ou controle concorrente"));
        }
        let uuid = text(&gpu["uuid"]);
        self.assert_owner(&uuid)?;
        let normalized = normalize_sample(sample, Some(now));
        let temperature = numeric(&normalized["temperature_c"]);
        let sampled = numeric(&normalized["sampled_at"]);
        let fresh = sampled.is_some_and(|at| (-2.0..=10.0).contains(&(now - at)));
        if !temperature.is_some_and(|temp| (0.0..=120.0).contains(&temp)) || !fresh {
            return Err(runtime("Sensores atuais obrigatórios antes de aplicar"));
        }
        let planned_at = numeric(plan.get("at").unwrap_or(&Value::Null)).unwrap_or(0.0);
        if plan.get("gpu_uuid") != Some(&json!(uuid)) || !(0.0..=30.0).contains(&(now - planned_at))
        {
            return Err(runtime("Plano antigo ou de outra GPU"));
        }
        let action = plan
            .get("action")
            .and_then(Value::as_str)
            .ok_or_else(|| AgentError {
                kind: "KeyError",
                message: "'action'".into(),
            })?;
        let checked = validated_plan(gpu, sample, action, None)?;
        for key in ["clock_mhz", "power_limit_w"] {
            if !hashpilot_history::pyjson::py_eq(
                plan.get(key).unwrap_or(&Value::Null),
                checked.get(key).unwrap_or(&Value::Null),
            ) {
                return Err(AgentError::value(
                    "Plano adulterado ou fora dos limites do hardware",
                ));
            }
        }
        if temperature.unwrap_or(0.0) >= thermal_limits(gpu).0
            && matches!(action, "clock_up" | "power_up")
        {
            return Err(runtime("Aumento bloqueado pela temperatura"));
        }
        if let Some(clock) =
            numeric(&checked["clock_mhz"]).filter(|_| !checked["clock_mhz"].is_null())
        {
            let value = clock.trunc() as i64;
            smi(command, &["-i", &uuid, "-lgc", &format!("{value},{value}")])?;
        } else if !checked["power_limit_w"].is_null() {
            smi(
                command,
                &["-i", &uuid, "-pl", &text(&checked["power_limit_w"])],
            )?;
        }
        Ok(checked)
    }

    /// `set_memory_clock`: trava a memória num clock da lista do driver.
    pub fn set_memory_clock(
        &self,
        gpu: &Value,
        clock: f64,
        supported: &[i64],
        command: SmiCommand,
    ) -> AgentResult<i64> {
        crate::nvidia::validate_device(gpu).map_err(AgentError::value)?;
        if !self.enabled || !self.owns {
            return Err(runtime("Hardware somente leitura ou controle concorrente"));
        }
        let value = clock.trunc() as i64;
        if !supported.contains(&value) {
            return Err(AgentError::value(
                "Clock de memória fora da lista do driver",
            ));
        }
        let uuid = text(&gpu["uuid"]);
        self.assert_owner(&uuid)?;
        smi(command, &["-i", &uuid, "-lmc", &format!("{value},{value}")])?;
        Ok(value)
    }

    /// `apply_reduction`: trava num clock suportado `<=` pedido e `<` atual (só reduz).
    /// `startup` trava mesmo com a GPU ociosa, antes do boost de fábrica esquentar a placa.
    pub fn apply_reduction(
        &self,
        gpu: &Value,
        sample: &Value,
        clock: f64,
        startup: bool,
        now: f64,
        command: SmiCommand,
    ) -> AgentResult<Option<i64>> {
        crate::nvidia::validate_device(gpu).map_err(AgentError::value)?;
        if !self.enabled || !self.owns {
            return Err(runtime("Hardware somente leitura ou controle concorrente"));
        }
        let uuid = text(&gpu["uuid"]);
        self.assert_owner(&uuid)?;
        let current = numeric(&normalize_sample(sample, Some(now))["clock_mhz"]);
        let clocks: BTreeSet<i64> = gpu
            .get("supported_graphics_clocks_mhz")
            .and_then(Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .filter_map(|item| numeric(item).filter(|value| *value != 0.0 && *value > 0.0))
                    .map(|value| value.trunc() as i64)
                    .collect()
            })
            .unwrap_or_default();
        let eligible = clocks
            .into_iter()
            .filter(|value| {
                (*value as f64) <= clock
                    && (startup || current.is_none_or(|current| (*value as f64) < current))
            })
            .max();
        let Some(value) = eligible else {
            return Ok(None);
        };
        smi(command, &["-i", &uuid, "-lgc", &format!("{value},{value}")])?;
        Ok(Some(value))
    }

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

impl Drop for NvidiaActuator {
    fn drop(&mut self) {
        self.unlock();
    }
}

/// Mantém só o arquivo `mining-*.log` mais recente da pasta.
fn newest_log(directory: &Path) -> Option<PathBuf> {
    let mut best: Option<(std::time::SystemTime, PathBuf)> = None;
    for entry in std::fs::read_dir(directory).ok()?.flatten() {
        let name = entry.file_name().to_string_lossy().into_owned();
        if !(name.starts_with("mining-") && name.ends_with(".log")) {
            continue;
        }
        let Ok(modified) = entry.metadata().and_then(|meta| meta.modified()) else {
            continue;
        };
        if best.as_ref().is_none_or(|(time, _)| modified > *time) {
            best = Some((modified, entry.path()));
        }
    }
    best.map(|(_, path)| path)
}

fn read_json_sig(path: &Path) -> Option<Value> {
    let raw = std::fs::read(path).ok()?;
    let text = String::from_utf8(raw).ok()?;
    serde_json::from_str(text.strip_prefix('\u{feff}').unwrap_or(&text)).ok()
}

/// Onde o controlador lê o minerador: o log exclusivo desta GPU ou o `latest.json` do monitor.
#[derive(Clone, Debug, Default)]
pub struct MinerSource {
    pub miner_log: Option<PathBuf>,
    pub log_dir: Option<PathBuf>,
    pub miner_index: Option<i64>,
}

/// `read_miner_data`: `(minerador, atual)`. Hashrate agregado de um rig nunca é atribuído
/// a uma GPU; dado ausente vem como objeto vazio e `false`.
pub fn read_miner_data(
    source: &MinerSource,
    gpu: &Value,
    monitor_state_dir: &Path,
    device_count: usize,
    now: f64,
    read_log: LogReader,
) -> AgentResult<(Value, bool)> {
    let mut path = source.miner_log.clone();
    if path.is_none()
        && let Some(directory) = &source.log_dir
    {
        match newest_log(directory) {
            Some(found) => path = Some(found),
            None => return Ok((json!({}), false)),
        }
    }
    if let Some(path) = path {
        let mut device = gpu.clone();
        if let Some(index) = source.miner_index {
            device["miner_index"] = json!(index);
        }
        let payload = crate::snapshots::read_device_payload(
            &device,
            &json!(path.to_string_lossy()),
            &Value::Null,
            now,
            StampZone::Local,
            read_log,
        )
        .map_err(AgentError::value)?;
        let miner = payload["miner"].clone();
        let stamp = numeric(miner.get("hashrate_at").unwrap_or(&Value::Null));
        let fresh = payload["log_error"].is_null()
            && stamp.is_some_and(|stamp| (0.0..=180.0).contains(&(now - stamp)));
        return Ok((miner, fresh));
    }
    let Some(snapshot) = read_json_sig(&monitor_state_dir.join("latest.json")) else {
        return Ok((json!({}), false));
    };
    let Some(entry) = snapshot_device_payload(&snapshot, gpu, device_count) else {
        return Ok((json!({}), false));
    };
    let collected = numeric(entry.get("collected_at").unwrap_or(&Value::Null))
        .or_else(|| numeric(snapshot.get("collected_at").unwrap_or(&Value::Null)))
        .unwrap_or(0.0);
    let miner = entry
        .get("miner")
        .filter(|value| truthy(value))
        .cloned()
        .unwrap_or_else(|| json!({}));
    Ok((miner, (0.0..=10.0).contains(&(now - collected))))
}

/// Argumentos do `gpu_agent.py --control`.
#[derive(Clone, Debug)]
pub struct ControlArgs {
    pub miner_pid: u32,
    pub uuid: Option<String>,
    pub index: Option<i64>,
    pub duration: i64,
    pub objective: String,
    pub profile: String,
    pub source: MinerSource,
    pub samples: u64,
}

/// Tudo que toca o mundo fora do laço. O serviço usa [`Hooks::real`]; o teste de paridade
/// troca nvidia-smi, relógio, processos e minerador por roteiros.
#[allow(clippy::type_complexity)]
pub struct Hooks {
    pub smi: Box<dyn FnMut(&[String]) -> Result<String, String>>,
    pub detect: Box<dyn FnMut() -> Result<Vec<Value>, String>>,
    pub now: Arc<dyn Fn() -> f64 + Send + Sync>,
    pub sleep: Box<dyn FnMut(f64)>,
    pub is_admin: Box<dyn Fn() -> bool>,
    /// `process_identity(pid)`: `None` quando o processo não pode ser lido.
    pub identity: Box<dyn FnMut(u32) -> Option<(u64, String)>>,
    /// `process_alive(pid)`.
    pub alive: Box<dyn FnMut(u32) -> bool>,
    pub read_miner: Box<dyn FnMut(&Value, usize, f64) -> AgentResult<(Value, bool)>>,
    pub ids: Box<dyn FnMut() -> String>,
    pub software: Value,
    pub pid: u32,
    /// `config.SETTINGS_PATH` (alvo térmico, objetivo, IA).
    pub settings_path: PathBuf,
    pub advisor: Option<Arc<RoutedAdvisor>>,
    /// Ctrl+C: o Python sai por `KeyboardInterrupt`, que pula o `except` e passa pelo `finally`.
    pub interrupted: Box<dyn Fn() -> bool>,
    /// [`GpuAgent::hold_heartbeat`]: o serviço deduplica os `hold` repetidos; o roteiro
    /// de paridade usa `None` para gravar cada decisão como o Python.
    pub hold_heartbeat: Option<f64>,
}

/// `process_identity` real: horário de criação e executável (minúsculo).
pub fn process_identity(pid: u32) -> Option<(u64, String)> {
    if pid == 0 {
        return None;
    }
    crate::win32::process_metadata(pid)
        .map(|meta| (meta.created_at, meta.executable.to_lowercase()))
}

impl Hooks {
    /// `read_log` é o leitor do fim do log do SRBMiner (`gpu_telemetry.read_log_tail`).
    pub fn real(
        monitor_state_dir: PathBuf,
        source: MinerSource,
        software: Value,
        read_log: fn(&Path) -> std::io::Result<String>,
    ) -> AgentResult<Self> {
        let global = hashpilot_core::config::resolve_state_dir(
            hashpilot_core::config::current_platform(),
            &hashpilot_core::config::process_env,
        )
        .map_err(runtime)?;
        let advisor = Arc::new(RoutedAdvisor::new(
            &global,
            None,
            None,
            None,
            crate::ai_advisor::Effects::real(),
        ));
        let clock: Arc<dyn Fn() -> f64 + Send + Sync> = Arc::new(|| {
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map_or(0.0, |elapsed| elapsed.as_secs_f64())
        });
        let detect_clock = clock.clone();
        Ok(Self {
            smi: Box::new(crate::nvidia::smi_command),
            detect: Box::new(move || {
                crate::nvidia::detect_nvidia_gpus(
                    &mut crate::nvidia::smi_command,
                    None,
                    None,
                    detect_clock(),
                )
            }),
            now: clock,
            sleep: Box::new(|seconds| {
                // Acorda a cada 100 ms para atender o Ctrl+C sem esperar o ciclo inteiro.
                let deadline =
                    std::time::Instant::now() + std::time::Duration::from_secs_f64(seconds);
                while std::time::Instant::now() < deadline
                    && !crate::win32::CTRL_STOP.load(std::sync::atomic::Ordering::SeqCst)
                {
                    std::thread::sleep(std::time::Duration::from_millis(100));
                }
            }),
            interrupted: Box::new(|| {
                crate::win32::CTRL_STOP.load(std::sync::atomic::Ordering::SeqCst)
            }),
            is_admin: Box::new(crate::win32::is_user_an_admin),
            identity: Box::new(process_identity),
            alive: Box::new(|pid| crate::win32::process_state(pid) == "alive"),
            read_miner: Box::new(move |gpu, count, now| {
                read_miner_data(
                    &source,
                    gpu,
                    &monitor_state_dir,
                    count,
                    now,
                    &mut |path: &Path| read_log(path),
                )
            }),
            ids: Box::new(crate::gpu_agent::uuid4_hex),
            software,
            pid: std::process::id(),
            settings_path: global.join("settings.json"),
            advisor: Some(advisor),
            hold_heartbeat: Some(crate::gpu_agent::HOLD_HEARTBEAT_SECONDS),
        })
    }
}

/// O `RoutedAdvisor` compartilhado entre o `GpuAgent` e o `BackgroundAdvisor`.
struct SharedAdvisor(Arc<RoutedAdvisor>);

impl Advisor for SharedAdvisor {
    fn advise(&self, context: &Value) -> Result<Value, String> {
        self.0.advise(context).map_err(|error| error.message)
    }
}

/// `round(valor, 3)` do Python para exibição.
fn round3(value: f64) -> Value {
    format!("{value:.3}")
        .parse::<f64>()
        .map_or(Value::Null, |rounded| json!(rounded))
}

fn clock_key(value: f64) -> String {
    (value.trunc() as i64).to_string()
}

fn memory_scores(explorer: &MemoryExplorer) -> Value {
    Value::Object(
        explorer
            .scores
            .iter()
            .map(|(clock, score)| (clock_key(*clock), round3(*score)))
            .collect(),
    )
}

fn float_or_null(value: Option<f64>) -> Value {
    value.map_or(Value::Null, |number| json!(number))
}

/// `int(x)` do Python para contadores do minerador (`int(miner.get(..., 0) or 0)`).
fn int_of(value: &Value) -> i64 {
    numeric(value).map_or(0, |number| number.trunc() as i64)
}

struct Loop<'a> {
    args: &'a ControlArgs,
    hooks: &'a mut Hooks,
    gpu: Value,
    state_dir: PathBuf,
    monitor_state_dir: PathBuf,
    status: Map<String, Value>,
    actuator: NvidiaActuator,
    agent: GpuAgent,
    memory_explorer: Option<MemoryExplorer>,
    memory_path: PathBuf,
}

impl Loop<'_> {
    fn now(&self) -> f64 {
        (self.hooks.now)()
    }

    fn other_supervisor(&self) -> bool {
        external_supervisor_active(
            &[&self.state_dir, &self.monitor_state_dir],
            Some(&text(&self.gpu["uuid"])),
            self.gpu["index"].as_i64(),
            self.hooks.pid,
        )
    }

    fn sample(&mut self) -> AgentResult<Value> {
        let now = self.now();
        read_sample(&self.gpu, &mut *self.hooks.smi, Some(now))
    }

    fn uuid(&self) -> String {
        text(&self.gpu["uuid"])
    }

    fn writes_plus(&mut self) {
        let writes = self
            .status
            .get("writes")
            .and_then(Value::as_i64)
            .unwrap_or(0);
        self.status.insert("writes".into(), json!(writes + 1));
    }

    fn set(&mut self, pairs: Vec<(&str, Value)>) {
        for (key, value) in pairs {
            self.status.insert(key.into(), value);
        }
    }

    fn write_status(&self) -> AgentResult<()> {
        crate::fsutil::atomic_json(
            &self.state_dir.join("gpu-agent-control.json"),
            &Value::Object(self.status.clone()),
            false,
            false,
        )
        .map_err(|message| AgentError {
            kind: "OSError",
            message,
        })
    }

    fn apply_recorded(&mut self, sample: &Value, plan: &Value, rate: &Value) -> AgentResult<()> {
        let now = self.now();
        self.actuator
            .apply(&self.gpu, sample, plan, false, now, &mut *self.hooks.smi)?;
        let measured = self.sample()?;
        self.agent
            .record_adjustment(&self.gpu, sample, &measured, plan, rate, None)
    }

    fn remembered(&self) -> Map<String, Value> {
        read_json_sig(&self.memory_path)
            .and_then(|data| data.as_object().cloned())
            .filter(|data| data.get("gpu_uuid") == Some(&json!(self.uuid())))
            .unwrap_or_default()
    }

    fn remember(&self, values: Vec<(&str, Value)>) {
        let mut data = self.remembered();
        data.insert("gpu_uuid".into(), json!(self.uuid()));
        for (key, value) in values {
            data.insert(key.into(), value);
        }
        data.insert("at".into(), json!(self.now()));
        let _ = crate::fsutil::atomic_json(&self.memory_path, &Value::Object(data), false, false);
    }

    fn start_clock(&self) -> f64 {
        if let Some(memory) = read_json_sig(&self.memory_path)
            && memory.get("gpu_uuid") == Some(&json!(self.uuid()))
            && let Some(clock) = numeric(memory.get("clock_mhz").unwrap_or(&Value::Null))
                .filter(|clock| *clock != 0.0)
        {
            return clock.min(SAFE_START_CLOCK_MHZ);
        }
        SAFE_START_CLOCK_MHZ
    }

    /// Cancela o teste em andamento e aplica o rollback, se houver.
    fn cancel_trial(&mut self, sample: &Value, miner: &Value) -> AgentResult<()> {
        let rollback = self.agent.cancel_trial(&self.gpu, sample, None)?;
        if let Some(rollback) = rollback
            && rollback["action"] != json!("hold")
        {
            self.apply_recorded(
                sample,
                &rollback,
                miner.get("hashrate").unwrap_or(&Value::Null),
            )?;
            self.writes_plus();
        }
        Ok(())
    }

    fn run(&mut self) -> AgentResult<()> {
        let uuid = self.uuid();
        let started = self.now();
        let other = self.other_supervisor();
        let now = self.now();
        self.actuator.acquire(&uuid, other, now)?;
        let recovered = {
            let actuator = &self.actuator;
            let uuid = uuid.clone();
            self.agent.recover_interrupted_trials(
                &self.gpu,
                &mut || actuator.assert_owner(&uuid),
                None,
            )?
        };
        self.set(vec![
            ("recovered_interrupted_trials", json!(recovered)),
            ("enabled", json!(true)),
        ]);
        let duration = json!(self.args.duration);
        let mut objective = self.args.objective.clone();
        let mut selected_profile = self.args.profile.clone();
        self.agent
            .start_benchmark(&self.gpu, &duration, &objective, None)?;
        let mut trial_active = false;
        let mut trial_rejected = 0i64;
        let mut cycles = 0u64;
        let mut last_request = 0.0f64;
        let mut last_thermal_adjustment = 0.0f64;
        let settings_path = self.hooks.settings_path.clone();
        let mut target_controller: Option<TargetTemperatureController> = None;
        let mut target_checked_at = f64::NEG_INFINITY;
        let mut hot_since: Option<f64> = None;
        let mut memory_supported: Vec<i64> = Vec::new();
        let device_count = self
            .status
            .get("device_count")
            .and_then(Value::as_u64)
            .unwrap_or(1) as usize;
        let background = self
            .hooks
            .advisor
            .clone()
            .map(|advisor| BackgroundAdvisor::new(advisor, 60.0, self.hooks.now.clone()));
        let mut background = background;
        let miner_pid = self.args.miner_pid;
        let identity = (self.hooks.identity)(miner_pid);
        loop {
            let same = identity.is_some()
                && (self.hooks.alive)(miner_pid)
                && (self.hooks.identity)(miner_pid) == identity;
            if !same {
                break;
            }
            if (self.hooks.interrupted)() {
                return Err(AgentError {
                    kind: "KeyboardInterrupt",
                    message: String::new(),
                });
            }
            if requested_control_stop(
                &self.state_dir,
                &uuid,
                started,
                Some(self.now()),
                self.hooks.pid,
            ) {
                self.set(vec![
                    ("mode", json!("stopped")),
                    (
                        "reason",
                        json!("Parada solicitada pelo gerenciador desta GPU"),
                    ),
                ]);
                break;
            }
            if self.other_supervisor() {
                return Err(runtime(
                    "Outro supervisor detectado; interrompendo controle concorrente",
                ));
            }
            let stamp = self.now();
            let (miner, fresh) = (self.hooks.read_miner)(&self.gpu, device_count, stamp)?;
            let mut sample = self.sample()?;
            if sample["temperature_c"].is_null() {
                return Err(runtime(
                    "Limite térmico ou sensor inválido: supervisor deve encerrar o minerador",
                ));
            }
            let now = self.now();
            let (soft_hw, hard) = thermal_limits(&self.gpu);
            if now - target_checked_at >= 10.0 {
                // Relido periodicamente: mudar o alvo nas Configurações vale sem reiniciar.
                target_checked_at = now;
                let target = clamp_target(read_target_temperature(&settings_path, &uuid), hard);
                match (target, target_controller.as_mut()) {
                    (None, _) => target_controller = None,
                    (Some(target), None) => {
                        target_controller = Some(TargetTemperatureController::new(target));
                        memory_supported = read_memory_clocks(&self.gpu, &mut *self.hooks.smi);
                        if memory_supported.len() >= 2 {
                            let best = numeric(
                                self.remembered().get("memory_mhz").unwrap_or(&Value::Null),
                            );
                            let candidates: Vec<f64> =
                                memory_supported.iter().map(|value| *value as f64).collect();
                            let mut explorer = MemoryExplorer::new(&candidates, target);
                            explorer.best_mhz = best.filter(|best| candidates.contains(best));
                            self.memory_explorer = Some(explorer);
                        }
                        // Partida segura: trava no último clock bom antes do boost de fábrica.
                        let clock = numeric(&sample["clock_mhz"]);
                        let running = clock.filter(|clock| *clock >= 1000.0);
                        let wanted = self.start_clock().min(running.unwrap_or(f64::INFINITY));
                        let locked = self.actuator.apply_reduction(
                            &self.gpu,
                            &sample,
                            wanted,
                            true,
                            now,
                            &mut *self.hooks.smi,
                        )?;
                        if let Some(locked) = locked {
                            self.writes_plus();
                            self.set(vec![("start_clock_mhz", json!(locked))]);
                            sample = self.sample()?;
                        }
                        if trial_active {
                            self.cancel_trial(&sample, &miner)?;
                            trial_active = false;
                        }
                    }
                    (Some(target), Some(controller)) => {
                        controller.target_c = target;
                        if let Some(explorer) = self.memory_explorer.as_mut() {
                            explorer.target_c = target;
                        }
                    }
                }
                if target.is_none() {
                    self.memory_explorer = None;
                }
                if let Some(explorer) = self.memory_explorer.as_mut() {
                    let finance = settings_path
                        .parent()
                        .unwrap_or(Path::new("."))
                        .join("finance-latest.json");
                    let (objective_now, price, revenue) =
                        read_optimization(&settings_path, &finance);
                    if objective_now != explorer.objective && explorer.best_mhz.is_some() {
                        explorer.explored_at = f64::NEG_INFINITY; // objetivo mudou: reexplorar
                    }
                    explorer.objective = objective_now;
                    explorer.price_kwh = price;
                    explorer.revenue_per_ths_day = revenue;
                }
            }
            let temperature = numeric(&sample["temperature_c"]).unwrap_or(0.0);
            let clock = numeric(&sample["clock_mhz"]).filter(|_| !sample["clock_mhz"].is_null());
            if temperature >= hard {
                // Sem alvo: contrato original (encerrar). Com alvo: reduz forte e só desiste após 10 s.
                let Some(controller) = target_controller.as_mut() else {
                    return Err(runtime(
                        "Limite térmico ou sensor inválido: supervisor deve encerrar o minerador",
                    ));
                };
                let since = *hot_since.get_or_insert(now);
                if now - since >= 10.0 {
                    return Err(runtime(
                        "Limite térmico por mais de 10 s mesmo reduzindo: supervisor deve encerrar o minerador",
                    ));
                }
                if let Some(clock) = clock {
                    let locked = self.actuator.apply_reduction(
                        &self.gpu,
                        &sample,
                        clock - 150.0,
                        false,
                        now,
                        &mut *self.hooks.smi,
                    )?;
                    if let Some(locked) = locked {
                        self.writes_plus();
                        controller.notify_applied(
                            "clock_down",
                            Some(clock),
                            Some(locked as f64),
                            now,
                            None,
                        );
                        self.remember(vec![("clock_mhz", json!(locked))]);
                    }
                }
                let target_c = target_controller
                    .as_ref()
                    .map(|controller| controller.target_c);
                self.set(vec![
                    ("mode", json!("target")),
                    ("sampled_at", json!(now)),
                    ("sample", sample.clone()),
                    (
                        "target_control",
                        json!({"target_c": target_c, "action": "clock_down",
                            "reason": format!("Emergência: {temperature:.0} °C; redução forte aplicada")}),
                    ),
                ]);
                self.write_status()?;
                cycles += 1;
                if self.args.samples > 0 && cycles >= self.args.samples {
                    break;
                }
                (self.hooks.sleep)(2.0);
                continue;
            }
            hot_since = None;
            if let Some(controller) = target_controller.as_mut() {
                let fan = read_fan(&self.gpu, &mut *self.hooks.smi);
                let rate_value = if fresh {
                    miner.get("hashrate").cloned().unwrap_or(Value::Null)
                } else {
                    Value::Null
                };
                let rate = numeric(&rate_value);
                let mut decision = controller.step(Some(temperature), fan, clock, now, rate);
                let mut applied = Value::Null;
                let excess = temperature - controller.target_c;
                if decision.action == "clock_down" && excess >= 2.0 && clock.is_some() {
                    // Muito acima do alvo: 15 MHz por °C, até 90 MHz.
                    let clock = clock.unwrap_or(0.0);
                    let step = (15.0 * excess.trunc()).min(90.0);
                    let locked = self.actuator.apply_reduction(
                        &self.gpu,
                        &sample,
                        clock - step,
                        false,
                        now,
                        &mut *self.hooks.smi,
                    )?;
                    if let Some(locked) = locked {
                        controller.notify_applied(
                            "clock_down",
                            Some(clock),
                            Some(locked as f64),
                            now,
                            rate,
                        );
                        self.remember(vec![("clock_mhz", json!(locked))]);
                        self.writes_plus();
                        applied = json!({"action": "clock_down", "clock_mhz": locked});
                    }
                } else if decision.action == "clock_up" && temperature >= soft_hw {
                    decision.reason =
                        "Folga em relação ao alvo, mas acima do limite de subida segura do atuador"
                            .into();
                } else if decision.action != "hold" {
                    let mut plan = validated_plan(&self.gpu, &sample, decision.action, None)?;
                    plan["gpu_uuid"] = json!(uuid);
                    plan["at"] = json!(now);
                    plan["reason"] = json!(decision.reason);
                    if plan["action"] != json!("hold") {
                        self.apply_recorded(&sample, &plan, &rate_value)?;
                        let action = text(&plan["action"]);
                        let controller = target_controller.as_mut().expect("alvo ativo");
                        controller.notify_applied(
                            &action,
                            clock,
                            numeric(&plan["clock_mhz"]),
                            now,
                            rate,
                        );
                        self.remember(vec![("clock_mhz", plan["clock_mhz"].clone())]);
                        self.writes_plus();
                        applied = plan;
                    }
                }
                let controller = target_controller.as_mut().expect("alvo ativo");
                let mut memory_info = Value::Null;
                if self.memory_explorer.is_some() {
                    let errors = int_of(miner.get("rejected").unwrap_or(&Value::Null))
                        + int_of(miner.get("hw_errors").unwrap_or(&Value::Null));
                    let actual = read_memory_clock(&self.gpu, &mut *self.hooks.smi);
                    let power = numeric(sample.get("power_w").unwrap_or(&Value::Null));
                    let explorer = self.memory_explorer.as_mut().expect("explorador");
                    let wanted = explorer.step(
                        now,
                        Some(temperature),
                        rate,
                        Some(errors as f64),
                        actual,
                        power,
                    );
                    if let Some(wanted) = wanted {
                        self.actuator.set_memory_clock(
                            &self.gpu,
                            wanted,
                            &memory_supported,
                            &mut *self.hooks.smi,
                        )?;
                        self.writes_plus();
                        // Outra memória muda o calor: o teto aprendido do núcleo deixa de valer.
                        controller.ceiling_mhz = None;
                        controller.pending_up = None;
                    }
                    let explorer = self.memory_explorer.as_ref().expect("explorador");
                    if let Some(best) = explorer.best_mhz
                        && explorer.queue.is_empty()
                    {
                        let scores = memory_scores(explorer);
                        self.remember(vec![("memory_mhz", json!(best)), ("memory_scores", scores)]);
                    }
                    let explorer = self.memory_explorer.as_ref().expect("explorador");
                    let unreachable: BTreeSet<i64> = explorer
                        .unreachable
                        .iter()
                        .map(|bits| f64::from_bits(*bits).trunc() as i64)
                        .collect();
                    memory_info = json!({"testing_mhz": float_or_null(explorer.current_mhz), "best_mhz": float_or_null(explorer.best_mhz),
                        "pending": explorer.queue.iter().map(|clock| clock.trunc() as i64).collect::<Vec<_>>(),
                        "unreachable": unreachable.into_iter().collect::<Vec<_>>(),
                        "objective": explorer.objective, "price_kwh": float_or_null(explorer.price_kwh),
                        "revenue_per_ths_day": float_or_null(explorer.revenue_per_ths_day),
                        "scores": memory_scores(explorer)});
                }
                let target_c = controller.target_c;
                self.set(vec![
                    ("mode", json!("target")),
                    ("sampled_at", json!(now)),
                    ("sample", sample.clone()),
                    (
                        "target_control",
                        json!({"target_c": target_c, "fan_pct": float_or_null(fan), "action": decision.action,
                            "reason": decision.reason, "ceiling_mhz": float_or_null(decision.ceiling_mhz),
                            "applied": applied, "memory": memory_info}),
                    ),
                ]);
                self.observe(&sample, &rate_value, &miner)?;
                self.write_status()?;
                cycles += 1;
                if self.args.samples > 0 && cycles >= self.args.samples {
                    break;
                }
                (self.hooks.sleep)(2.0);
                continue;
            }
            let margin = crate::reference::profile(&json!(selected_profile))
                .map_err(AgentError::value)?
                .get("thermal_margin")
                .and_then(numeric)
                .unwrap_or(0.0);
            let soft_limit = soft_hw - margin;
            let hashrate = miner.get("hashrate").cloned().unwrap_or(Value::Null);
            if temperature >= soft_limit && now - last_thermal_adjustment >= 6.0 {
                let mut cooling = validated_plan(&self.gpu, &sample, "clock_down", None)?;
                cooling["gpu_uuid"] = json!(uuid);
                cooling["at"] = json!(now);
                cooling["reason"] = json!("Alívio térmico gradual durante a medição");
                if cooling["action"] != json!("hold") {
                    self.apply_recorded(&sample, &cooling, &hashrate)?;
                    self.writes_plus();
                    self.set(vec![
                        ("mode", json!("cooling")),
                        ("thermal_proposal", cooling),
                    ]);
                    last_thermal_adjustment = now;
                    sample = self.sample()?;
                }
            }
            match self.benchmark_request(now, last_request, &selected_profile) {
                Ok(None) => {}
                Ok(Some((requested_profile, requested_objective, requested_duration, stamp))) => {
                    if trial_active {
                        self.cancel_trial(&sample, &miner)?;
                        trial_active = false;
                    }
                    objective = requested_objective;
                    self.agent
                        .start_benchmark(&self.gpu, &requested_duration, &objective, None)?;
                    selected_profile = requested_profile;
                    last_request = stamp;
                    self.set(vec![
                        ("profile", json!(selected_profile)),
                        ("mode", json!("manual_baseline")),
                        ("last_manual_request", json!(stamp)),
                    ]);
                }
                Err(Some(error)) => return Err(error),
                Err(None) => {
                    self.set(vec![(
                        "request_error",
                        json!("Pedido de benchmark inválido; ignorado"),
                    )]);
                }
            }
            if trial_active && !fresh {
                return Err(runtime(
                    "Monitor sem telemetria atual durante ajuste; encerrando controle",
                ));
            }
            let fresh_rate = if fresh { hashrate.clone() } else { Value::Null };
            let result = self.observe(&sample, &fresh_rate, &miner)?;
            self.set(vec![
                ("sampled_at", json!(now)),
                ("sample", sample.clone()),
                ("benchmark", result.clone()),
            ]);
            let mut answer: Option<Value> = None;
            let elapsed =
                numeric(result.get("elapsed_seconds").unwrap_or(&json!(0))).unwrap_or(0.0);
            let enabled = self
                .hooks
                .advisor
                .as_ref()
                .is_some_and(|advisor| advisor.enabled());
            if let Some(background) = background.as_mut()
                && enabled
                && elapsed >= 60.0
            {
                let pick = |key: &str| result.get(key).cloned().unwrap_or(Value::Null);
                let context = json!({"sample": sample, "benchmark": {"validated": pick("validated"),
                        "mean_hashrate_ths": pick("mean_hashrate_ths"), "mean_power_w": pick("mean_power_w"),
                        "efficiency_ths_per_w": pick("efficiency_ths_per_w")}, "allowed_actions": ACTIONS});
                let polled = background.poll(&context, None);
                self.set(vec![("ai_status", polled["status"].clone())]);
                answer = Some(polled);
            }
            if trial_active {
                let rejected =
                    (int_of(miner.get("rejected").unwrap_or(&json!(0))) - trial_rejected).max(0);
                let trial = self.agent.trial_feedback(
                    &self.gpu,
                    &sample,
                    miner.get("hashrate").unwrap_or(&Value::Null),
                    miner.get("hashrate_at").unwrap_or(&Value::Null),
                    rejected,
                    None,
                )?;
                self.set(vec![
                    ("mode", json!("validating")),
                    ("trial", trial.clone()),
                ]);
                if trial["state"] == json!("rejected") {
                    if let Some(rollback) = trial.get("rollback").filter(|value| truthy(value)) {
                        self.apply_recorded(&sample, rollback, &hashrate)?;
                        self.writes_plus();
                    }
                    trial_active = false;
                    self.set(vec![("mode", json!("rollback"))]);
                    self.agent
                        .start_benchmark(&self.gpu, &duration, &objective, None)?;
                } else if trial["state"] == json!("accepted") {
                    trial_active = false;
                    self.set(vec![("mode", json!("accepted"))]);
                    self.agent
                        .start_benchmark(&self.gpu, &duration, &objective, None)?;
                }
            } else if truthy(result.get("validated").unwrap_or(&Value::Null)) && fresh {
                // Explora um passo suportado pelo driver depois de um baseline medido.
                let mut advice = Value::Null;
                if let Some(answer) = &answer {
                    advice = answer["advice"].clone();
                    self.set(vec![("ai_status", answer["status"].clone())]);
                }
                let best = result.get("best_profile").filter(|value| !value.is_null());
                let advice_ref = (!advice.is_null()).then_some(&advice);
                let mut plan = control_plan(
                    &self.gpu,
                    &sample,
                    &objective,
                    &selected_profile,
                    soft_limit,
                    advice_ref,
                    best,
                )?;
                plan["gpu_uuid"] = json!(uuid);
                plan["at"] = json!(self.now());
                plan["reason"] = json!("Experimento após baseline validado");
                let gate = performance_probe_gate(&sample, &result, &selected_profile, soft_limit);
                self.set(vec![("probe_gate", gate.clone())]);
                let action = text(&plan["action"]);
                if action != "hold"
                    && (!matches!(action.as_str(), "clock_up" | "power_up")
                        || truthy(&gate["allowed"]))
                {
                    // Recusa um teste conflitante antes de mexer no hardware.
                    self.agent.assert_no_pending_trial(&self.gpu)?;
                    self.apply_recorded(&sample, &plan, &hashrate)?;
                    let baseline = result
                        .get("mean_hashrate_ths")
                        .cloned()
                        .unwrap_or(Value::Null);
                    self.agent
                        .record_application(&self.gpu, &plan, &sample, &baseline, None)?;
                    trial_rejected = int_of(miner.get("rejected").unwrap_or(&json!(0)));
                    trial_active = true;
                    self.writes_plus();
                    self.set(vec![("mode", json!("validating")), ("proposal", plan)]);
                } else {
                    self.set(vec![("mode", json!("hold")), ("proposal", plan)]);
                    self.agent
                        .start_benchmark(&self.gpu, &duration, &objective, None)?;
                }
            } else if result.get("state") == Some(&json!("complete")) {
                self.set(vec![("mode", json!("insufficient_data"))]);
                self.agent
                    .start_benchmark(&self.gpu, &duration, &objective, None)?;
            }
            self.write_status()?;
            cycles += 1;
            if self.args.samples > 0 && cycles >= self.args.samples {
                break;
            }
            (self.hooks.sleep)(2.0);
        }
        Ok(())
    }

    fn observe(&mut self, sample: &Value, rate: &Value, miner: &Value) -> AgentResult<Value> {
        let get = |key: &str, default: Value| miner.get(key).cloned().unwrap_or(default);
        self.agent.observe(
            &self.gpu,
            sample,
            rate,
            &get("accepted", json!(0)),
            &get("rejected", json!(0)),
            None,
            &get("hashrate_at", Value::Null),
            &get("hw_errors", json!(0)),
        )
    }

    /// `gpu-agent-request.json`: `Ok(Some(..))` para um pedido novo e válido, `Err(None)` para
    /// um pedido inválido (ignorado com aviso) e `Err(Some(erro))` para falha de leitura.
    #[allow(clippy::type_complexity)]
    fn benchmark_request(
        &self,
        now: f64,
        last_request: f64,
        selected_profile: &str,
    ) -> Result<Option<(String, String, Value, f64)>, Option<AgentError>> {
        let path = self.state_dir.join("gpu-agent-request.json");
        let raw = match std::fs::read(&path) {
            Ok(raw) => raw,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(error) => return Err(Some(os_error(error))),
        };
        let text_raw = String::from_utf8(raw).map_err(|_| None)?;
        let request: Value =
            serde_json::from_str(text_raw.strip_prefix('\u{feff}').unwrap_or(&text_raw))
                .map_err(|_| None)?;
        let Some(request) = request.as_object() else {
            return Err(None);
        };
        let stamp = numeric(request.get("at").unwrap_or(&Value::Null)).unwrap_or(0.0);
        let matches = request.get("action") == Some(&json!("benchmark"))
            && request.get("gpu_uuid") == Some(&json!(self.uuid()))
            && stamp > last_request
            && (0.0..=60.0).contains(&(now - stamp));
        if !matches {
            return Ok(None);
        }
        let requested_profile = request
            .get("profile")
            .cloned()
            .unwrap_or_else(|| json!(selected_profile));
        crate::reference::profile(&requested_profile).map_err(|_| None)?;
        let objective = request
            .get("objective")
            .cloned()
            .unwrap_or_else(|| json!("performance"));
        let duration_value = request
            .get("duration_seconds")
            .cloned()
            .unwrap_or(Value::Null);
        let duration = numeric(&duration_value).unwrap_or(300.0);
        let objective = objective
            .as_str()
            .filter(|item| matches!(*item, "performance" | "efficiency"));
        let (Some(objective), true) = (objective, (300.0..=3600.0).contains(&duration)) else {
            return Err(None);
        };
        let duration_json = if numeric(&duration_value).is_some() {
            duration_value
        } else {
            json!(300)
        };
        Ok(Some((
            text(&requested_profile),
            objective.to_string(),
            duration_json,
            stamp,
        )))
    }

    /// O `finally` do Python: devolve clock, potência original e memória, solta a posse.
    fn restore(&mut self, original_power: Option<f64>, owns: bool) {
        if owns {
            let uuid = self.uuid();
            let result = (|| -> AgentResult<()> {
                self.actuator.assert_owner(&uuid)?;
                smi(&mut *self.hooks.smi, &["-i", &uuid, "-rgc"])?;
                let minimum = numeric(self.gpu.get("power_min_w").unwrap_or(&Value::Null));
                let maximum = numeric(self.gpu.get("power_max_w").unwrap_or(&Value::Null));
                if let (Some(minimum), Some(maximum), Some(original)) =
                    (minimum, maximum, original_power)
                    && minimum <= original
                    && original <= maximum
                {
                    self.actuator.assert_owner(&uuid)?;
                    smi(
                        &mut *self.hooks.smi,
                        &["-i", &uuid, "-pl", &text(&json!(original))],
                    )?;
                }
                if self.memory_explorer.is_some() {
                    self.actuator.assert_owner(&uuid)?;
                    smi(&mut *self.hooks.smi, &["-i", &uuid, "-rmc"])?;
                }
                Ok(())
            })();
            if let Err(error) = result {
                self.status
                    .insert("restore_error".into(), json!(error.message));
            }
            self.actuator.close();
        }
        let now = self.now();
        self.set(vec![("enabled", json!(false)), ("sampled_at", json!(now))]);
        let _ = self.write_status();
    }
}

/// `control(args, state_dir, monitor_state_dir)`: 0 (minerador encerrou, parada pedida ou
/// amostras esgotadas) ou 1 (o gerenciador deve parar o minerador). Erros antes da posse da
/// GPU voltam como `Err`, como as exceções do Python.
pub fn control(
    args: &ControlArgs,
    state_dir: &Path,
    monitor_state_dir: &Path,
    hooks: &mut Hooks,
) -> AgentResult<i32> {
    if !(hooks.is_admin)() {
        return Err(runtime(
            "O supervisor de GPU precisa ser iniciado como administrador",
        ));
    }
    if !(300..=3600).contains(&args.duration) {
        return Err(AgentError::value(
            "Controle exige baseline de 300 a 3600 segundos",
        ));
    }
    crate::reference::profile(&json!(args.profile)).map_err(AgentError::value)?;
    let devices = (hooks.detect)().map_err(runtime)?;
    let gpu = select_device(
        &devices,
        args.uuid.as_deref(),
        args.index,
        args.source.miner_index,
    )?;
    let uuid = text(&gpu["uuid"]);
    if external_supervisor_active(
        &[state_dir, monitor_state_dir],
        Some(&uuid),
        gpu["index"].as_i64(),
        hooks.pid,
    ) {
        return Err(runtime(
            "Já existe supervisor ativo: o agente fica somente leitura",
        ));
    }
    if !(hooks.alive)(args.miner_pid) {
        return Err(runtime("Processo do minerador já não está ativo"));
    }
    let identity = (hooks.identity)(args.miner_pid);
    let is_srbminer = identity.as_ref().is_some_and(|(_, executable)| {
        Path::new(executable)
            .file_name()
            .is_some_and(|name| name.to_string_lossy().to_lowercase() == "srbminer-multi.exe")
    });
    if !is_srbminer {
        return Err(runtime(
            "Não foi possível confirmar identidade e criação do processo SRBMiner",
        ));
    }
    let now = (hooks.now)();
    let baseline = read_sample(&gpu, &mut *hooks.smi, Some(now))?;
    let original_power = numeric(&baseline["power_limit_w"]);
    let token = (hooks.ids)();
    let actuator = NvidiaActuator::new(state_dir, true, token, hooks.pid)?;
    let advisor: Option<Box<dyn Advisor>> = hooks
        .advisor
        .clone()
        .map(|advisor| Box::new(SharedAdvisor(advisor)) as Box<dyn Advisor>);
    let mut agent = GpuAgent::open(state_dir, advisor)?;
    agent.hold_heartbeat = hooks.hold_heartbeat;
    let ids = Rc::new(RefCell::new(std::mem::replace(
        &mut hooks.ids,
        Box::new(crate::gpu_agent::uuid4_hex),
    )));
    let source = ids.clone();
    agent.id_source = Box::new(move || (source.borrow_mut())());
    let clock = hooks.now.clone();
    agent.clock = Box::new(move || clock());
    let mut status = Map::new();
    for (key, value) in [
        ("enabled", json!(false)),
        ("controller_pid", json!(hooks.pid)),
        ("miner_pid", json!(args.miner_pid)),
        ("gpu_uuid", json!(uuid)),
        ("gpu_name", gpu["name"].clone()),
        ("mode", json!("baseline")),
        ("sampled_at", json!(now)),
        ("error", Value::Null),
        ("writes", json!(0)),
        ("software", hooks.software.clone()),
        ("profile", json!(args.profile)),
    ] {
        status.insert(key.into(), value);
    }
    let mut state = Loop {
        args,
        hooks,
        gpu,
        state_dir: state_dir.to_path_buf(),
        monitor_state_dir: monitor_state_dir.to_path_buf(),
        status,
        actuator,
        agent,
        memory_explorer: None,
        memory_path: state_dir.join("thermal-target.json"),
    };
    state
        .status
        .insert("device_count".into(), json!(devices.len()));
    let outcome = state.run();
    state.status.remove("device_count");
    let owns = state.actuator.owns;
    let code = match outcome {
        Ok(()) => 0,
        // Interrupção não marca falha no status: só o `finally` (restaurar e soltar a posse).
        Err(error) if error.kind == "KeyboardInterrupt" => 1,
        Err(error) => {
            state.set(vec![
                ("mode", json!("failed")),
                ("error", json!(error.message)),
                (
                    "reason",
                    json!("Controle encerrado; iniciador deve parar seu minerador"),
                ),
            ]);
            1
        }
    };
    state.restore(original_power, owns);
    let ids = Rc::try_unwrap(ids).ok().map(RefCell::into_inner);
    if let Some(ids) = ids {
        state.hooks.ids = ids;
    }
    Ok(code)
}
