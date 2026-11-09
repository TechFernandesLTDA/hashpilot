//! Consultas somente leitura ao `nvidia-smi` usadas pelo serviço do rig:
//! `gpu_agent.detect_nvidia_gpus` (com `query_capabilities` e `catalog_identity`) e
//! `gpu_telemetry.read_gpu_samples`. Nenhuma função aqui escreve clocks, potência ou
//! ventoinhas.
//!
//! As funções recebem o comando como closure (`SmiCommand`), para que os testes usem
//! respostas gravadas; [`smi_command`] é o `nvidia-smi` real, com prazo de 4 s.

use std::collections::HashMap;
use std::io::Read;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::sync::OnceLock;
use std::time::{Duration, Instant};

use hashpilot_core::numbers::numeric_str;
use serde_json::{Map, Value, json};

/// Executa o `nvidia-smi` com os argumentos dados e devolve o stdout sem espaços nas
/// pontas, ou o erro como texto.
pub type SmiCommand<'a> = &'a mut dyn FnMut(&[String]) -> Result<String, String>;

const TIMEOUT: Duration = Duration::from_secs(4);
pub const CAPABILITY_TTL: f64 = 600.0;
const UNAVAILABLE: [&str; 4] = ["N/A", "[N/A]", "NOT SUPPORTED", "[NOT SUPPORTED]"];
const TEXT_FIELDS: [(&str, &str); 6] = [
    ("driver_version", "driver_version"),
    ("serial", "serial"),
    ("pci.device_id", "pci_device_id"),
    ("pci.sub_device_id", "pci_subdevice_id"),
    ("vbios_version", "vbios_version"),
    ("compute_cap", "compute_capability"),
];
const NUMERIC_FIELDS: [(&str, &str); 7] = [
    ("power.min_limit", "power_min_w"),
    ("power.max_limit", "power_max_w"),
    ("power.default_limit", "power_default_w"),
    ("power.limit", "power_limit_w"),
    ("clocks.max.graphics", "graphics_max_mhz"),
    ("memory.total", "memory_mib"),
    ("temperature.gpu.tlimit", "temperature_limit_c"),
];
/// Campos de `read_gpu_samples`, na ordem do Python.
pub const SAMPLE_FIELDS: [&str; 15] = [
    "uuid",
    "index",
    "name",
    "driver_version",
    "temperature.gpu",
    "power.draw",
    "power.limit",
    "clocks.gr",
    "clocks.mem",
    "utilization.gpu",
    "utilization.memory",
    "memory.used",
    "memory.total",
    "fan.speed",
    "pstate",
];
const SAMPLE_NUMERIC: std::ops::Range<usize> = 4..14;

/// Caminho do executável do driver (PATH e, no Windows, System32), ou `None`.
pub fn nvidia_smi_path() -> Option<PathBuf> {
    let name = if cfg!(windows) {
        "nvidia-smi.exe"
    } else {
        "nvidia-smi"
    };
    let from_path = std::env::var_os("PATH")
        .into_iter()
        .flat_map(|paths| std::env::split_paths(&paths).collect::<Vec<_>>())
        .map(|directory| directory.join(name))
        .find(|candidate| candidate.is_file());
    from_path.or_else(|| {
        if !cfg!(windows) {
            return None;
        }
        let windows =
            std::env::var_os("WINDIR").map_or_else(|| PathBuf::from("C:/Windows"), PathBuf::from);
        Some(windows.join("System32").join(name)).filter(|candidate| candidate.is_file())
    })
}

fn hidden(command: &mut Command) -> &mut Command {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    command
}

/// `gpu_agent.smi_command`: executa o `nvidia-smi` com prazo de 4 s, sem janela.
pub fn run_smi(arguments: &[&str]) -> Result<String, String> {
    let executable = nvidia_smi_path().ok_or("Driver NVIDIA/nvidia-smi indisponível")?;
    let mut child = hidden(Command::new(executable).args(arguments))
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| error.to_string())?;
    let mut stdout = child.stdout.take().ok_or("stdout indisponível")?;
    let mut stderr = child.stderr.take().ok_or("stderr indisponível")?;
    let out = std::thread::spawn(move || {
        let mut buffer = Vec::new();
        stdout.read_to_end(&mut buffer).map(|_| buffer)
    });
    let err = std::thread::spawn(move || {
        let mut buffer = Vec::new();
        stderr.read_to_end(&mut buffer).map(|_| buffer)
    });
    let deadline = Instant::now() + TIMEOUT;
    let status = loop {
        if let Some(status) = child.try_wait().map_err(|error| error.to_string())? {
            break status;
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            return Err(format!(
                "nvidia-smi não respondeu em {} s",
                TIMEOUT.as_secs()
            ));
        }
        std::thread::sleep(Duration::from_millis(20));
    };
    let read = |handle: std::thread::JoinHandle<std::io::Result<Vec<u8>>>| {
        handle
            .join()
            .ok()
            .and_then(Result::ok)
            .map(|bytes| String::from_utf8_lossy(&bytes).into_owned())
            .unwrap_or_default()
    };
    let (stdout, stderr) = (read(out), read(err));
    if !status.success() {
        let message = if stderr.trim().is_empty() {
            stdout
        } else {
            stderr
        };
        return Err(message.trim().chars().take(240).collect());
    }
    Ok(stdout.trim().to_string())
}

