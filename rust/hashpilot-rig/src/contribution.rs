//! `contribution.py`: o proxy Stratum local da contribuição opcional.
//!
//! O minerador conecta neste proxy em `127.0.0.1`, e o proxy repassa tudo à pool Pearl da
//! Kryptex. Com a contribuição ligada, cerca de 3 min por hora (5 %) vão para a carteira
//! do autor: o proxy troca a carteira só nos campos de autorização e de share, nunca em
//! outro lugar. O tempo do autor é sempre pago adiantado pelo tempo verificado do usuário
//! (`verified_primary_seconds`), e uma janela gasta nunca é reaproveitada.
//!
//! A semântica numérica segue o Python: `max(0, x)` devolve o inteiro `0` quando `x <= 0`,
//! e inteiro com inteiro continua inteiro, para o ledger gravado ser o mesmo.

use std::io::{ErrorKind, Read, Write};
use std::net::{TcpListener, TcpStream, ToSocketAddrs};
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

use hashpilot_core::wallet::{AUTHOR_WALLET, valid_wallet_str};
use hashpilot_history::pyjson::encode;
use serde_json::{Map, Value, json};

use crate::fsutil::atomic_json;
use crate::lease::Lease;
use crate::manager::support_configuration;

pub const POOL_HOST: &str = "prl.kryptex.network";
pub const POOL_PORT: u16 = 7048;
pub const PERCENT: i64 = 5;
pub const CYCLE_SECONDS: i64 = 3600;
pub const PRIMARY_SECONDS: i64 = 3420;
pub const SUPPORT_SECONDS: i64 = CYCLE_SECONDS - PRIMARY_SECONDS;
pub const MAX_LINE: usize = 4 * 1024 * 1024;
pub const MAX_ACCOUNT_INTERVAL: f64 = 5.0;
const AUTH_METHODS: [&str; 4] = [
    "mining.authorize",
    "eth_submitLogin",
    "login",
    "mining.login",
];
const PREAUTH_METHODS: [&str; 4] = [
    "mining.subscribe",
    "mining.configure",
    "mining.extranonce.subscribe",
    "client.get_version",
];
const WALLET_KEYS: [&str; 4] = ["login", "wallet", "user", "username"];
const ROUTING_COUNTERS: [&str; 2] = ["primary_connected_seconds", "support_connected_seconds"];
const PROTECTION_COUNTERS: [&str; 7] = [
    "verified_primary_seconds",
    "reserved_support_seconds",
    "verified_support_seconds",
    "accepted_primary_shares",
    "accepted_support_shares",
    "rejected_primary_shares",
    "rejected_support_shares",
];
const SHARE_COUNTERS: [&str; 4] = [
    "accepted_primary_shares",
    "accepted_support_shares",
    "rejected_primary_shares",
    "rejected_support_shares",
];
const MAX_COUNTER: f64 = 1e12;

/// Número do Python: `int` ou `float`, com as regras de mistura de tipos.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Num {
    Int(i64),
    Float(f64),
}

impl Num {
    pub fn of(value: &Value) -> Num {
        match value {
            Value::Bool(flag) => Num::Int(i64::from(*flag)),
            Value::Number(number) => number
                .as_i64()
                .map_or_else(|| Num::Float(number.as_f64().unwrap_or(0.0)), Num::Int),
            _ => Num::Int(0),
        }
    }

    pub fn f(self) -> f64 {
        match self {
            Num::Int(value) => value as f64,
            Num::Float(value) => value,
        }
    }

    pub fn value(self) -> Value {
        match self {
            Num::Int(value) => json!(value),
            Num::Float(value) => json!(value),
        }
    }

    fn combine(self, other: Num, ints: fn(i64, i64) -> i64, floats: fn(f64, f64) -> f64) -> Num {
        match (self, other) {
            (Num::Int(a), Num::Int(b)) => Num::Int(ints(a, b)),
            _ => Num::Float(floats(self.f(), other.f())),
        }
    }

    pub fn plus(self, other: Num) -> Num {
        self.combine(other, |a, b| a + b, |a, b| a + b)
    }

    pub fn minus(self, other: Num) -> Num {
        self.combine(other, |a, b| a - b, |a, b| a - b)
    }

    pub fn times(self, other: Num) -> Num {
        self.combine(other, |a, b| a * b, |a, b| a * b)
    }

    /// `%` do Python (resultado com o sinal do divisor).
    pub fn modulo(self, other: Num) -> Num {
        self.combine(other, i64::rem_euclid, |a, b| a - b * (a / b).floor())
    }

    /// `max(self, other)`: o primeiro vence o empate.
    pub fn max(self, other: Num) -> Num {
        if other.f() > self.f() { other } else { self }
    }

    /// `min(self, other)`: o primeiro vence o empate.
    pub fn min(self, other: Num) -> Num {
        if other.f() < self.f() { other } else { self }
    }

    /// `math.floor(self / other)` (`/` sempre dá `float` no Python).
    pub fn floor_div(self, other: Num) -> Num {
        Num::Int((self.f() / other.f()).floor() as i64)
    }
}

const ZERO: Num = Num::Int(0);

fn int(value: i64) -> Num {
    Num::Int(value)
}

/// `valid_wallet` sobre um `Value` (só texto).
fn wallet_ok(value: &str) -> bool {
    valid_wallet_str(value)
}

fn is_number(value: &Value) -> bool {
    value.is_number()
}

