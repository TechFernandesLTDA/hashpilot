//! `finance.FinanceCollector` e `finance.PortfolioFinanceCollector`: ciclo de 60 s,
//! cache de trabalho `finance-cache.json` e arquivo permanente no histórico SQLite.
//!
//! Igual ao Python: as seis consultas rodam em paralelo; cada resposta é validada
//! com as mesmas mensagens; o total observado (pago + saldo) vira snapshot no máximo
//! a cada 45 s; o cache guarda só 35 dias e o SQLite nunca é truncado.

use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::{self, JoinHandle};
use std::time::{Duration, Instant};

use hashpilot_core::numbers::parse_py_float;
use hashpilot_core::wallet::valid_wallet_str;
use hashpilot_history::pyjson::{encode_spaced, repr_str, truthy, type_name};
use hashpilot_history::{Clock, HistoryStore};
use serde_json::{Map, Value, json};

use crate::fetch::{Fetcher, shared_market_fetcher, urls_for_wallet};
use crate::model::normalize_finance_state;

pub const CACHE_FILE: &str = "finance-cache.json";
pub const CYCLE: Duration = Duration::from_secs(60);
/// Janela do cache de trabalho; o histórico SQLite guarda tudo.
pub const CACHE_WINDOW_SECONDS: f64 = 35.0 * 86400.0;
/// Intervalo mínimo entre dois snapshots do total observado.
pub const SNAPSHOT_SPACING_SECONDS: f64 = 45.0;
pub const MARKET_TTL: Duration = Duration::from_secs(45);

/// O que o coletor usa do histórico: gravar o estado e ler o arquivo de 35 dias.
pub trait FinanceHistory: Send + Sync {
    fn finance(&self, state: &Value);
    fn finance_state(&self, since: f64, wallet: &str) -> Value;
}

impl FinanceHistory for Mutex<HistoryStore> {
    fn finance(&self, state: &Value) {
        let mut store = self.lock().unwrap_or_else(|poison| poison.into_inner());
        store.finance(state);
    }

    fn finance_state(&self, since: f64, wallet: &str) -> Value {
        // `history.ready.wait(2)`: o primeiro uso espera o esquema ficar pronto.
        let deadline = Instant::now() + Duration::from_secs(2);
        loop {
            let store = self.lock().unwrap_or_else(|poison| poison.into_inner());
            if store.is_ready() || store.is_closed() || Instant::now() >= deadline {
                return store
                    .finance_state(since, Some(wallet))
                    .unwrap_or_else(|_| json!({"wallet": wallet, "snapshots": [], "sources": {}}));
            }
            drop(store);
            thread::sleep(Duration::from_millis(20));
        }
    }
}

/// `float(valor)` do Python, com o texto do erro.
fn py_float_call(value: &Value) -> Result<f64, String> {
    match value {
        Value::Bool(flag) => Ok(f64::from(u8::from(*flag))),
        Value::Number(number) => Ok(number.as_f64().unwrap_or(f64::NAN)),
        Value::String(text) => parse_py_float(text)
            .ok_or_else(|| format!("could not convert string to float: {}", repr_str(text))),
        other => Err(format!(
            "float() argument must be a string or a real number, not '{}'",
            type_name(other)
        )),
    }
}

fn number_of(value: &Value) -> f64 {
    match value {
        Value::Bool(flag) => f64::from(u8::from(*flag)),
        Value::Number(number) => number.as_f64().unwrap_or(f64::NAN),
        _ => f64::NAN,
    }
}

fn fetched_at(source: Option<&Value>) -> f64 {
    source
        .and_then(|source| source.get("fetched_at"))
        .map_or(0.0, number_of)
}

fn object_mut<'a>(value: &'a mut Value, key: &str) -> &'a mut Map<String, Value> {
    if !value.is_object() {
        *value = Value::Object(Map::new());
    }
    let Value::Object(map) = value else {
        unreachable!("acabou de virar objeto")
    };
    let entry = map.entry(key).or_insert_with(|| Value::Object(Map::new()));
    if !entry.is_object() {
        *entry = Value::Object(Map::new());
    }
    match entry {
        Value::Object(inner) => inner,
        _ => unreachable!("acabou de virar objeto"),
    }
}

