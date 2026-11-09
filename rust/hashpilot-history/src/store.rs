//! `HistoryStore`: fila e thread gravadora, como no `history.py`.
//!
//! Quem chama só enfileira (nunca espera o disco); uma thread `history-writer` grava
//! cada trabalho numa transação curta. Banco ocupado repete o MESMO trabalho, sem
//! perder a fila; amostra malformada vai para `history-rejected.jsonl` e o gravador
//! segue; esquema futuro ou desconhecido fecha o histórico. A posição de cada log
//! arquivado só é persistida depois do commit das linhas correspondentes.

use std::collections::{HashMap, HashSet};
use std::fs::{self, File, OpenOptions};
use std::hash::{BuildHasher, RandomState};
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{self, Receiver, Sender};
use std::sync::{Arc, Condvar, Mutex, MutexGuard, PoisonError};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use hashpilot_core::miner_log::{STAMP, StampZone, parse_stamp};
use hashpilot_core::numbers::py_float;
use hashpilot_core::text::{py_str, splitlines};
use rusqlite::{Connection, OpenFlags};
use serde_json::{Map, Value, json};

use crate::keys::{log_key, thermal_key};
use crate::pyjson::{encode, index, key_error, repr_str, rstrip, truthy, type_name};
use crate::read;
use crate::schema::{self, SchemaFailure};
use crate::write::{Clock, Failure, Writer, sql_failure};

/// Campos brutos do minerador arquivados à parte, nunca copiados a cada amostra.
pub const RAW_MINER_FIELDS: [&str; 2] = ["recent_logs", "rate_points"];
const RIG_SAMPLE_ERROR: &str = "Amostra do rig precisa conter até seis UUIDs únicos";
const OFFSETS_SAVE_INTERVAL: Duration = Duration::from_secs(10);
const RETRY_DELAY: Duration = Duration::from_millis(250);
const CLOSE_TIMEOUT: Duration = Duration::from_secs(4);

/// Dependências injetáveis: geração de identificadores, relógio e fuso do log.
pub struct StoreOptions {
    /// `uuid.uuid4().hex` do Python: sessão e eventos sem chave.
    pub ids: Box<dyn FnMut() -> String + Send>,
    pub clock: Clock,
    /// Fuso do carimbo do log do SRBMiner (hora local por padrão).
    pub stamp_zone: StampZone,
}

impl Default for StoreOptions {
    fn default() -> Self {
        StoreOptions {
            ids: Box::new(random_hex_id),
            clock: Arc::new(now),
            stamp_zone: StampZone::Local,
        }
    }
}

fn now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|elapsed| elapsed.as_secs_f64())
        .unwrap_or(0.0)
}

/// 32 dígitos hexadecimais no formato de um UUID versão 4 (como `uuid4().hex`).
fn random_hex_id() -> String {
    static COUNTER: AtomicU64 = AtomicU64::new(0);
    let sequence = COUNTER.fetch_add(1, Ordering::Relaxed);
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|elapsed| elapsed.as_nanos())
        .unwrap_or(0);
    let high = RandomState::new().hash_one((nanos, sequence, 1_u8));
    let low = RandomState::new().hash_one((nanos, sequence, 2_u8));
    let mut bytes = [0_u8; 16];
    bytes[..8].copy_from_slice(&high.to_be_bytes());
    bytes[8..].copy_from_slice(&low.to_be_bytes());
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

/// Texto do caminho usado nas chaves e nos offsets (`str(path)` do Python).
///
/// Para deduplicar contra um arquivo gravado pelo Python, passe o caminho no mesmo
/// formato (no Windows, com `\`); o Rust não normaliza `/`.
fn path_text(path: &Path) -> String {
    path.to_string_lossy().into_owned()
}

/// `<stem>-log-offsets.json` ao lado do banco.
fn offsets_path(database: &Path) -> PathBuf {
    let stem = database
        .file_stem()
        .map(|stem| stem.to_string_lossy().into_owned())
        .unwrap_or_default();
    database.with_file_name(format!("{stem}-log-offsets.json"))
}