/// [`run_smi`] na forma de [`SmiCommand`].
pub fn smi_command(arguments: &[String]) -> Result<String, String> {
    let borrowed: Vec<&str> = arguments.iter().map(String::as_str).collect();
    run_smi(&borrowed)
}

/// `str.splitlines()` do Python.
pub fn splitlines(text: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut start = 0;
    let mut chars = text.char_indices().peekable();
    while let Some((position, character)) = chars.next() {
        let breaks = matches!(
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
        if !breaks {
            continue;
        }
        lines.push(&text[start..position]);
        let mut end = position + character.len_utf8();
        if character == '\r'
            && let Some((next, '\n')) = chars.peek().copied()
        {
            chars.next();
            end = next + 1;
        }
        start = end;
    }
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}

/// Uma linha no `csv.reader` padrão do Python (vírgula, aspas duplas, sem
/// `skipinitialspace`). Linha vazia vira lista vazia.
pub fn csv_row(line: &str) -> Vec<String> {
    if line.is_empty() {
        return Vec::new();
    }
    let mut fields = Vec::new();
    let mut field = String::new();
    let mut at_start = true;
    let mut quoted = false;
    let mut chars = line.chars().peekable();
    while let Some(character) = chars.next() {
        if quoted {
            if character == '"' {
                if chars.peek() == Some(&'"') {
                    chars.next();
                    field.push('"');
                } else {
                    quoted = false;
                }
            } else {
                field.push(character);
            }
        } else if character == ',' {
            fields.push(std::mem::take(&mut field));
            at_start = true;
            continue;
        } else if character == '"' && at_start {
            quoted = true;
        } else {
            field.push(character);
        }
        at_start = false;
    }
    fields.push(field);
    fields
}

fn rows(text: &str) -> Vec<Vec<String>> {
    splitlines(text).into_iter().map(csv_row).collect()
}

fn text_value(value: &str) -> Value {
    let value = value.trim();
    if value.is_empty() || UNAVAILABLE.contains(&value.to_uppercase().as_str()) {
        Value::Null
    } else {
        json!(value)
    }
}

fn number(value: &str) -> Value {
    numeric_str(value).map_or(Value::Null, |number| json!(number))
}

fn args(items: &[&str]) -> Vec<String> {
    items.iter().map(|item| (*item).to_owned()).collect()
}

/// `gpu_agent.query_capabilities(identifier)`: uma consulta por placa; se o driver não
/// aceitar um campo, cai na consulta campo a campo.
pub fn query_capabilities(identifier: &str, command: SmiCommand, now: f64) -> Map<String, Value> {
    let fields: Vec<(&str, &str, bool)> = TEXT_FIELDS
        .iter()
        .map(|(field, key)| (*field, *key, true))
        .chain(
            NUMERIC_FIELDS
                .iter()
                .map(|(field, key)| (*field, *key, false)),
        )
        .collect();
    let convert = |text: bool, value: &str| {
        if text {
            text_value(value)
        } else {
            number(value)
        }
    };
    let mut result = Map::new();
    let query = format!(
        "--query-gpu={}",
        fields
            .iter()
            .map(|(field, _, _)| *field)
            .collect::<Vec<_>>()
            .join(",")
    );
    let batch = command(&args(&[
        "-i",
        identifier,
        &query,
        "--format=csv,noheader,nounits",
    ]))
    .and_then(|output| {
        let row = rows(&output).into_iter().next().unwrap_or_default();
        if row.len() == fields.len() {
            Ok(row)
        } else {
            Err("Resposta NVIDIA incompleta".to_owned())
        }
    });
    match batch {
        Ok(row) => {
            for ((_, key, text), value) in fields.iter().zip(&row) {
                result.insert((*key).into(), convert(*text, value));
            }
        }
        Err(_) => {
            for (field, key, text) in &fields {
                let query = format!("--query-gpu={field}");
                let value = command(&args(&[
                    "-i",
                    identifier,
                    &query,
                    "--format=csv,noheader,nounits",
                ]))
                .map_or(Value::Null, |value| convert(*text, &value));
                result.insert((*key).into(), value);
            }
        }
    }
    let mut clocks: Vec<i64> = command(&args(&[
        "-i",
        identifier,
        "--query-supported-clocks=graphics",
        "--format=csv,noheader,nounits",
    ]))
    .map(|output| {
        splitlines(&output)
            .into_iter()
            .filter_map(numeric_str)
            .filter(|value| *value > 0.0 && value.fract() == 0.0)
            .map(|value| value as i64)
            .collect()
    })
    .unwrap_or_default();
    clocks.sort_unstable();
    clocks.dedup();
    result.insert("supported_graphics_clocks_mhz".into(), json!(clocks));
    result.insert("capabilities_queried_at".into(), json!(now));
    result
}

fn catalog() -> &'static Option<Value> {
    static CATALOG: OnceLock<Option<Value>> = OnceLock::new();
    CATALOG
        .get_or_init(|| serde_json::from_str(include_str!("../../../data/nvidia-gpus.json")).ok())
}