/// `_check_counter`: número finito em `[0, 1e12]`; booleano é inválido.
fn check_counter(
    entry: &Map<String, Value>,
    key: &str,
    message: &str,
    required: bool,
) -> Result<(), String> {
    if required && !entry.contains_key(key) {
        return Err(message.into());
    }
    let value = entry.get(key).cloned().unwrap_or(json!(0));
    if !is_number(&value) {
        return Err(message.into());
    }
    let number = Num::of(&value).f();
    if !number.is_finite() || !(0.0..=MAX_COUNTER).contains(&number) {
        return Err(message.into());
    }
    Ok(())
}

/// Troca `NaN`, `Infinity` e `-Infinity` fora de textos por um texto sentinela. O
/// `json.loads` do Python aceita esses literais, e a validação do ledger os recusa pela
/// mesma regra que recusa um texto; o `serde_json` nem leria o arquivo.
fn neutralize_python_literals(text: &str) -> String {
    let mut out = String::with_capacity(text.len());
    let mut chars = text.char_indices().peekable();
    let mut in_string = false;
    let mut escaped = false;
    while let Some((position, character)) = chars.next() {
        if in_string {
            out.push(character);
            if escaped {
                escaped = false;
            } else if character == '\\' {
                escaped = true;
            } else if character == '"' {
                in_string = false;
            }
            continue;
        }
        let rest = &text[position..];
        let literal = ["-Infinity", "Infinity", "NaN"]
            .into_iter()
            .find(|literal| rest.starts_with(literal));
        if let Some(literal) = literal {
            out.push_str("\"\\u0000nao-finito\"");
            for _ in 1..literal.chars().count() {
                chars.next();
            }
            continue;
        }
        if character == '"' {
            in_string = true;
        }
        out.push(character);
    }
    out
}

/// `schema_version in (1, 2)`, com `True == 1` e `1.0 == 1` do Python.
fn schema_number(value: &Value) -> Option<f64> {
    match value {
        Value::Bool(flag) => Some(f64::from(u8::from(*flag))),
        Value::Number(number) => number.as_f64(),
        _ => None,
    }
}

/// `ContributionLedger`: carregar nunca credita tempo parado; só `record_route` gera cota.
pub struct Ledger {
    pub path: PathBuf,
    pub wallet: String,
    pub data: Value,
    pub healthy: bool,
    pub error: Option<String>,
}

impl Ledger {
    pub fn open(path: &Path, wallet: &str) -> Result<Self, String> {
        if !wallet_ok(wallet) {
            return Err("Endereço público Pearl inválido".into());
        }
        let mut ledger = Self {
            path: path.to_path_buf(),
            wallet: wallet.into(),
            data: json!({"schema_version": 2, "wallets": {}}),
            healthy: true,
            error: None,
        };
        match std::fs::read(path) {
            Err(error) if error.kind() == ErrorKind::NotFound => {}
            Err(error) => {
                ledger.healthy = false;
                ledger.error = Some(error.to_string());
            }
            Ok(raw) => match Self::validated(&raw) {
                Ok(data) => ledger.data = data,
                Err(error) => {
                    ledger.healthy = false;
                    ledger.error = Some(error);
                }
            },
        }
        let wallets = ledger.data["wallets"]
            .as_object_mut()
            .expect("wallets validado como objeto");
        if !wallets.contains_key(wallet) {
            wallets.insert(
                wallet.into(),
                json!({"eligible_connected_seconds": 0.0, "primary_connected_seconds": 0.0, "support_connected_seconds": 0.0}),
            );
        }
        Ok(ledger)
    }

    fn validated(raw: &[u8]) -> Result<Value, String> {
        let text = String::from_utf8_lossy(raw);
        let text = text.strip_prefix('\u{feff}').unwrap_or(&text);
        let mut data: Value = serde_json::from_str(text)
            .or_else(|_| serde_json::from_str(&neutralize_python_literals(text)))
            .map_err(|error| error.to_string())?;
        let format = "Formato do ledger inválido".to_string();
        let schema = data.get("schema_version").and_then(schema_number);
        if !data.is_object()
            || !matches!(schema, Some(version) if version == 1.0 || version == 2.0)
            || !data.get("wallets").is_some_and(Value::is_object)
        {
            return Err(format);
        }
        if schema == Some(1.0) {
            // O tempo antigo não foi confirmado pela pool: fica no histórico, sem virar cota.
            for entry in data["wallets"]
                .as_object_mut()
                .expect("objeto")
                .values_mut()
            {
                if let Some(entry) = entry.as_object_mut() {
                    for key in PROTECTION_COUNTERS {
                        entry.insert(key.into(), json!(0));
                    }
                }
            }
            data["schema_version"] = json!(2);
        }
        for (identifier, entry) in data["wallets"].as_object().expect("objeto") {
            let Some(entry) = entry.as_object().filter(|_| wallet_ok(identifier)) else {
                return Err("Contador do ledger inválido".into());
            };
            check_counter(
                entry,
                "eligible_connected_seconds",
                "Contador do ledger inválido",
                true,
            )?;
            for key in ROUTING_COUNTERS {
                check_counter(
                    entry,
                    key,
                    "Contador de roteamento do ledger inválido",
                    false,
                )?;
            }
            for key in PROTECTION_COUNTERS {
                check_counter(
                    entry,
                    key,
                    "Contador de proteção da contribuição inválido",
                    false,
                )?;
            }
            let get = |key: &str| Num::of(entry.get(key).unwrap_or(&json!(0)));
            let verified = get("verified_primary_seconds");
            let reserved = get("reserved_support_seconds");
            let earned = verified
                .floor_div(int(PRIMARY_SECONDS))
                .times(int(SUPPORT_SECONDS));
            if reserved.f() > earned.f() + 1e-6
                || get("verified_support_seconds").f() > reserved.f() + 1e-6
            {
                return Err("Reserva da contribuição excede o tempo verificado do usuário".into());
            }
            if earned.minus(reserved).f() > SUPPORT_SECONDS as f64 + 1e-6 {
                return Err("Acúmulo de janelas de contribuição inválido".into());
            }
            if verified.f() > get("primary_connected_seconds").f() + 1e-6 {
                return Err("Tempo verificado excede roteamento principal".into());
            }
        }
        Ok(data)
    }

