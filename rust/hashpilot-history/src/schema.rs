//! Esquema e migrações do `history.sqlite3` — `HistoryStore.schema` do Python.
//!
//! Os comandos SQL são **idênticos, caractere a caractere**, aos do `history.py`: o
//! SQLite guarda o texto em `sqlite_master`, e o golden confere esse texto. Um banco
//! criado pelo Rust é indistinguível de um criado pelo Python.

use rusqlite::Connection;

/// `PRAGMA user_version` escrito por esta versão; um valor maior é recusado.
pub const SCHEMA_VERSION: i64 = 3;

pub const STATEMENTS: [&str; 8] = [
    "CREATE TABLE IF NOT EXISTS telemetry (\n            wallet TEXT NOT NULL, timestamp REAL NOT NULL, session TEXT NOT NULL,\n            source TEXT NOT NULL, gpu_json TEXT, miner_json TEXT NOT NULL,\n            miner_running INTEGER, errors_json TEXT, log_path TEXT,\n            PRIMARY KEY(wallet,timestamp))",
    "CREATE TABLE IF NOT EXISTS events (\n            wallet TEXT NOT NULL, event_key TEXT NOT NULL, timestamp REAL NOT NULL,\n            session TEXT NOT NULL, kind TEXT NOT NULL, data_json TEXT NOT NULL,\n            PRIMARY KEY(wallet,event_key))",
    "CREATE INDEX IF NOT EXISTS events_by_time ON events(wallet,timestamp)",
    "CREATE TABLE IF NOT EXISTS finance_snapshots (\n            wallet TEXT NOT NULL, timestamp REAL NOT NULL, total_prl REAL NOT NULL,\n            PRIMARY KEY(wallet,timestamp))",
    "CREATE TABLE IF NOT EXISTS finance_sources (\n            wallet TEXT NOT NULL, source TEXT NOT NULL, fetched_at REAL NOT NULL,\n            data_json TEXT NOT NULL, PRIMARY KEY(wallet,source,fetched_at))",
    "CREATE TABLE IF NOT EXISTS preferences (\n            wallet TEXT NOT NULL, name TEXT NOT NULL, data_json TEXT NOT NULL,\n            PRIMARY KEY(wallet,name))",
    "CREATE TABLE IF NOT EXISTS gpu_devices (\n            gpu_uuid TEXT PRIMARY KEY,first_seen REAL NOT NULL,last_seen REAL NOT NULL,identity_json TEXT NOT NULL)",
    "CREATE TABLE IF NOT EXISTS gpu_telemetry (\n            gpu_uuid TEXT NOT NULL,timestamp REAL NOT NULL,wallet TEXT NOT NULL,session TEXT NOT NULL,\n            source TEXT NOT NULL,gpu_json TEXT,miner_json TEXT NOT NULL,miner_running INTEGER,\n            enabled INTEGER,errors_json TEXT,log_path TEXT,PRIMARY KEY(gpu_uuid,wallet,timestamp))",
];

/// Tabela da migração v2 → v3: a chave primária de `gpu_telemetry` ganhou a carteira.
const GPU_TELEMETRY_NEW: &str = "CREATE TABLE gpu_telemetry_new (\n                    gpu_uuid TEXT NOT NULL,timestamp REAL NOT NULL,wallet TEXT NOT NULL,session TEXT NOT NULL,\n                    source TEXT NOT NULL,gpu_json TEXT,miner_json TEXT NOT NULL,miner_running INTEGER,\n                    enabled INTEGER,errors_json TEXT,log_path TEXT,PRIMARY KEY(gpu_uuid,wallet,timestamp))";
const GPU_FIELDS: &str = "gpu_uuid,timestamp,wallet,session,source,gpu_json,miner_json,miner_running,enabled,errors_json,log_path";

/// Por que o esquema não pôde ser preparado.
#[derive(Debug)]
pub enum SchemaFailure {
    /// Layout que esta versão não grava com segurança (`HistorySchemaError` no Python).
    /// O texto é o mesmo do Python, sem o prefixo "Histórico indisponível: ".
    Unsupported(String),
    /// Erro do SQLite (banco ocupado, disco); o gravador tenta de novo.
    Sqlite(rusqlite::Error),
}

impl From<rusqlite::Error> for SchemaFailure {
    fn from(error: rusqlite::Error) -> Self {
        SchemaFailure::Sqlite(error)
    }
}

/// Cria as tabelas que faltam e migra `gpu_telemetry`, numa transação `IMMEDIATE`.
pub fn ensure(connection: &Connection) -> Result<(), SchemaFailure> {
    connection
        .pragma_update_and_check(None, "journal_mode", "WAL", |row| row.get::<_, String>(0))?;
    connection.execute_batch("BEGIN IMMEDIATE")?;
    let result = migrate(connection).and_then(|()| Ok(connection.execute_batch("COMMIT")?));
    if result.is_err() {
        // Como o `connection.rollback()` do Python: um erro aqui não esconde o original.
        let _ = connection.execute_batch("ROLLBACK");
    }
    result
}

fn migrate(connection: &Connection) -> Result<(), SchemaFailure> {
    let version: i64 = connection.pragma_query_value(None, "user_version", |row| row.get(0))?;
    if version > SCHEMA_VERSION {
        return Err(SchemaFailure::Unsupported(
            "Versão futura do histórico não suportada".into(),
        ));
    }
    for statement in STATEMENTS {
        connection.execute_batch(statement)?;
    }
    let primary = primary_key(connection, "gpu_telemetry")?;
    if primary == ["gpu_uuid", "timestamp"] {
        connection.execute_batch(GPU_TELEMETRY_NEW)?;
        connection.execute_batch(&format!(
            "INSERT INTO gpu_telemetry_new ({GPU_FIELDS}) SELECT {GPU_FIELDS} FROM gpu_telemetry"
        ))?;
        let old: i64 =
            connection.query_row("SELECT COUNT(*) FROM gpu_telemetry", [], |row| row.get(0))?;
        let new: i64 =
            connection.query_row("SELECT COUNT(*) FROM gpu_telemetry_new", [], |row| {
                row.get(0)
            })?;
        if old != new {
            return Err(SchemaFailure::Unsupported(
                "Migração de GPU perdeu linhas".into(),
            ));
        }
        connection.execute_batch("DROP TABLE gpu_telemetry")?;
        connection.execute_batch("ALTER TABLE gpu_telemetry_new RENAME TO gpu_telemetry")?;
    } else if primary != ["gpu_uuid", "wallet", "timestamp"] {
        return Err(SchemaFailure::Unsupported(
            "Esquema de GPU desconhecido".into(),
        ));
    }
    connection.execute_batch(
        "CREATE INDEX IF NOT EXISTS gpu_telemetry_wallet_time ON gpu_telemetry(wallet,timestamp)",
    )?;
    if version < SCHEMA_VERSION {
        connection.pragma_update(None, "user_version", SCHEMA_VERSION)?;
    }
    Ok(())
}

/// Colunas da chave primária na ordem da chave (`PRAGMA table_info`, coluna `pk`).
pub fn primary_key(connection: &Connection, table: &str) -> rusqlite::Result<Vec<String>> {
    let mut statement = connection.prepare(&format!("PRAGMA table_info({table})"))?;
    let mut columns = statement
        .query_map([], |row| {
            Ok((row.get::<_, i64>(5)?, row.get::<_, String>(1)?))
        })?
        .collect::<rusqlite::Result<Vec<_>>>()?;
    columns.retain(|(position, _)| *position > 0);
    columns.sort_by_key(|(position, _)| *position);
    Ok(columns.into_iter().map(|(_, name)| name).collect())
}