/// `int(valor)` do Python para um offset lido do JSON; `None` onde o Python falharia.
fn py_int(value: &Value) -> Option<i128> {
    match value {
        Value::Bool(flag) => Some(i128::from(*flag)),
        Value::Number(number) => {
            if let Some(integer) = number.as_i64() {
                Some(i128::from(integer))
            } else if let Some(integer) = number.as_u64() {
                Some(i128::from(integer))
            } else {
                let real = number.as_f64()?;
                real.is_finite().then(|| real.trunc() as i128)
            }
        }
        Value::String(text) => {
            let trimmed = text.trim();
            let digits = trimmed.strip_prefix(['+', '-']).unwrap_or(trimmed);
            let underscores_ok =
                !digits.starts_with('_') && !digits.ends_with('_') && !digits.contains("__");
            if digits.is_empty() || !underscores_ok {
                return None;
            }
            trimmed.replace('_', "").parse().ok()
        }
        Value::Null | Value::Array(_) | Value::Object(_) => None,
    }
}

/// `HistoryStore.load_offsets`: arquivo ausente ou inválido só faz reprocessar (sem duplicar).
fn load_offsets(path: &Path) -> Map<String, Value> {
    let Ok(text) = fs::read_to_string(path) else {
        return Map::new();
    };
    let Ok(Value::Object(payload)) = serde_json::from_str::<Value>(&text) else {
        return Map::new();
    };
    let mut offsets = Map::new();
    for (name, value) in payload {
        let Some(offset) = py_int(&value) else {
            return Map::new();
        };
        if offset >= 0 {
            let offset = u64::try_from(offset).unwrap_or(u64::MAX);
            offsets.insert(name, json!(offset));
        }
    }
    offsets
}

/// Linhas completas (terminadas em `\n`) de um bloco; a incompleta fica para depois.
fn complete_lines(buffer: &[u8]) -> impl Iterator<Item = &[u8]> {
    buffer
        .split_inclusive(|byte| *byte == b'\n')
        .filter(|line| line.ends_with(b"\n"))
}

fn strip_bom(raw: &[u8]) -> &[u8] {
    raw.strip_prefix(b"\xef\xbb\xbf").unwrap_or(raw)
}

/// `raw.decode("utf-8-sig", errors="replace").rstrip()`.
fn decode_log_line(raw: &[u8]) -> String {
    rstrip(&String::from_utf8_lossy(strip_bom(raw))).to_string()
}

/// Lê de `offset` até o fim; devolve o bloco lido.
fn read_from(path: &Path, offset: u64) -> io::Result<Vec<u8>> {
    let mut file = File::open(path)?;
    file.seek(SeekFrom::Start(offset))?;
    let mut buffer = Vec::new();
    file.read_to_end(&mut buffer)?;
    Ok(buffer)
}

/// Uma linha do `thermal.jsonl` → (instante, objeto), com os erros do Python.
///
/// Texto que não é JSON (ou UTF-8) também vira `thermal_import_error`, mas a
/// mensagem é a do `serde_json`, não a do `json` do Python.
fn parse_thermal(raw: &[u8]) -> Result<(f64, Value), String> {
    let text = std::str::from_utf8(strip_bom(raw)).map_err(|error| {
        let position = error.valid_up_to();
        let byte = strip_bom(raw).get(position).copied().unwrap_or(0);
        format!("'utf-8' codec can't decode byte 0x{byte:02x} in position {position}: invalid start byte")
    })?;
    let payload: Value = serde_json::from_str(text).map_err(|error| error.to_string())?;
    let raw_stamp = index(&payload, "sampled_at")?;
    let stamp = py_float(raw_stamp).ok_or_else(|| match raw_stamp {
        Value::String(text) => format!("could not convert string to float: {}", repr_str(text)),
        other => format!(
            "float() argument must be a string or a real number, not '{}'",
            type_name(other)
        ),
    })?;
    if !stamp.is_finite() || stamp < 0.0 {
        return Err("Timestamp térmico inválido".into());
    }
    Ok((stamp, payload))
}

/// Chave de conjunto para o UUID (`{item['uuid'] for item in devices}`).
fn hash_key(value: &Value) -> Result<String, String> {
    match value {
        Value::String(text) => Ok(format!("s:{text}")),
        Value::Null => Ok("None".into()),
        Value::Bool(_) | Value::Number(_) => Ok(format!(
            "n:{}",
            crate::pyjson::number_key(value).unwrap_or(f64::NAN)
        )),
        other => Err(format!("unhashable type: '{}'", type_name(other))),
    }
}