    fn entry(&self) -> &Map<String, Value> {
        self.data["wallets"][&self.wallet]
            .as_object()
            .expect("entrada da carteira")
    }

    fn entry_mut(&mut self) -> &mut Map<String, Value> {
        self.data["wallets"][&self.wallet]
            .as_object_mut()
            .expect("entrada da carteira")
    }

    fn get(&self, key: &str) -> Num {
        Num::of(self.entry().get(key).unwrap_or(&json!(0)))
    }

    fn put(&mut self, key: &str, value: Num) {
        self.entry_mut().insert(key.into(), value.value());
    }

    /// Valor bruto de um contador (`entry.get(key, 0)`), como o status publica.
    pub fn raw(&self, key: &str) -> Value {
        self.entry().get(key).cloned().unwrap_or(json!(0))
    }

    pub fn seconds(&self) -> f64 {
        self.get("eligible_connected_seconds").f()
    }

    pub fn role(&self, enabled: bool) -> &'static str {
        let phase = Num::Float(self.seconds()).modulo(int(CYCLE_SECONDS)).f();
        if enabled && self.healthy && phase >= PRIMARY_SECONDS as f64 {
            "support"
        } else {
            "primary"
        }
    }

    pub fn until_switch(&self, enabled: bool) -> Option<f64> {
        if !enabled || !self.healthy {
            return None;
        }
        let phase = Num::Float(self.seconds()).modulo(int(CYCLE_SECONDS)).f();
        Some(if phase < PRIMARY_SECONDS as f64 {
            PRIMARY_SECONDS as f64 - phase
        } else {
            CYCLE_SECONDS as f64 - phase
        })
    }

    pub fn advance(&mut self, seconds: &Value, enabled: bool) -> Result<(), String> {
        let valid = is_number(seconds) && {
            let value = Num::of(seconds).f();
            value.is_finite() && value >= 0.0
        };
        if !valid {
            return Err("ValueError: Intervalo conectado inválido".into());
        }
        if !enabled || !self.healthy {
            return Ok(());
        }
        let mut remaining = Num::of(seconds).f();
        while remaining > 0.0 {
            let role = self.role(true);
            let step = remaining.min(self.until_switch(true).expect("habilitado"));
            if step <= 0.0 {
                return Err("RuntimeError: Fase de contribuição inconsistente".into());
            }
            let eligible = self
                .get("eligible_connected_seconds")
                .plus(Num::Float(step));
            self.put("eligible_connected_seconds", eligible);
            let key = if role == "support" {
                "support_connected_seconds"
            } else {
                "primary_connected_seconds"
            };
            let counter = Num::Float(self.get(key).f() + step);
            self.put(key, counter);
            remaining -= step;
        }
        Ok(())
    }

    pub fn save(&self) -> Result<(), String> {
        if self.healthy {
            atomic_json(&self.path, &self.data, true, false)?;
        }
        Ok(())
    }

    pub fn available_support(&self) -> Num {
        if !self.healthy {
            return Num::Float(0.0);
        }
        let earned = self
            .get("verified_primary_seconds")
            .floor_div(int(PRIMARY_SECONDS))
            .times(int(SUPPORT_SECONDS))
            .minus(self.get("reserved_support_seconds"));
        Num::Float(0.0).max(Num::Float(SUPPORT_SECONDS as f64).min(earned))
    }

    /// Paga adiantado toda a janela do autor antes de repassar a autorização dele. Uma
    /// queda pode desperdiçar a janela, mas nunca a reaproveita depois de reiniciar.
    pub fn reserve_support(&mut self) -> Result<Num, String> {
        let amount = self.available_support();
        let reserved = self.get("reserved_support_seconds").plus(amount);
        self.put("reserved_support_seconds", reserved);
        self.save()?;
        Ok(amount)
    }

    pub fn record_route(&mut self, seconds: Num, role: &str) -> Result<(), String> {
        if !self.healthy {
            return Ok(());
        }
        let value = seconds.f();
        if !value.is_finite() || !(0.0..=MAX_ACCOUNT_INTERVAL).contains(&value) {
            return Err("ValueError: Intervalo de roteamento inválido".into());
        }
        let mut seconds = seconds;
        if role == "support" {
            let remaining = ZERO.max(
                self.get("reserved_support_seconds")
                    .minus(self.get("verified_support_seconds")),
            );
            seconds = seconds.min(remaining);
        } else if role != "primary" {
            return Err("ValueError: Papel de roteamento inválido".into());
        }
        let eligible = self.get("eligible_connected_seconds").plus(seconds);
        self.put("eligible_connected_seconds", eligible);
        let counter = format!("{role}_connected_seconds");
        let total = self.get(&counter).plus(seconds);
        self.put(&counter, total);
        let verified = format!("verified_{role}_seconds");
        let total = self.get(&verified).plus(seconds);
        self.put(&verified, total);
        Ok(())
    }

    pub fn record_share(&mut self, role: &str, accepted: bool) {
        if !self.healthy {
            return;
        }
        let key = format!(
            "{}{role}_shares",
            if accepted { "accepted_" } else { "rejected_" }
        );
        let total = self.get(&key).plus(int(1));
        self.put(&key, total);
    }
}

