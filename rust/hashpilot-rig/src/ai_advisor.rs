//! `ai_advisor.py` e os consultores de `gpu_agent.py` (`OllamaAdvisor`, `BackgroundAdvisor`).
//!
//! A IA só sugere uma ação do conjunto fixo (`hold`, `clock_up`, ...); nunca envia comandos,
//! clocks nem potências, e o supervisor aplica a sugestão dentro dos próprios limites.
//!
//! Garantias que o port mantém:
//! - o Ollama é local (só loopback), sem proxy do sistema;
//! - só modelos gratuitos na nuvem, com cota local de 40 consultas por dia e 60 s entre elas;
//! - só medições numéricas saem do computador (`telemetry_context`);
//! - a chave fica no Cofre de Credenciais do Windows e nunca aparece em erro, log ou arquivo.

use std::io::Read;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, LazyLock, Mutex};
use std::time::Duration;

use hashpilot_history::pyjson::{encode_spaced, truthy};
use regex::Regex;
use serde_json::{Map, Value, json};

pub const ACTIONS: [&str; 6] = [
    "hold",
    "clock_up",
    "clock_down",
    "power_up",
    "power_down",
    "restore_best",
];
pub const PROVIDERS: [(&str, &str, &str); 3] = [
    (
        "openrouter",
        "OpenRouter · Cloud Free",
        "https://openrouter.ai/settings/keys",
    ),
    (
        "opencode",
        "OpenCode · Cloud Free pelo cliente",
        "https://opencode.ai/docs/",
    ),
    (
        "ollama",
        "Ollama local · usa CPU e RAM",
        "https://ollama.com/download/windows",
    ),
];
pub const SYSTEM: &str = "You advise NVIDIA mining. Return only JSON with exactly action and reason. \
action must be one of hold,clock_up,clock_down,power_up,power_down,restore_best. \
Never return code, commands, numeric settings or tools. Prioritize thermal safety \
and accepted shares. Explain briefly in Portuguese, at most 400 characters.";
const OLLAMA_SYSTEM: &str = "You are a local NVIDIA mining advisor. Return JSON matching the schema. \
Choose one small action from the enum. Never emit commands, clocks, power values or code. \
Prioritize thermal safety and accepted shares. Explain in Portuguese in one sentence.";

/// Erro com o tipo do Python, para o texto (`str(error)`) e o tipo baterem.
#[derive(Clone, Debug, PartialEq)]
pub struct AiError {
    pub kind: &'static str,
    pub message: String,
}

impl AiError {
    pub fn value(message: impl Into<String>) -> Self {
        Self {
            kind: "ValueError",
            message: message.into(),
        }
    }

    fn of(kind: &'static str, message: impl Into<String>) -> Self {
        Self {
            kind,
            message: message.into(),
        }
    }

    /// `json.loads` recusou o texto (`json.JSONDecodeError`, subclasse de `ValueError`).
    fn decode(error: &serde_json::Error) -> Self {
        Self::of("JSONDecodeError", error.to_string())
    }

    /// `f"{type(error).__name__}: {error}"`.
    pub fn describe(&self) -> String {
        format!("{}: {}", self.kind, self.message)
    }
}

impl std::fmt::Display for AiError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.message)
    }
}

pub type AiResult<T> = Result<T, AiError>;

/// `str.isspace()` do Python: o espaço Unicode mais os separadores `\x1c`–`\x1f`.
fn py_space(character: char) -> bool {
    character.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&character)
}

/// `str.strip()`.
pub fn py_strip(text: &str) -> &str {
    text.trim_matches(py_space)
}

fn provider_label(provider: &str) -> &'static str {
    PROVIDERS
        .iter()
        .find(|(name, _, _)| *name == provider)
        .map_or("IA opcional", |(_, label, _)| label)
}

static MODEL: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\A[A-Za-z0-9._:/-]{1,160}\z").expect("fixo"));
static OPENCODE_MODEL: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\Aopencode/[a-z0-9.-]+-free\z").expect("fixo"));
static OLLAMA_MODEL: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"\A[a-zA-Z0-9._:/-]{1,120}\z").expect("fixo"));
static FENCE: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"(?si)\A```(?:json)?\s*\n(.*?)\n```\z").expect("fixo"));

/// `configuration(settings)`: provedor, modelo e consentimento validados.
pub fn configuration(settings: &Value) -> AiResult<Value> {
    let Some(map) = settings.as_object() else {
        return Err(AiError::value("Configuração de IA precisa ser um objeto"));
    };
    let empty = json!({});
    let raw = map.get("ai").unwrap_or(&empty);
    let Some(raw) = raw.as_object() else {
        return Err(AiError::value("A seção ai precisa ser um objeto"));
    };
    let fallback = if map.get("ai_enabled").is_some_and(truthy) {
        "ollama"
    } else {
        "openrouter"
    };
    let provider = raw
        .get("provider")
        .cloned()
        .unwrap_or_else(|| json!(fallback));
    let Some(provider) = provider
        .as_str()
        .filter(|name| PROVIDERS.iter().any(|(item, _, _)| item == name))
    else {
        return Err(AiError::value("Provedor de IA inválido"));
    };
    if let Some(model) = raw.get("model")
        && !model.is_null()
        && !model.is_string()
    {
        return Err(AiError::value("Modelo precisa ser texto"));
    }
    let model = match raw.get("model").and_then(Value::as_str) {
        Some(text) if !text.is_empty() => text.to_string(),
        _ => match provider {
            "ollama" => "qwen2.5:0.5b",
            "openrouter" => "openrouter/free",
            _ => "opencode/glm-5.3-flash-free",
        }
        .to_string(),
    };
    if !MODEL.is_match(&model) {
        return Err(AiError::value("Identificador de modelo inválido"));
    }
    if provider == "openrouter" && model != "openrouter/free" && !model.ends_with(":free") {
        return Err(AiError::value(
            "Selecione openrouter/free ou um modelo :free",
        ));
    }
    if provider == "opencode" && !OPENCODE_MODEL.is_match(&model) {
        return Err(AiError::value("OpenCode exige um modelo opencode/...-free"));
    }
    Ok(
        json!({"provider": provider, "model": model, "cloud_consent": raw.get("cloud_consent") == Some(&Value::Bool(true)),
        "enabled": map.get("ai_enabled") == Some(&Value::Bool(true)), "free_only": true}),
    )
}

