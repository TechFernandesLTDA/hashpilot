//! `process_backend.ProcessBackend`: criação, identidade e encerramento seguro dos
//! processos do rig.
//!
//! Só encerra processos criados por este gerenciador. A identidade é PID + horário de
//! criação (FILETIME, em texto) + executável, e protege contra PID reutilizado e
//! processos alheios. O formato da identidade é o do Python, então o Rust reconhece
//! um minerador que o serviço Python iniciou (e vice-versa) pelo `rig-manager.json`.

use std::collections::{HashMap, VecDeque};
use std::fs::OpenOptions;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

use hashpilot_core::miner_log::parse_miner_device_map;
use serde_json::{Map, Value, json};

use crate::argv::windows_argv;
use crate::win32::{self, Metadata};

/// `subprocess.CREATE_NO_WINDOW`.
pub const CREATE_NO_WINDOW: u32 = 0x0800_0000;
const COMMAND_LINE_CACHE: usize = 64;

/// `os.path.normcase(os.path.abspath(caminho))`: absoluto pelo `GetFullPathNameW`
/// (o mesmo do `ntpath.abspath`), separador `\` e minúsculas.
pub fn normalized_path(value: &str) -> String {
    let absolute = std::path::absolute(value).unwrap_or_else(|_| PathBuf::from(value));
    let mut text = absolute.to_string_lossy().replace('/', "\\").to_lowercase();
    // `normpath` tira a barra final, menos na raiz da unidade (`c:\`).
    while text.len() > 1 && text.ends_with('\\') && !(text.len() == 3 && text.as_bytes()[1] == b':')
    {
        text.pop();
    }
    text
}

fn text<'a>(identity: &'a Value, key: &str) -> &'a str {
    identity.get(key).and_then(Value::as_str).unwrap_or("")
}

/// PID válido do Python: inteiro positivo, não booleano.
fn pid_of(value: &Value) -> Option<u32> {
    value
        .as_u64()
        .filter(|pid| *pid > 0 && !value.is_boolean())
        .and_then(|pid| u32::try_from(pid).ok())
}

fn metadata_value(pid: u32, metadata: &Metadata) -> Value {
    json!({
        "pid": pid,
        "created_at": metadata.created_at.to_string(),
        "executable": metadata.executable,
    })
}

fn same_identity(current: &Metadata, identity: &Value) -> bool {
    current.created_at.to_string() == text(identity, "created_at")
        && normalized_path(&current.executable) == normalized_path(text(identity, "executable"))
}

fn random_hex() -> String {
    use std::hash::{BuildHasher, Hasher};
    let mut out = String::with_capacity(32);
    while out.len() < 32 {
        let mut hasher = std::collections::hash_map::RandomState::new().build_hasher();
        hasher.write_u128(
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map_or(0, |elapsed| elapsed.as_nanos()),
        );
        out.push_str(&format!("{:016x}", hasher.finish()));
    }
    out.truncate(32);
    out
}

/// Roda um comando do PowerShell sem janela e devolve a saída padrão em UTF-8.
fn powershell(script: &str, timeout: Duration) -> Result<String, String> {
    let script = format!("[Console]::OutputEncoding=[Text.UTF8Encoding]::new(); {script}");
    let mut command = Command::new("powershell.exe");
    command
        .args(["-NoProfile", "-Command", &script])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    no_window(&mut command);
    let child = command.spawn().map_err(|error| error.to_string())?;
    let output = wait_output(child, timeout)?;
    if !output.status.success() {
        return Err(format!("PowerShell terminou com {}", output.status));
    }
    Ok(String::from_utf8_lossy(&output.stdout).into_owned())
}

/// `subprocess.run(..., timeout=)`: passado o prazo, o filho é encerrado e é erro.
fn wait_output(mut child: Child, timeout: Duration) -> Result<std::process::Output, String> {
    let deadline = Instant::now() + timeout;
    loop {
        match child.try_wait() {
            Ok(Some(_)) => return child.wait_with_output().map_err(|error| error.to_string()),
            Ok(None) if Instant::now() >= deadline => {
                let _ = child.kill();
                let _ = child.wait();
                return Err(format!("tempo esgotado após {} s", timeout.as_secs()));
            }
            Ok(None) => std::thread::sleep(Duration::from_millis(20)),
            Err(error) => return Err(error.to_string()),
        }
    }
}

#[cfg(windows)]
fn no_window(command: &mut Command) {
    use std::os::windows::process::CommandExt;
    command.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(not(windows))]
fn no_window(_command: &mut Command) {}

