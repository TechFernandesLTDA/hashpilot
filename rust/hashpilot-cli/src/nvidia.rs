//! Consulta somente leitura ao `nvidia-smi` — porte de `nvidia_smi.py` e de
//! `gpu_telemetry.read_gpu_samples`. Nunca escreve clocks, potência ou ventoinhas.

use std::io::Read;
use std::path::PathBuf;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use hashpilot_core::numbers::numeric_str;
use serde_json::{Map, Value, json};

/// Campos consultados em lote, na mesma ordem do Python.
pub const FIELDS: [&str; 15] = [
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
const NUMERIC_FIELDS: std::ops::Range<usize> = 4..14;
const TIMEOUT: Duration = Duration::from_secs(4);

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

/// Executa o `nvidia-smi` com prazo; erro do driver volta como texto curto.
pub fn run_smi(arguments: &[&str]) -> Result<String, String> {
    let executable = nvidia_smi_path().ok_or("Driver NVIDIA/nvidia-smi indisponível")?;
    let mut child = hidden(Command::new(executable).args(arguments))
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|error| error.to_string())?;
    let mut stdout = child.stdout.take().expect("stdout canalizado");
    let mut stderr = child.stderr.take().expect("stderr canalizado");
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
    let stdout = String::from_utf8_lossy(
        &out.join()
            .map_err(|_| "leitura do stdout falhou")?
            .unwrap_or_default(),
    )
    .into_owned();
    let stderr = String::from_utf8_lossy(
        &err.join()
            .map_err(|_| "leitura do stderr falhou")?
            .unwrap_or_default(),
    )
    .into_owned();
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

pub fn now_epoch() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0.0, |elapsed| elapsed.as_secs_f64())
}

/// Converte uma linha CSV do `nvidia-smi` numa amostra. Valores `[N/A]` viram
/// ausência explícita; temperatura fora de 0–120 °C marca a amostra como inválida.
pub fn parse_row(line: &str, sampled_at: f64) -> Option<Map<String, Value>> {
    let values: Vec<&str> = line.split(',').map(str::trim).collect();
    if values.len() != FIELDS.len() || !values[0].starts_with("GPU-") {
        return None;
    }
    let index: i64 = values[1].parse().ok()?;
    let mut sample = Map::new();
    for (position, (field, value)) in FIELDS.iter().zip(&values).enumerate() {
        let entry = if position == 1 {
            json!(index)
        } else if NUMERIC_FIELDS.contains(&position) {
            numeric_str(value).map_or(Value::Null, |number| json!(number))
        } else {
            json!(value)
        };
        sample.insert((*field).to_string(), entry);
    }
    let temperature = sample.get("temperature.gpu").and_then(Value::as_f64);
    let error = match temperature {
        Some(value) if (0.0..=120.0).contains(&value) => Value::Null,
        _ => json!("Temperatura NVIDIA indisponível ou inválida"),
    };
    sample.insert("sampled_at".into(), json!(sampled_at));
    sample.insert("error".into(), error);
    Some(sample)
}

/// Uma consulta CSV para todas as placas.
pub fn query_gpus() -> Result<Vec<Map<String, Value>>, String> {
    let query = format!("--query-gpu={}", FIELDS.join(","));
    let output = run_smi(&[&query, "--format=csv,noheader,nounits"])?;
    let sampled_at = now_epoch();
    Ok(output
        .lines()
        .filter_map(|line| parse_row(line, sampled_at))
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_a_real_shaped_row_and_marks_missing_values() {
        let row = "GPU-bafb0ad5-dd9e-ee1d-a0c0-221c0dfbfc75, 0, NVIDIA GeForce RTX 3070, 617.14, 74, 152.21, 240.00, \
                   1740, 5001, 100, 3, 1024, 8192, 100, P2";
        let sample = parse_row(row, 10.0).unwrap();
        assert_eq!(sample["index"], json!(0));
        assert_eq!(sample["temperature.gpu"], json!(74.0));
        assert_eq!(sample["pstate"], json!("P2"));
        assert_eq!(sample["error"], Value::Null);
        let missing = row.replace(", 74,", ", [N/A],");
        let sample = parse_row(&missing, 10.0).unwrap();
        assert_eq!(sample["temperature.gpu"], Value::Null);
        assert_eq!(
            sample["error"],
            json!("Temperatura NVIDIA indisponível ou inválida")
        );
        assert!(parse_row("lixo", 0.0).is_none());
        assert!(parse_row(&row.replace(", 0,", ", x,"), 0.0).is_none());
    }
}