/// `telemetry_context`: só medições numéricas finitas e não negativas saem do computador.
pub fn telemetry_context(context: &Value) -> AiResult<Value> {
    const SAMPLE: [&str; 10] = [
        "temperature_c",
        "power_w",
        "power_limit_w",
        "clock_mhz",
        "utilization_pct",
        "temperature.gpu",
        "power.draw",
        "power.limit",
        "clocks.gr",
        "utilization.gpu",
    ];
    const BENCHMARK: [&str; 7] = [
        "mean_hashrate_ths",
        "mean_power_w",
        "efficiency_ths_per_w",
        "independent_hash_samples",
        "invalid_samples",
        "validated",
        "elapsed_seconds",
    ];
    let Some(context) = context.as_object() else {
        return Err(AiError::value("Contexto de IA inválido"));
    };
    let mut result = Map::new();
    for (section, keys) in [("sample", &SAMPLE[..]), ("benchmark", &BENCHMARK[..])] {
        let source = context
            .get(section)
            .filter(|value| truthy(value))
            .and_then(Value::as_object);
        let mut kept = Map::new();
        for key in keys {
            let Some(value) = source.and_then(|source| source.get(*key)) else {
                continue;
            };
            if section == "benchmark" && *key == "validated" && value.is_boolean() {
                kept.insert((*key).into(), value.clone());
                continue;
            }
            if let Some(number) = value.as_f64().filter(|_| value.is_number())
                && number.is_finite()
                && number >= 0.0
            {
                kept.insert((*key).into(), value.clone());
            }
        }
        result.insert(section.into(), Value::Object(kept));
    }
    Ok(Value::Object(result))
}

/// `completion_text`: a resposta final do modelo; raciocínio sem resposta ou corte por
/// limite de geração não viram sugestão.
pub fn completion_text(body: &Value) -> AiResult<String> {
    let choice = body
        .get("choices")
        .and_then(Value::as_array)
        .filter(|choices| !choices.is_empty())
        .and_then(|choices| choices[0].as_object())
        .ok_or_else(|| AiError::value("Provedor sem resposta final utilizável"))?;
    if choice.get("finish_reason") == Some(&json!("length")) {
        return Err(AiError::value(
            "Modelo atingiu o limite de geração; nenhuma sugestão aplicada",
        ));
    }
    let content = choice
        .get("message")
        .and_then(Value::as_object)
        .and_then(|message| message.get("content"));
    let content = match content {
        Some(Value::Array(parts)) => Some(
            parts
                .iter()
                .filter_map(Value::as_object)
                .filter(|part| part.get("type") == Some(&json!("text")))
                .filter_map(|part| part.get("text").and_then(Value::as_str))
                .collect::<String>(),
        ),
        Some(Value::String(text)) => Some(text.clone()),
        _ => None,
    };
    let Some(content) = content.filter(|text| !py_strip(text).is_empty()) else {
        return Err(AiError::value(
            "Modelo não devolveu uma resposta final JSON",
        ));
    };
    let content = py_strip(&content);
    Ok(match FENCE.captures(content) {
        Some(found) => py_strip(&found[1]).to_string(),
        None => content.to_string(),
    })
}

/// `OllamaAdvisor.parse`: exatamente `action` (do conjunto) e `reason` (1 a 400 caracteres).
pub fn parse_advice(text: &Value) -> AiResult<Value> {
    let Some(text) = text.as_str() else {
        return Err(AiError::value("Resposta da IA precisa ser texto JSON"));
    };
    let value: Value = serde_json::from_str(text).map_err(|error| AiError::decode(&error))?;
    let valid_shape = value.as_object().is_some_and(|map| {
        map.len() == 2
            && map.contains_key("reason")
            && map
                .get("action")
                .and_then(Value::as_str)
                .is_some_and(|action| ACTIONS.contains(&action))
    });
    if !valid_shape {
        return Err(AiError::value("Resposta da IA fora do conjunto permitido"));
    }
    let reason_ok = value["reason"]
        .as_str()
        .is_some_and(|reason| (1..=400).contains(&py_strip(reason).chars().count()));
    if !reason_ok {
        return Err(AiError::value("Justificativa da IA inválida"));
    }
    Ok(value)
}