/// Linhas do `ConvertTo-Json -Compress`: um objeto sozinho vira lista de um.
fn json_rows(output: &str) -> Result<Vec<Value>, String> {
    let trimmed = output.trim();
    let value: Value = if trimmed.is_empty() {
        json!([])
    } else {
        serde_json::from_str(trimmed).map_err(|error| error.to_string())?
    };
    Ok(match value {
        Value::Array(rows) => rows,
        Value::Null => Vec::new(),
        row => vec![row],
    })
}

/// `--gpu-id` de uma linha de comando do SRBMiner: `None` quando ausente ou inválido
/// (o chamador trata como "pode estar usando qualquer GPU").
pub fn selected_gpus(argv: &[String]) -> Option<Vec<String>> {
    let index = argv.iter().position(|item| item == "--gpu-id")?;
    let selected: Vec<String> = argv.get(index + 1)?.split(',').map(str::to_owned).collect();
    selected
        .iter()
        .all(|item| !item.is_empty() && item.chars().all(|c| c.is_ascii_digit()))
        .then_some(selected)
}

/// Decide, pela lista de mineradores já em execução, se a GPU `index` está livre.
/// Falha fechada: minerador sem linha de comando legível também bloqueia.
pub fn check_gpu_free(rows: &[Value], index: &str) -> Result<(), String> {
    for row in rows {
        let argv = windows_argv(row.get("CommandLine").and_then(Value::as_str).unwrap_or(""));
        if argv.is_empty() {
            return Err(
                "Minerador existente sem identidade legível; início bloqueado para evitar duplicação"
                    .into(),
            );
        }
        let busy = selected_gpus(&argv).is_none_or(|ids| ids.iter().any(|id| id == index));
        if busy {
            let pid = row
                .get("ProcessId")
                .map_or_else(|| "None".into(), Value::to_string);
            return Err(format!(
                "Minerador já existente para GPU {index} (PID {pid}); início bloqueado para evitar duplicação"
            ));
        }
    }
    Ok(())
}

/// Lista os SRBMiner em execução, com a linha de comando e as GPUs do `--gpu-id`
/// (`null` = pode estar usando qualquer uma). Só leitura.
pub fn running_miners() -> Result<Vec<Value>, String> {
    let script = "@(Get-CimInstance Win32_Process -Filter \"Name='SRBMiner-MULTI.exe'\" -ErrorAction Stop | Select-Object ProcessId,CommandLine) | ConvertTo-Json -Compress";
    let output = powershell(script, Duration::from_secs(8))?;
    Ok(json_rows(&output)?
        .into_iter()
        .map(|row| {
            let argv = windows_argv(row.get("CommandLine").and_then(Value::as_str).unwrap_or(""));
            json!({"pid": row.get("ProcessId"), "gpu_ids": selected_gpus(&argv), "argv_count": argv.len()})
        })
        .collect())
}

/// `process_backend.ProcessBackend`.
#[derive(Default)]
pub struct ProcessBackend {
    children: HashMap<u32, (Child, Value)>,
    /// Linha de comando é fixa desde a criação: (pid, criação, executável) a identifica.
    command_lines: HashMap<(u32, String, String), Vec<String>>,
    command_order: VecDeque<(u32, String, String)>,
}

impl ProcessBackend {
    pub fn new() -> Self {
        Self::default()
    }

    /// `inspect(pid)`: `{"pid", "created_at", "executable"}` de um processo vivo.
    pub fn inspect(&mut self, pid: &Value, command_line: bool) -> Option<Value> {
        let pid = pid_of(pid)?;
        let metadata = win32::process_metadata(pid)?;
        let mut value = metadata_value(pid, &metadata);
        if command_line {
            let key = (
                pid,
                metadata.created_at.to_string(),
                normalized_path(&metadata.executable),
            );
            if !self.command_lines.contains_key(&key) {
                let script = format!(
                    "$p=Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}'; if($p){{$p.CommandLine}}"
                );
                let output = powershell(&script, Duration::from_secs(8)).ok()?;
                let argv = windows_argv(output.trim());
                if argv.is_empty() {
                    value["argv"] = json!(argv);
                    return Some(value);
                }
                if self.command_lines.len() >= COMMAND_LINE_CACHE
                    && let Some(oldest) = self.command_order.pop_front()
                {
                    self.command_lines.remove(&oldest);
                }
                self.command_order.push_back(key.clone());
                self.command_lines.insert(key.clone(), argv);
            }
            value["argv"] = json!(self.command_lines[&key]);
        }
        Some(value)
    }