/// Junta o arquivo dos últimos 35 dias ao estado do cache, como o `work()` e o
/// `_collector()` do Python: o snapshot do cache vence no mesmo instante, e uma
/// fonte arquivada só entra se for mais nova.
pub fn merge_archived(state: &mut Value, archived: &Value) {
    let mut points: Vec<(u64, f64, Value)> = Vec::new();
    let mut add = |point: &Value| {
        let stamp = number_of(point.get("timestamp").unwrap_or(&Value::Null));
        let key = (stamp + 0.0).to_bits();
        match points.iter_mut().find(|item| item.0 == key) {
            Some(item) => item.2 = point.clone(),
            None => points.push((key, stamp, point.clone())),
        }
    };
    for source in [archived, &*state] {
        if let Some(Value::Array(items)) = source.get("snapshots") {
            items.iter().for_each(&mut add);
        }
    }
    points.sort_by(|a, b| a.1.total_cmp(&b.1));
    if let Value::Object(map) = state {
        map.insert(
            "snapshots".into(),
            Value::Array(points.into_iter().map(|item| item.2).collect()),
        );
    }
    if let Some(Value::Object(archived_sources)) = archived.get("sources") {
        let sources = object_mut(state, "sources");
        for (key, value) in archived_sources {
            if fetched_at(Some(value)) > fetched_at(sources.get(key)) {
                sources.insert(key.clone(), value.clone());
            }
        }
    }
}

/// Fila de duas amostras do Python (`queue.Queue(maxsize=2)`): a mais velha sai.
#[derive(Clone, Default)]
pub struct Samples(Arc<Mutex<VecDeque<Value>>>);

impl Samples {
    pub fn publish(&self, state: Value) {
        let mut queue = self.0.lock().unwrap_or_else(|poison| poison.into_inner());
        if queue.len() >= 2 {
            queue.pop_front();
        }
        queue.push_back(state);
    }

    /// `samples.get_nowait()`.
    pub fn take(&self) -> Option<Value> {
        self.0
            .lock()
            .unwrap_or_else(|poison| poison.into_inner())
            .pop_front()
    }
}

#[derive(Default)]
struct StopFlag {
    stopped: Mutex<bool>,
    signal: Condvar,
}

impl StopFlag {
    fn is_set(&self) -> bool {
        *self
            .stopped
            .lock()
            .unwrap_or_else(|poison| poison.into_inner())
    }

    fn set(&self) {
        *self
            .stopped
            .lock()
            .unwrap_or_else(|poison| poison.into_inner()) = true;
        self.signal.notify_all();
    }

    /// `stop.wait(intervalo)`.
    fn wait(&self, interval: Duration) {
        let guard = self
            .stopped
            .lock()
            .unwrap_or_else(|poison| poison.into_inner());
        let _ = self
            .signal
            .wait_timeout_while(guard, interval, |stopped| !*stopped);
    }
}

/// Thread do ciclo de 60 s. Parar é imediato: não espera o fim do intervalo.
pub struct Worker {
    stop: Arc<StopFlag>,
    handle: Option<JoinHandle<()>>,
    pub samples: Samples,
}

impl Worker {
    fn spawn(mut body: impl FnMut(&Samples) + Send + 'static, interval: Duration) -> Self {
        let stop = Arc::new(StopFlag::default());
        let samples = Samples::default();
        let (flag, queue) = (stop.clone(), samples.clone());
        let handle = thread::Builder::new()
            .name("finance".into())
            .spawn(move || {
                while !flag.is_set() {
                    body(&queue);
                    flag.wait(interval);
                }
            })
            .ok();
        Self {
            stop,
            handle,
            samples,
        }
    }

    pub fn stop(&mut self) {
        self.stop.set();
        if let Some(handle) = self.handle.take() {
            let _ = handle.join();
        }
    }
}

impl Drop for Worker {
    fn drop(&mut self) {
        self.stop();
    }
}