/// `json.dumps(valor)` padrão: separadores `", "`/`": "` e `ensure_ascii=True`.
pub fn dumps_default(value: &Value) -> String {
    let mut out = String::new();
    for character in encode_spaced(value).chars() {
        if (character as u32) < 0x7f {
            out.push(character);
        } else {
            let mut units = [0u16; 2];
            for unit in character.encode_utf16(&mut units) {
                out.push_str(&format!("\\u{unit:04x}"));
            }
        }
    }
    out
}

/// `float(x)` do Python para `int`, `float` e texto numérico.
fn py_float(value: &Value) -> AiResult<f64> {
    match value {
        Value::Bool(flag) => Ok(f64::from(u8::from(*flag))),
        Value::Number(number) => Ok(number.as_f64().unwrap_or(f64::NAN)),
        Value::String(text) => hashpilot_core::numbers::parse_py_float(text).ok_or_else(|| {
            AiError::value(format!(
                "could not convert string to float: {}",
                hashpilot_history::pyjson::repr_str(text)
            ))
        }),
        _ => Err(AiError::of(
            "TypeError",
            "float() argument must be a string or a real number",
        )),
    }
}

/// `urlparse(endpoint)`: esquema (minúsculo), usuário/senha e host (minúsculo, sem colchetes).
fn url_parts(endpoint: &str) -> (String, bool, Option<String>) {
    let (scheme, rest) = endpoint
        .split_once("://")
        .map_or(("", endpoint), |(scheme, rest)| (scheme, rest));
    let netloc = rest.split(['/', '?', '#']).next().unwrap_or("");
    let (userinfo, hostport) = netloc
        .rsplit_once('@')
        .map_or((None, netloc), |(user, host)| (Some(user), host));
    let host = if let Some(inner) = hostport.strip_prefix('[') {
        inner.split(']').next().unwrap_or("").to_string()
    } else {
        hostport.split(':').next().unwrap_or("").to_string()
    };
    let credentials = userinfo.is_some_and(|user| !user.is_empty());
    let host = (!host.is_empty()).then(|| host.to_lowercase());
    (scheme.to_lowercase(), credentials, host)
}

/// `OllamaAdvisor`: só loopback, só ações JSON; inferência na CPU para não disputar a GPU.
#[derive(Clone, Debug, PartialEq)]
pub struct OllamaAdvisor {
    pub endpoint: String,
    pub model: String,
    pub timeout: f64,
    /// `timeout` como o Python o guarda: `max(1, min(30, x))` devolve o `int` nos extremos.
    pub timeout_repr: Value,
}

impl OllamaAdvisor {
    pub fn new(model: &Value, endpoint: &str, timeout: &Value) -> AiResult<Self> {
        let (scheme, credentials, host) = url_parts(endpoint);
        if scheme != "http"
            || !matches!(host.as_deref(), Some("localhost" | "127.0.0.1" | "::1"))
            || credentials
        {
            return Err(AiError::value(
                "A IA deve usar o Ollama local, sem API externa",
            ));
        }
        let Some(model) = model.as_str().filter(|text| OLLAMA_MODEL.is_match(text)) else {
            return Err(AiError::value("Nome de modelo inválido"));
        };
        if model.ends_with(":cloud") || model.ends_with("-cloud") {
            return Err(AiError::value("Ollama local não aceita modelos cloud"));
        }
        let raw = py_float(timeout)?;
        let (timeout, timeout_repr) = if raw.is_nan() || raw >= 30.0 {
            (30.0, json!(30))
        } else if raw <= 1.0 {
            (1.0, json!(1))
        } else {
            (raw, json!(raw))
        };
        Ok(Self {
            endpoint: endpoint.trim_end_matches('/').to_string(),
            model: model.to_string(),
            timeout,
            timeout_repr,
        })
    }

    pub fn local(model: &str) -> AiResult<Self> {
        Self::new(&json!(model), "http://127.0.0.1:11434", &json!(12))
    }

    /// O corpo enviado ao `/api/generate`.
    pub fn payload(&self, context: &Value) -> Value {
        json!({"model": self.model, "stream": false, "format": {"type": "object", "properties": {
                "action": {"type": "string", "enum": ACTIONS}, "reason": {"type": "string"}},
                "required": ["action", "reason"], "additionalProperties": false},
            "keep_alive": 0, "options": {"num_gpu": 0, "num_ctx": 2048, "num_predict": 160, "temperature": 0},
            "system": OLLAMA_SYSTEM, "prompt": encode_spaced(context)})
    }

    pub fn advise(&self, context: &Value) -> AiResult<Value> {
        let agent: ureq::Agent = ureq::Agent::config_builder()
            .proxy(None)
            .timeout_global(Some(Duration::from_secs_f64(self.timeout)))
            .http_status_as_error(false)
            .build()
            .into();
        let response = agent
            .post(&format!("{}/api/generate", self.endpoint))
            .header("Content-Type", "application/json")
            .send(dumps_default(&self.payload(context)).as_bytes())
            .map_err(|error| AiError::of("URLError", format!("<urlopen error {error}>")))?;
        let status = response.status();
        if !status.is_success() {
            return Err(AiError::of(
                "HTTPError",
                format!(
                    "HTTP Error {}: {}",
                    status.as_u16(),
                    status.canonical_reason().unwrap_or("")
                ),
            ));
        }
        let mut body = Vec::new();
        response
            .into_body()
            .into_reader()
            .take(65537)
            .read_to_end(&mut body)
            .map_err(|error| AiError::of("OSError", error.to_string()))?;
        if body.len() > 65536 {
            return Err(AiError::value("Resposta da IA excede o limite"));
        }
        let decoded: Value =
            serde_json::from_slice(&body).map_err(|error| AiError::decode(&error))?;
        let Some(text) = decoded.get("response") else {
            return Err(AiError::of("KeyError", "'response'"));
        };
        parse_advice(text)
    }
}