/// `record_route` com o intervalo vindo de um `Value` (booleano é inválido, como no Python).
pub fn record_route_value(ledger: &mut Ledger, seconds: &Value, role: &str) -> Result<(), String> {
    if !ledger.healthy {
        return Ok(());
    }
    if !is_number(seconds) {
        return Err("ValueError: Intervalo de roteamento inválido".into());
    }
    ledger.record_route(Num::of(seconds), role)
}

fn rewrite_wallet(value: &mut Value, primary: &str, active: &str) -> bool {
    let Some(text) = value.as_str() else {
        return false;
    };
    if text == primary {
        *value = json!(active);
        return true;
    }
    if let Some(rest) = text.strip_prefix(primary)
        && rest.starts_with('.')
    {
        *value = json!(format!("{active}{rest}"));
        return true;
    }
    false
}

/// `rewrite_message`: só os campos de carteira da autorização e do share mudam.
/// Devolve a mensagem reescrita e se ela é a autorização.
pub fn rewrite_message(
    message: &Value,
    primary: &str,
    active: &str,
    authenticated: bool,
) -> Result<(Value, bool), String> {
    let Some(method) = message
        .get("method")
        .and_then(Value::as_str)
        .filter(|_| message.is_object())
    else {
        return Err("Mensagem Stratum não reconhecida".into());
    };
    let is_auth = AUTH_METHODS.contains(&method);
    if !is_auth && !PREAUTH_METHODS.contains(&method) && method != "mining.submit" {
        return Err(
            "Mensagem Stratum desconhecida; método fora do protocolo de mineração permitido".into(),
        );
    }
    let mut result = message.clone();
    let params = result.get_mut("params");
    if is_auth {
        let replaced = match params {
            Some(Value::Array(items)) if !items.is_empty() => {
                rewrite_wallet(&mut items[0], primary, active)
            }
            Some(Value::Object(map)) => {
                let candidates: Vec<&str> = WALLET_KEYS
                    .into_iter()
                    .filter(|key| map.contains_key(*key))
                    .collect();
                candidates.len() == 1
                    && rewrite_wallet(
                        map.get_mut(candidates[0]).expect("chave presente"),
                        primary,
                        active,
                    )
            }
            _ => false,
        };
        if !replaced {
            return Err(
                "Autorização incompatível com a carteira principal; conexão encerrada".into(),
            );
        }
        return Ok((result, true));
    }
    if !authenticated && !PREAUTH_METHODS.contains(&method) {
        return Err("Autenticação Stratum desconhecida; conexão encerrada antes de minerar".into());
    }
    if method == "mining.submit" {
        match params {
            Some(Value::Array(items)) if !items.is_empty() => {
                rewrite_wallet(&mut items[0], primary, active);
            }
            Some(Value::Object(map)) => {
                for key in WALLET_KEYS {
                    if let Some(value) = map.get_mut(key) {
                        rewrite_wallet(value, primary, active);
                    }
                }
            }
            _ => {}
        }
    }
    Ok((result, false))
}

/// `json.dumps(valor, separators=(",", ":"))` com o `ensure_ascii=True` padrão: tudo fora
/// do ASCII imprimível (inclusive `\x7f`) sai como `\uXXXX`, em pares para fora do BMP.
pub fn dumps_ascii(value: &Value) -> String {
    let mut out = String::new();
    for character in encode(value).chars() {
        if (character as u32) < 0x7f {
            out.push(character);
            continue;
        }
        let mut units = [0u16; 2];
        for unit in character.encode_utf16(&mut units) {
            out.push_str(&format!("\\u{unit:04x}"));
        }
    }
    out
}

/// `read_settings`: o JSON do arquivo, se for objeto; senão `None` (`null`).
pub fn read_settings(path: &Path) -> Value {
    std::fs::read(path)
        .ok()
        .and_then(|raw| {
            let text = String::from_utf8(raw).ok()?;
            serde_json::from_str::<Value>(text.strip_prefix('\u{feff}').unwrap_or(&text)).ok()
        })
        .filter(Value::is_object)
        .unwrap_or(Value::Null)
}

/// Identificador Stratum rastreável: texto ou inteiro (booleano não).
fn id_key(identifier: &Value) -> Option<String> {
    match identifier {
        Value::String(text) => Some(format!("s:{text}")),
        Value::Number(number) if number.is_i64() || number.is_u64() => Some(format!("i:{number}")),
        _ => None,
    }
}

/// `x == y` do Python entre identificadores.
fn same_id(left: &Value, right: &Value) -> bool {
    hashpilot_history::pyjson::py_eq(left, right)
}

/// `response.get('error') in (None, False)`: `0` e `0.0` também valem, porque `0 == False`.
fn no_error(response: &Value) -> bool {
    match response.get("error") {
        None | Some(Value::Null) | Some(Value::Bool(false)) => true,
        Some(Value::Number(number)) => number.as_f64() == Some(0.0),
        _ => false,
    }
}

/// `str.strip()` de bytes: espaço, `\t`, `\n`, `\r`, `\x0b` e `\x0c`.
fn blank(line: &[u8]) -> bool {
    line.iter()
        .all(|byte| matches!(byte, b' ' | b'\t' | b'\n' | b'\r' | 0x0b | 0x0c))
}

/// Erro de uma conexão, com o tipo do Python para o texto ficar igual ao do `rig`.
#[derive(Debug)]
pub enum Failure {
    /// `ContributionWindowEnded`: fim normal da janela do autor.
    WindowEnded,
    Error {
        kind: &'static str,
        message: String,
    },
}