/// Dicionário do minerador sem os campos brutos.
fn without_raw(miner: &Map<String, Value>) -> Map<String, Value> {
    miner
        .iter()
        .filter(|(key, _)| !RAW_MINER_FIELDS.contains(&key.as_str()))
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect()
}

enum Job {
    Write { kind: &'static str, payload: Value },
    Stop,
}

struct State {
    sender: Option<Sender<Job>>,
    unfinished: usize,
    closed: bool,
    ready: bool,
    worker_done: bool,
    last_error: Option<String>,
    last_commit: Option<f64>,
}

struct Shared {
    state: Mutex<State>,
    changed: Condvar,
}

impl Shared {
    fn lock(&self) -> MutexGuard<'_, State> {
        self.state.lock().unwrap_or_else(PoisonError::into_inner)
    }
}

/// Arquivo permanente local; cada commit sobrevive ao encerramento do processo.
pub struct HistoryStore {
    path: PathBuf,
    wallet: String,
    session: String,
    shared: Arc<Shared>,
    worker: Option<JoinHandle<()>>,
    log_offsets: Map<String, Value>,
    ids: Box<dyn FnMut() -> String + Send>,
    clock: Clock,
    stamp_zone: StampZone,
}

impl HistoryStore {
    pub fn open(path: impl Into<PathBuf>, wallet: &str) -> io::Result<Self> {
        Self::with_options(path, wallet, StoreOptions::default())
    }

