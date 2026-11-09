//! Semântica do Python de que o histórico precisa: `json.dumps` compacto,
//! `str.rstrip`, veracidade (`bool(x)`), igualdade numérica e o texto exato dos
//! erros comuns (`KeyError`, `AttributeError`, `TypeError`).
//!
//! O texto do erro importa: o Python grava `str(error)` em `history-rejected.jsonl`
//! e em `last_error`, e o port precisa gravar a mesma coisa.

use hashpilot_core::text::py_float_repr;
use serde_json::Value;

/// `json.dumps(valor, ensure_ascii=False, separators=(",", ":"))`, byte a byte.
///
/// Os números de ponto flutuante saem como `repr(float)` (`1e+16`, `1e-07`, `100.0`),
/// diferente do formato do `serde_json`; o escape de texto é o mesmo nos dois.
pub fn encode(value: &Value) -> String {
    let mut out = String::new();
    write_value(&mut out, value, (",", ":"));
    out
}

/// `json.dumps(valor, ensure_ascii=False)` com os separadores padrão (`", "` e `": "`),
/// como o `finance-cache.json`.
pub fn encode_spaced(value: &Value) -> String {
    let mut out = String::new();
    write_value(&mut out, value, (", ", ": "));
    out
}

/// `json.dumps(valor, ensure_ascii=False, indent=2)`, o formato do `atomic_json` para
/// arquivos lidos por pessoas (`rig-manager.json`, `settings.json`).
pub fn encode_indented(value: &Value) -> String {
    let mut out = String::new();
    write_indented(&mut out, value, 0);
    out
}

fn write_indented(out: &mut String, value: &Value, level: usize) {
    let newline = |out: &mut String, level: usize| {
        out.push('\n');
        out.push_str(&"  ".repeat(level));
    };
    match value {
        Value::Array(items) if !items.is_empty() => {
            out.push('[');
            for (position, item) in items.iter().enumerate() {
                if position > 0 {
                    out.push(',');
                }
                newline(out, level + 1);
                write_indented(out, item, level + 1);
            }
            newline(out, level);
            out.push(']');
        }
        Value::Object(map) if !map.is_empty() => {
            out.push('{');
            for (position, (key, item)) in map.iter().enumerate() {
                if position > 0 {
                    out.push(',');
                }
                newline(out, level + 1);
                write_text(out, key);
                out.push_str(": ");
                write_indented(out, item, level + 1);
            }
            newline(out, level);
            out.push('}');
        }
        other => write_value(out, other, (", ", ": ")),
    }
}

fn write_value(out: &mut String, value: &Value, separators: (&str, &str)) {
    match value {
        Value::Null => out.push_str("null"),
        Value::Bool(flag) => out.push_str(if *flag { "true" } else { "false" }),
        Value::Number(number) => {
            if number.is_f64() {
                out.push_str(&py_float_repr(number.as_f64().unwrap_or(f64::NAN)));
            } else {
                out.push_str(&number.to_string());
            }
        }
        Value::String(text) => write_text(out, text),
        Value::Array(items) => {
            out.push('[');
            for (position, item) in items.iter().enumerate() {
                if position > 0 {
                    out.push_str(separators.0);
                }
                write_value(out, item, separators);
            }
            out.push(']');
        }
        Value::Object(map) => {
            out.push('{');
            for (position, (key, item)) in map.iter().enumerate() {
                if position > 0 {
                    out.push_str(separators.0);
                }
                write_text(out, key);
                out.push_str(separators.1);
                write_value(out, item, separators);
            }
            out.push('}');
        }
    }
}

/// Escape do `json.dumps` com `ensure_ascii=False`: só aspas, barra invertida e
/// caracteres de controle (`\u001f` em hexadecimal minúsculo); o resto sai cru.
fn write_text(out: &mut String, text: &str) {
    out.push('"');
    for character in text.chars() {
        match character {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            control if (control as u32) < 0x20 => {
                out.push_str(&format!("\\u{:04x}", control as u32));
            }
            other => out.push(other),
        }
    }
    out.push('"');
}

/// Nome do tipo Python de um valor JSON, como aparece nas mensagens de erro.
pub fn type_name(value: &Value) -> &'static str {
    match value {
        Value::Null => "NoneType",
        Value::Bool(_) => "bool",
        Value::Number(number) if number.is_f64() => "float",
        Value::Number(_) => "int",
        Value::String(_) => "str",
        Value::Array(_) => "list",
        Value::Object(_) => "dict",
    }
}

