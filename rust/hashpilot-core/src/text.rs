//! Utilitários de texto com a semântica do Python (`splitlines`, `str()`).

use serde_json::Value;

/// `str.splitlines()` do Python: separa em `\n`, `\r`, `\r\n` e nos demais
/// separadores de linha Unicode, sem gerar linha vazia no fim.
pub fn splitlines(text: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut start = 0;
    let mut chars = text.char_indices().peekable();
    while let Some((position, character)) = chars.next() {
        let is_break = matches!(
            character,
            '\n' | '\r'
                | '\u{0b}'
                | '\u{0c}'
                | '\u{1c}'
                | '\u{1d}'
                | '\u{1e}'
                | '\u{85}'
                | '\u{2028}'
                | '\u{2029}'
        );
        if !is_break {
            continue;
        }
        lines.push(&text[start..position]);
        let mut end = position + character.len_utf8();
        if character == '\r' {
            if let Some((next_position, '\n')) = chars.peek().copied() {
                chars.next();
                end = next_position + 1;
            }
        }
        start = end;
    }
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}

/// `repr(float)` do Python 3: dígitos mínimos de ida e volta, notação científica
/// fora de `1e-4 <= |x| < 1e16`, expoente com sinal e ao menos dois dígitos.
pub fn py_float_repr(value: f64) -> String {
    if value.is_nan() {
        return "nan".into();
    }
    if value.is_infinite() {
        return if value > 0.0 {
            "inf".into()
        } else {
            "-inf".into()
        };
    }
    let sign = if value.is_sign_negative() { "-" } else { "" };
    if value == 0.0 {
        return format!("{sign}0.0");
    }
    // `{:e}` do Rust já produz os dígitos mínimos de ida e volta: "1.2345e3".
    let scientific = format!("{:e}", value.abs());
    let (mantissa, exponent) = scientific.split_once('e').unwrap_or((&scientific, "0"));
    let exponent: i32 = exponent.parse().unwrap_or(0);
    let digits: String = mantissa.chars().filter(char::is_ascii_digit).collect();
    if (-4..16).contains(&exponent) {
        if exponent >= 0 {
            let point = exponent as usize + 1;
            let (integer, fraction) = if digits.len() > point {
                (digits[..point].to_string(), digits[point..].to_string())
            } else {
                (
                    format!("{digits}{}", "0".repeat(point - digits.len())),
                    String::new(),
                )
            };
            let fraction = if fraction.is_empty() {
                "0".to_string()
            } else {
                fraction
            };
            format!("{sign}{integer}.{fraction}")
        } else {
            format!("{sign}0.{}{digits}", "0".repeat((-exponent - 1) as usize))
        }
    } else {
        let (first, rest) = digits.split_at(1);
        let body = if rest.is_empty() {
            first.to_string()
        } else {
            format!("{first}.{rest}")
        };
        let exponent_sign = if exponent < 0 { '-' } else { '+' };
        format!("{sign}{body}e{exponent_sign}{:02}", exponent.abs())
    }
}

/// `str(valor)` do Python para valores JSON (usado onde o Python faz `str(...)`).
///
/// Listas e objetos saem como JSON, não como `repr` do Python — só aparecem em
/// dados corrompidos, e o resultado continua sendo texto explícito.
pub fn py_str(value: &Value) -> String {
    match value {
        Value::Null => "None".into(),
        Value::Bool(flag) => if *flag { "True" } else { "False" }.into(),
        Value::String(text) => text.clone(),
        Value::Number(number) => {
            if number.is_i64() || number.is_u64() {
                number.to_string()
            } else {
                py_float_repr(number.as_f64().unwrap_or(f64::NAN))
            }
        }
        Value::Array(_) | Value::Object(_) => value.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn splitlines_matches_python() {
        assert_eq!(
            splitlines("a\nb\r\nc\rd\x0ce\u{2028}f\n"),
            ["a", "b", "c", "d", "e", "f"]
        );
        assert_eq!(splitlines(""), Vec::<&str>::new());
        assert_eq!(splitlines("\n\n"), ["", ""]);
        assert_eq!(splitlines("sem fim"), ["sem fim"]);
    }

    #[test]
    fn float_repr_matches_python() {
        for (value, expected) in [
            (1.0, "1.0"),
            (1.5, "1.5"),
            (1e16, "1e+16"),
            (1e-7, "1e-07"),
            (0.0001, "0.0001"),
            (0.00001, "1e-05"),
            (12345.678, "12345.678"),
            (123456789012345680.0, "1.2345678901234568e+17"),
            (-0.0, "-0.0"),
            (1e15, "1000000000000000.0"),
            (2.5e-300, "2.5e-300"),
        ] {
            assert_eq!(py_float_repr(value), expected, "{value}");
        }
        assert_eq!(py_str(&json!(null)), "None");
        assert_eq!(py_str(&json!(true)), "True");
        assert_eq!(py_str(&json!(12345)), "12345");
        assert_eq!(py_str(&json!(1.0)), "1.0");
    }
}
