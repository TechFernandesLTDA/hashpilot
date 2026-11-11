//! Consultas do histórico — `restore`, `restore_gpu`, `gpu_stats`, `finance_state`
//! e `stats` do Python, com o mesmo formato de saída.
//!
//! Cada função decide como o Python: erro do SQLite ou JSON inválido vira o campo
//! `error` (ou estado vazio) quando o Python o captura; quando o Python deixa a
//! exceção subir (ex.: `json.loads(None)` numa linha antiga com `gpu_json` NULL),
//! o Rust devolve `Err` com a mesma mensagem.

use std::collections::HashMap;
use std::collections::hash_map::Entry;
use std::path::Path;
use std::time::Duration;

use rusqlite::types::Value as Sql;
use rusqlite::{Connection, OpenFlags, ToSql};
use serde_json::{Map, Value, json};

use crate::pyjson::{get, number_key, truthy, type_name};

/// Erro de leitura: `Caught` o Python transforma em resposta; `Raised` ele propaga.
#[derive(Debug)]
pub(crate) enum ReadError {
    Caught(String),
    Raised(String),
}

impl From<rusqlite::Error> for ReadError {
    fn from(error: rusqlite::Error) -> Self {
        ReadError::Caught(error.to_string())
    }
}

/// Erro de `get` (AttributeError) que o Python não captura nas leituras.
fn raised(message: String) -> ReadError {
    ReadError::Raised(message)
}

/// Abre o banco para leitura com os mesmos PRAGMAs do Python (`busy_timeout=200`).
///
/// `read_only` abre sem permissão de escrita (CLI); o arquivo nunca é criado aqui.
pub fn open_reader(path: &Path, read_only: bool) -> rusqlite::Result<Connection> {
    let access = if read_only {
        OpenFlags::SQLITE_OPEN_READ_ONLY
    } else {
        OpenFlags::SQLITE_OPEN_READ_WRITE
    };
    let connection = Connection::open_with_flags(path, access | OpenFlags::SQLITE_OPEN_NO_MUTEX)?;
    connection.busy_timeout(Duration::from_millis(200))?;
    Ok(connection)
}

/// Linhas de uma consulta; sem banco (arquivo ausente), nenhuma — como `HistoryStore.read`.
fn rows(
    connection: Option<&Connection>,
    sql: &str,
    parameters: &[&dyn ToSql],
) -> rusqlite::Result<Vec<Vec<Sql>>> {
    let Some(connection) = connection else {
        return Ok(Vec::new());
    };
    let mut statement = connection.prepare(sql)?;
    let columns = statement.column_count();
    let mapped = statement.query_map(parameters, |row| {
        (0..columns)
            .map(|column| row.get::<_, Sql>(column))
            .collect::<rusqlite::Result<Vec<_>>>()
    })?;
    mapped.collect()
}

/// Valor do SQLite como o `sqlite3` do Python o entrega (REAL → float, INTEGER → int).
pub fn sql_to_json(value: &Sql) -> Value {
    match value {
        Sql::Null | Sql::Blob(_) => Value::Null,
        Sql::Integer(integer) => json!(integer),
        Sql::Real(real) => json!(real),
        Sql::Text(text) => Value::String(text.clone()),
    }
}

/// `json.loads(coluna)`.
fn json_column(value: &Sql) -> Result<Value, ReadError> {
    let text = match value {
        Sql::Text(text) => text.as_str(),
        Sql::Blob(bytes) => {
            std::str::from_utf8(bytes).map_err(|error| ReadError::Caught(error.to_string()))?
        }
        Sql::Null => return Err(type_error("NoneType")),
        Sql::Integer(_) => return Err(type_error("int")),
        Sql::Real(_) => return Err(type_error("float")),
    };
    // JSONDecodeError é ValueError: o Python captura. O texto da mensagem difere.
    serde_json::from_str(text).map_err(|error| ReadError::Caught(error.to_string()))
}

