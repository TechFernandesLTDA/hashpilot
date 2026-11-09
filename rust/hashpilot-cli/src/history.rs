//! `hashpilot history`: consultas ao `history.sqlite3`, abertas SOMENTE PARA LEITURA.
//!
//! O banco nunca é criado nem alterado (nem o evento de sessão que o painel grava):
//! arquivo ausente equivale a um histórico vazio, como no Python.

use std::path::Path;

use hashpilot_core::config::{DEFAULT_WALLET, load_settings};
use hashpilot_core::text::py_str;
use hashpilot_history::HistoryReader;
use serde_json::{Value, json};

pub enum Query {
    Stats,
    Restore,
    Gpu(String),
    Gpus(Option<String>),
    Finance,
}

impl Query {
    pub fn parse(name: &str, rest: &[String]) -> Result<Self, String> {
        match (name, rest) {
            ("stats", []) => Ok(Query::Stats),
            ("restore", []) => Ok(Query::Restore),
            ("gpu", [uuid]) => Ok(Query::Gpu(uuid.clone())),
            ("gpu", _) => Err("history gpu exige exatamente um UUID".into()),
            ("gpus", []) => Ok(Query::Gpus(None)),
            ("gpus", [uuid]) => Ok(Query::Gpus(Some(uuid.clone()))),
            ("finance", []) => Ok(Query::Finance),
            ("stats" | "restore" | "gpus" | "finance", _) => {
                Err(format!("history {name}: argumentos demais"))
            }
            (other, _) => Err(format!("consulta de histórico desconhecida: {other}")),
        }
    }
}

/// `config.WALLET`: `str(settings.get("wallet", DEFAULT_WALLET)).strip()`.
pub fn settings_wallet(state_dir: &Path) -> Result<String, String> {
    let settings = load_settings(&state_dir.join("settings.json"))?;
    Ok(settings
        .get("wallet")
        .map_or_else(|| DEFAULT_WALLET.to_string(), py_str)
        .trim()
        .to_string())
}

pub fn run(database: &Path, wallet: &str, since: f64, query: &Query) -> Result<Value, String> {
    let reader = HistoryReader::open(database)
        .map_err(|error| format!("{}: {error}", database.display()))?;
    let result = match query {
        Query::Stats => reader.stats(wallet)?,
        Query::Restore => reader.restore(wallet, since)?,
        Query::Gpu(uuid) => reader.restore_gpu(uuid, since, wallet)?,
        Query::Gpus(uuid) => reader.gpu_stats(uuid.as_deref()),
        Query::Finance => reader.finance_state(wallet, since)?,
    };
    Ok(json!({
        "database": database,
        "exists": database.exists(),
        "wallet": wallet,
        "since": since,
        "result": result,
    }))
}
