//! `gpu_agent.py`, a parte que não escreve no hardware nem fala com a IA: leituras do
//! `nvidia-smi`, planos limitados de ajuste e o `GPUAgent`, que mede benchmarks e testes
//! de ajuste num SQLite (`gpu-agent.sqlite3`). O banco é o mesmo do Python, com o mesmo
//! esquema e o mesmo texto JSON, para os dois motores lerem o histórico um do outro.
//!
//! A aritmética segue o Python onde o tipo aparece no resultado: `max(0, x)` devolve o
//! inteiro `0`, e `statistics.mean` é a média exata (frações), arredondada uma vez só.

use std::cmp::Ordering;
use std::path::{Path, PathBuf};

use hashpilot_core::numbers::numeric;
use hashpilot_core::thermal::EMERGENCY_C;
use hashpilot_history::pyjson::{encode_indented, encode_spaced, py_eq, truthy};
use rusqlite::types::{Value as SqlValue, ValueRef};
use rusqlite::{Connection, params_from_iter};
use serde_json::{Map, Value, json};

use crate::contribution::Num;
use crate::nvidia::{SmiCommand, csv_row, splitlines};

pub const ACTIONS: [&str; 6] = [
    "hold",
    "clock_up",
    "clock_down",
    "power_up",
    "power_down",
    "restore_best",
];
const UNSTABLE: [&str; 7] = [
    "thermal_or_sensor",
    "stale_sensor",
    "incomplete_sensor",
    "miner_restarted",
    "new_rejected_share",
    "invalid_counters",
    "hardware_error",
];

/// Erro com o tipo do Python (`ValueError`, `RuntimeError`, `TypeError`): o laço de
/// controle grava `str(error)` no status, e os goldens comparam `Tipo: mensagem`.
#[derive(Clone, Debug, PartialEq)]
pub struct AgentError {
    pub kind: &'static str,
    pub message: String,
}

impl AgentError {
    pub fn value(message: impl Into<String>) -> Self {
        Self {
            kind: "ValueError",
            message: message.into(),
        }
    }

    pub fn runtime(message: impl Into<String>) -> Self {
        Self {
            kind: "RuntimeError",
            message: message.into(),
        }
    }

    fn sql(error: rusqlite::Error) -> Self {
        Self {
            kind: "sqlite3.Error",
            message: error.to_string(),
        }
    }

    fn os(error: std::io::Error) -> Self {
        Self {
            kind: "OSError",
            message: error.to_string(),
        }
    }

    /// `f"{type(error).__name__}: {error}"`.
    pub fn describe(&self) -> String {
        format!("{}: {}", self.kind, self.message)
    }
}

impl std::fmt::Display for AgentError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.message)
    }
}

pub type AgentResult<T> = Result<T, AgentError>;

fn wall_clock() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0.0, |elapsed| elapsed.as_secs_f64())
}

fn opt(value: Option<f64>) -> Value {
    value.map_or(Value::Null, |number| json!(number))
}

fn field<'a>(value: &'a Value, key: &str) -> &'a Value {
    static NULL: Value = Value::Null;
    value.get(key).unwrap_or(&NULL)
}

fn num(value: &Value) -> Option<f64> {
    numeric(value)
}

