//! Histórico SQLite do HashPilot (fase 2 do port).
//!
//! Lê e grava o `history.sqlite3` do aplicativo Python sem conversão: mesmo esquema
//! (texto idêntico em `sqlite_master`), mesma migração v2 → v3, mesmas chaves
//! SHA-256 de deduplicação e o mesmo JSON, byte a byte. Um banco pode alternar entre
//! o Python e o Rust sem duplicar linhas nem perder offsets de log.
//!
//! - [`HistoryStore`]: gravação por fila e thread, como o `history.HistoryStore`.
//! - [`HistoryReader`]: leitura somente leitura (CLI), que nunca cria nem altera o banco.
//! - [`retention`]: redução da telemetria antiga e limpeza dos `hold` velhos do agente.

pub mod keys;
pub mod pyjson;
pub mod read;
pub mod retention;
pub mod schema;
mod store;
mod write;

use std::path::{Path, PathBuf};

pub use read::HistoryReader;
pub use store::{HistoryStore, RAW_MINER_FIELDS, StoreOptions};
pub use write::Clock;

/// Nome do arquivo dentro da pasta de estado (`PearlMonitor`).
pub const FILE_NAME: &str = "history.sqlite3";

/// `STATE_DIR / "history.sqlite3"`.
pub fn default_path(state_dir: &Path) -> PathBuf {
    state_dir.join(FILE_NAME)
}
