//! Configuração local — porte de `config.py`, com plataforma e ambiente explícitos.
//!
//! O produto é exclusivo do Windows: fora dele, `resolve_state_dir` só aceita a
//! pasta temporária de testes (`HASHPILOT_TEST_STATE_DIR`), nunca o produto real.

use std::path::{Path, PathBuf};

use serde_json::{Map, Value};

use crate::wallet::AUTHOR_WALLET;

pub const DEFAULT_WALLET: &str = AUTHOR_WALLET;
pub const DEFAULT_POOL: &str = "prl.kryptex.network:7048";
pub const TEST_STATE_ENV: &str = "HASHPILOT_TEST_STATE_DIR";
pub const UNSUPPORTED_PLATFORM: &str =
    "O HashPilot está disponível somente para Windows 10/11 de 64 bits.";
/// Nome legado da pasta de estado, mantido por compatibilidade.
pub const STATE_DIR_NAME: &str = "PearlMonitor";

/// Plataforma no formato do `sys.platform` do Python.
pub fn current_platform() -> &'static str {
    if cfg!(windows) {
        "win32"
    } else if cfg!(target_os = "macos") {
        "darwin"
    } else {
        "linux"
    }
}

/// `config.resolve_state_dir`.
pub fn resolve_state_dir(
    platform: &str,
    env: &dyn Fn(&str) -> Option<String>,
) -> Result<PathBuf, String> {
    if platform == "win32" {
        let local = env("LOCALAPPDATA").unwrap_or_else(|| {
            let home = env("USERPROFILE")
                .or_else(|| env("HOME"))
                .unwrap_or_default();
            format!("{home}/AppData/Local")
        });
        return Ok(py_path(&format!("{local}/{STATE_DIR_NAME}")));
    }
    match env(TEST_STATE_ENV).filter(|value| !value.is_empty()) {
        Some(root) => Ok(py_path(&format!("{root}/{STATE_DIR_NAME}"))),
        None => Err(UNSUPPORTED_PLATFORM.into()),
    }
}

/// `config.load_settings`: ausente vira objeto vazio; ilegível é erro explícito.
pub fn load_settings(path: &Path) -> Result<Map<String, Value>, String> {
    let text = match std::fs::read_to_string(path) {
        Ok(text) => text,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(Map::new()),
        Err(error) => {
            return Err(format!(
                "Não foi possível ler a configuração local em {}: {error}",
                path.display()
            ));
        }
    };
    let value: Value =
        serde_json::from_str(text.trim_start_matches('\u{feff}')).map_err(|error| {
            format!(
                "Não foi possível ler a configuração local em {}: {error}",
                path.display()
            )
        })?;
    match value {
        Value::Object(map) => Ok(map),
        _ => Err("settings.json precisa conter um objeto JSON".into()),
    }
}

/// `config.log_dir`: `log_dir` das configurações, com variáveis expandidas, ou
/// `<pai do estado>/Mining/Pearl/logs`.
pub fn log_dir(
    state_dir: &Path,
    settings: &Map<String, Value>,
    env: &dyn Fn(&str) -> Option<String>,
) -> PathBuf {
    let configured = settings.get("log_dir").filter(|value| truthy_text(value));
    let raw = match configured {
        Some(Value::String(text)) => text.clone(),
        Some(other) => crate::text::py_str(other),
        None => {
            let parent = state_dir.parent().unwrap_or(state_dir);
            format!("{}/Mining/Pearl/logs", parent.to_string_lossy())
        }
    };
    let expanded = if cfg!(windows) {
        expandvars_nt(&raw, env)
    } else {
        expandvars_posix(&raw, env)
    };
    py_path(&expanded)
}

fn truthy_text(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(flag) => *flag,
        Value::String(text) => !text.is_empty(),
        Value::Number(number) => number.as_f64().is_some_and(|value| value != 0.0),
        Value::Array(items) => !items.is_empty(),
        Value::Object(map) => !map.is_empty(),
    }
}

/// `str(Path(texto))` do Python na plataforma atual.
pub fn py_path(text: &str) -> PathBuf {
    PathBuf::from(if cfg!(windows) {
        windows_path_str(text)
    } else {
        posix_path_str(text)
    })
}

/// `str(PurePosixPath(texto))`: barras repetidas e `.` somem, `//` inicial fica.
pub fn posix_path_str(text: &str) -> String {
    if text.is_empty() {
        return ".".into();
    }
    let root = if text.starts_with("//") && !text.starts_with("///") {
        "//"
    } else if text.starts_with('/') {
        "/"
    } else {
        ""
    };
    let parts: Vec<&str> = text
        .split('/')
        .filter(|part| !part.is_empty() && *part != ".")
        .collect();
    let joined = parts.join("/");
    if root.is_empty() && joined.is_empty() {
        ".".into()
    } else {
        format!("{root}{joined}")
    }
}