impl Failure {
    fn value(message: impl Into<String>) -> Self {
        Failure::Error {
            kind: "ValueError",
            message: message.into(),
        }
    }

    fn io(error: std::io::Error) -> Self {
        Failure::Error {
            kind: "OSError",
            message: error.to_string(),
        }
    }

    fn tagged(text: String) -> Self {
        match text.split_once(": ") {
            Some(("ValueError", message)) => Failure::Error {
                kind: "ValueError",
                message: message.into(),
            },
            Some(("RuntimeError", message)) => Failure::Error {
                kind: "RuntimeError",
                message: message.into(),
            },
            _ => Failure::Error {
                kind: "OSError",
                message: text,
            },
        }
    }

    /// `str(error)`.
    pub fn message(&self) -> String {
        match self {
            Failure::WindowEnded => "Limite da janela de contribuição atingido".into(),
            Failure::Error { message, .. } => message.clone(),
        }
    }

    /// `f"{type(error).__name__}: {error}"`.
    pub fn describe(&self) -> String {
        match self {
            Failure::WindowEnded => format!("ContributionWindowEnded: {}", self.message()),
            Failure::Error { kind, message } => format!("{kind}: {message}"),
        }
    }
}

pub type Connector = Box<dyn FnMut() -> std::io::Result<TcpStream> + Send>;
pub type Clock = Box<dyn Fn() -> f64 + Send>;

/// `socket.create_connection((POOL_HOST, POOL_PORT), timeout=10)`.
pub fn connect_pool() -> std::io::Result<TcpStream> {
    let mut last = std::io::Error::new(ErrorKind::NotFound, "pool sem endereço");
    for address in (POOL_HOST, POOL_PORT).to_socket_addrs()? {
        match TcpStream::connect_timeout(&address, Duration::from_secs(10)) {
            Ok(stream) => return Ok(stream),
            Err(error) => last = error,
        }
    }
    Err(last)
}

fn wall_clock() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0.0, |elapsed| elapsed.as_secs_f64())
}

/// `LocalStratumProxy`: um minerador, escuta só em loopback, upstream fixo de produção,
/// e desliga a contribuição em até 2 s quando a configuração muda.
pub struct Proxy {
    pub wallet: String,
    pub settings_path: PathBuf,
    pub state_dir: PathBuf,
    pub ledger: Ledger,
    pub port: u16,
    connector: Connector,
    clock: Clock,
    pub stop: Arc<AtomicBool>,
    pub safety_disabled: bool,
    support_deadline: Option<f64>,
    pub status: Map<String, Value>,
}

impl Proxy {
    pub fn new(
        wallet: &str,
        settings_path: &Path,
        state_dir: &Path,
        port: u16,
        connector: Option<Connector>,
        clock: Option<Clock>,
    ) -> Result<Self, String> {
        if !wallet_ok(wallet) {
            return Err("Endereço público Pearl inválido".into());
        }
        std::fs::create_dir_all(state_dir).map_err(|error| error.to_string())?;
        let ledger = Ledger::open(&state_dir.join("support-mining-ledger.json"), wallet)?;
        let start = Instant::now();
        let status = json!({"pid": std::process::id(), "ready": false, "connected": false, "pool_authorized": false,
            "port": null, "enabled": false, "primary_wallet": wallet, "support_wallet": AUTHOR_WALLET,
            "active_wallet": wallet, "active_role": "primary", "percent": PERCENT, "method": "mining_time",
            "upstream": format!("{POOL_HOST}:{POOL_PORT}"), "error": null});
        Ok(Self {
            wallet: wallet.into(),
            settings_path: settings_path.to_path_buf(),
            state_dir: state_dir.to_path_buf(),
            ledger,
            port,
            connector: connector.unwrap_or_else(|| Box::new(connect_pool)),
            clock: clock.unwrap_or_else(|| Box::new(move || start.elapsed().as_secs_f64())),
            stop: Arc::new(AtomicBool::new(false)),
            safety_disabled: false,
            support_deadline: None,
            status: status.as_object().expect("objeto").clone(),
        })
    }

    fn now(&self) -> f64 {
        (self.clock)()
    }

    fn stopped(&self) -> bool {
        self.stop.load(Ordering::SeqCst)
    }

    pub fn configuration(&self) -> Value {
        let mut result =
            support_configuration(&read_settings(&self.settings_path), &json!(self.wallet));
        if !self.ledger.healthy {
            result["enabled"] = json!(false);
            result["reason"] = json!("Ledger inválido; contribuição desligada");
            result["configuration_valid"] = json!(false);
        }
        if self.safety_disabled {
            result["enabled"] = json!(false);
            result["reason"] = json!(
                "Contribuição desativada por falha de segurança/protocolo; usando carteira principal"
            );
        }
        result
    }

    fn update(&mut self, pairs: Vec<(&str, Value)>) {
        for (key, value) in pairs {
            self.status.insert(key.into(), value);
        }
    }