/// `json.dumps(valor)` com os separadores padrão; `ascii` é o `ensure_ascii=True` padrão.
pub fn py_dumps(value: &Value, ascii: bool) -> String {
    let text = encode_spaced(value);
    if !ascii {
        return text;
    }
    let mut out = String::with_capacity(text.len());
    for character in text.chars() {
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

/// `json.loads(Path(p).read_text(encoding="utf-8-sig"))`; `None` em erro de leitura ou JSON.
pub fn read_json(path: &Path) -> Option<Value> {
    let raw = std::fs::read(path).ok()?;
    let text = String::from_utf8(raw).ok()?;
    serde_json::from_str(text.strip_prefix('\u{feff}').unwrap_or(&text)).ok()
}

/// `str.strip()`: o Python também considera espaço os separadores `\x1c`–`\x1f`.
fn py_strip(text: &str) -> &str {
    text.trim_matches(|c: char| c.is_whitespace() || ('\x1c'..='\x1f').contains(&c))
}

/// `int(valor)` do Python para números, booleanos e texto inteiro.
fn py_int(value: &Value) -> Option<i64> {
    match value {
        Value::Bool(flag) => Some(i64::from(*flag)),
        Value::Number(number) => number.as_i64().or_else(|| {
            number
                .as_f64()
                .filter(|v| v.is_finite())
                .map(|v| v.trunc() as i64)
        }),
        Value::String(text) => {
            let text = py_strip(text).replace('_', "");
            text.parse::<i64>().ok()
        }
        _ => None,
    }
}

// --- média exata (`statistics.mean`) -------------------------------------------------

fn big_add_shifted(acc: &mut Vec<u64>, mantissa: u64, shift: u64) {
    let limb = (shift / 64) as usize;
    let bits = shift % 64;
    let wide = u128::from(mantissa) << bits;
    let parts = [wide as u64, (wide >> 64) as u64];
    if acc.len() < limb + 3 {
        acc.resize(limb + 3, 0);
    }
    let mut carry = 0u128;
    let mut index = limb;
    for part in parts {
        let total = u128::from(acc[index]) + u128::from(part) + carry;
        acc[index] = total as u64;
        carry = total >> 64;
        index += 1;
    }
    while carry != 0 {
        if index == acc.len() {
            acc.push(0);
        }
        let total = u128::from(acc[index]) + carry;
        acc[index] = total as u64;
        carry = total >> 64;
        index += 1;
    }
}

fn big_trim(value: &mut Vec<u64>) {
    while value.last() == Some(&0) {
        value.pop();
    }
}

fn big_cmp(left: &[u64], right: &[u64]) -> Ordering {
    left.len()
        .cmp(&right.len())
        .then_with(|| left.iter().rev().cmp(right.iter().rev()))
}

fn big_sub(left: &[u64], right: &[u64]) -> Vec<u64> {
    let mut out = Vec::with_capacity(left.len());
    let mut borrow = 0i128;
    for (index, limb) in left.iter().enumerate() {
        let mut total = i128::from(*limb) - i128::from(*right.get(index).unwrap_or(&0)) - borrow;
        borrow = 0;
        if total < 0 {
            total += 1 << 64;
            borrow = 1;
        }
        out.push(total as u64);
    }
    big_trim(&mut out);
    out
}

fn big_bits(value: &[u64]) -> u64 {
    value.last().map_or(0, |top| {
        (value.len() as u64 - 1) * 64 + u64::from(64 - top.leading_zeros())
    })
}

fn big_shl(value: &[u64], shift: u64) -> Vec<u64> {
    let mut out = vec![0u64; (shift / 64) as usize];
    let bits = shift % 64;
    let mut carry = 0u64;
    for limb in value {
        if bits == 0 {
            out.push(*limb);
        } else {
            out.push((limb << bits) | carry);
            carry = limb >> (64 - bits);
        }
    }
    if carry != 0 {
        out.push(carry);
    }
    big_trim(&mut out);
    out
}

fn big_divrem(value: &[u64], divisor: u64) -> (Vec<u64>, u64) {
    let mut out = vec![0u64; value.len()];
    let mut remainder = 0u128;
    for index in (0..value.len()).rev() {
        let current = (remainder << 64) | u128::from(value[index]);
        out[index] = (current / u128::from(divisor)) as u64;
        remainder = current % u128::from(divisor);
    }
    big_trim(&mut out);
    (out, remainder as u64)
}

fn big_bit(value: &[u64], position: u64) -> bool {
    value
        .get((position / 64) as usize)
        .is_some_and(|limb| limb >> (position % 64) & 1 == 1)
}

fn big_low_nonzero(value: &[u64], count: u64) -> bool {
    let full = (count / 64) as usize;
    if value.iter().take(full).any(|limb| *limb != 0) {
        return true;
    }
    let bits = count % 64;
    bits != 0
        && value
            .get(full)
            .is_some_and(|limb| limb & ((1u64 << bits) - 1) != 0)
}

fn pow2(exponent: i64) -> f64 {
    if exponent >= -1022 {
        f64::from_bits(((exponent + 1023) as u64) << 52)
    } else {
        f64::from_bits(1u64 << (exponent + 1074))
    }
}

/// `statistics.mean(valores)` para `float`: a soma exata dividida por `n`, com um único
/// arredondamento ao par. A soma de floats, ingênua ou compensada, erra em casos como
/// `[0.1, 0.2, 0.3]`.
pub fn exact_mean(values: &[f64]) -> f64 {
    let parts: Vec<(bool, u64, i64)> = values
        .iter()
        .filter(|value| **value != 0.0)
        .map(|value| {
            let bits = value.to_bits();
            let exponent = ((bits >> 52) & 0x7ff) as i64;
            let fraction = bits & ((1u64 << 52) - 1);
            let (mantissa, power) = if exponent == 0 {
                (fraction, -1074)
            } else {
                (fraction | (1u64 << 52), exponent - 1075)
            };
            (bits >> 63 == 1, mantissa, power)
        })
        .collect();
    let Some(minimum) = parts.iter().map(|part| part.2).min() else {
        return 0.0;
    };
    let (mut positive, mut negative) = (Vec::new(), Vec::new());
    for (sign, mantissa, power) in &parts {
        let target = if *sign { &mut negative } else { &mut positive };
        big_add_shifted(target, *mantissa, (*power - minimum) as u64);
    }
    big_trim(&mut positive);
    big_trim(&mut negative);
    let (sign, total) = match big_cmp(&positive, &negative) {
        Ordering::Equal => return 0.0,
        Ordering::Greater => (false, big_sub(&positive, &negative)),
        Ordering::Less => (true, big_sub(&negative, &positive)),
    };
    let scale = 130u64.saturating_sub(big_bits(&total));
    let (quotient, remainder) = big_divrem(&big_shl(&total, scale), values.len() as u64);
    let length = big_bits(&quotient) as i64;
    let exponent = minimum - scale as i64;
    let shift = if length - 1 + exponent >= -1022 {
        length - 53
    } else {
        -1074 - exponent
    } as u64;
    let mut mantissa = {
        let shifted = big_shl(&quotient, 0);
        let limbs = (shift / 64) as usize;
        let bits = shift % 64;
        let low = *shifted.get(limbs).unwrap_or(&0);
        let high = *shifted.get(limbs + 1).unwrap_or(&0);
        if bits == 0 {
            low
        } else {
            (low >> bits) | (high << (64 - bits))
        }
    };
    let half = shift > 0 && big_bit(&quotient, shift - 1);
    let rest = remainder != 0 || (shift > 1 && big_low_nonzero(&quotient, shift - 1));
    if half && (rest || mantissa & 1 == 1) {
        mantissa += 1;
    }
    let power = exponent + shift as i64;
    let magnitude = if power >= -1074 {
        (mantissa as f64) * pow2(power)
    } else {
        (mantissa as f64) * pow2(power + 600) * pow2(-600)
    };
    if sign { -magnitude } else { magnitude }
}

fn mean_of(values: &[f64]) -> Option<f64> {
    (!values.is_empty()).then(|| exact_mean(values))
}

// --- funções puras ---------------------------------------------------------------------

/// `normalize_sample`: as chaves do `read_sample` ou do `nvidia-smi`, todas numéricas.
pub fn normalize_sample(sample: &Value, now: Option<f64>) -> Value {
    let now = now.unwrap_or_else(wall_clock);
    let pairs = [
        ("temperature_c", "temperature.gpu"),
        ("power_w", "power.draw"),
        ("power_limit_w", "power.limit"),
        ("clock_mhz", "clocks.gr"),
        ("utilization_pct", "utilization.gpu"),
    ];
    let mut result = Map::new();
    for (key, alias) in pairs {
        let value = sample
            .get(key)
            .or_else(|| sample.get(alias))
            .unwrap_or(&Value::Null);
        result.insert(key.into(), opt(num(value)));
    }
    let stamp = match sample.get("sampled_at") {
        Some(value) => num(value),
        None => Some(now),
    };
    result.insert("sampled_at".into(), opt(stamp));
    Value::Object(result)
}

/// `thermal_limits(gpu)`: `(suave, rígido)`. 76 e 83 °C são tetos conservadores do
/// produto; um limite menor do driver sempre vence.
pub fn thermal_limits(gpu: &Value) -> (f64, f64) {
    let ceiling = EMERGENCY_C as f64;
    let hard = match num(field(gpu, "temperature_limit_c")) {
        Some(driver) if driver != 0.0 && (50.0..=110.0).contains(&driver) => {
            if driver - 2.0 < ceiling {
                driver - 2.0
            } else {
                ceiling
            }
        }
        _ => ceiling,
    };
    let soft = if hard - 7.0 < 76.0 { hard - 7.0 } else { 76.0 };
    (soft, hard)
}

/// `thermal_window_summary(rows, now)`: estabilidade térmica do último minuto.
pub fn thermal_window_summary(rows: &[Value], now: f64) -> Value {
    let recent: Vec<&Value> = rows
        .iter()
        .filter(|row| {
            let stamp = num(field(row, "stamp")).unwrap_or(f64::NAN);
            truthy(field(row, "valid"))
                && now - 60.0 <= stamp
                && stamp <= now
                && num(field(row, "temperature")).is_some()
        })
        .collect();
    let stamp = |row: &Value| Num::of(field(row, "stamp"));
    let span = if recent.len() > 1 {
        stamp(recent[recent.len() - 1]).minus(stamp(recent[0]))
    } else {
        Num::Int(0)
    };
    let gaps: Vec<Num> = recent
        .windows(2)
        .map(|pair| stamp(pair[1]).minus(stamp(pair[0])))
        .collect();
    let values: Vec<f64> = recent
        .iter()
        .map(|row| num(field(row, "temperature")).unwrap_or(0.0))
        .collect();
    let trend = (values.len() >= 10)
        .then(|| exact_mean(&values[values.len() - 5..]) - exact_mean(&values[..5]));
    let maximum = values
        .iter()
        .copied()
        .fold(None, |best: Option<f64>, value| {
            Some(best.map_or(value, |best| if value > best { value } else { best }))
        });
    let minimum = values
        .iter()
        .copied()
        .fold(None, |best: Option<f64>, value| {
            Some(best.map_or(value, |best| if value < best { value } else { best }))
        });
    let widest_gap = gaps
        .iter()
        .map(|gap| gap.f())
        .fold(f64::NEG_INFINITY, f64::max);
    let stable = values.len() >= 15
        && span.f() >= 45.0
        && !gaps.is_empty()
        && widest_gap <= 10.0
        && maximum.unwrap_or(0.0) - minimum.unwrap_or(0.0) <= 1.0
        && trend.is_some_and(|rise| rise <= 0.2);
    json!({"samples": values.len(), "span_seconds": span.value(), "peak_c": opt(maximum),
        "rise_c": opt(trend), "stable": stable})
}

/// `performance_probe_gate`: só sobe com margem térmica; margem de 1 °C exige um
/// benchmark validado com janela térmica estável.
pub fn performance_probe_gate(
    sample: &Value,
    benchmark: &Value,
    profile: &str,
    soft_limit: f64,
) -> Value {
    let window = benchmark
        .get("thermal_window")
        .filter(|window| truthy(window))
        .cloned()
        .unwrap_or_else(|| json!({}));
    let measured =
        field(benchmark, "validated") == &json!(true) && field(&window, "stable") == &json!(true);
    let margin: i64 = if profile == "performance" && measured {
        1
    } else {
        3
    };
    let temperature = num(field(sample, "temperature_c"));
    let peak = num(field(&window, "peak_c"));
    let mut allowed = temperature.is_some_and(|temp| temp < soft_limit - margin as f64);
    if margin == 1 {
        allowed = allowed && peak.is_some_and(|peak| peak < soft_limit - 1.0);
    }
    json!({"allowed": allowed, "margin_c": margin, "threshold_c": soft_limit - margin as f64,
        "reason": if allowed { "Degrau após estabilidade térmica medida" } else { "Aguardando margem térmica para subir" },
        "thermal_window": window})
}

fn supported_clocks(gpu: &Value) -> AgentResult<Vec<i64>> {
    let mut clocks = std::collections::BTreeSet::new();
    if let Some(items) = field(gpu, "supported_graphics_clocks_mhz").as_array() {
        for value in items {
            if !num(value).is_some_and(|number| number != 0.0) {
                continue;
            }
            if value.is_string() {
                return Err(AgentError {
                    kind: "TypeError",
                    message: "'>' not supported between instances of 'str' and 'int'".into(),
                });
            }
            let number = num(value).unwrap_or(0.0);
            if number > 0.0 {
                clocks.insert(number.trunc() as i64);
            }
        }
    }
    Ok(clocks.into_iter().collect())
}

/// `validated_plan`: um único degrau suportado pelo driver (até 15 MHz ou 5 W), nunca
/// fora dos limites reais. `blocked` explica quando nada pode ser escrito.
pub fn validated_plan(
    gpu: &Value,
    sample: &Value,
    action: &str,
    best_profile: Option<&Value>,
) -> AgentResult<Value> {
    crate::nvidia::validate_device(gpu).map_err(AgentError::value)?;
    if !ACTIONS.contains(&action) {
        return Err(AgentError::value("Ação fora do conjunto permitido"));
    }
    let sample = normalize_sample(sample, None);
    let mut plan =
        json!({"action": "hold", "clock_mhz": null, "power_limit_w": null, "blocked": null});
    let clocks = supported_clocks(gpu)?;
    let current = num(field(&sample, "clock_mhz"));
    let power = num(field(&sample, "power_limit_w"));
    let [minimum, maximum, default] =
        ["power_min_w", "power_max_w", "power_default_w"].map(|key| num(field(gpu, key)));
    let mut action = action;
    if matches!(action, "clock_up" | "clock_down" | "restore_best") {
        let Some(current) = current.filter(|_| !clocks.is_empty()) else {
            plan["blocked"] =
                json!("Clock suportado não informado pelo driver; nenhuma escrita permitida");
            return Ok(plan);
        };
        if action == "restore_best" {
            let desired = best_profile
                .and_then(|best| best.get("clock_mhz"))
                .and_then(num);
            let Some(desired) =
                desired.filter(|desired| clocks.iter().any(|clock| *clock as f64 == *desired))
            else {
                plan["blocked"] = json!("Melhor perfil sem clock suportado");
                return Ok(plan);
            };
            action = if desired > current {
                "clock_up"
            } else if desired < current {
                "clock_down"
            } else {
                return Ok(plan);
            };
        }
        let eligible: Vec<i64> = clocks
            .iter()
            .copied()
            .filter(|clock| {
                let clock = *clock as f64;
                if action == "clock_up" {
                    current < clock && clock <= current + 15.0
                } else {
                    current - 15.0 <= clock && clock < current
                }
            })
            .collect();
        if eligible.is_empty() {
            plan["blocked"] = json!("Nenhum degrau suportado de até 15 MHz nesta direção");
        } else {
            let chosen = if action == "clock_up" {
                eligible[0]
            } else {
                eligible[eligible.len() - 1]
            };
            plan["action"] = json!(action);
            plan["clock_mhz"] = json!(chosen);
        }
    } else if matches!(action, "power_up" | "power_down") {
        let (Some(minimum), Some(maximum), Some(default), Some(power)) =
            (minimum, maximum, default, power)
        else {
            plan["blocked"] = json!("Limites reais de potência indisponíveis ou inconsistentes");
            return Ok(plan);
        };
        if !(0.0 < minimum && minimum <= default && default <= maximum)
            || !(minimum <= power && power <= maximum)
        {
            plan["blocked"] = json!("Limites reais de potência indisponíveis ou inconsistentes");
            return Ok(plan);
        }
        let step = power + if action == "power_up" { 5.0 } else { -5.0 };
        let capped = if step < default { step } else { default };
        let target = if capped > minimum { capped } else { minimum };
        if (action == "power_up" && target <= power) || (action == "power_down" && target >= power)
        {
            plan["blocked"] = json!("Limite de potência atingido");
        } else {
            plan["action"] = json!(action);
            plan["power_limit_w"] = json!(target);
        }
    }
    Ok(plan)
}

/// `control_plan`: um conselho que não muda nada não pode vetar para sempre a
/// exploração limitada de desempenho.
pub fn control_plan(
    gpu: &Value,
    sample: &Value,
    objective: &str,
    selected_profile: &str,
    soft_limit: f64,
    advice: Option<&Value>,
    best_profile: Option<&Value>,
) -> AgentResult<Value> {
    let temperature = num(field(sample, "temperature_c"));
    let desired = if objective == "efficiency" {
        "power_down"
    } else {
        "clock_up"
    };
    if temperature.is_none_or(|temp| temp >= soft_limit) {
        return validated_plan(gpu, sample, "clock_down", best_profile);
    }
    let mut plan = validated_plan(gpu, sample, desired, best_profile)?;
    if let Some(advice) = advice.filter(|advice| truthy(advice)) {
        let action = field(advice, "action");
        let cautious = matches!(action.as_str(), Some("hold" | "clock_down" | "power_down"));
        if selected_profile == "performance" || cautious {
            if advice.get("action").is_none() {
                return Err(AgentError {
                    kind: "KeyError",
                    message: "'action'".into(),
                });
            }
            let Some(text) = action.as_str() else {
                return Err(AgentError::value("Ação fora do conjunto permitido"));
            };
            let advised = validated_plan(gpu, sample, text, best_profile)?;
            if advised["action"] != "hold" || objective != "performance" {
                plan = advised;
            } else {
                plan["advisory_noop"] = json!(true);
            }
        }
    }
    Ok(plan)
}

// --- leituras do nvidia-smi ------------------------------------------------------------

fn query(gpu: &Value, query: &str) -> Vec<String> {
    vec![
        "-i".into(),
        field(gpu, "uuid").as_str().unwrap_or_default().into(),
        query.into(),
        "--format=csv,noheader,nounits".into(),
    ]
}

/// `read_sample`: temperatura, potência, limite, clock e uso; erro se a temperatura não
/// vier ou sair de 0–120 °C.
pub fn read_sample(gpu: &Value, command: SmiCommand, now: Option<f64>) -> AgentResult<Value> {
    crate::nvidia::validate_device(gpu).map_err(AgentError::value)?;
    let text = command(&query(
        gpu,
        "--query-gpu=temperature.gpu,power.draw,power.limit,clocks.gr,utilization.gpu",
    ))
    .map_err(AgentError::runtime)?;
    let values = splitlines(&text)
        .first()
        .map(|line| csv_row(line))
        .unwrap_or_default();
    if values.len() != 5 {
        return Err(AgentError::runtime("Resposta NVIDIA incompleta"));
    }
    let mut result = Map::new();
    for (key, value) in [
        "temperature_c",
        "power_w",
        "power_limit_w",
        "clock_mhz",
        "utilization_pct",
    ]
    .iter()
    .zip(&values)
    {
        result.insert((*key).into(), opt(num(&json!(value))));
    }
    result.insert("sampled_at".into(), json!(now.unwrap_or_else(wall_clock)));
    if !num(&result["temperature_c"]).is_some_and(|temp| (0.0..=120.0).contains(&temp)) {
        return Err(AgentError::runtime("Temperatura NVIDIA inválida"));
    }
    Ok(Value::Object(result))
}

fn first_line_number(gpu: &Value, command: SmiCommand, name: &str) -> Option<f64> {
    let text = command(&query(gpu, name)).ok()?;
    let line = splitlines(py_strip(&text)).first().copied()?;
    num(&json!(line))
}

/// `read_fan`: ventoinha em %, ou `None` (placa sem leitura, erro do driver).
pub fn read_fan(gpu: &Value, command: SmiCommand) -> Option<f64> {
    first_line_number(gpu, command, "--query-gpu=fan.speed")
        .filter(|value| (0.0..=100.0).contains(value))
}

/// `read_memory_clocks`: clocks de memória suportados, do maior para o menor.
pub fn read_memory_clocks(gpu: &Value, command: SmiCommand) -> Vec<i64> {
    let Ok(text) = command(&query(gpu, "--query-supported-clocks=memory")) else {
        return Vec::new();
    };
    let mut clocks: Vec<i64> = splitlines(&text)
        .iter()
        .filter_map(|line| num(&json!(py_strip(line))))
        .filter(|value| *value != 0.0 && *value > 0.0)
        .map(|value| value.trunc() as i64)
        .collect::<std::collections::BTreeSet<_>>()
        .into_iter()
        .collect();
    clocks.reverse();
    clocks
}

/// `read_memory_clock`: clock de memória atual.
pub fn read_memory_clock(gpu: &Value, command: SmiCommand) -> Option<f64> {
    first_line_number(gpu, command, "--query-gpu=clocks.mem")
}

/// `read_optimization`: `(objetivo, preço R$/kWh, receita R$ por TH/s por dia)`. Lucro
/// sem preço ou sem receita volta a hashrate.
pub fn read_optimization(
    settings_path: &Path,
    finance_path: &Path,
) -> (String, Option<f64>, Option<f64>) {
    let settings = read_json(settings_path).unwrap_or_else(|| json!({}));
    let objective = settings
        .is_object()
        .then(|| field(&settings, "optimize_for").clone());
    let price = if settings.is_object() {
        num(field(&settings, "electricity_price_brl_kwh"))
    } else {
        None
    };
    let mut revenue = None;
    if let Some(finance) = read_json(finance_path).filter(Value::is_object) {
        let forecast = finance
            .get("forecast")
            .filter(|value| truthy(value))
            .cloned()
            .unwrap_or_else(|| json!({}));
        if forecast.is_object() {
            let day = num(field(&forecast, "brl_day"));
            let rate = num(field(&forecast, "hashrate_ths"));
            if let (Some(day), Some(rate)) = (day, rate)
                && day > 0.0
                && rate > 0.0
            {
                revenue = Some(day / rate);
            }
        }
    }
    let profit = objective == Some(json!("profit"));
    if !profit || price.is_none_or(|price| price < 0.0) || revenue.is_none() {
        return ("hashrate".into(), price, revenue);
    }
    ("profit".into(), price, revenue)
}

/// `read_target_temperature`: alvo por GPU em `gpu_settings`, senão o global.
pub fn read_target_temperature(settings_path: &Path, gpu_uuid: &str) -> Option<f64> {
    let settings = read_json(settings_path).filter(Value::is_object)?;
    let entry = field(&settings, "gpu_settings")
        .as_object()
        .and_then(|map| map.get(gpu_uuid));
    let mut value = entry
        .and_then(Value::as_object)
        .map_or(&Value::Null, |entry| {
            entry.get("target_temperature_c").unwrap_or(&Value::Null)
        });
    if value.is_null() {
        value = field(&settings, "target_temperature_c");
    }
    num(value)
}

// --- seleção do dispositivo e coordenação ----------------------------------------------

/// `select_device(devices, args)`: pelo UUID (o índice, se vier, precisa bater) ou pelo
/// índice NVIDIA; `miner_index` é o índice do SRBMiner confirmado por PCI.
pub fn select_device(
    devices: &[Value],
    uuid: Option<&str>,
    index: Option<i64>,
    miner_index: Option<i64>,
) -> AgentResult<Value> {
    let gpu = match uuid.filter(|uuid| !uuid.is_empty()) {
        Some(uuid) => {
            let gpu = devices
                .iter()
                .find(|item| field(item, "uuid") == &json!(uuid));
            if let (Some(gpu), Some(index)) = (gpu, index)
                && !py_eq(field(gpu, "index"), &json!(index))
            {
                return Err(AgentError::runtime(
                    "UUID e índice NVIDIA não correspondem; atualização de identidade necessária",
                ));
            }
            gpu
        }
        None => devices
            .iter()
            .find(|item| py_eq(field(item, "index"), &json!(index.unwrap_or(0)))),
    };
    let Some(gpu) = gpu else {
        return Err(AgentError::runtime("GPU NVIDIA selecionada não encontrada"));
    };
    let mut gpu = gpu.clone();
    if let Some(miner_index) = miner_index {
        if !(0..=255).contains(&miner_index) {
            return Err(AgentError::runtime("Índice SRBMiner inválido"));
        }
        gpu["miner_index"] = json!(miner_index);
    }
    crate::nvidia::validate_device(&gpu).map_err(AgentError::value)?;
    Ok(gpu)
}

/// `snapshot_device_payload`: nunca usa o hashrate agregado do rig como se fosse de
/// uma GPU.
pub fn snapshot_device_payload(
    snapshot: &Value,
    gpu: &Value,
    device_count: usize,
) -> Option<Value> {
    let uuid = field(gpu, "uuid");
    let rig = snapshot.get("rig").filter(|rig| truthy(rig));
    let devices = match rig {
        Some(rig) => rig.get("devices").or_else(|| snapshot.get("devices")),
        None => snapshot.get("devices"),
    };
    match devices {
        Some(Value::Object(map)) => {
            return map
                .get(uuid.as_str()?)
                .filter(|entry| entry.is_object())
                .cloned();
        }
        Some(Value::Array(items)) => {
            return items
                .iter()
                .filter(|entry| entry.is_object())
                .find(|entry| {
                    let identity = [field(entry, "identity"), field(entry, "gpu")]
                        .into_iter()
                        .find(|candidate| truthy(candidate))
                        .unwrap_or(entry);
                    field(identity, "uuid") == uuid || field(entry, "gpu_uuid") == uuid
                })
                .cloned();
        }
        _ => {}
    }
    let direct = field(snapshot, "gpu");
    let direct = if truthy(direct) {
        field(direct, "uuid")
    } else {
        &Value::Null
    };
    let reported = if truthy(direct) {
        direct
    } else {
        let agent = field(snapshot, "gpu_agent");
        let inner = if truthy(agent) {
            field(agent, "gpu")
        } else {
            &Value::Null
        };
        if truthy(inner) {
            field(inner, "uuid")
        } else {
            &Value::Null
        }
    };
    if reported == uuid {
        return Some(snapshot.clone());
    }
    if !truthy(reported) && device_count == 1 && py_eq(field(gpu, "index"), &json!(0)) {
        return Some(snapshot.clone());
    }
    None
}

/// `requested_control_stop`: o gerenciador pediu a parada deste supervisor (mesma GPU,
/// mesmo PID, depois do início e há no máximo 60 s).
pub fn requested_control_stop(
    state_dir: &Path,
    gpu_uuid: &str,
    started_at: f64,
    now: Option<f64>,
    pid: u32,
) -> bool {
    let stamp = now.unwrap_or_else(wall_clock);
    let Some(request) = read_json(&state_dir.join("gpu-agent-stop.json")).filter(Value::is_object)
    else {
        return false;
    };
    let requested = num(field(&request, "at")).unwrap_or(0.0);
    field(&request, "gpu_uuid") == &json!(gpu_uuid)
        && py_eq(field(&request, "pid"), &json!(pid))
        && requested >= started_at
        && (-2.0..=60.0).contains(&(stamp - requested))
}

/// `external_supervisor_active`: outro supervisor (térmico ou agente) ainda controla esta
/// GPU. Um PID que não dá para provar morto conta como vivo.
pub fn external_supervisor_active(
    directories: &[&Path],
    gpu_uuid: Option<&str>,
    gpu_index: Option<i64>,
    own_pid: u32,
) -> bool {
    for directory in directories {
        for name in ["thermal-control.json", "gpu-agent-control.json"] {
            let Some(status) = read_json(&directory.join(name)).filter(Value::is_object) else {
                continue;
            };
            let pid = field(&status, "controller_pid");
            let direct = field(&status, "gpu_uuid");
            let reported = if truthy(direct) {
                direct.clone()
            } else {
                let gpu = field(&status, "gpu");
                if truthy(gpu) {
                    field(gpu, "uuid").clone()
                } else {
                    Value::Null
                }
            };
            if let Some(uuid) = gpu_uuid {
                if truthy(&reported) && reported != json!(uuid) {
                    continue;
                }
                if !truthy(&reported)
                    && let Some(index) = gpu_index
                {
                    let reported_index = status.get("gpu_index").cloned().unwrap_or(json!(0));
                    match py_int(&reported_index) {
                        Some(value) if value == index => {}
                        _ => continue,
                    }
                }
            }
            if !py_eq(pid, &json!(own_pid))
                && truthy(field(&status, "enabled"))
                && crate::gpu_defaults::process_state(pid) != "dead"
            {
                return true;
            }
        }
    }
    false
}

// --- GPUAgent sobre SQLite -------------------------------------------------------------

/// Consultor de IA do `recommend`: devolve `{"action", "reason"}` ou o texto do erro.
pub trait Advisor {
    fn advise(&self, context: &Value) -> Result<Value, String>;
}

const SCHEMA: &str = "
  CREATE TABLE IF NOT EXISTS benchmarks(id TEXT PRIMARY KEY,gpu_uuid TEXT NOT NULL,started REAL NOT NULL,
    duration REAL NOT NULL,objective TEXT NOT NULL,state TEXT NOT NULL,device_json TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY,session TEXT NOT NULL,stamp REAL NOT NULL,
    hash_stamp REAL,hashrate REAL,accepted INTEGER,rejected INTEGER,temperature REAL,power REAL,
    power_limit REAL,clock REAL,valid INTEGER NOT NULL,reason TEXT NOT NULL);
  CREATE INDEX IF NOT EXISTS observations_session ON observations(session,stamp);
  CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY,gpu_uuid TEXT NOT NULL,stamp REAL NOT NULL,
    session TEXT,payload_json TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS trials(id INTEGER PRIMARY KEY,gpu_uuid TEXT NOT NULL,stamp REAL NOT NULL,
    state TEXT NOT NULL,payload_json TEXT NOT NULL);
  CREATE TABLE IF NOT EXISTS adjustments(id INTEGER PRIMARY KEY,gpu_uuid TEXT NOT NULL,stamp REAL NOT NULL,
    payload_json TEXT NOT NULL);
";

fn sql_to_json(value: ValueRef<'_>) -> Value {
    match value {
        ValueRef::Null => Value::Null,
        ValueRef::Integer(number) => json!(number),
        ValueRef::Real(number) => json!(number),
        ValueRef::Text(text) => json!(String::from_utf8_lossy(text)),
        ValueRef::Blob(bytes) => json!(bytes),
    }
}

fn to_sql(value: &Value) -> SqlValue {
    match value {
        Value::Null => SqlValue::Null,
        Value::Bool(flag) => SqlValue::Integer(i64::from(*flag)),
        Value::Number(number) => number.as_i64().map_or_else(
            || SqlValue::Real(number.as_f64().unwrap_or(0.0)),
            SqlValue::Integer,
        ),
        Value::String(text) => SqlValue::Text(text.clone()),
        other => SqlValue::Text(other.to_string()),
    }
}

/// Chave do perfil: `(clock, power_limit)` em bits, `None` para `NULL`.
type ProfileKey = (Option<u64>, Option<u64>);

/// `uuid.uuid4().hex`.
pub fn uuid4_hex() -> String {
    let mut text: Vec<char> = crate::manager::random_hex(32).chars().collect();
    text[12] = '4';
    text[16] = ['8', '9', 'a', 'b'][text[16].to_digit(16).unwrap_or(0) as usize % 4];
    text.into_iter().collect()
}

/// `GPUAgent`: um banco SQLite, GPUs e sessões separadas; o histórico só cresce.
pub struct GpuAgent {
    pub path: PathBuf,
    db: Connection,
    pub advisor: Option<Box<dyn Advisor>>,
    /// Gerador do id do benchmark (`uuid4().hex`); os testes o trocam por uma sequência.
    pub id_source: Box<dyn FnMut() -> String>,
    /// `time.time()` usado quando `now` não vem; os testes o fixam.
    pub clock: Box<dyn Fn() -> f64>,
}

impl GpuAgent {
    pub fn open(state_dir: &Path, advisor: Option<Box<dyn Advisor>>) -> AgentResult<Self> {
        std::fs::create_dir_all(state_dir).map_err(AgentError::os)?;
        let path = state_dir.join("gpu-agent.sqlite3");
        let db = Connection::open(&path).map_err(AgentError::sql)?;
        db.busy_timeout(std::time::Duration::from_secs(5))
            .map_err(AgentError::sql)?;
        db.query_row("PRAGMA journal_mode=WAL", [], |_| Ok(()))
            .map_err(AgentError::sql)?;
        db.execute_batch("PRAGMA synchronous=FULL;")
            .map_err(AgentError::sql)?;
        db.execute_batch(SCHEMA).map_err(AgentError::sql)?;
        Ok(Self {
            path,
            db,
            advisor,
            id_source: Box::new(uuid4_hex),
            clock: Box::new(wall_clock),
        })
    }

    fn now(&self) -> f64 {
        (self.clock)()
    }

    fn rows(&self, query: &str, values: &[Value]) -> AgentResult<Vec<Value>> {
        let mut statement = self.db.prepare(query).map_err(AgentError::sql)?;
        let names: Vec<String> = statement.column_names().map(str::to_owned).collect();
        let rows = statement
            .query_map(params_from_iter(values.iter().map(to_sql)), |row| {
                let mut map = Map::new();
                for (index, name) in names.iter().enumerate() {
                    map.insert(name.clone(), sql_to_json(row.get_ref(index)?));
                }
                Ok(Value::Object(map))
            })
            .map_err(AgentError::sql)?;
        rows.collect::<Result<Vec<_>, _>>().map_err(AgentError::sql)
    }

    fn row(&self, query: &str, values: &[Value]) -> AgentResult<Option<Value>> {
        Ok(self.rows(query, values)?.into_iter().next())
    }

    fn execute(&self, query: &str, values: &[Value]) -> AgentResult<()> {
        self.db
            .execute(query, params_from_iter(values.iter().map(to_sql)))
            .map(|_| ())
            .map_err(AgentError::sql)
    }

    /// Todas as linhas de uma tabela, na ordem de inserção (para auditoria e testes).
    pub fn table(&self, name: &str) -> AgentResult<Vec<Vec<Value>>> {
        let mut statement = self
            .db
            .prepare(&format!("SELECT * FROM {name} ORDER BY rowid"))
            .map_err(AgentError::sql)?;
        let count = statement.column_count();
        let rows = statement
            .query_map([], |row| {
                (0..count)
                    .map(|index| row.get_ref(index).map(sql_to_json))
                    .collect()
            })
            .map_err(AgentError::sql)?;
        rows.collect::<Result<Vec<_>, _>>().map_err(AgentError::sql)
    }

    pub fn record_adjustment(
        &self,
        gpu: &Value,
        before: &Value,
        after: &Value,
        plan: &Value,
        hashrate: &Value,
        now: Option<f64>,
    ) -> AgentResult<()> {
        crate::nvidia::validate_device(gpu).map_err(AgentError::value)?;
        let reason = plan
            .get("reason")
            .cloned()
            .unwrap_or_else(|| json!("Ajuste do controlador"));
        let payload = json!({"before": normalize_sample(before, Some(self.now())), "after": normalize_sample(after, Some(self.now())), "plan": plan,
            "hashrate_ths": opt(num(hashrate)), "state": "applied", "reason": reason});
        self.execute(
            "INSERT INTO adjustments(gpu_uuid,stamp,payload_json) VALUES(?,?,?)",
            &[
                field(gpu, "uuid").clone(),
                json!(now.unwrap_or_else(|| self.now())),
                json!(py_dumps(&payload, true)),
            ],
        )
    }

    pub fn start_benchmark(
        &mut self,
        gpu: &Value,
        duration_seconds: &Value,
        objective: &str,
        now: Option<f64>,
    ) -> AgentResult<Value> {
        crate::nvidia::validate_device(gpu).map_err(AgentError::value)?;
        let duration = num(duration_seconds).filter(|duration| (60.0..=3600.0).contains(duration));
        let Some(duration) = duration else {
            return Err(AgentError::value(
                "Benchmark deve durar entre 60 e 3600 segundos",
            ));
        };
        if !matches!(objective, "performance" | "efficiency") {
            return Err(AgentError::value(
                "Objetivo precisa ser performance ou efficiency",
            ));
        }
        let stamp = now.unwrap_or_else(|| self.now());
        let identifier = (self.id_source)();
        let uuid = field(gpu, "uuid").clone();
        let transaction = self.db.unchecked_transaction().map_err(AgentError::sql)?;
        self.execute(
            "UPDATE benchmarks SET state='superseded' WHERE gpu_uuid=? AND state='running'",
            std::slice::from_ref(&uuid),
        )?;
        self.execute(
            "INSERT INTO benchmarks VALUES (?,?,?,?,?,?,?)",
            &[
                json!(identifier),
                uuid.clone(),
                json!(stamp),
                json!(duration),
                json!(objective),
                json!("running"),
                json!(py_dumps(gpu, true)),
            ],
        )?;
        transaction.commit().map_err(AgentError::sql)?;
        self.status(uuid.as_str().unwrap_or_default(), Some(stamp))
    }

    fn latest(&self, uuid: &str) -> AgentResult<Option<Value>> {
        self.row(
            "SELECT * FROM benchmarks WHERE gpu_uuid=? ORDER BY started DESC,rowid DESC LIMIT 1",
            &[json!(uuid)],
        )
    }

    #[allow(clippy::too_many_arguments)]
    pub fn observe(
        &mut self,
        gpu: &Value,
        sample: &Value,
        hashrate_ths: &Value,
        accepted: &Value,
        rejected: &Value,
        now: Option<f64>,
        hash_sampled_at: &Value,
        hw_errors: &Value,
    ) -> AgentResult<Value> {
        crate::nvidia::validate_device(gpu).map_err(AgentError::value)?;
        let stamp = now.unwrap_or_else(|| self.now());
        let sample = normalize_sample(sample, Some(stamp));
        let rate = num(hashrate_ths);
        let hash_stamp = num(hash_sampled_at);
        let counters: Vec<Option<f64>> = [accepted, rejected, hw_errors]
            .iter()
            .map(|value| num(value))
            .collect();
        let counters_valid = counters.iter().all(|value| {
            value.is_some_and(|v| (0.0..=9.223_372_036_854_776e18).contains(&v) && v.fract() == 0.0)
        });
        let [accepted, rejected, hw_errors] = if counters_valid {
            [0, 1, 2].map(|index| counters[index].unwrap_or(0.0) as i64)
        } else {
            [0, 0, 0]
        };
        let (_, hard) = thermal_limits(gpu);
        let temperature = num(&sample["temperature_c"]);
        let sampled_at = num(&sample["sampled_at"]);
        let (mut valid, mut reason) = (true, "ok");
        if temperature.is_none_or(|temp| !(0.0 <= temp && temp < hard)) {
            (valid, reason) = (false, "thermal_or_sensor");
        } else if sampled_at.is_none_or(|at| stamp - at > 10.0 || at > stamp + 2.0) {
            (valid, reason) = (false, "stale_sensor");
        } else if rate.is_none_or(|rate| rate <= 0.0)
            || hash_stamp.is_none_or(|at| !(0.0..=180.0).contains(&(stamp - at)))
        {
            (valid, reason) = (false, "missing_or_stale_hashrate");
        } else if num(&sample["power_w"]).is_none_or(|power| power <= 0.0)
            || num(&sample["clock_mhz"]).is_none()
        {
            (valid, reason) = (false, "incomplete_sensor");
        } else if !counters_valid {
            (valid, reason) = (false, "invalid_counters");
        } else if hw_errors != 0 {
            (valid, reason) = (false, "hardware_error");
        }
        let uuid = field(gpu, "uuid").as_str().unwrap_or_default().to_owned();
        let transaction = self.db.unchecked_transaction().map_err(AgentError::sql)?;
        if let Some(session) = self
            .latest(&uuid)?
            .filter(|session| session["state"] == "running")
        {
            let started = num(&session["started"]).unwrap_or(0.0);
            if hash_stamp.is_some_and(|at| at < started) {
                (valid, reason) = (false, "hashrate_before_benchmark");
            }
            let previous = self.row(
                "SELECT accepted,rejected FROM observations WHERE session=? ORDER BY id DESC LIMIT 1",
                std::slice::from_ref(&session["id"]),
            )?;
            if let Some(previous) = previous.filter(|_| counters_valid) {
                let before = |key: &str| previous[key].as_i64().unwrap_or(0);
                if accepted < before("accepted") || rejected < before("rejected") {
                    (valid, reason) = (false, "miner_restarted");
                } else if rejected > before("rejected") {
                    (valid, reason) = (false, "new_rejected_share");
                }
            }
            self.execute(
                "INSERT INTO observations(session,stamp,hash_stamp,hashrate,accepted,rejected,
                    temperature,power,power_limit,clock,valid,reason) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                &[
                    session["id"].clone(),
                    json!(stamp),
                    opt(hash_stamp),
                    opt(rate),
                    json!(accepted),
                    json!(rejected),
                    sample["temperature_c"].clone(),
                    sample["power_w"].clone(),
                    sample["power_limit_w"].clone(),
                    sample["clock_mhz"].clone(),
                    json!(i64::from(valid)),
                    json!(reason),
                ],
            )?;
            if stamp >= started + num(&session["duration"]).unwrap_or(0.0) {
                self.execute(
                    "UPDATE benchmarks SET state=? WHERE id=?",
                    &[json!("complete"), session["id"].clone()],
                )?;
            }
        }
        transaction.commit().map_err(AgentError::sql)?;
        self.status(&uuid, Some(stamp))
    }

    pub fn status(&self, gpu_uuid: &str, now: Option<f64>) -> AgentResult<Value> {
        let stamp = now.unwrap_or_else(|| self.now());
        let Some(session) = self.latest(gpu_uuid)? else {
            return Ok(
                json!({"state": "idle", "gpu_uuid": gpu_uuid, "reason": "Nenhum benchmark executado nesta GPU"}),
            );
        };
        let mut result = session.as_object().cloned().unwrap_or_default();
        let device = result.shift_remove("device_json").and_then(|text| {
            text.as_str()
                .and_then(|text| serde_json::from_str(text).ok())
        });
        result.insert("device".into(), device.unwrap_or(Value::Null));
        let rows = self.rows(
            "SELECT * FROM observations WHERE session=? ORDER BY stamp,id",
            std::slice::from_ref(&session["id"]),
        )?;
        let decision = self.row(
            "SELECT payload_json FROM decisions WHERE gpu_uuid=? ORDER BY id DESC LIMIT 1",
            &[json!(gpu_uuid)],
        )?;
        let started = num(&session["started"]).unwrap_or(0.0);
        let duration = num(&session["duration"]).unwrap_or(0.0);
        let elapsed = Num::Int(0).max(Num::Float(duration).min(Num::Float(stamp - started)));
        let progress = Num::Int(1).min(Num::Float(elapsed.f() / duration));
        let mut coverage = 0.0;
        for pair in rows.windows(2) {
            let gap = num(&pair[1]["stamp"]).unwrap_or(0.0) - num(&pair[0]["stamp"]).unwrap_or(0.0);
            if 0.0 < gap
                && gap <= 15.0
                && !pair[0]["temperature"].is_null()
                && !pair[1]["temperature"].is_null()
            {
                coverage += gap;
            }
        }
        let valid = |row: &Value| truthy(&row["valid"]);
        let invalid = rows.iter().filter(|row| !valid(row)).count();
        let unstable = rows
            .iter()
            .filter(|row| num(&row["stamp"]).unwrap_or(0.0) - started >= 30.0)
            .filter(|row| {
                row["reason"]
                    .as_str()
                    .is_some_and(|reason| UNSTABLE.contains(&reason))
            })
            .count();
        // Uma linha repetida do log do minerador é uma medição, não várias.
        let mut measurements: Vec<(u64, &Value)> = Vec::new();
        for row in &rows {
            let hash_stamp = num(&row["hash_stamp"]);
            if valid(row)
                && let Some(hash_stamp) = hash_stamp.filter(|at| at - started >= 30.0)
            {
                let key = (hash_stamp + 0.0).to_bits();
                if !measurements.iter().any(|(item, _)| *item == key) {
                    measurements.push((key, row));
                }
            }
        }
        let column = |key: &str, items: &[&Value]| -> Vec<f64> {
            items
                .iter()
                .map(|row| num(&row[key]).unwrap_or(0.0))
                .collect()
        };
        let measured: Vec<&Value> = measurements.iter().map(|(_, row)| *row).collect();
        let mean_rate = mean_of(&column("hashrate", &measured));
        let mean_power = mean_of(&column("power", &measured));
        let efficiency = match (mean_rate, mean_power) {
            (Some(rate), Some(power)) if power != 0.0 => Some(rate / power),
            _ => None,
        };
        let state = session["state"].as_str().unwrap_or_default();
        let mut validated = state == "complete"
            && measured.len() >= 3
            && unstable == 0
            && coverage >= duration * 0.7;
        let mut reason = if validated {
            "Benchmark concluído com medições independentes"
        } else if state == "running" {
            "Coletando hashrate real e estabilidade"
        } else {
            "Amostras insuficientes ou instáveis; repetir benchmark"
        };
        let mut profiles: Vec<(ProfileKey, Vec<&Value>)> = Vec::new();
        for row in &measured {
            let key = (
                num(&row["clock"]).map(f64::to_bits),
                num(&row["power_limit"]).map(f64::to_bits),
            );
            match profiles.iter_mut().find(|(item, _)| *item == key) {
                Some((_, group)) => group.push(row),
                None => profiles.push((key, vec![row])),
            }
        }
        let metric = if session["objective"] == "performance" {
            "hashrate_ths"
        } else {
            "efficiency_ths_per_w"
        };
        let mut best: Option<Value> = None;
        for (_, group) in &profiles {
            if group.len() < 3 {
                continue;
            }
            let rate = exact_mean(&column("hashrate", group));
            let power = exact_mean(&column("power", group));
            let candidate = json!({"clock_mhz": group[0]["clock"], "power_limit_w": group[0]["power_limit"], "hashrate_ths": rate,
                "power_w": power, "efficiency_ths_per_w": rate / power, "samples": group.len()});
            let better = best.as_ref().is_none_or(|best| {
                num(&candidate[metric]).unwrap_or(0.0) > num(&best[metric]).unwrap_or(0.0)
            });
            if better {
                best = Some(candidate);
            }
        }
        validated = validated && best.is_some();
        if state == "complete" && !validated {
            reason = "Amostras insuficientes, perfil ainda sem estabilidade ou cobertura interrompida; repetir benchmark";
        }
        let last = decision.and_then(|row| {
            row["payload_json"]
                .as_str()
                .and_then(|text| serde_json::from_str::<Value>(text).ok())
        });
        for (key, value) in [
            ("elapsed_seconds", elapsed.value()),
            ("progress", progress.value()),
            ("samples", json!(rows.len())),
            ("observed_seconds", json!(coverage)),
            ("thermal_window", thermal_window_summary(&rows, stamp)),
            ("invalid_samples", json!(invalid)),
            ("unstable_samples", json!(unstable)),
            ("independent_hash_samples", json!(measured.len())),
            ("mean_hashrate_ths", opt(mean_rate)),
            ("mean_power_w", opt(mean_power)),
            ("efficiency_ths_per_w", opt(efficiency)),
            ("validated", json!(validated)),
            ("reason", json!(reason)),
            ("best_profile", best.unwrap_or(Value::Null)),
            ("last_decision", last.unwrap_or(Value::Null)),
        ] {
            result.insert(key.into(), value);
        }
        Ok(Value::Object(result))
    }

    /// `recommend`: proposta limitada (nunca escreve hardware). A IA só troca a ação
    /// quando a política já ia agir, a temperatura está abaixo do limite suave e o
    /// benchmark está estável.
    pub fn recommend(
        &mut self,
        gpu: &Value,
        sample: &Value,
        ollama: bool,
        now: Option<f64>,
    ) -> AgentResult<Value> {
        crate::nvidia::validate_device(gpu).map_err(AgentError::value)?;
        let stamp = now.unwrap_or_else(|| self.now());
        let sample = normalize_sample(sample, Some(stamp));
        let uuid = field(gpu, "uuid").as_str().unwrap_or_default().to_owned();
        let benchmark = self.status(&uuid, Some(stamp))?;
        let (soft, hard) = thermal_limits(gpu);
        let temperature = num(&sample["temperature_c"]);
        let mut action = json!("hold");
        let mut reason = json!("Aguardando benchmark e sensores estáveis");
        let mut mode = "adaptive";
        let unstable = truthy(field(&benchmark, "unstable_samples"));
        match temperature {
            None => reason = json!("Sensor ausente ou antigo: ajuste bloqueado"),
            Some(temp) => {
                let Some(sampled_at) = num(&sample["sampled_at"]) else {
                    return Err(AgentError {
                        kind: "TypeError",
                        message: "unsupported operand type(s) for -: 'float' and 'NoneType'".into(),
                    });
                };
                if stamp - sampled_at > 10.0 {
                    reason = json!("Sensor ausente ou antigo: ajuste bloqueado");
                } else if temp >= hard {
                    (action, reason, mode) = (
                        json!("clock_down"),
                        json!("Emergência térmica: reduzir e solicitar pausa ao supervisor"),
                        "emergency",
                    );
                } else if temp >= soft {
                    (action, reason) = (
                        json!("clock_down"),
                        json!("Temperatura próxima do limite; reduzir gradualmente"),
                    );
                } else if benchmark["state"] == "running"
                    && num(field(&benchmark, "elapsed_seconds")).unwrap_or(0.0) >= 60.0
                    && num(field(&benchmark, "independent_hash_samples")).unwrap_or(0.0) >= 2.0
                {
                    if unstable {
                        (action, reason) = (
                            json!("clock_down"),
                            json!(
                                "Benchmark instável ou shares rejeitadas: reduzir e validar novamente"
                            ),
                        );
                    } else if num(&sample["utilization_pct"]).is_some_and(|util| util >= 90.0)
                        && temp <= soft - 3.0
                    {
                        if benchmark.get("objective") == Some(&json!("efficiency")) {
                            (action, reason) = (
                                json!("power_down"),
                                json!(
                                    "Testar redução pequena de potência e medir hashrate por watt"
                                ),
                            );
                        } else {
                            (action, reason) = (
                                json!("clock_up"),
                                json!(
                                    "Testar próximo clock suportado e validar rendimento e temperatura"
                                ),
                            );
                        }
                    }
                } else if truthy(field(&benchmark, "validated"))
                    && truthy(field(&benchmark, "best_profile"))
                {
                    (action, reason) = (
                        json!("restore_best"),
                        json!("Reutilizar melhor perfil medido nesta GPU e neste benchmark"),
                    );
                }
            }
        }
        let mut advice_status = "disabled".to_string();
        if ollama && mode != "emergency" {
            match self.advisor.as_ref() {
                None => {
                    advice_status =
                        "unavailable: Ollama não configurado; política adaptativa ativa".into()
                }
                Some(advisor) => {
                    let summary: Map<String, Value> = [
                        "state",
                        "objective",
                        "independent_hash_samples",
                        "mean_hashrate_ths",
                        "efficiency_ths_per_w",
                        "invalid_samples",
                    ]
                    .into_iter()
                    .map(|key| (key.to_string(), field(&benchmark, key).clone()))
                    .collect();
                    let context = json!({"gpu": field(gpu, "name"), "sample": sample, "benchmark": summary,
                        "safe_default": {"action": action, "reason": reason}, "soft_temperature_c": soft});
                    let adopt = action != "hold"
                        && temperature.is_some_and(|temp| temp < soft)
                        && !unstable;
                    let outcome = advisor.advise(&context).and_then(|advice| {
                        if !adopt {
                            return Ok(None);
                        }
                        let advised_action = advice
                            .get("action")
                            .cloned()
                            .ok_or_else(|| "'action'".to_string())?;
                        let advised_reason = advice
                            .get("reason")
                            .cloned()
                            .ok_or_else(|| "'reason'".to_string())?;
                        Ok(Some((advised_action, advised_reason)))
                    });
                    match outcome {
                        Ok(advised) => {
                            if let Some(advised) = advised {
                                (action, reason) = advised;
                            }
                            advice_status = "local_ollama".into();
                        }
                        Err(error) => {
                            advice_status = format!(
                                "unavailable: {}",
                                error.chars().take(160).collect::<String>()
                            )
                        }
                    }
                }
            }
        }
        let Some(action_text) = action.as_str() else {
            return Err(AgentError::value("Ação fora do conjunto permitido"));
        };
        let mut plan = validated_plan(
            gpu,
            &sample,
            action_text,
            benchmark.get("best_profile").filter(|best| !best.is_null()),
        )?;
        for (key, value) in [
            ("reason", reason),
            ("source", json!(mode)),
            ("ai_status", json!(advice_status)),
            ("gpu_uuid", json!(uuid)),
            ("at", json!(stamp)),
            ("session", field(&benchmark, "id").clone()),
            ("pause_required", json!(mode == "emergency")),
            ("writes_hardware", json!(false)),
        ] {
            plan[key] = value;
        }
        let transaction = self.db.unchecked_transaction().map_err(AgentError::sql)?;
        let previous = self.row(
            "SELECT stamp,payload_json FROM decisions WHERE gpu_uuid=? AND json_extract(payload_json,'$.action')!='hold' ORDER BY id DESC LIMIT 1",
            &[json!(uuid)],
        )?;
        if previous
            .is_some_and(|row| stamp - num(&row["stamp"]).unwrap_or(f64::NEG_INFINITY) < 30.0)
            && mode != "emergency"
        {
            plan["action"] = json!("hold");
            plan["clock_mhz"] = Value::Null;
            plan["power_limit_w"] = Value::Null;
            plan["reason"] = json!("Validando o ajuste anterior por pelo menos 30 segundos");
        }
        self.execute(
            "INSERT INTO decisions(gpu_uuid,stamp,session,payload_json) VALUES(?,?,?,?)",
            &[
                json!(uuid),
                json!(stamp),
                field(&benchmark, "id").clone(),
                json!(py_dumps(&plan, false)),
            ],
        )?;
        transaction.commit().map_err(AgentError::sql)?;
        Ok(plan)
    }

    pub fn assert_no_pending_trial(&self, gpu: &Value) -> AgentResult<()> {
        crate::nvidia::validate_device(gpu).map_err(AgentError::value)?;
        if self
            .row(
                "SELECT 1 FROM trials WHERE gpu_uuid=? AND state='validating'",
                &[field(gpu, "uuid").clone()],
            )?
            .is_some()
        {
            return Err(AgentError::runtime(
                "O ajuste anterior ainda está em validação",
            ));
        }
        Ok(())
    }

    /// Só um novo dono exclusivo do hardware pode aposentar o teste do supervisor
    /// anterior. Nenhuma reversão é deduzida entre processos: o novo controlador mede
    /// a própria referência; as linhas antigas ficam como auditoria.
    pub fn recover_interrupted_trials(
        &self,
        gpu: &Value,
        ownership_check: &mut dyn FnMut() -> AgentResult<()>,
        now: Option<f64>,
    ) -> AgentResult<usize> {
        crate::nvidia::validate_device(gpu).map_err(AgentError::value)?;
        ownership_check()?;
        let stamp = now.unwrap_or_else(|| self.now());
        let transaction = self.db.unchecked_transaction().map_err(AgentError::sql)?;
        let rows = self.rows(
            "SELECT id,payload_json FROM trials WHERE gpu_uuid=? AND state='validating'",
            &[field(gpu, "uuid").clone()],
        )?;
        for row in &rows {
            let payload: Option<Value> = row["payload_json"]
                .as_str()
                .and_then(|text| serde_json::from_str(text).ok());
            let Some(mut payload) = payload.filter(Value::is_object) else {
                return Err(AgentError::value(
                    "Estado do teste anterior inválido; recuperação recusada",
                ));
            };
            payload["interrupted_at"] = json!(stamp);
            payload["interruption_reason"] =
                json!("Novo supervisor exclusivo; baseline será medido novamente");
            self.execute(
                "UPDATE trials SET state='interrupted_controller_restart',payload_json=? WHERE id=?",
                &[json!(py_dumps(&payload, false)), row["id"].clone()],
            )?;
        }
        transaction.commit().map_err(AgentError::sql)?;
        Ok(rows.len())
    }

    /// Chamar só DEPOIS de uma escrita bem-sucedida do supervisor único do hardware.
    pub fn record_application(
        &self,
        gpu: &Value,
        plan: &Value,
        before: &Value,
        baseline_hashrate: &Value,
        now: Option<f64>,
    ) -> AgentResult<()> {
        crate::nvidia::validate_device(gpu).map_err(AgentError::value)?;
        let sample = normalize_sample(before, Some(self.now()));
        let baseline = num(baseline_hashrate);
        let Some(baseline) = baseline
            .filter(|baseline| *baseline > 0.0 && field(plan, "gpu_uuid") == field(gpu, "uuid"))
        else {
            return Err(AgentError::value(
                "Aplicação precisa da GPU e hashrate de referência reais",
            ));
        };
        let stamp = now.unwrap_or_else(|| self.now());
        let payload = json!({"before": sample, "plan": plan, "baseline_hashrate_ths": baseline, "samples": []});
        let transaction = self.db.unchecked_transaction().map_err(AgentError::sql)?;
        if self
            .row(
                "SELECT 1 FROM trials WHERE gpu_uuid=? AND state='validating'",
                &[field(gpu, "uuid").clone()],
            )?
            .is_some()
        {
            return Err(AgentError::runtime(
                "O ajuste anterior ainda está em validação",
            ));
        }
        self.execute(
            "INSERT INTO trials(gpu_uuid,stamp,state,payload_json) VALUES(?,?,'validating',?)",
            &[
                field(gpu, "uuid").clone(),
                json!(stamp),
                json!(py_dumps(&payload, true)),
            ],
        )?;
        transaction.commit().map_err(AgentError::sql)
    }

    fn inverse(action: &Value) -> &'static str {
        match action.as_str() {
            Some("clock_up") => "clock_down",
            Some("clock_down") => "clock_up",
            Some("power_up") => "power_down",
            Some("power_down") => "power_up",
            _ => "hold",
        }
    }

    /// `trial_feedback`: valida o teste pelo hashrate medido; a reversão também é só uma
    /// proposta limitada.
    pub fn trial_feedback(
        &self,
        gpu: &Value,
        sample: &Value,
        hashrate_ths: &Value,
        hash_sampled_at: &Value,
        new_rejected_shares: i64,
        now: Option<f64>,
    ) -> AgentResult<Value> {
        crate::nvidia::validate_device(gpu).map_err(AgentError::value)?;
        let stamp = now.unwrap_or_else(|| self.now());
        let sample = normalize_sample(sample, Some(stamp));
        let transaction = self.db.unchecked_transaction().map_err(AgentError::sql)?;
        let Some(row) = self.row(
            "SELECT * FROM trials WHERE gpu_uuid=? AND state='validating' ORDER BY id DESC LIMIT 1",
            &[field(gpu, "uuid").clone()],
        )?
        else {
            return Ok(json!({"state": "idle"}));
        };
        let mut payload: Value = row["payload_json"]
            .as_str()
            .and_then(|text| serde_json::from_str(text).ok())
            .ok_or_else(|| AgentError::value("Estado do teste inválido"))?;
        payload["after"] = sample.clone();
        let started = num(&row["stamp"]).unwrap_or(0.0);
        let elapsed = stamp - started;
        let rate = num(hashrate_ths).filter(|rate| *rate != 0.0);
        let hash_stamp = num(hash_sampled_at).filter(|at| *at != 0.0);
        if let (Some(rate), Some(hash_stamp)) = (rate, hash_stamp)
            && started + 30.0 <= hash_stamp
            && hash_stamp <= stamp
            && stamp - hash_stamp <= 180.0
        {
            let samples = payload["samples"]
                .as_array_mut()
                .ok_or_else(|| AgentError::value("Estado do teste inválido"))?;
            if !samples.iter().any(|item| num(&item[0]) == Some(hash_stamp)) {
                samples.push(json!([hash_stamp, rate]));
            }
        }
        let soft = thermal_limits(gpu).0;
        let temperature = num(&sample["temperature_c"]);
        let hot = temperature.is_none_or(|temp| temp >= soft);
        let mut failure = new_rejected_shares != 0 || hot;
        let values: Vec<f64> = payload["samples"]
            .as_array()
            .map_or_else(Vec::new, |items| {
                items
                    .iter()
                    .map(|item| num(&item[1]).unwrap_or(0.0))
                    .collect()
            });
        let measured = mean_of(&values);
        let baseline = num(&payload["baseline_hashrate_ths"]).unwrap_or(0.0);
        if elapsed >= 90.0
            && values.len() >= 2
            && measured.is_some_and(|measured| measured < baseline * 0.97)
        {
            failure = true;
        }
        let mut state = if failure {
            "rejected"
        } else if elapsed >= 90.0 && values.len() >= 2 {
            "accepted"
        } else {
            "validating"
        };
        if elapsed > 240.0 && state == "validating" {
            state = "rejected";
        }
        payload["measured_hashrate_ths"] = opt(measured);
        payload["result_reason"] = json!(match state {
            "rejected" => "Temperatura, rejeição, perda >3% ou prazo de validação excedido",
            "accepted" => "Pelo menos 2 amostras independentes, 90 s e perda inferior a 3%",
            _ => "Coletando amostras independentes",
        });
        self.execute(
            "UPDATE trials SET state=?,payload_json=? WHERE id=?",
            &[
                json!(state),
                json!(py_dumps(&payload, true)),
                row["id"].clone(),
            ],
        )?;
        transaction.commit().map_err(AgentError::sql)?;
        let mut result = json!({"state": state, "elapsed_seconds": elapsed, "measured_hashrate_ths": opt(measured),
            "baseline_hashrate_ths": payload["baseline_hashrate_ths"], "independent_hash_samples": values.len()});
        if state == "rejected" {
            let mut inverse = Self::inverse(&payload["plan"]["action"]);
            if hot {
                inverse = "clock_down";
            }
            let mut rollback = validated_plan(gpu, &sample, inverse, None)?;
            rollback["gpu_uuid"] = field(gpu, "uuid").clone();
            rollback["at"] = json!(stamp);
            rollback["reason"] =
                json!("Teste instável ou perda de hashrate; reverter o último degrau");
            result["rollback"] = rollback;
        }
        Ok(result)
    }

    /// `export_policy`: dica de curta duração para o supervisor, nunca uma autorização.
    pub fn export_policy(
        &self,
        gpu: &Value,
        directory: &Path,
        now: Option<f64>,
    ) -> AgentResult<Option<Value>> {
        crate::nvidia::validate_device(gpu).map_err(AgentError::value)?;
        let stamp = now.unwrap_or_else(|| self.now());
        let status = self.status(field(gpu, "uuid").as_str().unwrap_or_default(), Some(stamp))?;
        if !truthy(field(&status, "validated")) || !truthy(field(&status, "best_profile")) {
            return Ok(None);
        }
        let best = &status["best_profile"];
        let payload = json!({"schema_version": 1, "gpu_uuid": field(gpu, "uuid"), "gpu_name": field(gpu, "name"),
            "benchmark_id": status["id"], "validated": true, "generated_at": stamp, "valid_until": stamp + 3600.0,
            "target_clock_mhz": best["clock_mhz"], "target_power_w": best["power_limit_w"], "hashrate_ths": best["hashrate_ths"],
            "objective": status["objective"], "authorization": false,
            "reason": "Melhor perfil medido; supervisor mantém seus próprios limites térmicos e de hardware"});
        std::fs::create_dir_all(directory).map_err(AgentError::os)?;
        let destination = directory.join("gpu-agent-policy.json");
        let temporary = directory.join(format!("gpu-agent-policy.{}.tmp", std::process::id()));
        std::fs::write(&temporary, encode_indented(&payload)).map_err(AgentError::os)?;
        std::fs::rename(&temporary, &destination).map_err(AgentError::os)?;
        Ok(Some(payload))
    }

    /// `cancel_trial`: um benchmark manual cancela o experimento pendente e propõe
    /// desfazê-lo.
    pub fn cancel_trial(
        &self,
        gpu: &Value,
        sample: &Value,
        now: Option<f64>,
    ) -> AgentResult<Option<Value>> {
        crate::nvidia::validate_device(gpu).map_err(AgentError::value)?;
        let transaction = self.db.unchecked_transaction().map_err(AgentError::sql)?;
        let Some(row) = self.row(
            "SELECT * FROM trials WHERE gpu_uuid=? AND state='validating' ORDER BY id DESC LIMIT 1",
            &[field(gpu, "uuid").clone()],
        )?
        else {
            return Ok(None);
        };
        let payload: Value = row["payload_json"]
            .as_str()
            .and_then(|text| serde_json::from_str(text).ok())
            .unwrap_or(Value::Null);
        self.execute(
            "UPDATE trials SET state='cancelled_by_user' WHERE id=?",
            std::slice::from_ref(&row["id"]),
        )?;
        transaction.commit().map_err(AgentError::sql)?;
        let mut inverse = Self::inverse(&payload["plan"]["action"]);
        let current = normalize_sample(sample, Some(self.now()));
        if num(&current["temperature_c"]).is_none_or(|temp| temp >= thermal_limits(gpu).0) {
            inverse = "clock_down";
        }
        let mut plan = validated_plan(gpu, sample, inverse, None)?;
        plan["gpu_uuid"] = field(gpu, "uuid").clone();
        plan["at"] = json!(now.unwrap_or_else(|| self.now()));
        plan["reason"] = json!("Benchmark solicitado: desfazer experimento anterior");
        Ok(Some(plan))
    }
}

/// Usado pelos testes: a soma de floats da esquerda para a direita, para mostrar que ela
/// erra onde `exact_mean` acerta.
pub fn naive_mean(values: &[f64]) -> f64 {
    values.iter().sum::<f64>() / values.len() as f64
}