    pub fn with_options(
        path: impl Into<PathBuf>,
        wallet: &str,
        mut options: StoreOptions,
    ) -> io::Result<Self> {
        let path = path.into();
        if let Some(parent) = path
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
        {
            fs::create_dir_all(parent)?;
        }
        let session = (options.ids)();
        let offsets_file = offsets_path(&path);
        let log_offsets = load_offsets(&offsets_file);
        let (sender, receiver) = mpsc::channel();
        let shared = Arc::new(Shared {
            state: Mutex::new(State {
                sender: Some(sender),
                unfinished: 0,
                closed: false,
                ready: false,
                worker_done: false,
                last_error: None,
                last_commit: None,
            }),
            changed: Condvar::new(),
        });
        let worker = Worker {
            path: path.clone(),
            offsets_path: offsets_file,
            committed_offsets: log_offsets.clone(),
            offsets_saved_at: None,
            writer: Writer::new(wallet.to_string(), session.clone(), options.clock.clone()),
            shared: shared.clone(),
        };
        let handle = thread::Builder::new()
            .name("history-writer".into())
            .spawn(move || worker.run(receiver))?;
        let store = HistoryStore {
            path,
            wallet: wallet.to_string(),
            session: session.clone(),
            shared,
            worker: Some(handle),
            log_offsets,
            ids: options.ids,
            clock: options.clock,
            stamp_zone: options.stamp_zone,
        };
        store.submit(
            "event",
            json!({"timestamp": (store.clock)(), "key": format!("session:{session}"),
                   "kind": "session_started", "data": {"session": session}}),
        );
        Ok(store)
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn wallet(&self) -> &str {
        &self.wallet
    }

    pub fn session(&self) -> &str {
        &self.session
    }

    /// Último erro do gravador (`None` depois de um commit bem-sucedido).
    pub fn last_error(&self) -> Option<String> {
        self.shared.lock().last_error.clone()
    }

    pub fn last_commit(&self) -> Option<f64> {
        self.shared.lock().last_commit
    }

    /// O esquema foi preparado (ou recusado) pelo gravador.
    pub fn is_ready(&self) -> bool {
        self.shared.lock().ready
    }

    pub fn is_closed(&self) -> bool {
        self.shared.lock().closed
    }

    /// Enfileira; `false` se o histórico já fechou (nada é gravado).
    fn submit(&self, kind: &'static str, payload: Value) -> bool {
        let mut state = self.shared.lock();
        if state.closed {
            return false;
        }
        let Some(sender) = &state.sender else {
            return false;
        };
        if sender.send(Job::Write { kind, payload }).is_err() {
            return false;
        }
        state.unfinished += 1;
        true
    }

    /// `HistoryStore.telemetry`: amostra do painel; as linhas recentes do log viram eventos.
    ///
    /// `Err` é a exceção que o Python levantaria para quem chamou. A amostra é
    /// enfileirada antes das linhas, como no Python: sem `collected_at`, ela é
    /// rejeitada pelo gravador e o erro também volta para quem chamou.
    pub fn telemetry(&mut self, data: &Value, source: &str) -> Result<(), String> {
        let Value::Object(map) = data else {
            return Err(format!("'{}' object is not iterable", type_name(data)));
        };
        let empty = Map::new();
        let miner = match map.get("miner") {
            None => &empty,
            Some(Value::Object(miner)) => miner,
            Some(other) => {
                return Err(format!(
                    "'{}' object has no attribute 'items'",
                    type_name(other)
                ));
            }
        };
        let mut clean = map.clone();
        clean.insert("miner".into(), Value::Object(without_raw(miner)));
        self.submit("telemetry", json!({"data": clean, "source": source}));
        let recent = match miner.get("recent_logs") {
            None => "",
            Some(Value::String(text)) => text.as_str(),
            Some(other) => {
                return Err(format!(
                    "'{}' object has no attribute 'splitlines'",
                    type_name(other)
                ));
            }
        };
        let lines = splitlines(recent);
        if lines.is_empty() {
            return Ok(());
        }
        let collected_at = map
            .get("collected_at")
            .ok_or_else(|| key_error("collected_at"))?;
        let log_path = map.get("log_path").cloned().unwrap_or(Value::Null);
        let path = if truthy(&log_path) {
            py_str(&log_path)
        } else {
            String::new()
        };
        let wallet = map.get("wallet").cloned().unwrap_or(Value::Null);
        let events: Vec<Value> = lines
            .into_iter()
            .map(|line| {
                json!({"timestamp": collected_at, "key": log_key(&path, line), "kind": "miner_log",
                       "wallet": wallet, "data": {"line": line, "path": log_path}})
            })
            .collect();
        self.submit("events", json!({"events": events}));
        Ok(())
    }

    /// `HistoryStore.finance`.
    pub fn finance(&mut self, state: &Value) -> bool {
        self.submit("finance", state.clone())
    }

    /// `HistoryStore.rig_telemetry`: até seis GPUs por UUID, sem as linhas brutas do log.
    pub fn rig_telemetry(&mut self, rig: &Value) -> Result<(), String> {
        let Value::Object(map) = rig else {
            return Err(format!(
                "'{}' object has no attribute 'get'",
                type_name(rig)
            ));
        };
        let devices = match map.get("devices") {
            None => Vec::new(),
            Some(Value::Array(devices)) => devices.clone(),
            Some(other) => {
                return Err(format!(
                    "object of type '{}' has no len()",
                    type_name(other)
                ));
            }
        };
        if devices.len() > 6 {
            return Err(RIG_SAMPLE_ERROR.into());
        }
        let mut unique = HashSet::new();
        for device in &devices {
            unique.insert(hash_key(index(device, "uuid")?)?);
        }
        if unique.len() != devices.len() {
            return Err(RIG_SAMPLE_ERROR.into());
        }
        let mut compact_devices = Vec::with_capacity(devices.len());
        for device in devices {
            let Value::Object(mut device) = device else {
                return Err("'devices' precisa conter objetos".into());
            };
            let miner = match device.get("miner") {
                Some(Value::Object(miner)) => without_raw(miner),
                Some(value) if truthy(value) => {
                    return Err(format!(
                        "'{}' object has no attribute 'items'",
                        type_name(value)
                    ));
                }
                _ => Map::new(),
            };
            device.insert("miner".into(), Value::Object(miner));
            compact_devices.push(Value::Object(device));
        }
        let mut compact = map.clone();
        compact.insert("devices".into(), Value::Array(compact_devices));
        self.submit("rig_telemetry", Value::Object(compact));
        Ok(())
    }

    /// `HistoryStore.preferences`.
    pub fn preferences(&mut self, data: &Value) -> bool {
        self.submit("preferences", data.clone())
    }

    /// `HistoryStore.event`: chave vazia ou ausente recebe um identificador novo.
    pub fn event(
        &mut self,
        timestamp: Value,
        kind: &str,
        data: Value,
        key: Option<&str>,
        wallet: Option<&str>,
    ) -> bool {
        let key = match key {
            Some(key) if !key.is_empty() => key.to_string(),
            _ => (self.ids)(),
        };
        self.submit(
            "event",
            json!({"timestamp": timestamp, "key": key, "kind": kind, "data": data, "wallet": wallet}),
        )
    }

    /// `HistoryStore.archive_log`: segue o arquivo inteiro, inclusive rajadas maiores que
    /// o tail do painel. As linhas novas vão num único lote; a posição só é persistida
    /// depois do commit. Uma linha incompleta fica para a próxima leitura.
    pub fn archive_log(
        &mut self,
        path: &Path,
        gpu_uuid: Option<&str>,
        wallet: Option<&str>,
    ) -> io::Result<()> {
        let size = fs::metadata(path)?.len();
        let name = path_text(path);
        let mut offset = self.offset(&name);
        if offset > size {
            offset = 0;
        }
        if offset == size {
            return Ok(());
        }
        let buffer = read_from(path, offset)?;
        let mut position = offset;
        let mut stamps: HashMap<String, Option<f64>> = HashMap::new();
        let mut events = Vec::new();
        for raw in complete_lines(&buffer) {
            position += raw.len() as u64;
            let line = decode_log_line(raw);
            let stamp = STAMP.captures(&line).and_then(|captures| {
                let text = captures[1].to_string();
                *stamps
                    .entry(text.clone())
                    .or_insert_with(|| parse_stamp(&text, self.stamp_zone))
            });
            let timestamp = stamp.unwrap_or_else(|| (self.clock)());
            events.push(json!({"timestamp": timestamp, "key": log_key(&name, &line), "kind": "miner_log",
                               "data": {"line": line, "path": name, "gpu_uuid": gpu_uuid}, "wallet": wallet}));
        }
        self.log_offsets.insert(name.clone(), json!(position));
        if !events.is_empty() {
            self.submit(
                "events",
                json!({"events": events, "offset": [name, position]}),
            );
        }
        Ok(())
    }

    /// `HistoryStore.archive_thermal`: ajustes do supervisor, inclusive com o painel fechado.
    ///
    /// Como no Python, a posição deste arquivo fica só em memória: depois de reabrir,
    /// o arquivo é relido e a chave SHA-256 impede duplicatas.
    pub fn archive_thermal(&mut self, path: &Path) -> io::Result<()> {
        if !path.is_file() {
            return Ok(());
        }
        let name = path_text(path);
        let mut offset = self.offset(&name);
        if offset > fs::metadata(path)?.len() {
            offset = 0;
        }
        let buffer = read_from(path, offset)?;
        let mut position = offset;
        for raw in complete_lines(&buffer) {
            position += raw.len() as u64;
            let key = thermal_key(&name, raw);
            match parse_thermal(raw) {
                Ok((stamp, payload)) => {
                    self.event(json!(stamp), "thermal_control", payload, Some(&key), None);
                }
                Err(error) => {
                    let stamp = json!((self.clock)());
                    self.event(
                        stamp,
                        "thermal_import_error",
                        json!({"path": name, "error": error}),
                        Some(&key),
                        None,
                    );
                }
            }
        }
        self.log_offsets.insert(name, json!(position));
        Ok(())
    }

    fn offset(&self, name: &str) -> u64 {
        self.log_offsets
            .get(name)
            .and_then(Value::as_u64)
            .unwrap_or(0)
    }

    /// Espera a fila esvaziar; `false` se o prazo acabar antes.
    pub fn flush(&self, timeout: Duration) -> bool {
        let state = self.shared.lock();
        let (state, _) = self
            .shared
            .changed
            .wait_timeout_while(state, timeout, |state| state.unfinished > 0)
            .unwrap_or_else(PoisonError::into_inner);
        state.unfinished == 0
    }

    /// Registra `session_closed`, encerra o gravador e espera até 4 s por ele.
    pub fn close(&mut self) {
        {
            let mut state = self.shared.lock();
            if !state.closed {
                let key = (self.ids)();
                let payload = json!({"timestamp": (self.clock)(), "key": key, "kind": "session_closed",
                                     "data": {"session": self.session}, "wallet": null});
                if let Some(sender) = state.sender.take() {
                    if sender
                        .send(Job::Write {
                            kind: "event",
                            payload,
                        })
                        .is_ok()
                    {
                        state.unfinished += 1;
                    }
                    if sender.send(Job::Stop).is_ok() {
                        state.unfinished += 1;
                    }
                }
                state.closed = true;
            }
        }
        let state = self.shared.lock();
        let (state, _) = self
            .shared
            .changed
            .wait_timeout_while(state, CLOSE_TIMEOUT, |state| !state.worker_done)
            .unwrap_or_else(PoisonError::into_inner);
        let done = state.worker_done;
        drop(state);
        if done {
            if let Some(handle) = self.worker.take() {
                let _ = handle.join();
            }
        }
    }

    fn reader(&self) -> rusqlite::Result<Option<Connection>> {
        if self.path.exists() {
            read::open_reader(&self.path, false).map(Some)
        } else {
            Ok(None)
        }
    }

    /// `HistoryStore.restore(since)`. `Err` é a exceção que o Python deixaria subir.
    pub fn restore(&self, since: f64) -> Result<Value, String> {
        match self.reader() {
            Ok(connection) => read::restore(connection.as_ref(), &self.wallet, since),
            Err(error) => {
                Ok(json!({"points": {}, "preferences": {}, "count": 0, "error": error.to_string()}))
            }
        }
    }

    /// `HistoryStore.restore_gpu(gpu_uuid, since, wallet)`.
    pub fn restore_gpu(&self, gpu_uuid: &str, since: f64, wallet: &str) -> Result<Value, String> {
        match self.reader() {
            Ok(connection) => read::restore_gpu(connection.as_ref(), gpu_uuid, since, wallet),
            Err(error) => {
                Ok(json!({"points": {}, "count": 0, "identity": {}, "error": error.to_string()}))
            }
        }
    }

    /// `HistoryStore.gpu_stats(gpu_uuid)`.
    pub fn gpu_stats(&self, gpu_uuid: Option<&str>) -> Value {
        match self.reader() {
            Ok(connection) => read::gpu_stats(connection.as_ref(), gpu_uuid),
            Err(_) => json!({}),
        }
    }

    /// `HistoryStore.finance_state(since, wallet)`; sem carteira, a do histórico.
    pub fn finance_state(&self, since: f64, wallet: Option<&str>) -> Result<Value, String> {
        let wallet = wallet.unwrap_or(&self.wallet);
        match self.reader() {
            Ok(connection) => read::finance_state(connection.as_ref(), wallet, since),
            Err(_) => Ok(json!({"wallet": wallet, "snapshots": [], "sources": {}})),
        }
    }

    /// `HistoryStore.stats()`.
    pub fn stats(&self) -> Result<Value, String> {
        let connection = self.reader().map_err(|error| error.to_string())?;
        read::stats(connection.as_ref(), &self.wallet)
    }
}

impl Drop for HistoryStore {
    fn drop(&mut self) {
        self.close();
    }
}

struct Worker {
    path: PathBuf,
    offsets_path: PathBuf,
    committed_offsets: Map<String, Value>,
    offsets_saved_at: Option<Instant>,
    writer: Writer,
    shared: Arc<Shared>,
}

impl Worker {
    fn run(mut self, receiver: Receiver<Job>) {
        let mut connection: Option<Connection> = None;
        while let Ok(job) = receiver.recv() {
            let Job::Write { kind, payload } = job else {
                self.task_done();
                break;
            };
            let mut stop = false;
            loop {
                match self.attempt(&mut connection, kind, &payload) {
                    Ok(()) => {
                        {
                            let mut state = self.shared.lock();
                            state.last_commit = Some((self.writer.clock)());
                            state.last_error = None;
                        }
                        if kind == "events" {
                            self.commit_offset(&payload);
                        }
                        break;
                    }
                    Err(Failure::Schema(message)) => {
                        let mut state = self.shared.lock();
                        state.last_error = Some(format!("Histórico indisponível: {message}"));
                        state.ready = true;
                        state.closed = true;
                        drain(&receiver, &mut state);
                        stop = true;
                        break;
                    }
                    Err(Failure::Rejected(message)) => {
                        let mut error = format!("Amostra rejeitada: {message}");
                        if self.record_rejected(kind, &message).is_err() {
                            error.push_str("; não foi possível gravar o diagnóstico");
                        }
                        self.shared.lock().last_error = Some(error);
                        break;
                    }
                    Err(Failure::Transient(message)) => {
                        self.shared.lock().last_error = Some(message);
                        connection = None;
                        thread::sleep(RETRY_DELAY);
                        let mut state = self.shared.lock();
                        if state.closed {
                            state.ready = true;
                            drain(&receiver, &mut state);
                            stop = true;
                            break;
                        }
                    }
                }
            }
            self.task_done();
            if stop {
                break;
            }
        }
        self.save_offsets(true);
        drop(connection);
        let mut state = self.shared.lock();
        state.worker_done = true;
        self.shared.changed.notify_all();
    }