/// `RequestBudget`: cota local da nuvem, compartilhada por todos os controladores e pela
/// interface, contando também as tentativas que falharam.
pub fn reserve_budget(state_dir: &Path, now: &Value) -> AiResult<()> {
    let stamp = now.as_f64().filter(|_| now.is_number());
    let Some(stamp) =
        stamp.filter(|value| value.is_finite() && (0.0..=253402300799.0).contains(value))
    else {
        return Err(AiError::value("Horário inválido para a cota de IA"));
    };
    let day = chrono_day(stamp);
    std::fs::create_dir_all(state_dir)
        .map_err(|error| AiError::of("OSError", error.to_string()))?;
    let sql = |error: rusqlite::Error| AiError::of("OperationalError", error.to_string());
    let mut db = rusqlite::Connection::open(state_dir.join("ai-requests.sqlite3")).map_err(sql)?;
    db.busy_timeout(Duration::from_secs(2)).map_err(sql)?;
    db.execute(
        "CREATE TABLE IF NOT EXISTS requests(day TEXT,stamp REAL)",
        [],
    )
    .map_err(sql)?;
    let transaction = db
        .transaction_with_behavior(rusqlite::TransactionBehavior::Immediate)
        .map_err(sql)?;
    let count: i64 = transaction
        .query_row("SELECT count(*) FROM requests WHERE day=?", [&day], |row| {
            row.get(0)
        })
        .map_err(sql)?;
    let last: Option<f64> = transaction
        .query_row("SELECT max(stamp) FROM requests", [], |row| row.get(0))
        .map_err(sql)?;
    if count >= 40 {
        return Err(AiError::value(
            "Limite local de 40 consultas cloud/dia atingido",
        ));
    }
    if last.is_some_and(|last| stamp - last < 60.0) {
        return Err(AiError::value(
            "Intervalo global da IA: aguarde 60 segundos",
        ));
    }
    transaction
        .execute(
            "INSERT INTO requests VALUES(?,?)",
            rusqlite::params![day, stamp],
        )
        .map_err(sql)?;
    transaction.commit().map_err(sql)
}

/// Data UTC (`%Y-%m-%d`) de um horário Unix, pelo algoritmo civil de Howard Hinnant.
fn chrono_day(stamp: f64) -> String {
    let days = (stamp / 86400.0).floor() as i64;
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    format!("{year:04}-{month:02}-{day:02}")
}

/// `request_json`: sem proxy do sistema e sem redirecionamento; o erro nunca leva corpo,
/// URL, cabeçalhos nem a chave.
pub fn request_json(url: &str, payload: Option<&Value>, key: Option<&str>) -> AiResult<Value> {
    if let Some(key) = key
        && (key.is_empty()
            || key
                .chars()
                .any(|c| py_space(c) || !(33..=126).contains(&(c as u32))))
    {
        return Err(AiError::value("Chave inválida"));
    }
    let agent: ureq::Agent = ureq::Agent::config_builder()
        .proxy(None)
        .max_redirects(0)
        .timeout_global(Some(Duration::from_secs(20)))
        .http_status_as_error(false)
        .build()
        .into();
    let offline = || AiError::value("Provedor sem conexão ou tempo de resposta excedido");
    let response = match payload {
        Some(payload) => {
            let mut request = agent
                .post(url)
                .header("Content-Type", "application/json")
                .header("User-Agent", "HashPilot/0.3");
            if let Some(key) = key.filter(|key| !key.is_empty()) {
                request = request.header("Authorization", &format!("Bearer {key}"));
            }
            request.send(dumps_default(payload).as_bytes())
        }
        None => {
            let mut request = agent
                .get(url)
                .header("Content-Type", "application/json")
                .header("User-Agent", "HashPilot/0.3");
            if let Some(key) = key.filter(|key| !key.is_empty()) {
                request = request.header("Authorization", &format!("Bearer {key}"));
            }
            request.call()
        }
    }
    .map_err(|_| offline())?;
    let status = response.status().as_u16();
    if (300..400).contains(&status) {
        return Err(AiError::value("Redirecionamento do provedor recusado"));
    }
    if status >= 400 {
        return Err(AiError::value(match status {
            401 => "Autenticação recusada; confira sua chave".to_string(),
            402 => "Créditos/cota indisponíveis; sem fallback pago".to_string(),
            429 => "Limite gratuito atingido; controle adaptativo permanece ativo".to_string(),
            other => format!("Provedor indisponível (HTTP {other})"),
        }));
    }
    let mut body = Vec::new();
    response
        .into_body()
        .into_reader()
        .take(2_000_001)
        .read_to_end(&mut body)
        .map_err(|_| offline())?;
    if body.len() > 2_000_000 {
        return Err(AiError::value("Resposta do provedor excedeu o limite"));
    }
    serde_json::from_slice(&body).map_err(|_| AiError::value("Resposta JSON inválida do provedor"))
}

/// `credential(provider)`: lê a chave do Cofre de Credenciais do Windows (`HashPilot/AI/...`).
/// `None` quando não há chave salva. O valor nunca é registrado.
pub fn credential(provider: &str) -> AiResult<Option<String>> {
    if !PROVIDERS.iter().any(|(name, _, _)| *name == provider) {
        return Err(AiError::value("Provedor inválido"));
    }
    read_credential(&format!("HashPilot/AI/{provider}"))
}