    /// `start`: cria o processo sem janela, com saída anexada ao log, e devolve a
    /// identidade com `argv` e um `launch_id` novo.
    pub fn start(
        &mut self,
        arguments: &[String],
        working_dir: &Path,
        log_path: Option<&Path>,
    ) -> Result<Value, String> {
        let (program, rest) = arguments
            .split_first()
            .ok_or_else(|| "Comando vazio".to_owned())?;
        let mut command = Command::new(program);
        command
            .args(rest)
            .current_dir(working_dir)
            .stdin(Stdio::null());
        match log_path {
            Some(path) => {
                if let Some(parent) = path.parent() {
                    std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
                }
                let output = OpenOptions::new()
                    .create(true)
                    .append(true)
                    .open(path)
                    .map_err(|error| error.to_string())?;
                let error_output = output.try_clone().map_err(|error| error.to_string())?;
                command.stdout(output).stderr(error_output);
            }
            None => {
                command.stdout(Stdio::null()).stderr(Stdio::null());
            }
        }
        no_window(&mut command);
        let mut child = command.spawn().map_err(|error| error.to_string())?;
        let Some(mut metadata) = self.inspect(&json!(child.id()), false) else {
            if child.try_wait().ok().flatten().is_none() {
                let _ = child.kill();
            }
            return Err("Processo encerrou durante a inicialização".into());
        };
        metadata["argv"] = json!(arguments);
        metadata["launch_id"] = json!(random_hex());
        self.children.insert(child.id(), (child, metadata.clone()));
        Ok(metadata)
    }

    /// `is_owned`: o processo da identidade ainda é o mesmo que foi criado.
    pub fn is_owned(&mut self, identity: &Value) -> bool {
        if !identity.get("argv").is_some_and(Value::is_array)
            || !hashpilot_history::pyjson::truthy(identity.get("launch_id").unwrap_or(&Value::Null))
        {
            return false;
        }
        let Some(pid) = identity.get("pid").and_then(pid_of) else {
            return false;
        };
        let Some(metadata) = win32::process_metadata(pid) else {
            return false;
        };
        if !same_identity(&metadata, identity) {
            return false;
        }
        if let Some((child, launched)) = self.children.get_mut(&pid)
            && launched.get("launch_id") == identity.get("launch_id")
        {
            return matches!(child.try_wait(), Ok(None));
        }
        let Some(actual) = self.inspect(&json!(pid), true) else {
            return false;
        };
        let expected: Vec<String> = identity["argv"]
            .as_array()
            .map(|items| items.iter().map(py_str).collect())
            .unwrap_or_default();
        let observed: Vec<String> =
            serde_json::from_value(actual["argv"].clone()).unwrap_or_default();
        !observed.is_empty()
            && !expected.is_empty()
            && normalized_path(&observed[0]) == normalized_path(&expected[0])
            && observed[1..] == expected[1..]
    }

    /// `stop`: encerra só um processo próprio; devolve se o pedido de encerramento saiu.
    pub fn stop(&mut self, identity: &Value) -> bool {
        if !self.is_owned(identity) {
            return false;
        }
        let Some(pid) = identity.get("pid").and_then(pid_of) else {
            return false;
        };
        if let Some((child, launched)) = self.children.get_mut(&pid)
            && launched.get("launch_id") == identity.get("launch_id")
        {
            // `Popen.terminate()` no Windows é `TerminateProcess`, como o `kill()`.
            return child.kill().is_ok();
        }
        win32::terminate_if(pid, &|current| same_identity(current, identity))
    }