    pub fn publish(&mut self, configuration: Option<&Value>) -> Result<(), String> {
        let configuration = configuration
            .cloned()
            .unwrap_or_else(|| self.configuration());
        let enabled = configuration["enabled"] == json!(true);
        let until = match self.support_deadline {
            Some(deadline) => Some(ZERO.max(Num::Float(deadline - self.now()))),
            None if enabled => Some(ZERO.max(int(PRIMARY_SECONDS).minus(
                Num::of(&self.ledger.raw("verified_primary_seconds")).modulo(int(PRIMARY_SECONDS)),
            ))),
            None => None,
        };
        let routing = self.status["ready"] == json!(true)
            && self.status["connected"] == json!(true)
            && self.status["pool_authorized"] == json!(true);
        let scheduled = if enabled && self.ledger.available_support().f() > 0.0 {
            "support"
        } else {
            "primary"
        };
        let shares: Map<String, Value> = SHARE_COUNTERS
            .into_iter()
            .map(|key| (key.to_string(), self.ledger.raw(key)))
            .collect();
        self.update(vec![
            ("enabled", configuration["enabled"].clone()),
            (
                "configuration_valid",
                configuration["configuration_valid"].clone(),
            ),
            ("reason", configuration["reason"].clone()),
            ("eligible_connected_seconds", json!(self.ledger.seconds())),
            (
                "seconds_until_switch",
                until.map_or(Value::Null, Num::value),
            ),
            (
                "ledger_error",
                self.ledger.error.clone().map_or(Value::Null, Value::String),
            ),
            ("sampled_at", json!(wall_clock())),
            ("routing_active", json!(routing)),
            ("scheduled_role", json!(scheduled)),
            (
                "quota_accounting",
                json!("verified_primary_prepaid_support_v2"),
            ),
            (
                "verified_primary_seconds",
                self.ledger.raw("verified_primary_seconds"),
            ),
            (
                "reserved_support_seconds",
                self.ledger.raw("reserved_support_seconds"),
            ),
            (
                "verified_support_seconds",
                self.ledger.raw("verified_support_seconds"),
            ),
            ("share_responses", Value::Object(shares)),
        ]);
        atomic_json(
            &self.state_dir.join("support-mining.json"),
            &Value::Object(self.status.clone()),
            false,
            false,
        )
    }

    pub fn close(&self) {
        self.stop.store(true, Ordering::SeqCst);
    }

    /// `serve_connection`: repassa uma conexão do minerador até ela fechar, a janela
    /// mudar de papel ou o protocolo sair do permitido.
    pub fn serve_connection(
        &mut self,
        client: &mut TcpStream,
        upstream: &mut TcpStream,
    ) -> Result<(), Failure> {
        let mut session = Session::start(self)?;
        let result = session.run(self, client, upstream);
        let result = match result {
            Err(Failure::WindowEnded) => Ok(()),
            Err(error) => {
                if session.role == "support" {
                    self.safety_disabled = true;
                }
                Err(error)
            }
            Ok(()) => Ok(()),
        };
        let now = self.now();
        let finish = session.account(self, now).map_err(Failure::tagged);
        let saved = self.ledger.save().map_err(|error| Failure::Error {
            kind: "OSError",
            message: error,
        });
        self.support_deadline = None;
        self.status.insert("pool_authorized".into(), json!(false));
        let wallet = json!(self.wallet);
        self.update(vec![
            ("connected", json!(false)),
            ("active_role", json!("primary")),
            ("active_wallet", wallet),
        ]);
        let published = self.publish(None).map_err(|error| Failure::Error {
            kind: "OSError",
            message: error,
        });
        result.and(finish).and(saved).and(published)
    }

    /// `_send`: contrapressão limitada a 1 s, para um lado parado não impedir o desligamento.
    fn send(&self, target: &mut TcpStream, data: &[u8]) -> Result<(), Failure> {
        let mut remaining = data;
        let deadline = self.now() + 1.0;
        while !remaining.is_empty() && !self.stopped() {
            if self
                .support_deadline
                .is_some_and(|limit| self.now() >= limit)
            {
                return Err(Failure::WindowEnded);
            }
            if self.now() > deadline {
                return Err(Failure::Error {
                    kind: "TimeoutError",
                    message: "Conexão Stratum sem resposta".into(),
                });
            }
            match target.write(remaining) {
                Ok(0) => {
                    return Err(Failure::Error {
                        kind: "ConnectionError",
                        message: "Conexão Stratum encerrada".into(),
                    });
                }
                Ok(count) => remaining = &remaining[count..],
                Err(error) if error.kind() == ErrorKind::WouldBlock => {
                    std::thread::sleep(Duration::from_millis(2))
                }
                Err(error) if error.kind() == ErrorKind::Interrupted => {}
                Err(error) => return Err(Failure::io(error)),
            }
        }
        Ok(())
    }

    /// `run`: trava o diretório (um proxy por GPU) e atende conexões até `stop`.
    pub fn run(&mut self) -> Result<(), String> {
        let mut lease = Lease::acquire(
            &self.state_dir.join("support-mining-owner.json"),
            "Já existe um proxy de contribuição usando este diretório de logs",
        )?;
        // Outra instância pode ter atualizado o ledger depois da construção.
        if self.ledger.path.exists() {
            self.ledger = Ledger::open(&self.ledger.path.clone(), &self.wallet.clone())?;
        }
        let result = self.run_owned();
        lease.close();
        result
    }