/// `finance.FinanceCollector` de uma carteira.
pub struct FinanceCollector {
    wallet: String,
    urls: Vec<(&'static str, String)>,
    fetcher: Fetcher,
    path: Option<PathBuf>,
    state: Value,
    history: Option<Arc<dyn FinanceHistory>>,
    clock: Clock,
}

impl FinanceCollector {
    /// `state_dir` `None` não lê nem grava cache (consulta avulsa da CLI).
    pub fn new(
        state_dir: Option<&Path>,
        history: Option<Arc<dyn FinanceHistory>>,
        wallet: &str,
        fetcher: Fetcher,
        clock: Clock,
    ) -> Self {
        let path = state_dir.map(|directory| directory.join(CACHE_FILE));
        let mut state = json!({"wallet": wallet, "sources": {}, "snapshots": []});
        if let Some(cached) = path.as_deref().and_then(read_cache)
            && cached.get("wallet").and_then(Value::as_str) == Some(wallet)
        {
            state = normalize_finance_state(&cached, clock());
        }
        Self {
            wallet: wallet.to_owned(),
            urls: urls_for_wallet(wallet),
            fetcher,
            path,
            state,
            history,
            clock,
        }
    }

    pub fn wallet(&self) -> &str {
        &self.wallet
    }

    pub fn state(&self) -> &Value {
        &self.state
    }

    pub fn state_mut(&mut self) -> &mut Value {
        &mut self.state
    }

    /// Valida uma resposta como o `sample()` do Python; devolve os dados normalizados.
    fn accept(&self, key: &str, payload: Value) -> Result<Value, String> {
        if key == "rewards" && !payload.is_array() {
            return Err("Histórico de recompensas inválido".into());
        }
        if key != "rewards" && !payload.is_object() {
            return Err("Resposta financeira precisa ser um objeto".into());
        }
        let wrapped = json!({"sources": {key: {"fetched_at": (self.clock)(), "data": payload}}});
        let normalized = normalize_finance_state(&wrapped, (self.clock)());
        let payload = normalized["sources"]
            .get(key)
            .and_then(|source| source.get("data"))
            .cloned()
            .unwrap_or_else(|| json!({}));
        match key {
            "price" => match payload.get("pearl-2").and_then(|asset| asset.get("brl")) {
                Some(Value::Null) => {
                    return Err(
                        "'>' not supported between instances of 'NoneType' and 'int'".into(),
                    );
                }
                Some(price) if number_of(price) > 0.0 => {}
                _ => return Err("Cotação da Pearl (pearl-2) ausente".into()),
            },
            "balance" if payload.get("total").is_none_or(Value::is_null) => {
                return Err("Saldo total ausente na resposta da pool".into());
            }
            "payout_history" if !payload.get("results").is_some_and(Value::is_array) => {
                return Err("Histórico de pagamentos ausente na resposta da pool".into());
            }
            "payouts" if payload.get("paid").is_none_or(Value::is_null) => {
                return Err("Total pago ausente na resposta da pool".into());
            }
            _ => {}
        }
        Ok(payload)
    }

    /// Uma rodada de consultas. `Err` é o que o Python levantaria de dentro do
    /// `sample()` (total pago não numérico vindo do cache, pasta impossível de criar).
    pub fn sample(&mut self) -> Result<Value, String> {
        let fetcher = &self.fetcher;
        let results: Vec<Result<Value, String>> = thread::scope(|scope| {
            let handles: Vec<_> = self
                .urls
                .iter()
                .map(|(_, url)| scope.spawn(move || fetcher(url)))
                .collect();
            handles
                .into_iter()
                .map(|handle| {
                    handle
                        .join()
                        .unwrap_or_else(|_| Err("consulta interrompida".into()))
                })
                .collect()
        });
        let mut errors = Map::new();
        for ((key, _), result) in self.urls.iter().zip(results) {
            match result.and_then(|payload| self.accept(key, payload)) {
                Ok(payload) => {
                    let source = json!({"fetched_at": (self.clock)(), "data": payload});
                    object_mut(&mut self.state, "sources").insert((*key).into(), source);
                }
                Err(error) => {
                    errors.insert((*key).into(), Value::String(error));
                }
            }
        }
        let sources = self.state["sources"].clone();
        // pago + saldo atual (confirmado + pendente). Um saque só desloca saldo para
        // "pago" e não conta de novo como recompensa.
        if !errors.contains_key("balance")
            && !errors.contains_key("payouts")
            && let (Some(balance), Some(payouts)) = (sources.get("balance"), sources.get("payouts"))
        {
            let paid = &payouts["data"]["paid"];
            let total = py_float_call(paid)? + py_float_call(&balance["data"]["total"])?;
            let now = (self.clock)();
            if let Value::Object(map) = &mut self.state
                && let Some(Value::Array(snapshots)) = map.get_mut("snapshots")
            {
                let last = snapshots.last().map(|point| number_of(&point["timestamp"]));
                if last.is_none_or(|last| now - last >= SNAPSHOT_SPACING_SECONDS) {
                    snapshots.push(json!({"timestamp": now, "total_prl": total}));
                }
            }
        }
        if let Value::Object(map) = &mut self.state {
            map.insert("errors".into(), Value::Object(errors));
            map.insert("attempted_at".into(), json!((self.clock)()));
        }
        if let Some(history) = &self.history {
            history.finance(&self.state);
        }
        // Só o cache de trabalho é podado; o arquivo SQLite não é truncado.
        let cutoff = (self.clock)() - CACHE_WINDOW_SECONDS;
        if let Some(Value::Array(snapshots)) = self.state.get_mut("snapshots") {
            snapshots.retain(|point| number_of(&point["timestamp"]) >= cutoff);
        }
        if let Some(path) = self.path.clone() {
            if let Some(parent) = path.parent() {
                fs::create_dir_all(parent).map_err(|error| error.to_string())?;
            }
            if let Err(error) = write_cache(&path, &self.state) {
                object_mut(&mut self.state, "errors").insert("cache".into(), json!(error));
            }
        }
        Ok(self.state.clone())
    }