    fn attempt(
        &mut self,
        connection: &mut Option<Connection>,
        kind: &str,
        payload: &Value,
    ) -> Result<(), Failure> {
        if connection.is_none() {
            let opened =
                open_writer(&self.path).map_err(|error| Failure::Transient(error.to_string()))?;
            schema::ensure(&opened).map_err(|failure| match failure {
                SchemaFailure::Unsupported(message) => Failure::Schema(message),
                SchemaFailure::Sqlite(error) => Failure::Transient(error.to_string()),
            })?;
            *connection = Some(opened);
            self.shared.lock().ready = true;
        }
        let Some(open) = connection.as_mut() else {
            return Err(Failure::Transient("conexão indisponível".into()));
        };
        let transaction = open.transaction().map_err(sql_failure)?;
        self.writer.write(&transaction, kind, payload)?;
        transaction.commit().map_err(sql_failure)
    }

    fn task_done(&self) {
        let mut state = self.shared.lock();
        state.unfinished = state.unfinished.saturating_sub(1);
        self.shared.changed.notify_all();
    }

    fn commit_offset(&mut self, payload: &Value) {
        let Some(Value::Array(offset)) = payload.get("offset") else {
            return;
        };
        let (Some(Value::String(path)), Some(position)) = (offset.first(), offset.get(1)) else {
            return;
        };
        self.committed_offsets
            .insert(path.clone(), position.clone());
        self.save_offsets(false);
    }