/// `gpu_agent.catalog_identity(name, pci_device_id)`: identidade do catálogo; nunca
/// vira limite de controle.
pub fn catalog_identity(name: &str, pci_device_id: &Value) -> Map<String, Value> {
    let lookup = || -> Option<Map<String, Value>> {
        let gpus = catalog().as_ref()?.get("gpus")?.as_array()?;
        let normalized = name
            .split_whitespace()
            .collect::<Vec<_>>()
            .join(" ")
            .to_lowercase();
        let matches: Vec<&Value> = gpus
            .iter()
            .filter(|item| {
                let mut names = vec![item.get("name").map(text_of)];
                if let Some(aliases) = item.get("aliases").and_then(Value::as_array) {
                    names.extend(aliases.iter().map(|alias| Some(text_of(alias))));
                }
                names
                    .into_iter()
                    .flatten()
                    .any(|candidate| candidate.to_lowercase() == normalized)
            })
            .collect();
        let [item] = matches.as_slice() else {
            return Some(Map::new());
        };
        let raw = match pci_device_id {
            Value::Null => String::new(),
            Value::String(text) if text.is_empty() => String::new(),
            other => text_of(other),
        };
        let stripped = if raw.len() >= 2 && raw[..2].eq_ignore_ascii_case("0x") {
            &raw[2..]
        } else {
            raw.as_str()
        };
        let mut code = stripped.to_uppercase();
        if code.chars().count() == 8 {
            code = code.chars().take(4).collect();
        }
        if !code.is_empty() {
            let known = item
                .get("pci_device_ids")
                .and_then(Value::as_array)
                .is_some_and(|ids| ids.iter().any(|id| id.as_str() == Some(code.as_str())));
            if !known {
                return Some(
                    [("catalog_match".to_owned(), json!("name_only_pci_mismatch"))]
                        .into_iter()
                        .collect(),
                );
            }
        }
        let field = |key: &str| item.get(key).cloned().unwrap_or(Value::Null);
        let mut result = Map::new();
        result.insert("catalog_id".into(), item.get("id")?.clone());
        result.insert("architecture".into(), field("architecture"));
        result.insert("form_factor".into(), field("form_factor"));
        result.insert("family".into(), field("family"));
        result.insert(
            "catalog_match".into(),
            json!(if code.is_empty() {
                "name_only"
            } else {
                "name_and_pci"
            }),
        );
        result.insert(
            "catalog_sources".into(),
            item.get("sources").cloned().unwrap_or_else(|| json!([])),
        );
        Some(result)
    };
    lookup().unwrap_or_default()
}

fn text_of(value: &Value) -> String {
    value
        .as_str()
        .map_or_else(|| value.to_string(), str::to_owned)
}

/// `gpu_agent.validate_device`: só GPUs NVIDIA com UUID do driver.
pub fn validate_device(gpu: &Value) -> Result<(), String> {
    let uuid = gpu.get("uuid").map(text_of).unwrap_or_default();
    let valid = gpu.get("vendor") == Some(&json!("NVIDIA"))
        && uuid.strip_prefix("GPU-").is_some_and(|rest| {
            !rest.is_empty() && rest.chars().all(|c| c.is_ascii_alphanumeric() || c == '-')
        });
    if valid {
        Ok(())
    } else {
        Err("O agente aceita apenas GPUs NVIDIA identificadas pelo driver".into())
    }
}

/// Capacidades guardadas por `(uuid, pci_bus_id, nome)`.
pub type CapabilityCache = HashMap<(String, String, String), Map<String, Value>>;