    fn run_owned(&mut self) -> Result<(), String> {
        let listener =
            TcpListener::bind(("127.0.0.1", self.port)).map_err(|error| error.to_string())?;
        listener
            .set_nonblocking(true)
            .map_err(|error| error.to_string())?;
        let port = listener
            .local_addr()
            .map_err(|error| error.to_string())?
            .port();
        self.update(vec![("port", json!(port)), ("ready", json!(true))]);
        let mut result = self.publish(None);
        let mut waited = Instant::now();
        while result.is_ok() && !self.stopped() {
            let (mut client, address) = match listener.accept() {
                Ok(accepted) => accepted,
                Err(error) if error.kind() == ErrorKind::WouldBlock => {
                    if waited.elapsed() >= Duration::from_millis(500) {
                        result = self.publish(None);
                        waited = Instant::now();
                    }
                    std::thread::sleep(Duration::from_millis(20));
                    continue;
                }
                Err(error) => {
                    result = Err(error.to_string());
                    break;
                }
            };
            if !address.ip().is_loopback() || address.ip().to_string() != "127.0.0.1" {
                continue;
            }
            let outcome = (self.connector)()
                .map_err(Failure::io)
                .and_then(|mut upstream| self.serve_connection(&mut client, &mut upstream));
            if let Err(error) = outcome {
                let text: String = error.message().chars().take(240).collect();
                self.update(vec![("error", json!(text)), ("connected", json!(false))]);
                result = self.publish(None);
            }
            waited = Instant::now();
        }
        drop(listener);
        let saved = self.ledger.save();
        let wallet = json!(self.wallet);
        self.update(vec![
            ("ready", json!(false)),
            ("connected", json!(false)),
            ("active_role", json!("primary")),
            ("active_wallet", wallet),
        ]);
        let published = self.publish(None);
        result.and(saved).and(published)
    }
}

/// Estado de uma conexão (`serve_connection`).
struct Session {
    role: &'static str,
    active: String,
    configuration: Value,
    authenticated: bool,
    pool_authorized: bool,
    auth_id: Option<Value>,
    shares: Vec<(String, &'static str)>,
    pending: Vec<String>,
    previous: f64,
    last_publish: f64,
    client_buffer: Vec<u8>,
    upstream_buffer: Vec<u8>,
}

impl Session {
    fn start(proxy: &mut Proxy) -> Result<Self, Failure> {
        let configuration = proxy.configuration();
        let enabled = configuration["enabled"] == json!(true);
        let role = if enabled && proxy.ledger.available_support().f() > 0.0 {
            "support"
        } else {
            "primary"
        };
        let allowance = if role == "support" {
            proxy
                .ledger
                .reserve_support()
                .map_err(|error| Failure::Error {
                    kind: "OSError",
                    message: error,
                })?
                .f()
        } else {
            0.0
        };
        proxy.support_deadline = (role == "support").then(|| proxy.now() + allowance);
        let active = if role == "support" {
            AUTHOR_WALLET.to_string()
        } else {
            proxy.wallet.clone()
        };
        proxy.update(vec![
            ("connected", json!(true)),
            ("active_role", json!(role)),
            ("active_wallet", json!(active)),
            ("error", Value::Null),
        ]);
        proxy
            .publish(Some(&configuration))
            .map_err(|error| Failure::Error {
                kind: "OSError",
                message: error,
            })?;
        let previous = proxy.now();
        Ok(Self {
            role,
            active,
            configuration,
            authenticated: false,
            pool_authorized: false,
            auth_id: None,
            shares: Vec::new(),
            pending: Vec::new(),
            previous,
            last_publish: previous,
            client_buffer: Vec::new(),
            upstream_buffer: Vec::new(),
        })
    }

    /// `account(now)`: só tempo com a pool autorizada e a contribuição ligada vira cota.
    fn account(&mut self, proxy: &mut Proxy, now: f64) -> Result<(), String> {
        let mut delta = ZERO.max(Num::Float(now - self.previous));
        if self.pool_authorized
            && self.configuration["enabled"] == json!(true)
            && delta.f() <= MAX_ACCOUNT_INTERVAL
        {
            if self.role == "support" {
                let deadline = proxy.support_deadline.unwrap_or(self.previous);
                delta = delta.min(ZERO.max(Num::Float(deadline - self.previous)));
            }
            proxy.ledger.record_route(delta, self.role)?;
        }
        self.previous = now;
        Ok(())
    }

    fn run(
        &mut self,
        proxy: &mut Proxy,
        client: &mut TcpStream,
        upstream: &mut TcpStream,
    ) -> Result<(), Failure> {
        client.set_nonblocking(true).map_err(Failure::io)?;
        upstream.set_nonblocking(true).map_err(Failure::io)?;
        let mut chunk = vec![0u8; 65536];
        while !proxy.stopped() {
            let now = proxy.now();
            self.account(proxy, now).map_err(Failure::tagged)?;
            let new_configuration = proxy.configuration();
            let enabled = new_configuration["enabled"] == json!(true);
            let switch = (self.role == "support"
                && (!enabled || proxy.support_deadline.is_some_and(|limit| now >= limit)))
                || (self.role == "primary"
                    && enabled
                    && proxy.ledger.available_support().f() > 0.0);
            if switch {
                proxy.ledger.save().map_err(|error| Failure::Error {
                    kind: "OSError",
                    message: error,
                })?;
                proxy.status.insert(
                    "reason".into(),
                    json!(
                        "Alternando carteira por reconexão Stratum; o minerador continua executando"
                    ),
                );
                return Ok(());
            }
            self.configuration = new_configuration;
            if now - self.last_publish >= 1.0 {
                proxy.ledger.save().map_err(|error| Failure::Error {
                    kind: "OSError",
                    message: error,
                })?;
                let configuration = self.configuration.clone();
                proxy
                    .publish(Some(&configuration))
                    .map_err(|error| Failure::Error {
                        kind: "OSError",
                        message: error,
                    })?;
                self.last_publish = now;
            }
            // `select([client, upstream], [], [], .25)`: até 250 ms esperando algum lado.
            let waited = Instant::now();
            loop {
                let mut progressed = false;
                for from_client in [true, false] {
                    let source: &mut TcpStream = if from_client { client } else { upstream };
                    match source.read(&mut chunk) {
                        Ok(0) => return Ok(()),
                        Ok(count) => {
                            progressed = true;
                            let data = chunk[..count].to_vec();
                            if from_client {
                                if !self.on_client(proxy, upstream, &data)? {
                                    return Ok(());
                                }
                            } else if !self.on_upstream(proxy, client, &data)? {
                                return Ok(());
                            }
                        }
                        Err(error)
                            if matches!(
                                error.kind(),
                                ErrorKind::WouldBlock | ErrorKind::Interrupted
                            ) => {}
                        Err(error) => return Err(Failure::io(error)),
                    }
                }
                if progressed || waited.elapsed() >= Duration::from_millis(250) {
                    break;
                }
                std::thread::sleep(Duration::from_millis(2));
            }
        }
        Ok(())
    }