    /// Grava as posições confirmadas (no máximo a cada 10 s, exceto ao fechar).
    fn save_offsets(&mut self, force: bool) {
        if !force
            && self
                .offsets_saved_at
                .is_some_and(|at| at.elapsed() < OFFSETS_SAVE_INTERVAL)
        {
            return;
        }
        let temporary = self.offsets_path.with_file_name(format!(
            "{}.tmp",
            self.offsets_path
                .file_name()
                .map(|name| name.to_string_lossy().into_owned())
                .unwrap_or_default()
        ));
        let text = encode(&Value::Object(self.committed_offsets.clone()));
        if fs::write(&temporary, text)
            .and_then(|()| fs::rename(&temporary, &self.offsets_path))
            .is_ok()
        {
            self.offsets_saved_at = Some(Instant::now());
        }
    }

    fn record_rejected(&self, kind: &str, error: &str) -> io::Result<()> {
        let line = encode(&json!({"at": (self.writer.clock)(), "kind": kind, "error": error}));
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(self.path.with_file_name("history-rejected.jsonl"))?;
        file.write_all(format!("{line}\n").as_bytes())
    }
}

/// Descarta os trabalhos na fila (histórico fechado).
fn drain(receiver: &Receiver<Job>, state: &mut State) {
    while receiver.try_recv().is_ok() {
        state.unfinished = state.unfinished.saturating_sub(1);
    }
}

/// Conexão do gravador: cria o arquivo; WAL e `synchronous=NORMAL` (commits sobrevivem
/// a um processo morto; só uma queda de energia pode perder os últimos).
fn open_writer(path: &Path) -> rusqlite::Result<Connection> {
    let connection = Connection::open_with_flags(
        path,
        OpenFlags::SQLITE_OPEN_READ_WRITE
            | OpenFlags::SQLITE_OPEN_CREATE
            | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )?;
    connection.busy_timeout(Duration::from_millis(200))?;
    connection.pragma_update(None, "synchronous", "NORMAL")?;
    Ok(connection)
}