#[cfg(windows)]
#[allow(unsafe_code)]
fn read_credential(target: &str) -> AiResult<Option<String>> {
    use windows_sys::Win32::Foundation::GetLastError;
    use windows_sys::Win32::Security::Credentials::{CREDENTIALW, CredFree, CredReadW};
    let wide: Vec<u16> = target.encode_utf16().chain(std::iter::once(0)).collect();
    let mut pointer: *mut CREDENTIALW = std::ptr::null_mut();
    // SAFETY: alvo terminado em zero; o ponteiro devolvido é liberado por CredFree.
    let read = unsafe { CredReadW(wide.as_ptr(), 1, 0, &mut pointer) };
    if read == 0 {
        // SAFETY: leitura do erro da última chamada nesta thread.
        let code = unsafe { GetLastError() };
        return if code == 1168 {
            Ok(None)
        } else {
            Err(AiError::value(
                "Cofre do Windows indisponível ou acesso negado",
            ))
        };
    }
    // SAFETY: CredReadW teve sucesso; o blob tem CredentialBlobSize bytes.
    let blob = unsafe {
        let credential = &*pointer;
        let bytes = std::slice::from_raw_parts(
            credential.CredentialBlob,
            credential.CredentialBlobSize as usize,
        )
        .to_vec();
        CredFree(pointer.cast());
        bytes
    };
    String::from_utf8(blob)
        .map(Some)
        .map_err(|_| AiError::of("UnicodeDecodeError", "chave fora do UTF-8"))
}

#[cfg(not(windows))]
fn read_credential(_target: &str) -> AiResult<Option<String>> {
    Err(AiError::value("Credenciais exigem o cofre do Windows"))
}

/// O pedido ao cliente OpenCode (`subprocess.run`).
pub struct OpencodeRun<'a> {
    pub executable: &'a Path,
    pub arguments: Vec<String>,
    pub directory: &'a Path,
    pub environment: Vec<(String, String)>,
    pub timeout: Duration,
}

/// Tudo o que sai do processo: a rede, o cofre, o cliente OpenCode, a cota e o relógio.
/// O teste troca cada peça por um roteiro; o serviço usa `Effects::real()`.
#[allow(clippy::type_complexity)]
pub struct Effects {
    pub request_json:
        Box<dyn Fn(&str, Option<&Value>, Option<&str>) -> AiResult<Value> + Send + Sync>,
    pub credential: Box<dyn Fn(&str) -> AiResult<Option<String>> + Send + Sync>,
    pub which: Box<dyn Fn(&str) -> Option<PathBuf> + Send + Sync>,
    pub run: Box<dyn Fn(&OpencodeRun) -> AiResult<(i32, Vec<u8>)> + Send + Sync>,
    pub reserve: Box<dyn Fn(&Path) -> AiResult<()> + Send + Sync>,
    pub ollama: Box<dyn Fn(&OllamaAdvisor, &Value) -> AiResult<Value> + Send + Sync>,
    pub now: Box<dyn Fn() -> f64 + Send + Sync>,
}

fn wall_clock() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0.0, |elapsed| elapsed.as_secs_f64())
}

/// `shutil.which(name)` no Windows: procura o nome exato nas pastas do `PATH`.
fn which(name: &str) -> Option<PathBuf> {
    std::env::split_paths(&std::env::var_os("PATH")?)
        .map(|directory| directory.join(name))
        .find(|candidate| candidate.is_file())
}

/// `subprocess.run(..., stdout=arquivo, timeout=40)`: a saída vai para um arquivo, e no
/// prazo estourado o processo é encerrado.
fn run_opencode(request: &OpencodeRun) -> AiResult<(i32, Vec<u8>)> {
    let output_path = request.directory.join("opencode-output.bin");
    let output = std::fs::File::create(&output_path)
        .map_err(|error| AiError::of("OSError", error.to_string()))?;
    let mut command = std::process::Command::new(request.executable);
    command
        .args(&request.arguments)
        .current_dir(request.directory)
        .envs(request.environment.iter().map(|(key, value)| (key, value)))
        .stdin(std::process::Stdio::null())
        .stdout(output)
        .stderr(std::process::Stdio::null());
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        command.creation_flags(0x0800_0000);
    }
    let mut child = command
        .spawn()
        .map_err(|error| AiError::of("OSError", error.to_string()))?;
    let started = std::time::Instant::now();
    let code = loop {
        if let Some(status) = child
            .try_wait()
            .map_err(|error| AiError::of("OSError", error.to_string()))?
        {
            break status.code().unwrap_or(1);
        }
        if started.elapsed() >= request.timeout {
            let _ = child.kill();
            let _ = child.wait();
            return Err(AiError::value("OpenCode excedeu o tempo de resposta"));
        }
        std::thread::sleep(Duration::from_millis(50));
    };
    let mut raw = Vec::new();
    std::fs::File::open(&output_path)
        .and_then(|file| file.take(65537).read_to_end(&mut raw))
        .map_err(|error| AiError::of("OSError", error.to_string()))?;
    Ok((code, raw))
}