    /// Início do `work()`: migra o estado para o histórico e funde o arquivo de 35
    /// dias. Devolve o estado a publicar antes da primeira consulta, se houver fontes.
    pub fn prepare(&mut self) -> Option<Value> {
        if let Some(history) = self.history.clone() {
            history.finance(&self.state); // migração idempotente do JSON legado
            let archived =
                history.finance_state((self.clock)() - CACHE_WINDOW_SECONDS, &self.wallet);
            merge_archived(&mut self.state, &archived);
        }
        truthy(&self.state["sources"]).then(|| self.state.clone())
    }

    /// `work()` numa thread própria: publica o cache e consulta a cada `interval`.
    ///
    /// Diferença consciente: no Python, um erro dentro do `sample()` encerra a thread
    /// em silêncio; aqui a rodada é descartada e o ciclo continua.
    pub fn spawn(mut self, interval: Duration) -> Worker {
        let mut first = true;
        Worker::spawn(
            move |samples| {
                if std::mem::take(&mut first)
                    && let Some(state) = self.prepare()
                {
                    samples.publish(state);
                }
                if let Ok(state) = self.sample() {
                    samples.publish(state);
                }
            },
            interval,
        )
    }
}

/// Lê o `finance-cache.json` (com ou sem BOM); erro de leitura ou JSON é ignorado.
fn read_cache(path: &Path) -> Option<Value> {
    let bytes = fs::read(path).ok()?;
    let bytes = bytes.strip_prefix(b"\xef\xbb\xbf").unwrap_or(&bytes);
    let value: Value = serde_json::from_slice(bytes).ok()?;
    value.is_object().then_some(value)
}

/// Escrita atômica: `finance-cache.tmp` e troca, com o texto do `json.dumps` padrão.
fn write_cache(path: &Path, state: &Value) -> Result<(), String> {
    let temporary = path.with_extension("tmp");
    fs::write(&temporary, encode_spaced(state)).map_err(|error| error.to_string())?;
    fs::rename(&temporary, path).map_err(|error| error.to_string())
}

/// `finance.PortfolioFinanceCollector`: um cache por carteira pública e um saldo
/// contado uma vez, com cotação e rede compartilhadas por 45 s.
pub struct PortfolioFinanceCollector {
    state_dir: PathBuf,
    history: Option<Arc<dyn FinanceHistory>>,
    wallets: Arc<Mutex<BTreeSet<String>>>,
    collectors: BTreeMap<String, FinanceCollector>,
    fetcher: Fetcher,
    default_wallet: String,
    clock: Clock,
    state: Value,
}

impl PortfolioFinanceCollector {
    /// `default_wallet` é o `config.WALLET`; o cache dela fica na própria pasta de
    /// estado e o das outras em `wallets/<carteira>/`.
    pub fn new(
        state_dir: &Path,
        history: Option<Arc<dyn FinanceHistory>>,
        wallets: Option<Vec<String>>,
        fetcher: Fetcher,
        default_wallet: &str,
        clock: Clock,
    ) -> Self {
        let wallets: BTreeSet<String> = match wallets {
            Some(items) if !items.is_empty() => items.into_iter().collect(),
            _ => [default_wallet.to_owned()].into(),
        };
        Self {
            state_dir: state_dir.to_owned(),
            history,
            wallets: Arc::new(Mutex::new(wallets)),
            collectors: BTreeMap::new(),
            fetcher: shared_market_fetcher(fetcher, MARKET_TTL),
            default_wallet: default_wallet.to_owned(),
            clock,
            state: json!({"schema_version": 2, "primary_wallet": default_wallet, "wallets": {}}),
        }
    }

