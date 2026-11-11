//! Retenção diária dos bancos, em segundo plano no `rig --serve`.
//!
//! Roda [`hashpilot_history::retention::run`] numa thread própria: o laço de 2 s do
//! serviço (mineradores, supervisores, pedidos) nunca espera por ela, e ela só toca
//! SQLite (nada de processos, clocks, potência ou ventoinhas). A primeira passada é
//! adiada ([`FIRST_DELAY`]) para não disputar o banco com a partida do painel e do
//! serviço; depois, uma vez por dia, ou em uma hora se a anterior falhou (banco ocupado,
//! por exemplo). Cada transação é curta e em WAL; erro nunca derruba o serviço: o
//! relatório da última passada fica em `retention-status.json`, na pasta de estado.

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread::JoinHandle;
use std::time::Duration;

use hashpilot_history::retention::{self, Policy};
use serde_json::json;

/// Espera antes da primeira passada depois que o serviço sobe.
pub const FIRST_DELAY: Duration = Duration::from_secs(15 * 60);
/// Intervalo entre passadas bem-sucedidas.
pub const INTERVAL: Duration = Duration::from_secs(24 * 60 * 60);
/// Nova tentativa depois de uma passada com erro.
pub const RETRY: Duration = Duration::from_secs(60 * 60);

/// Arquivo com o relatório da última passada.
pub const STATUS_FILE: &str = "retention-status.json";

/// Tarefa em segundo plano; soltar (`drop`) pede para ela parar.
pub struct DailyRetention {
    stop: Arc<AtomicBool>,
    handle: Option<JoinHandle<()>>,
}

fn epoch() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0.0, |elapsed| elapsed.as_secs_f64())
}

/// Dorme `duration` em passos de um segundo; `false` se pediram para parar.
fn wait(stop: &AtomicBool, duration: Duration) -> bool {
    let deadline = std::time::Instant::now() + duration;
    while std::time::Instant::now() < deadline {
        if stop.load(Ordering::SeqCst) {
            return false;
        }
        std::thread::sleep(Duration::from_secs(1).min(deadline - std::time::Instant::now()));
    }
    !stop.load(Ordering::SeqCst)
}

/// Uma passada completa: histórico da pasta de estado e todos os bancos do agente.
/// Devolve `true` se não houve erro.
pub fn run_once(state_dir: &Path, log_dir: &Path, now: f64, policy: &Policy) -> bool {
    let agents = retention::agent_databases(state_dir, log_dir);
    let report = retention::run(
        &hashpilot_history::default_path(state_dir),
        &agents,
        now,
        policy,
        true,
    );
    let ok = report["errors"].as_array().is_none_or(Vec::is_empty);
    let status = json!({"finished_at": epoch(), "ok": ok, "report": report});
    // Sem o arquivo de status a retenção continua valendo; não há o que fazer além de seguir.
    let _ = crate::fsutil::atomic_json(&state_dir.join(STATUS_FILE), &status, false, false);
    ok
}

impl DailyRetention {
    pub fn spawn(state_dir: PathBuf, log_dir: PathBuf) -> Self {
        let stop = Arc::new(AtomicBool::new(false));
        let flag = stop.clone();
        let handle = std::thread::Builder::new()
            .name("retencao-bancos".into())
            .spawn(move || {
                let mut delay = FIRST_DELAY;
                while wait(&flag, delay) {
                    let ok = run_once(&state_dir, &log_dir, epoch(), &Policy::default());
                    delay = if ok { INTERVAL } else { RETRY };
                }
            })
            .ok();
        DailyRetention { stop, handle }
    }

    /// A thread ainda está viva (para testes e diagnóstico).
    pub fn is_running(&self) -> bool {
        self.handle
            .as_ref()
            .is_some_and(|handle| !handle.is_finished())
    }

    /// Pede para parar e espera a thread sair (no máximo a passada em curso).
    pub fn shutdown(mut self) {
        self.stop.store(true, Ordering::SeqCst);
        if let Some(handle) = self.handle.take() {
            let _ = handle.join();
        }
    }
}

impl Drop for DailyRetention {
    fn drop(&mut self) {
        // Só sinaliza: uma passada em curso vai até o fim (transações curtas) e nenhuma
        // outra começa; o encerramento do serviço não espera por ela. Se o processo sair
        // no meio, o SQLite desfaz a transação aberta.
        self.stop.store(true, Ordering::SeqCst);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(label: &str) -> PathBuf {
        let folder = std::env::temp_dir().join(format!(
            "hp-retention-task-{label}-{}-{}",
            std::process::id(),
            crate::gpu_agent::uuid4_hex()
        ));
        std::fs::create_dir_all(&folder).unwrap();
        folder
    }

    #[test]
    fn passada_sem_bancos_grava_so_o_status() {
        let folder = scratch("vazia");
        assert!(run_once(
            &folder,
            &folder,
            1_800_000_000.0,
            &Policy::default()
        ));
        let status: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(folder.join(STATUS_FILE)).unwrap())
                .unwrap();
        assert_eq!(status["ok"], true);
        assert_eq!(status["report"]["history"]["exists"], false);
        assert!(
            !folder.join("history.sqlite3").exists(),
            "nunca cria o banco"
        );
        let _ = std::fs::remove_dir_all(&folder);
    }

    #[test]
    fn tarefa_espera_o_atraso_inicial_e_para_quando_pedem() {
        let folder = scratch("thread");
        let task = DailyRetention::spawn(folder.clone(), folder.clone());
        std::thread::sleep(Duration::from_millis(50));
        assert!(task.is_running());
        let started = std::time::Instant::now();
        task.shutdown();
        assert!(started.elapsed() < Duration::from_secs(3));
        // Ainda no atraso inicial: nenhuma passada rodou.
        assert!(!folder.join(STATUS_FILE).exists());
        let _ = std::fs::remove_dir_all(&folder);
    }
}