impl Effects {
    pub fn real() -> Self {
        Self {
            request_json: Box::new(request_json),
            credential: Box::new(credential),
            which: Box::new(which),
            run: Box::new(run_opencode),
            reserve: Box::new(|state_dir| reserve_budget(state_dir, &json!(wall_clock()))),
            ollama: Box::new(|advisor, context| advisor.advise(context)),
            now: Box::new(wall_clock),
        }
    }
}

/// Pasta temporária `HashPilot-advice-*`, apagada ao sair.
struct TemporaryDirectory(PathBuf);

impl TemporaryDirectory {
    fn new() -> AiResult<Self> {
        let path = std::env::temp_dir().join(format!(
            "HashPilot-advice-{}",
            crate::manager::random_hex(8)
        ));
        std::fs::create_dir_all(&path)
            .map_err(|error| AiError::of("OSError", error.to_string()))?;
        Ok(Self(path))
    }
}

impl Drop for TemporaryDirectory {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// `RoutedAdvisor`: escolhe Ollama, OpenRouter ou OpenCode pela configuração atual.
pub struct RoutedAdvisor {
    pub state_dir: PathBuf,
    pub settings_path: PathBuf,
    catalog: Mutex<Option<(Vec<Value>, f64)>>,
    label: Mutex<String>,
    pub config_override: Option<Value>,
    pub key_override: Option<String>,
    effects: Effects,
}

impl RoutedAdvisor {
    pub fn new(
        state_dir: &Path,
        settings_path: Option<&Path>,
        config_override: Option<Value>,
        key_override: Option<String>,
        effects: Effects,
    ) -> Self {
        Self {
            state_dir: state_dir.to_path_buf(),
            settings_path: settings_path
                .map_or_else(|| state_dir.join("settings.json"), Path::to_path_buf),
            catalog: Mutex::new(None),
            label: Mutex::new("IA opcional".into()),
            config_override,
            key_override,
            effects,
        }
    }

    pub fn settings(&self) -> AiResult<Value> {
        if let Some(config) = &self.config_override {
            return Ok(config.clone());
        }
        match std::fs::read(&self.settings_path) {
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(json!({})),
            Err(error) => Err(AiError::of("OSError", error.to_string())),
            Ok(raw) => {
                let text = String::from_utf8_lossy(&raw);
                let value: Value =
                    serde_json::from_str(text.strip_prefix('\u{feff}').unwrap_or(&text))
                        .map_err(|error| AiError::decode(&error))?;
                if value.is_object() {
                    Ok(value)
                } else {
                    Err(AiError::value("Configuração de IA inválida"))
                }
            }
        }
    }

    pub fn enabled(&self) -> bool {
        self.settings()
            .is_ok_and(|settings| settings.get("ai_enabled") == Some(&Value::Bool(true)))
    }

    pub fn label(&self) -> String {
        self.label.lock().expect("trava").clone()
    }

    pub fn advise(&self, context: &Value) -> AiResult<Value> {
        let config = configuration(&self.settings()?)?;
        if config["enabled"] != json!(true) {
            return Err(AiError::value("IA desativada; política adaptativa ativa"));
        }
        let provider = config["provider"].as_str().expect("validado");
        *self.label.lock().expect("trava") = provider_label(provider).into();
        let safe = telemetry_context(context)?;
        if provider == "ollama" {
            let advisor = OllamaAdvisor::local(config["model"].as_str().expect("validado"))?;
            return (self.effects.ollama)(&advisor, &safe);
        }
        if config["cloud_consent"] != json!(true) {
            return Err(AiError::value(
                "Autorize o envio de telemetria resumida à nuvem",
            ));
        }
        if provider == "opencode" {
            return self.opencode(&config, &safe);
        }
        let key = match &self.key_override {
            Some(key) if !key.is_empty() => Some(key.clone()),
            _ => (self.effects.credential)("openrouter")?,
        };
        let Some(key) = key.filter(|key| !key.is_empty()) else {
            return Err(AiError::value(
                "Configure sua chave OpenRouter no assistente de IA",
            ));
        };
        let model = config["model"].as_str().expect("validado");
        let catalog = {
            let cached = self.catalog.lock().expect("trava").clone();
            match cached {
                Some((catalog, at))
                    if !catalog.is_empty() && (self.effects.now)() - at <= 300.0 =>
                {
                    catalog
                }
                _ => {
                    let response = (self.effects.request_json)(
                        "https://openrouter.ai/api/v1/models",
                        None,
                        None,
                    )?;
                    let data = response
                        .get("data")
                        .ok_or_else(|| AiError::of("KeyError", "'data'"))?;
                    let catalog = data.as_array().cloned().unwrap_or_default();
                    *self.catalog.lock().expect("trava") =
                        Some((catalog.clone(), (self.effects.now)()));
                    catalog
                }
            }
        };
        let selected = catalog
            .iter()
            .find(|item| item.get("id") == Some(&json!(model)));
        let free = selected
            .and_then(|item| item.get("pricing"))
            .is_some_and(|pricing| {
                ["prompt", "completion"].iter().all(|key| {
                    pricing.get(*key).and_then(|value| py_float(value).ok()) == Some(0.0)
                })
            });
        if !free {
            return Err(AiError::value(
                "Modelo não confirmado como gratuito no catálogo atual",
            ));
        }
        (self.effects.reserve)(&self.state_dir)?;
        let body = (self.effects.request_json)(
            "https://openrouter.ai/api/v1/chat/completions",
            Some(
                &json!({"model": model, "messages": [{"role": "system", "content": SYSTEM},
                    {"role": "user", "content": dumps_default(&safe)}],
                "temperature": 0, "max_tokens": 4096, "reasoning": {"effort": "low"},
                "provider": {"max_price": {"prompt": 0, "completion": 0, "request": 0}},
                "response_format": {"type": "json_object"}}),
            ),
            Some(&key),
        )?;
        parse_advice(&json!(completion_text(&body)?))
    }