    /// `wait`: espera o processo da identidade sair (ou o PID passar a ser outro).
    pub fn wait(&mut self, identity: &Value, timeout: Duration) -> bool {
        let deadline = Instant::now() + timeout;
        loop {
            let gone = identity
                .get("pid")
                .and_then(pid_of)
                .and_then(win32::process_metadata)
                .is_none_or(|current| !same_identity(&current, identity));
            if gone {
                if let Some(pid) = identity.get("pid").and_then(pid_of)
                    && let Some((child, _)) = self.children.get_mut(&pid)
                    && matches!(child.try_wait(), Ok(Some(_)))
                {
                    self.children.remove(&pid);
                }
                return true;
            }
            if Instant::now() >= deadline {
                return false;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
    }

    /// `assert_gpu_available`: bloqueia o início se um SRBMiner (órfão ou ilegível)
    /// pode estar usando a GPU `index`.
    pub fn assert_gpu_available(&self, index: &str) -> Result<(), String> {
        let script = "@(Get-CimInstance Win32_Process -Filter \"Name='SRBMiner-MULTI.exe'\" -ErrorAction Stop | Select-Object ProcessId,CommandLine) | ConvertTo-Json -Compress";
        let output = powershell(script, Duration::from_secs(8)).map_err(|_| {
            "Não foi possível verificar mineradores existentes; início bloqueado".to_owned()
        })?;
        check_gpu_free(&json_rows(&output)?, index)
    }

    /// `legacy_candidates`: processos que podem ser de uma sessão antiga (minerador,
    /// Python e PowerShell), com identidade completa e `launch_id` de legado.
    pub fn legacy_candidates(&mut self, miner_path: &Path) -> Result<Vec<Value>, String> {
        let name = miner_path
            .file_name()
            .map(|name| name.to_string_lossy().replace('\'', "''"))
            .unwrap_or_default();
        let script = format!(
            "@(Get-CimInstance Win32_Process -Filter \"Name = '{name}' OR Name = 'python.exe' OR Name = 'pythonw.exe' OR Name = 'powershell.exe' OR Name = 'pwsh.exe'\" | Select-Object ProcessId,ParentProcessId,CommandLine) | ConvertTo-Json -Compress"
        );
        let output = powershell(&script, Duration::from_secs(10)).map_err(|_| {
            "Não foi possível identificar com segurança os processos legados".to_owned()
        })?;
        let mut found = Vec::new();
        for item in json_rows(&output)? {
            if let Some(mut metadata) =
                self.inspect(item.get("ProcessId").unwrap_or(&Value::Null), false)
            {
                let line = item
                    .get("CommandLine")
                    .and_then(Value::as_str)
                    .unwrap_or("");
                metadata["argv"] = json!(windows_argv(line));
                metadata["launch_id"] = json!(format!("legacy-{}", random_hex()));
                metadata["parent_pid"] =
                    item.get("ParentProcessId").cloned().unwrap_or(Value::Null);
                found.push(metadata);
            }
        }
        Ok(found)
    }

    /// `miner_device_map`: pergunta ao SRBMiner (`--list-devices`, não minera) o índice
    /// de cada GPU NVIDIA e casa pelo barramento PCI.
    pub fn miner_device_map(
        &self,
        miner_path: &Path,
        devices: &[Value],
    ) -> Result<HashMap<String, i64>, String> {
        let mut command = Command::new(miner_path);
        command
            .args(["--list-devices", "--disable-gpu-amd", "--disable-gpu-intel"])
            .current_dir(miner_path.parent().unwrap_or(Path::new(".")))
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        no_window(&mut command);
        let failed = || {
            "SRBMiner não confirmou sua lista de dispositivos; mineração não iniciada".to_owned()
        };
        let child = command.spawn().map_err(|_| failed())?;
        let output = wait_output(child, Duration::from_secs(30)).map_err(|_| failed())?;
        if !output.status.success() {
            return Err(failed());
        }
        let text = format!(
            "{}\n{}",
            String::from_utf8_lossy(&output.stdout),
            String::from_utf8_lossy(&output.stderr)
        );
        parse_miner_device_map(&text, devices)
    }
}

/// `str(valor)` para um item de `argv` gravado no JSON.
fn py_str(value: &Value) -> String {
    hashpilot_core::text::py_str(value)
}

/// Identidade só com os campos que o Python grava, para comparar com o `inspect`.
pub fn identity_core(identity: &Value) -> Map<String, Value> {
    ["pid", "created_at", "executable"]
        .iter()
        .filter_map(|key| {
            identity
                .get(*key)
                .map(|value| ((*key).to_owned(), value.clone()))
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gpu_ids_and_busy_rules() {
        let argv = |line: &str| windows_argv(line);
        assert_eq!(
            selected_gpus(&argv("m --gpu-id 0,1")),
            Some(vec!["0".into(), "1".into()])
        );
        assert_eq!(selected_gpus(&argv("m --gpu-id a,1")), None);
        assert_eq!(selected_gpus(&argv("m --gpu-id")), None);
        assert_eq!(selected_gpus(&argv("m")), None);
        let row = |line: &str| json!({"ProcessId": 42, "CommandLine": line});
        assert!(check_gpu_free(&[row("m --gpu-id 1")], "0").is_ok());
        assert_eq!(
            check_gpu_free(&[row("m --gpu-id 0,1")], "1").unwrap_err(),
            "Minerador já existente para GPU 1 (PID 42); início bloqueado para evitar duplicação"
        );
        assert!(check_gpu_free(&[row("m --algorithm x")], "3").is_err());
        assert!(
            check_gpu_free(&[json!({"ProcessId": 7, "CommandLine": null})], "0")
                .unwrap_err()
                .starts_with("Minerador existente sem identidade legível")
        );
        assert_eq!(json_rows("{\"a\":1}").unwrap().len(), 1);
        assert!(json_rows("").unwrap().is_empty());
    }
}