fn type_error(name: &str) -> ReadError {
    ReadError::Raised(format!(
        "the JSON object must be str, bytes or bytearray, not {name}"
    ))
}

/// `json.loads(x) or {}`.
fn object_or_empty(value: Value) -> Value {
    if truthy(&value) { value } else { json!({}) }
}

/// `valor >= since` do Python, só para números (e booleanos).
fn at_least(value: &Value, since: f64) -> Result<bool, ReadError> {
    match number_key(value) {
        Some(number) => Ok(number >= since),
        None => Err(ReadError::Raised(format!(
            "'>=' not supported between instances of '{}' and 'float'",
            type_name(value)
        ))),
    }
}

/// Séries do gráfico: dicionário do Python com chave numérica, depois `sorted(items())`.
///
/// O índice por hash mantém a montagem linear: o histórico real tem centenas de
/// milhares de amostras, e uma busca linear por chave a tornava quadrática.
struct Series {
    names: [&'static str; 4],
    points: [Vec<(f64, Value, Value)>; 4],
    positions: [HashMap<u64, usize>; 4],
}

impl Series {
    fn new() -> Self {
        Series {
            names: ["hash", "temp", "power", "fan"],
            points: Default::default(),
            positions: Default::default(),
        }
    }

    /// `d[chave] = valor`: chave igual (`1 == 1.0`) mantém a primeira e troca o valor.
    fn insert(&mut self, series: usize, key: &Value, value: &Value) {
        let Some(number) = number_key(key) else {
            return;
        };
        let points = &mut self.points[series];
        match self.positions[series].entry(number.to_bits()) {
            Entry::Occupied(slot) => points[*slot.get()].2 = value.clone(),
            Entry::Vacant(slot) => {
                slot.insert(points.len());
                points.push((number, key.clone(), value.clone()));
            }
        }
    }