    /// O cliente OpenCode oficial, numa pasta vazia, com todas as permissões negadas: um
    /// agente de código nunca roda contra o rig nem contra o repositório do usuário.
    fn opencode(&self, config: &Value, context: &Value) -> AiResult<Value> {
        let Some(executable) = (self.effects.which)("opencode.exe") else {
            return Err(AiError::value(
                "Instale o OpenCode oficial e faça login; cliente não encontrado",
            ));
        };
        let response =
            (self.effects.request_json)("https://opencode.ai/zen/v1/models", None, None)?;
        let catalog = response
            .get("data")
            .ok_or_else(|| AiError::of("KeyError", "'data'"))?;
        let full = config["model"].as_str().expect("validado");
        let model = full.split_once('/').map_or("", |(_, model)| model);
        let listed = catalog.as_array().is_some_and(|items| {
            items
                .iter()
                .any(|item| item.get("id") == Some(&json!(model)))
        });
        if !listed {
            return Err(AiError::value(
                "Modelo gratuito removido do catálogo OpenCode",
            ));
        }
        (self.effects.reserve)(&self.state_dir)?;
        let directory = TemporaryDirectory::new()?;
        let conf = json!({"model": full, "small_model": full, "permission": {"*": "deny"}, "plugin": [], "mcp": {},
            "instructions": [], "share": "disabled",
            "agent": {"hashpilot": {"mode": "primary", "prompt": SYSTEM, "steps": 1, "permission": {"*": "deny"}}}});
        let environment = vec![
            (
                "OPENCODE_CONFIG_DIR".to_string(),
                directory.0.to_string_lossy().into_owned(),
            ),
            ("OPENCODE_DISABLE_CLAUDE_CODE".into(), "1".into()),
            ("OPENCODE_DISABLE_DEFAULT_PLUGINS".into(), "1".into()),
            ("OPENCODE_DISABLE_AUTOUPDATE".into(), "1".into()),
            ("OPENCODE_DISABLE_LSP_DOWNLOAD".into(), "1".into()),
            ("OPENCODE_PERMISSION".into(), "{\"*\":\"deny\"}".into()),
            ("OPENCODE_CONFIG_CONTENT".into(), dumps_default(&conf)),
        ];
        let arguments = vec![
            "--pure".to_string(),
            "run".into(),
            "--format".into(),
            "json".into(),
            "--agent".into(),
            "hashpilot".into(),
            "--model".into(),
            full.into(),
            "--title".into(),
            "HashPilot telemetry".into(),
            format!("{SYSTEM}\n{}", dumps_default(context)),
        ];
        let (code, raw) = (self.effects.run)(&OpencodeRun {
            executable: &executable,
            arguments,
            directory: &directory.0,
            environment,
            timeout: Duration::from_secs(40),
        })?;
        if code != 0 || raw.len() > 65536 {
            return Err(AiError::value(
                "OpenCode indisponível; confira login/modelo gratuito",
            ));
        }
        let text = String::from_utf8(raw)
            .map_err(|_| AiError::of("UnicodeDecodeError", "saída fora do UTF-8"))?;
        let mut parts = String::new();
        for line in splitlines(&text) {
            let event: Value =
                serde_json::from_str(line).map_err(|error| AiError::decode(&error))?;
            let Some(event) = event.as_object() else {
                return Err(AiError::value("Evento OpenCode inválido"));
            };
            if event.get("type") == Some(&json!("text")) {
                let Some(text) = event
                    .get("part")
                    .and_then(|part| part.get("text"))
                    .and_then(Value::as_str)
                else {
                    return Err(AiError::value("Resposta OpenCode inválida"));
                };
                parts.push_str(text);
            }
        }
        parse_advice(&json!(parts))
    }
}

/// `str.splitlines()`.
fn splitlines(text: &str) -> Vec<&str> {
    crate::nvidia::splitlines(text)
}

/// O que o `BackgroundAdvisor` consulta: o `RoutedAdvisor` (com configuração viva) ou um
/// consultor fixo.
pub trait Advice: Send + Sync {
    fn advise(&self, context: &Value) -> AiResult<Value>;
    fn label(&self) -> String;
    /// `Some(configuração)` quando é o consultor roteado (`isinstance(..., RoutedAdvisor)`).
    fn routed_settings(&self) -> Option<AiResult<Value>> {
        None
    }
}

impl Advice for RoutedAdvisor {
    fn advise(&self, context: &Value) -> AiResult<Value> {
        RoutedAdvisor::advise(self, context)
    }

    fn label(&self) -> String {
        RoutedAdvisor::label(self)
    }

