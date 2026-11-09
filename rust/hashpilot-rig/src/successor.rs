//! `miner_identity.py`: reconhece um SRBMiner reiniciado pela sessão gravada da GPU,
//! sem confiar no PID.

use hashpilot_core::text::py_str;
use hashpilot_history::pyjson::{py_eq, truthy};
use serde_json::{Value, json};

use crate::process::normalized_path;

/// `option(argv, nome)`: o valor depois de `nome`, só se ele aparecer uma vez.
pub fn option<'a>(argv: &'a [Value], name: &str) -> Option<&'a Value> {
    let target = json!(name);
    if argv.iter().filter(|item| py_eq(item, &target)).count() != 1 {
        return None;
    }
    let index = argv.iter().position(|item| py_eq(item, &target))?;
    argv.get(index + 1)
}

/// `int(valor)` do Python para o `created_at` (texto de dígitos, inteiro, `float`
/// truncado ou booleano). `None` onde o Python levantaria `TypeError`/`ValueError`.
pub fn py_int(value: &Value) -> Option<i128> {
    match value {
        Value::Bool(flag) => Some(i128::from(*flag)),
        Value::Number(number) => number
            .as_i64()
            .map(i128::from)
            .or_else(|| number.as_u64().map(i128::from))
            .or_else(|| {
                number
                    .as_f64()
                    .filter(|value| value.is_finite())
                    .map(|value| value.trunc() as i128)
            }),
        Value::String(text) => {
            let text = text.trim();
            let (negative, digits) = match text.as_bytes().first() {
                Some(b'-') => (true, &text[1..]),
                Some(b'+') => (false, &text[1..]),
                _ => (false, text),
            };
            let valid = !digits.is_empty()
                && !digits.starts_with('_')
                && !digits.ends_with('_')
                && !digits.contains("__")
                && digits.chars().all(|c| c.is_ascii_digit() || c == '_');
            if !valid {
                return None;
            }
            let parsed: i128 = digits.replace('_', "").parse().ok()?;
            Some(if negative { -parsed } else { parsed })
        }
        _ => None,
    }
}

fn path_key(value: &Value) -> String {
    normalized_path(&py_str(value))
}

/// `match_successor(candidate, previous, device, miner_path)`.
pub fn match_successor(
    candidate: &Value,
    previous: &Value,
    device: &Value,
    miner_path: &str,
) -> bool {
    let empty = Vec::new();
    let argv = candidate
        .get("argv")
        .filter(|value| truthy(value))
        .and_then(Value::as_array)
        .unwrap_or(&empty);
    let nothing = json!({});
    let old = previous
        .get("miner")
        .filter(|value| truthy(value))
        .unwrap_or(&nothing);
    let null = Value::Null;
    if argv.is_empty()
        || py_eq(
            candidate.get("pid").unwrap_or(&null),
            old.get("pid").unwrap_or(&null),
        )
    {
        return false;
    }
    let (Some(new_created), Some(old_created)) = (
        candidate.get("created_at").and_then(py_int),
        old.get("created_at").and_then(py_int),
    ) else {
        return false;
    };
    if new_created <= old_created {
        return false;
    }
    let miner = normalized_path(miner_path);
    let executable = candidate
        .get("executable")
        .cloned()
        .unwrap_or_else(|| json!(""));
    if path_key(&executable) != miner || path_key(&argv[0]) != miner {
        return false;
    }
    let log = option(argv, "--log-file");
    let previous_log = previous.get("miner_log").unwrap_or(&null);
    if !log.is_some_and(truthy)
        || !truthy(previous_log)
        || path_key(log.unwrap_or(&null)) != path_key(previous_log)
    {
        return false;
    }
    let Some(index) = device.get("miner_index").or_else(|| device.get("index")) else {
        return false;
    };
    let same = |name: &str, expected: &Value| py_eq(option(argv, name).unwrap_or(&null), expected);
    same("--gpu-id", &json!(py_str(index)))
        && same("--wallet", previous.get("wallet").unwrap_or(&null))
        && same("--pool", previous.get("pool").unwrap_or(&null))
        && same("--worker", device.get("worker_name").unwrap_or(&null))
}