    fn into_json(mut self) -> Value {
        let mut out = Map::new();
        for (name, points) in self.names.iter().zip(self.points.iter_mut()) {
            points.sort_by(|a, b| a.0.total_cmp(&b.0));
            let list = points
                .iter()
                .map(|(_, key, value)| json!([key, value]))
                .collect();
            out.insert((*name).to_string(), Value::Array(list));
        }
        Value::Object(out)
    }
}

const SENSORS: [(usize, &str); 3] = [(1, "temperature.gpu"), (2, "power.draw"), (3, "fan.speed")];

fn add_sensors(series: &mut Series, gpu: &Value, stamp: &Value) -> Result<(), ReadError> {
    for (position, field) in SENSORS {
        if let Some(value) = get(gpu, field).map_err(raised)? {
            if !value.is_null() {
                series.insert(position, stamp, value);
            }
        }
    }
    Ok(())
}

/// `HistoryStore.restore(since)`: séries da carteira e preferências.
pub(crate) fn restore(
    connection: Option<&Connection>,
    wallet: &str,
    since: f64,
) -> Result<Value, String> {
    let attempt = || -> Result<Value, ReadError> {
        let found = rows(
            connection,
            "SELECT timestamp,gpu_json,miner_json,source FROM telemetry WHERE wallet=? AND timestamp>=? ORDER BY timestamp",
            &[&wallet, &since],
        )?;
        let mut series = Series::new();
        for row in &found {
            let stamp = sql_to_json(&row[0]);
            let gpu = object_or_empty(json_column(&row[1])?);
            let miner = json_column(&row[2])?;
            if let Some(rate_time) = get(&miner, "hashrate_at").map_err(raised)? {
                if truthy(rate_time) && at_least(rate_time, since)? {
                    if let Some(rate) = get(&miner, "hashrate").map_err(raised)? {
                        if !rate.is_null() {
                            series.insert(0, rate_time, rate);
                        }
                    }
                }
            }
            add_sensors(&mut series, &gpu, &stamp)?;
        }
        let mut preferences = Map::new();
        for row in rows(
            connection,
            "SELECT name,data_json FROM preferences WHERE wallet=?",
            &[&wallet],
        )? {
            let Sql::Text(name) = &row[0] else { continue };
            preferences.insert(name.clone(), json_column(&row[1])?);
        }
        Ok(json!({"points": series.into_json(), "preferences": preferences, "count": found.len()}))
    };
    match attempt() {
        Ok(value) => Ok(value),
        Err(ReadError::Caught(message)) => {
            Ok(json!({"points": {}, "preferences": {}, "count": 0, "error": message}))
        }
        Err(ReadError::Raised(message)) => Err(message),
    }
}

/// `HistoryStore.restore_gpu(gpu_uuid, since, wallet)`.
///
/// O hashrate só entra se tiver sido medido até 180 s antes da amostra.
pub(crate) fn restore_gpu(
    connection: Option<&Connection>,
    gpu_uuid: &str,
    since: f64,
    wallet: &str,
) -> Result<Value, String> {
    let attempt = || -> Result<Value, ReadError> {
        if wallet.is_empty() {
            return Err(ReadError::Caught("Carteira da GPU obrigatória".into()));
        }
        let found = rows(
            connection,
            "SELECT timestamp,gpu_json,miner_json FROM gpu_telemetry WHERE gpu_uuid=? AND wallet=? AND timestamp>=? ORDER BY timestamp",
            &[&gpu_uuid, &wallet, &since],
        )?;
        let mut series = Series::new();
        for row in &found {
            let stamp = sql_to_json(&row[0]);
            let stamp_number = number_key(&stamp).unwrap_or(f64::NAN);
            let gpu = object_or_empty(json_column(&row[1])?);
            let miner = json_column(&row[2])?;
            if let Some(measured) = get(&miner, "hashrate_at").map_err(raised)? {
                if truthy(measured) && at_least(measured, since)? {
                    let age = stamp_number - number_key(measured).unwrap_or(f64::NAN);
                    if (0.0..=180.0).contains(&age) {
                        if let Some(rate) = get(&miner, "hashrate").map_err(raised)? {
                            if !rate.is_null() {
                                series.insert(0, measured, rate);
                            }
                        }
                    }
                }
            }
            add_sensors(&mut series, &gpu, &stamp)?;
        }
        let identity = rows(
            connection,
            "SELECT identity_json FROM gpu_devices WHERE gpu_uuid=?",
            &[&gpu_uuid],
        )?;
        let identity = match identity.first() {
            Some(row) => json_column(&row[0])?,
            None => json!({}),
        };
        Ok(json!({"points": series.into_json(), "count": found.len(), "identity": identity}))
    };
    match attempt() {
        Ok(value) => Ok(value),
        Err(ReadError::Caught(message)) => {
            Ok(json!({"points": {}, "count": 0, "identity": {}, "error": message}))
        }
        Err(ReadError::Raised(message)) => Err(message),
    }
}

/// `HistoryStore.gpu_stats(gpu_uuid=None)`: `{uuid: [linhas, primeira, última]}`.
pub(crate) fn gpu_stats(connection: Option<&Connection>, gpu_uuid: Option<&str>) -> Value {
    let mut sql =
        String::from("SELECT gpu_uuid,COUNT(*),MIN(timestamp),MAX(timestamp) FROM gpu_telemetry");
    let found = match gpu_uuid {
        Some(identifier) => {
            sql.push_str(" WHERE gpu_uuid=? GROUP BY gpu_uuid");
            rows(connection, &sql, &[&identifier])
        }
        None => {
            sql.push_str(" GROUP BY gpu_uuid");
            rows(connection, &sql, &[])
        }
    };
    let Ok(found) = found else {
        return json!({});
    };
    let mut out = Map::new();
    for row in found {
        let key = match &row[0] {
            Sql::Text(text) => text.clone(),
            other => hashpilot_core::text::py_str(&sql_to_json(other)),
        };
        out.insert(
            key,
            json!([
                sql_to_json(&row[1]),
                sql_to_json(&row[2]),
                sql_to_json(&row[3])
            ]),
        );
    }
    Value::Object(out)
}

/// `HistoryStore.finance_state(since, wallet)`: pontos e a fonte mais recente de cada tipo.
pub(crate) fn finance_state(
    connection: Option<&Connection>,
    wallet: &str,
    since: f64,
) -> Result<Value, String> {
    let attempt = || -> Result<Value, ReadError> {
        let snapshots: Vec<Value> = rows(
            connection,
            "SELECT timestamp,total_prl FROM finance_snapshots WHERE wallet=? AND timestamp>=? ORDER BY timestamp",
            &[&wallet, &since],
        )?
        .iter()
        .map(|row| json!({"timestamp": sql_to_json(&row[0]), "total_prl": sql_to_json(&row[1])}))
        .collect();
        let mut sources = Map::new();
        for row in rows(
            connection,
            "SELECT source,MAX(fetched_at),data_json FROM finance_sources WHERE wallet=? GROUP BY source",
            &[&wallet],
        )? {
            let Sql::Text(name) = &row[0] else { continue };
            sources.insert(
                name.clone(),
                json!({"fetched_at": sql_to_json(&row[1]), "data": json_column(&row[2])?}),
            );
        }
        Ok(json!({"wallet": wallet, "snapshots": snapshots, "sources": sources}))
    };
    match attempt() {
        Ok(value) => Ok(value),
        Err(ReadError::Caught(_)) => Ok(json!({"wallet": wallet, "snapshots": [], "sources": {}})),
        Err(ReadError::Raised(message)) => Err(message),
    }
}

/// `HistoryStore.stats()`: `[linhas, primeiro, último]` por tabela. Sem captura de erro,
/// como no Python.
pub(crate) fn stats(connection: Option<&Connection>, wallet: &str) -> Result<Value, String> {
    let mut out = Map::new();
    for (table, column) in [
        ("telemetry", "timestamp"),
        ("gpu_telemetry", "timestamp"),
        ("events", "timestamp"),
        ("finance_snapshots", "timestamp"),
        ("finance_sources", "fetched_at"),
    ] {
        let found = rows(
            connection,
            &format!("SELECT COUNT(*),MIN({column}),MAX({column}) FROM {table} WHERE wallet=?"),
            &[&wallet],
        )
        .map_err(|error| error.to_string())?;
        let Some(row) = found.first() else {
            return Err("list index out of range".into());
        };
        out.insert(
            table.to_string(),
            Value::Array(row.iter().map(sql_to_json).collect()),
        );
    }
    Ok(Value::Object(out))
}

/// Leitor somente leitura do `history.sqlite3` (CLI): nunca cria nem altera o banco.
pub struct HistoryReader {
    connection: Option<Connection>,
}

impl HistoryReader {
    /// Arquivo ausente equivale a um histórico vazio, como no Python.
    pub fn open(path: &Path) -> rusqlite::Result<Self> {
        let connection = if path.exists() {
            Some(open_reader(path, true)?)
        } else {
            None
        };
        Ok(HistoryReader { connection })
    }

    pub fn restore(&self, wallet: &str, since: f64) -> Result<Value, String> {
        restore(self.connection.as_ref(), wallet, since)
    }

    pub fn restore_gpu(&self, gpu_uuid: &str, since: f64, wallet: &str) -> Result<Value, String> {
        restore_gpu(self.connection.as_ref(), gpu_uuid, since, wallet)
    }

    pub fn gpu_stats(&self, gpu_uuid: Option<&str>) -> Value {
        gpu_stats(self.connection.as_ref(), gpu_uuid)
    }

    pub fn finance_state(&self, wallet: &str, since: f64) -> Result<Value, String> {
        finance_state(self.connection.as_ref(), wallet, since)
    }

    pub fn stats(&self, wallet: &str) -> Result<Value, String> {
        stats(self.connection.as_ref(), wallet)
    }
}