    /// Respostas da pool; `false` encerra a conexão (autorização recusada).
    fn on_upstream(
        &mut self,
        proxy: &mut Proxy,
        client: &mut TcpStream,
        data: &[u8],
    ) -> Result<bool, Failure> {
        self.upstream_buffer.extend_from_slice(data);
        while let Some(position) = self.upstream_buffer.iter().position(|byte| *byte == b'\n') {
            let line: Vec<u8> = self
                .upstream_buffer
                .drain(..=position)
                .take(position)
                .collect();
            if line.len() > MAX_LINE {
                return Err(Failure::value("Resposta Stratum excede limite"));
            }
            let response: Value = if blank(&line) {
                json!({})
            } else {
                serde_json::from_slice(&line).map_err(|error| Failure::value(error.to_string()))?
            };
            if !response.is_object() {
                return Err(Failure::value("Resposta Stratum inválida"));
            }
            let identifier = response.get("id").cloned().unwrap_or(Value::Null);
            let key = id_key(&identifier);
            if let Some(key) = &key {
                self.pending.retain(|item| item != key);
            }
            let accepted =
                response.get("result") == Some(&Value::Bool(true)) && no_error(&response);
            if self
                .auth_id
                .as_ref()
                .is_some_and(|auth| same_id(&identifier, auth))
            {
                let now = proxy.now();
                self.account(proxy, now).map_err(Failure::tagged)?;
                self.pool_authorized = accepted;
                proxy
                    .status
                    .insert("pool_authorized".into(), json!(accepted));
                self.auth_id = None;
                if !accepted {
                    if self.role == "support" {
                        proxy.safety_disabled = true;
                    }
                    return Ok(false);
                }
            } else if let Some(key) = key
                && let Some(position) = self.shares.iter().position(|(item, _)| *item == key)
            {
                let (_, role) = self.shares.remove(position);
                proxy.ledger.record_share(role, accepted);
            }
        }
        if self.upstream_buffer.len() > MAX_LINE {
            return Err(Failure::value("Resposta Stratum excede limite"));
        }
        proxy.send(client, data)?;
        Ok(true)
    }

    /// Pedidos do minerador: valida, reescreve a carteira e repassa à pool.
    fn on_client(
        &mut self,
        proxy: &mut Proxy,
        upstream: &mut TcpStream,
        data: &[u8],
    ) -> Result<bool, Failure> {
        self.client_buffer.extend_from_slice(data);
        while let Some(position) = self.client_buffer.iter().position(|byte| *byte == b'\n') {
            let line: Vec<u8> = self
                .client_buffer
                .drain(..=position)
                .take(position)
                .collect();
            if line.len() + 1 > MAX_LINE {
                return Err(Failure::value("Mensagem Stratum excede o limite"));
            }
            if blank(&line) {
                continue;
            }
            let message: Value = serde_json::from_slice(&line).map_err(|_| {
                Failure::value(
                    "Protocolo Stratum não reconhecido; nenhum redirecionamento permitido",
                )
            })?;
            let (rewritten, is_auth) =
                rewrite_message(&message, &proxy.wallet, &self.active, self.authenticated)
                    .map_err(Failure::value)?;
            let identifier = message.get("id").cloned().unwrap_or(Value::Null);
            let key = id_key(&identifier);
            if let Some(key) = &key {
                if self.pending.contains(key) || self.pending.len() >= 256 {
                    return Err(Failure::value(
                        "Identificador Stratum duplicado ou excesso de pedidos pendentes",
                    ));
                }
                self.pending.push(key.clone());
            }
            if is_auth {
                if self.authenticated || key.is_none() {
                    return Err(Failure::value(
                        "Autorização duplicada ou sem identificador verificável",
                    ));
                }
                self.auth_id = Some(identifier.clone());
            }
            if message["method"] == json!("mining.submit") {
                if self.role == "support" && !self.pool_authorized {
                    return Err(Failure::value("Contribuição sem autorização confirmada"));
                }
                let duplicated = key
                    .as_ref()
                    .is_some_and(|key| self.shares.iter().any(|(item, _)| item == key));
                let is_auth_id = self
                    .auth_id
                    .as_ref()
                    .is_some_and(|auth| same_id(&identifier, auth));
                match key {
                    Some(key) if !is_auth_id && !duplicated && self.shares.len() < 256 => {
                        self.shares.push((key, self.role));
                    }
                    _ => {
                        return Err(Failure::value(
                            "Identificador de share inválido ou pendente",
                        ));
                    }
                }
            }
            let mut out = dumps_ascii(&rewritten).into_bytes();
            out.push(b'\n');
            proxy.send(upstream, &out)?;
            self.authenticated = self.authenticated || is_auth;
        }
        if self.client_buffer.len() >= MAX_LINE {
            return Err(Failure::value("Mensagem Stratum excede o limite"));
        }
        Ok(true)
    }
}