/// `gpu_agent.detect_nvidia_gpus(command, max_devices, cache, now)`.
pub fn detect_nvidia_gpus(
    command: SmiCommand,
    max_devices: Option<i64>,
    mut cache: Option<&mut CapabilityCache>,
    now: f64,
) -> Result<Vec<Value>, String> {
    if max_devices.is_some_and(|limit| limit <= 0) {
        return Ok(Vec::new());
    }
    let output = command(&args(&[
        "--query-gpu=index,name,uuid,pci.bus_id",
        "--format=csv,noheader,nounits",
    ]))?;
    let mut devices = Vec::new();
    for row in rows(&output) {
        let [index, name, identifier, pci] = row.as_slice() else {
            continue;
        };
        let (index, name, identifier, pci) =
            (index.trim(), name.trim(), identifier.trim(), pci.trim());
        if index.is_empty()
            || !index.chars().all(|c| c.is_ascii_digit())
            || !identifier.starts_with("GPU-")
        {
            continue;
        }
        let key = (identifier.to_owned(), pci.to_owned(), name.to_owned());
        let fresh = |entry: &Map<String, Value>| {
            entry
                .get("capabilities_queried_at")
                .and_then(Value::as_f64)
                .is_some_and(|at| (0.0..CAPABILITY_TTL).contains(&(now - at)))
        };
        let cached = match cache
            .as_deref_mut()
            .and_then(|cache| cache.get(&key).cloned())
        {
            Some(entry) if fresh(&entry) => entry,
            _ => {
                let entry = query_capabilities(identifier, command, now);
                if let Some(cache) = cache.as_deref_mut() {
                    cache.insert(key, entry.clone());
                }
                entry
            }
        };
        let mut gpu = Map::new();
        gpu.insert("vendor".into(), json!("NVIDIA"));
        let index: u64 = index
            .parse()
            .map_err(|_| format!("invalid literal for int() with base 10: '{index}'"))?;
        gpu.insert("index".into(), json!(index));
        gpu.insert("name".into(), json!(name));
        gpu.insert("uuid".into(), json!(identifier));
        gpu.insert("pci_bus_id".into(), json!(pci));
        gpu.extend(cached);
        let pci_device_id = gpu.get("pci_device_id").cloned().unwrap_or(Value::Null);
        gpu.extend(catalog_identity(name, &pci_device_id));
        gpu.entry("architecture").or_insert(Value::Null);
        let gpu = Value::Object(gpu);
        validate_device(&gpu)?;
        devices.push(gpu);
        if max_devices.is_some_and(|limit| devices.len() as i64 >= limit.max(0)) {
            break;
        }
    }
    Ok(devices)
}

/// `gpu_telemetry.read_gpu_samples(devices)`: uma consulta para todas as placas.
/// Erros e ausência não viram zeros.
pub fn read_gpu_samples(
    devices: &[Value],
    command: SmiCommand,
    now: f64,
) -> Result<Map<String, Value>, String> {
    let mut result = Map::new();
    for gpu in devices {
        validate_device(gpu)?;
        let identifier = text_of(&gpu["uuid"]);
        let index = gpu.get("index").cloned().ok_or("'index'")?;
        let name = gpu.get("name").cloned().ok_or("'name'")?;
        result.insert(
            identifier.clone(),
            json!({"uuid": identifier, "index": index, "name": name, "sampled_at": now,
                "error": "GPU não apareceu na consulta NVIDIA"}),
        );
    }
    if devices.is_empty() {
        return Ok(result);
    }
    let query = format!("--query-gpu={}", SAMPLE_FIELDS.join(","));
    match command(&args(&[&query, "--format=csv,noheader,nounits"])) {
        Ok(output) => {
            for row in rows(&output) {
                let values: Vec<String> = row.iter().map(|value| value.trim().to_owned()).collect();
                if values.len() != SAMPLE_FIELDS.len() || !result.contains_key(&values[0]) {
                    continue;
                }
                let Ok(index) = values[1].parse::<i64>() else {
                    continue;
                };
                let mut sample = Map::new();
                for (position, (field, value)) in SAMPLE_FIELDS.iter().zip(&values).enumerate() {
                    let converted = if position == 1 {
                        json!(index)
                    } else if SAMPLE_NUMERIC.contains(&position) {
                        number(value)
                    } else {
                        json!(value)
                    };
                    sample.insert((*field).into(), converted);
                }
                sample.insert("sampled_at".into(), json!(now));
                let temperature = sample.get("temperature.gpu").and_then(Value::as_f64);
                let error = match temperature {
                    Some(value) if (0.0..=120.0).contains(&value) => Value::Null,
                    _ => json!("Temperatura NVIDIA indisponível ou inválida"),
                };
                sample.insert("error".into(), error);
                result.insert(values[0].clone(), Value::Object(sample));
            }
        }
        Err(error) => {
            let message: String = error.chars().take(240).collect();
            for sample in result.values_mut() {
                sample["error"] = json!(message);
            }
        }
    }
    Ok(result)
}