/// Aproximação de `str(PureWindowsPath(texto))`: `/` vira `\`, separadores repetidos
/// e `.` somem, prefixo UNC `\\` é preservado.
pub fn windows_path_str(text: &str) -> String {
    let unified = text.replace('/', "\\");
    let unc = unified.starts_with("\\\\");
    let parts: Vec<&str> = unified
        .split('\\')
        .filter(|part| !part.is_empty() && *part != ".")
        .collect();
    let mut joined = parts.join("\\");
    if unc {
        joined = format!("\\\\{joined}");
    } else if unified.starts_with('\\') {
        joined = format!("\\{joined}");
    }
    if joined.len() == 2 && joined.ends_with(':') {
        joined.push('\\');
    }
    if joined.is_empty() {
        ".".into()
    } else {
        joined
    }
}

/// `posixpath.expandvars`: `$NOME` e `${NOME}` (nome ASCII `\w`); ausente fica como está.
pub fn expandvars_posix(text: &str, env: &dyn Fn(&str) -> Option<String>) -> String {
    if !text.contains('$') {
        return text.to_string();
    }
    let chars: Vec<char> = text.chars().collect();
    let mut output = String::new();
    let mut index = 0;
    let word = |c: char| c.is_ascii_alphanumeric() || c == '_';
    while index < chars.len() {
        if chars[index] == '$' {
            if chars.get(index + 1) == Some(&'{') {
                if let Some(close) = chars[index + 2..].iter().position(|c| *c == '}') {
                    let name: String = chars[index + 2..index + 2 + close].iter().collect();
                    let end = index + 2 + close + 1;
                    match env(&name) {
                        Some(value) => output.push_str(&value),
                        None => output.extend(&chars[index..end]),
                    }
                    index = end;
                    continue;
                }
            } else {
                let length = chars[index + 1..].iter().take_while(|c| word(**c)).count();
                if length > 0 {
                    let name: String = chars[index + 1..index + 1 + length].iter().collect();
                    let end = index + 1 + length;
                    match env(&name) {
                        Some(value) => output.push_str(&value),
                        None => output.extend(&chars[index..end]),
                    }
                    index = end;
                    continue;
                }
            }
        }
        output.push(chars[index]);
        index += 1;
    }
    output
}

/// `ntpath.expandvars`: `%NOME%`, `$NOME`, `${NOME}`, `%%` e `$$` literais e nada
/// expandido entre aspas simples. No Windows a busca de variável ignora maiúsculas.
pub fn expandvars_nt(text: &str, env: &dyn Fn(&str) -> Option<String>) -> String {
    if !text.contains('$') && !text.contains('%') {
        return text.to_string();
    }
    let varchar = |c: char| c.is_ascii_alphanumeric() || c == '_' || c == '-';
    let mut path: Vec<char> = text.chars().collect();
    let mut output = String::new();
    let mut index = 0usize;
    while index < path.len() {
        let character = path[index];
        match character {
            '\'' => {
                path = path[index + 1..].to_vec();
                match path.iter().position(|c| *c == '\'') {
                    Some(close) => {
                        output.push('\'');
                        output.extend(&path[..=close]);
                        index = close;
                    }
                    None => {
                        output.push('\'');
                        output.extend(&path);
                        index = path.len().saturating_sub(1);
                    }
                }
            }
            '%' => {
                if path.get(index + 1) == Some(&'%') {
                    output.push('%');
                    index += 1;
                } else {
                    path = path[index + 1..].to_vec();
                    match path.iter().position(|c| *c == '%') {
                        Some(close) => {
                            let name: String = path[..close].iter().collect();
                            match env(&name) {
                                Some(value) => output.push_str(&value),
                                None => output.push_str(&format!("%{name}%")),
                            }
                            index = close;
                        }
                        None => {
                            output.push('%');
                            output.extend(&path);
                            index = path.len().saturating_sub(1);
                        }
                    }
                }
            }
            '$' => {
                if path.get(index + 1) == Some(&'$') {
                    output.push('$');
                    index += 1;
                } else if path.get(index + 1) == Some(&'{') {
                    path = path[index + 2..].to_vec();
                    match path.iter().position(|c| *c == '}') {
                        Some(close) => {
                            let name: String = path[..close].iter().collect();
                            match env(&name) {
                                Some(value) => output.push_str(&value),
                                None => output.push_str(&format!("${{{name}}}")),
                            }
                            index = close;
                        }
                        None => {
                            output.push_str("${");
                            output.extend(&path);
                            index = path.len().saturating_sub(1);
                        }
                    }
                } else {
                    let mut name = String::new();
                    index += 1;
                    while let Some(&next) = path.get(index) {
                        if !varchar(next) {
                            break;
                        }
                        name.push(next);
                        index += 1;
                    }
                    match env(&name) {
                        Some(value) => output.push_str(&value),
                        None => output.push_str(&format!("${name}")),
                    }
                    if index < path.len() {
                        index -= 1;
                    }
                }
            }
            other => output.push(other),
        }
        index += 1;
    }
    output
}

/// Lê variáveis do processo; no Windows sem diferenciar maiúsculas, como o Python.
pub fn process_env(name: &str) -> Option<String> {
    std::env::var(name).ok()
}
