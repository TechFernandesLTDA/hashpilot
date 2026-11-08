//! Conversão numérica com a semântica do `float()` do Python.
//!
//! O HashPilot nunca transforma dado ausente ou inválido em zero: onde o Python
//! levantaria `ValueError`/`TypeError`, estas funções devolvem `None`.

use serde_json::Value;

/// `float(valor)` do Python para um valor JSON; `None` onde o Python levantaria erro.
///
/// Booleanos convertem como no Python (`True` → 1.0); as funções públicas abaixo os
/// rejeitam antes, como `gpu_agent.numeric`, `payout.number` e `valuation.numeric`.
pub fn py_float(value: &Value) -> Option<f64> {
    match value {
        Value::Bool(flag) => Some(if *flag { 1.0 } else { 0.0 }),
        Value::Number(number) => number.as_f64(),
        Value::String(text) => parse_py_float(text),
        Value::Null | Value::Array(_) | Value::Object(_) => None,
    }
}

/// Texto → `f64` como `float(str)` do Python: espaços nas pontas, `inf`/`nan` e
/// sublinhados só entre dígitos (`"1_000"`), mas não `"1__0"`, `"_1"` ou `"0x10"`.
pub fn parse_py_float(text: &str) -> Option<f64> {
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return None;
    }
    if !trimmed.contains('_') {
        return trimmed.parse::<f64>().ok();
    }
    let chars: Vec<char> = trimmed.chars().collect();
    for (position, character) in chars.iter().enumerate() {
        if *character == '_' {
            let before = position > 0 && chars[position - 1].is_ascii_digit();
            let after = chars.get(position + 1).is_some_and(char::is_ascii_digit);
            if !before || !after {
                return None;
            }
        }
    }
    trimmed.replace('_', "").parse::<f64>().ok()
}

/// `gpu_agent.numeric`: número finito ou `None`; booleano é rejeitado.
pub fn numeric(value: &Value) -> Option<f64> {
    if value.is_boolean() {
        return None;
    }
    py_float(value).filter(|number| number.is_finite())
}

/// `numeric` sobre texto já extraído (por exemplo, um grupo de regex do log).
pub fn numeric_str(text: &str) -> Option<f64> {
    parse_py_float(text).filter(|number| number.is_finite())
}

/// `numeric` sobre um `f64` já calculado: descarta infinito e NaN.
pub fn finite(value: f64) -> Option<f64> {
    value.is_finite().then_some(value)
}

/// `payout.number`: finito e não negativo.
pub fn non_negative(value: &Value) -> Option<f64> {
    numeric(value).filter(|number| *number >= 0.0)
}

/// `valuation.numeric`: finito e, com `positive`, estritamente maior que zero.
pub fn valuation_numeric(value: &Value, positive: bool) -> Option<f64> {
    numeric(value).filter(|number| !positive || *number > 0.0)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn follows_python_float_rules() {
        assert_eq!(parse_py_float(" 2 "), Some(2.0));
        assert_eq!(parse_py_float("1_000"), Some(1000.0));
        assert_eq!(parse_py_float("1__0"), None);
        assert_eq!(parse_py_float("_1"), None);
        assert_eq!(parse_py_float("1_"), None);
        assert_eq!(parse_py_float("0x10"), None);
        assert_eq!(parse_py_float(".5"), Some(0.5));
        assert_eq!(parse_py_float("5."), Some(5.0));
        assert!(numeric(&json!("inf")).is_none());
        assert!(numeric(&json!(true)).is_none());
        assert_eq!(non_negative(&json!(-1)), None);
        assert_eq!(valuation_numeric(&json!(0), true), None);
        assert_eq!(valuation_numeric(&json!(0), false), Some(0.0));
    }
}