    /// Controle das carteiras que continua valendo com a thread rodando.
    pub fn wallets_handle(&self) -> WalletsHandle {
        WalletsHandle {
            wallets: self.wallets.clone(),
            default_wallet: self.default_wallet.clone(),
        }
    }

    pub fn state(&self) -> &Value {
        &self.state
    }

    fn collector(&mut self, wallet: &str) {
        if self.collectors.contains_key(wallet) {
            return;
        }
        let directory = if wallet == self.default_wallet {
            self.state_dir.clone()
        } else {
            self.state_dir.join("wallets").join(wallet)
        };
        let mut collector = FinanceCollector::new(
            Some(&directory),
            self.history.clone(),
            wallet,
            self.fetcher.clone(),
            self.clock.clone(),
        );
        if let Some(history) = &self.history {
            let archived = history.finance_state((self.clock)() - CACHE_WINDOW_SECONDS, wallet);
            merge_archived(collector.state_mut(), &archived);
        }
        self.collectors.insert(wallet.to_owned(), collector);
    }

    /// Uma rodada de todas as carteiras, no máximo três ao mesmo tempo.
    pub fn sample(&mut self) -> Value {
        let wallets: Vec<String> = self
            .wallets
            .lock()
            .unwrap_or_else(|poison| poison.into_inner())
            .iter()
            .cloned()
            .collect();
        for wallet in &wallets {
            self.collector(wallet);
        }
        let mut selected: Vec<&mut FinanceCollector> = self
            .collectors
            .iter_mut()
            .filter(|(wallet, _)| wallets.contains(wallet))
            .map(|(_, collector)| collector)
            .collect();
        let mut states = Map::new();
        for group in selected.chunks_mut(3) {
            let results: Vec<(String, Value)> = thread::scope(|scope| {
                let handles: Vec<_> = group
                    .iter_mut()
                    .map(|collector| {
                        scope.spawn(move || {
                            let state = collector.sample().unwrap_or_else(|error| {
                                let mut state = collector.state().clone();
                                object_mut(&mut state, "errors")
                                    .insert("collector".into(), json!(error));
                                state
                            });
                            (collector.wallet().to_owned(), state)
                        })
                    })
                    .collect();
                handles
                    .into_iter()
                    .filter_map(|handle| handle.join().ok())
                    .collect()
            });
            for (wallet, state) in results {
                states.insert(wallet, state);
            }
        }
        self.state = json!({
            "schema_version": 2,
            "primary_wallet": self.default_wallet,
            "wallets": states,
            "attempted_at": (self.clock)(),
        });
        self.state.clone()
    }

    /// `work()`: uma rodada a cada `interval`, publicada na fila de duas amostras.
    pub fn spawn(mut self, interval: Duration) -> Worker {
        Worker::spawn(move |samples| samples.publish(self.sample()), interval)
    }
}

/// `configure_wallets` com a thread em andamento.
#[derive(Clone)]
pub struct WalletsHandle {
    wallets: Arc<Mutex<BTreeSet<String>>>,
    default_wallet: String,
}

impl WalletsHandle {
    /// Só carteiras válidas entram; nenhuma válida volta para a carteira principal.
    pub fn configure(&self, wallets: &[String]) {
        let mut desired: BTreeSet<String> = wallets
            .iter()
            .filter(|wallet| valid_wallet_str(wallet))
            .cloned()
            .collect();
        if desired.is_empty() {
            desired.insert(self.default_wallet.clone());
        }
        *self
            .wallets
            .lock()
            .unwrap_or_else(|poison| poison.into_inner()) = desired;
    }

    pub fn current(&self) -> Vec<String> {
        self.wallets
            .lock()
            .unwrap_or_else(|poison| poison.into_inner())
            .iter()
            .cloned()
            .collect()
    }
}