    fn routed_settings(&self) -> Option<AiResult<Value>> {
        Some(self.settings())
    }
}

#[derive(Default)]
struct BackgroundState {
    last_started: Option<f64>,
    completed_at: Option<f64>,
    advice: Option<Value>,
    error: Option<String>,
    config_signature: Option<String>,
    revision: u64,
}

/// `BackgroundAdvisor`: no máximo uma inferência por minuto (uma por hora na nuvem depois
/// de uma resposta); o laço de sensores e controle nunca espera a IA.
pub struct BackgroundAdvisor {
    advisor: Arc<dyn Advice>,
    interval: f64,
    state: Arc<Mutex<BackgroundState>>,
    running: Arc<AtomicBool>,
    thread: Option<std::thread::JoinHandle<()>>,
    clock: Arc<dyn Fn() -> f64 + Send + Sync>,
}

/// `str(True)`, `str(None)`, `str(1)`: o texto do Python na assinatura da configuração.
fn py_str(value: Option<&Value>) -> String {
    match value {
        None | Some(Value::Null) => "None".into(),
        Some(Value::Bool(true)) => "True".into(),
        Some(Value::Bool(false)) => "False".into(),
        Some(Value::String(text)) => text.clone(),
        Some(other) => encode_spaced(other),
    }
}

/// `json.dumps(valor, sort_keys=True)`.
fn sorted_dumps(value: &Value) -> String {
    fn sort(value: &Value) -> Value {
        match value {
            Value::Object(map) => {
                let mut keys: Vec<&String> = map.keys().collect();
                keys.sort();
                Value::Object(
                    keys.into_iter()
                        .map(|key| (key.clone(), sort(&map[key])))
                        .collect(),
                )
            }
            Value::Array(items) => Value::Array(items.iter().map(sort).collect()),
            other => other.clone(),
        }
    }
    dumps_default(&sort(value))
}

impl BackgroundAdvisor {
    pub fn new(
        advisor: Arc<dyn Advice>,
        interval_seconds: f64,
        clock: Arc<dyn Fn() -> f64 + Send + Sync>,
    ) -> Self {
        Self {
            advisor,
            interval: interval_seconds.max(60.0),
            state: Arc::new(Mutex::new(BackgroundState::default())),
            running: Arc::new(AtomicBool::new(false)),
            thread: None,
            clock,
        }
    }

    /// Espera a consulta em andamento (só para testes determinísticos).
    pub fn join(&mut self) {
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }

    pub fn poll(&mut self, context: &Value, now: Option<f64>) -> Value {
        let stamp = now.unwrap_or_else(|| (self.clock)());
        let alive = self.running.load(Ordering::SeqCst);
        let mut state = self.state.lock().expect("trava");
        let mut cloud = false;
        if let Some(settings) = self.advisor.routed_settings() {
            let Ok(config) = settings else {
                return json!({"advice": null, "status": "unavailable: configuração de IA inválida", "in_flight": alive});
            };
            let empty = json!({});
            let signature = sorted_dumps(config.get("ai").unwrap_or(&empty))
                + &py_str(config.get("ai_enabled"));
            if state.config_signature.as_deref() != Some(&signature) {
                state.config_signature = Some(signature);
                state.revision += 1;
                state.advice = None;
                state.error = None;
                state.completed_at = None;
                state.last_started = None;
            }
            cloud = config
                .get("ai")
                .and_then(Value::as_object)
                .and_then(|ai| ai.get("provider"))
                .is_some_and(|provider| {
                    *provider == json!("openrouter") || *provider == json!("opencode")
                });
        }
        let interval = self.interval.max(if cloud && state.advice.is_some() {
            3600.0
        } else {
            60.0
        });
        let mut alive = alive;
        if !alive
            && state
                .last_started
                .is_none_or(|started| stamp - started >= interval)
        {
            state.last_started = Some(stamp);
            let copied = context.clone();
            let revision = state.revision;
            let (advisor, shared, running, clock) = (
                self.advisor.clone(),
                self.state.clone(),
                self.running.clone(),
                self.clock.clone(),
            );
            running.store(true, Ordering::SeqCst);
            if let Some(previous) = self.thread.take() {
                let _ = previous.join();
            }
            self.thread = Some(
                std::thread::Builder::new()
                    .name("hashpilot-advisor".into())
                    .spawn(move || {
                        let outcome = advisor.advise(&copied);
                        let mut state = shared.lock().expect("trava");
                        if revision == state.revision {
                            match outcome {
                                Ok(advice) => {
                                    state.advice = Some(advice);
                                    state.error = None;
                                }
                                Err(error) => {
                                    state.advice = None;
                                    state.error = Some(error.message.chars().take(160).collect());
                                }
                            }
                            state.completed_at = Some(clock());
                        }
                        running.store(false, Ordering::SeqCst);
                    })
                    .expect("thread do consultor"),
            );
            alive = true;
        }
        let fresh = state
            .completed_at
            .is_some_and(|done| (0.0..=90.0).contains(&(stamp - done)));
        let waiting = state.last_started.map_or(0, |started| {
            ((interval - (stamp - started)).trunc() as i64).max(0)
        });
        let label = self.advisor.label();
        let status = if fresh && state.advice.is_some() {
            label
        } else if alive {
            "pending: IA em segundo plano".into()
        } else if let Some(error) = &state.error {
            format!("unavailable: {error}")
        } else {
            format!("{label} · em espera; próxima consulta em {waiting} s")
        };
        let age = state.completed_at.map_or(Value::Null, |done| {
            let delta = stamp - done;
            if delta > 0.0 { json!(delta) } else { json!(0) }
        });
        json!({"advice": if fresh { state.advice.clone() } else { None }, "status": status, "in_flight": alive,
            "next_request_seconds": waiting, "response_age_seconds": age})
    }
}