/// `bool(valor)`: vazio, zero, `None` e `False` são falsos.
pub fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(flag) => *flag,
        Value::Number(number) => number.as_f64().is_some_and(|x| x != 0.0),
        Value::String(text) => !text.is_empty(),
        Value::Array(items) => !items.is_empty(),
        Value::Object(map) => !map.is_empty(),
    }
}

/// `str(KeyError(chave))`.
pub fn key_error(key: &str) -> String {
    format!("'{key}'")
}

/// `objeto.get(chave)`: `None` se faltar; erro se o objeto não for um dicionário.
pub fn get<'a>(object: &'a Value, key: &str) -> Result<Option<&'a Value>, String> {
    match object {
        Value::Object(map) => Ok(map.get(key)),
        other => Err(format!(
            "'{}' object has no attribute 'get'",
            type_name(other)
        )),
    }
}

/// `objeto[chave]` com a mensagem de erro do Python.
pub fn index<'a>(object: &'a Value, key: &str) -> Result<&'a Value, String> {
    match object {
        Value::Object(map) => map.get(key).ok_or_else(|| key_error(key)),
        Value::Array(_) => Err("list indices must be integers or slices, not str".into()),
        Value::String(_) => Err("string indices must be integers, not 'str'".into()),
        other => Err(format!(
            "'{}' object is not subscriptable",
            type_name(other)
        )),
    }
}

/// Número como o Python o compara (`True == 1`, `1 == 1.0`, `-0.0 == 0.0`).
pub fn number_key(value: &Value) -> Option<f64> {
    let number = match value {
        Value::Bool(flag) => f64::from(u8::from(*flag)),
        Value::Number(number) => number.as_f64()?,
        _ => return None,
    };
    Some(if number == 0.0 { 0.0 } else { number })
}

/// `a == b` do Python para valores JSON.
pub fn py_eq(left: &Value, right: &Value) -> bool {
    match (number_key(left), number_key(right)) {
        (Some(a), Some(b)) => a == b,
        _ => left == right,
    }
}

/// `str.rstrip()`: o Python também considera espaço os separadores `\x1c`–`\x1f`.
pub fn rstrip(text: &str) -> &str {
    text.trim_end_matches(|c: char| c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c))
}

/// `repr(texto)` para mensagens de erro (`could not convert string to float: 'abc'`).
pub fn repr_str(text: &str) -> String {
    let quote = if text.contains('\'') && !text.contains('"') {
        '"'
    } else {
        '\''
    };
    let mut out = String::from(quote);
    for character in text.chars() {
        match character {
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if c == quote => {
                out.push('\\');
                out.push(c);
            }
            c if (c as u32) < 0x20 || (0x7f..0xa0).contains(&(c as u32)) => {
                out.push_str(&format!("\\x{:02x}", c as u32));
            }
            c => out.push(c),
        }
    }
    out.push(quote);
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn encode_matches_python_json_dumps() {
        let value = json!({"b": [1e16, 1e-7, -0.0, 0.1, 100.0, 12345678901234567890_u64, -3],
                           "a": "ação \u{2028} \u{1} \"x\" \\ / \t", "c": null, "d": true});
        assert_eq!(
            encode(&value),
            "{\"b\":[1e+16,1e-07,-0.0,0.1,100.0,12345678901234567890,-3],\"a\":\"ação \u{2028} \\u0001 \\\"x\\\" \\\\ / \\t\",\"c\":null,\"d\":true}"
        );
        assert_eq!(
            encode_spaced(&json!({"a": [1, 2.5], "b": {}})),
            "{\"a\": [1, 2.5], \"b\": {}}"
        );
    }

    #[test]
    fn python_semantics_helpers() {
        assert!(py_eq(&json!(true), &json!(1)));
        assert!(py_eq(&json!(1), &json!(1.0)));
        assert!(!py_eq(&json!(null), &json!(false)));
        assert_eq!(rstrip("linha\x1c\x1f \r\n"), "linha");
        assert_eq!(index(&json!({}), "x").unwrap_err(), "'x'");
        assert_eq!(
            index(&json!(null), "x").unwrap_err(),
            "'NoneType' object is not subscriptable"
        );
        assert_eq!(
            get(&json!([1]), "x").unwrap_err(),
            "'list' object has no attribute 'get'"
        );
        assert_eq!(repr_str("abc"), "'abc'");
        assert_eq!(repr_str("it's"), "\"it's\"");
        assert!(!truthy(&json!(0.0)) && truthy(&json!("0")));
    }
}
