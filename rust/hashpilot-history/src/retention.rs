//! Retenção dos bancos locais: reduz a telemetria antiga e apaga decisões `hold` velhas.
//!
//! - `history.sqlite3`: linhas de `gpu_telemetry` e da tabela legada `telemetry` com mais
//!   de [`Policy::max_age_seconds`] viram **uma linha por minuto** (por GPU, carteira e
//!   origem). Fica a última amostra do minuto, com a média dos sensores numéricos e do
//!   hashrate medido no minuto, e um campo `retention` no `miner_json` dizendo quantas
//!   amostras ela resume. Sensor ausente nunca vira zero: entra na média só o que existe.
//! - `gpu-agent.sqlite3`: decisões `hold` sem escrita no hardware e sem pedido de pausa,
//!   mais velhas que o limite, são apagadas. A última decisão de cada GPU fica sempre
//!   (é a que o `status` mostra); ajustes, emergências e as demais tabelas não mudam.
//!
//! Cada janela (uma hora de uma GPU, ou um bloco de ids de decisões) é uma transação
//! curta (`BEGIN IMMEDIATE`, milissegundos), com uma pausa entre elas: em WAL o painel e
//! o supervisor continuam gravando. Rodar de novo não muda nada (minuto já reduzido tem
//! uma linha só). Nunca há `VACUUM`: o espaço liberado é reaproveitado pelo SQLite.
//! Sem `apply`, o banco é aberto somente para leitura e só a contagem é devolvida.

use std::path::{Path, PathBuf};
use std::time::Duration;

use rusqlite::types::Value as Sql;
use rusqlite::{Connection, OpenFlags, OptionalExtension, ToSql};
use serde_json::{Map, Value, json};

use crate::pyjson::encode;

/// Telemetria e decisões ficam completas por 30 dias.
pub const DEFAULT_MAX_AGE_DAYS: f64 = 30.0;

/// Sensores do `nvidia-smi` (`gpu_telemetry.NUMERIC_FIELDS`) que viram média no minuto.
pub const SENSOR_FIELDS: [&str; 10] = [
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
];

/// Parâmetros da retenção. [`Policy::default`] é o do serviço.
#[derive(Clone, Debug)]
pub struct Policy {
    /// Idade a partir da qual a telemetria é reduzida e os `hold` são apagados.
    pub max_age_seconds: f64,
    /// Tamanho do balde da telemetria reduzida (uma linha por balde).
    pub bucket_seconds: f64,
    /// Janela de telemetria tratada por transação.
    pub window_seconds: f64,
    /// Ids de `decisions` examinados por transação.
    pub decision_chunk: i64,
    /// Espera entre duas transações, para o painel e o supervisor gravarem.
    pub pause: Duration,
    /// `busy_timeout` das conexões da retenção.
    pub busy_timeout: Duration,
}

impl Default for Policy {
    fn default() -> Self {
        Policy {
            max_age_seconds: DEFAULT_MAX_AGE_DAYS * 86_400.0,
            bucket_seconds: 60.0,
            window_seconds: 3_600.0,
            decision_chunk: 2_000,
            pause: Duration::from_millis(25),
            busy_timeout: Duration::from_secs(2),
        }
    }
}

fn sql_error(path: &Path) -> impl Fn(rusqlite::Error) -> String + '_ {
    move |error| format!("{}: {error}", path.display())
}

/// Abre um banco existente; nunca cria o arquivo.
fn open(path: &Path, apply: bool, policy: &Policy) -> Result<Connection, String> {
    let access = if apply {
        OpenFlags::SQLITE_OPEN_READ_WRITE
    } else {
        OpenFlags::SQLITE_OPEN_READ_ONLY
    };
    let connection = Connection::open_with_flags(path, access | OpenFlags::SQLITE_OPEN_NO_MUTEX)
        .map_err(sql_error(path))?;
    connection
        .busy_timeout(policy.busy_timeout)
        .map_err(sql_error(path))?;
    Ok(connection)
}

fn table_exists(connection: &Connection, name: &str) -> rusqlite::Result<bool> {
    connection
        .query_row(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            [name],
            |_| Ok(()),
        )
        .optional()
        .map(|found| found.is_some())
}

fn finite(value: &Value) -> Option<f64> {
    match value {
        Value::Number(number) => number.as_f64().filter(|number| number.is_finite()),
        _ => None,
    }
}

fn mean(values: &[f64]) -> Option<f64> {
    if values.is_empty() {
        None
    } else {
        Some(values.iter().sum::<f64>() / values.len() as f64)
    }
}

fn parse(text: &Sql) -> Value {
    match text {
        Sql::Text(text) => serde_json::from_str(text).unwrap_or(Value::Null),
        _ => Value::Null,
    }
}

/// Uma linha de telemetria lida para a redução.
struct Sample {
    rowid: i64,
    timestamp: f64,
    gpu: Value,
    miner: Value,
}

/// A linha que resume um minuto: `gpu_json` e `miner_json` novos da última amostra.
fn merge(samples: &[Sample], bucket_seconds: f64) -> (Option<String>, String) {
    let last = samples.last().expect("minuto com amostras");
    let base = samples
        .iter()
        .rev()
        .find(|sample| sample.gpu.is_object())
        .map(|sample| sample.gpu.clone());
    let gpu = base.map(|mut gpu| {
        for field in SENSOR_FIELDS {
            let values: Vec<f64> = samples
                .iter()
                .filter_map(|sample| sample.gpu.get(field).and_then(finite))
                .collect();
            if let Some(average) = mean(&values) {
                gpu[field] = json!(average);
            }
        }
        encode(&gpu)
    });
    // Hashrate: uma medição por `hashrate_at` (a mesma linha do log se repete a cada 2 s).
    let mut observations: Vec<(f64, f64)> = Vec::new();
    for sample in samples {
        let (Some(at), Some(rate)) = (
            sample.miner.get("hashrate_at").and_then(finite),
            sample.miner.get("hashrate").and_then(finite),
        ) else {
            continue;
        };
        if !observations.iter().any(|(seen, _)| *seen == at) {
            observations.push((at, rate));
        }
    }
    let mut miner = match &last.miner {
        Value::Object(map) => map.clone(),
        _ => Map::new(),
    };
    if let Some(average) = mean(
        &observations
            .iter()
            .map(|(_, rate)| *rate)
            .collect::<Vec<_>>(),
    ) {
        let latest = observations
            .iter()
            .map(|(at, _)| *at)
            .fold(f64::NEG_INFINITY, f64::max);
        miner.insert("hashrate".into(), json!(average));
        miner.insert("hashrate_at".into(), json!(latest));
    }
    miner.insert(
        "retention".into(),
        json!({
            "method": "mean",
            "bucket_seconds": bucket_seconds,
            "samples": samples.len(),
            "hashrate_samples": observations.len(),
            "from": samples[0].timestamp,
            "to": last.timestamp,
        }),
    );
    (gpu, encode(&Value::Object(miner)))
}

/// Uma tabela de telemetria e como achar as séries dela.
struct Table {
    name: &'static str,
    /// Colunas que identificam a série (prefixo da chave primária, antes de `timestamp`).
    key: &'static [&'static str],
}

const TABLES: [Table; 2] = [
    Table {
        name: "gpu_telemetry",
        key: &["gpu_uuid", "wallet"],
    },
    Table {
        name: "telemetry",
        key: &["wallet"],
    },
];

#[derive(Default)]
struct Totals {
    minutes: u64,
    removed: u64,
    windows: u64,
}

impl Totals {
    fn json(&self) -> Value {
        json!({"minutes_reduced": self.minutes, "rows_removed": self.removed, "transactions": self.windows})
    }
}

/// Reduz a telemetria mais velha que `policy.max_age_seconds` (contada a partir de `now`).
pub fn compact_telemetry(
    path: &Path,
    now: f64,
    policy: &Policy,
    apply: bool,
) -> Result<Value, String> {
    if !path.is_file() {
        return Ok(json!({"database": path, "exists": false}));
    }
    let connection = open(path, apply, policy)?;
    let failed = sql_error(path);
    let version: i64 = connection
        .query_row("PRAGMA user_version", [], |row| row.get(0))
        .map_err(&failed)?;
    if version > 3 {
        return Err(format!(
            "{}: versão futura do histórico ({version}); retenção recusada",
            path.display()
        ));
    }
    let bucket = policy.bucket_seconds;
    if !(bucket.is_finite() && bucket > 0.0 && policy.window_seconds.is_finite()) {
        return Err("retenção: balde e janela precisam ser positivos".into());
    }
    // A janela é um múltiplo do balde: um minuto nunca fica dividido entre duas transações.
    let policy = &Policy {
        window_seconds: (policy.window_seconds / bucket).ceil().max(1.0) * bucket,
        ..policy.clone()
    };
    let cutoff = ((now - policy.max_age_seconds) / bucket).floor() * bucket;
    let mut tables = Map::new();
    for table in &TABLES {
        if !table_exists(&connection, table.name).map_err(&failed)? {
            continue;
        }
        let totals = compact_table(&connection, table, cutoff, policy, apply).map_err(&failed)?;
        tables.insert(table.name.into(), totals.json());
    }
    Ok(
        json!({"database": path, "exists": true, "applied": apply, "cutoff": cutoff, "tables": tables}),
    )
}

fn compact_table(
    connection: &Connection,
    table: &Table,
    cutoff: f64,
    policy: &Policy,
    apply: bool,
) -> rusqlite::Result<Totals> {
    let key_columns = table.key.join(",");
    let key_filter = table
        .key
        .iter()
        .map(|column| format!("{column}=?"))
        .collect::<Vec<_>>()
        .join(" AND ");
    let series: Vec<Vec<Sql>> = {
        let mut statement = connection.prepare(&format!(
            "SELECT DISTINCT {key_columns} FROM {}",
            table.name
        ))?;
        statement
            .query_map([], |row| {
                (0..table.key.len())
                    .map(|index| row.get::<_, Sql>(index))
                    .collect()
            })?
            .collect::<rusqlite::Result<_>>()?
    };
    let next_sql = format!(
        "SELECT MIN(timestamp) FROM {} WHERE {key_filter} AND timestamp>=? AND timestamp<?",
        table.name
    );
    let rows_sql = format!(
        "SELECT rowid,timestamp,source,gpu_json,miner_json FROM {} WHERE {key_filter} \
         AND timestamp>=? AND timestamp<? ORDER BY timestamp,rowid",
        table.name
    );
    let update_sql = format!(
        "UPDATE {} SET gpu_json=?,miner_json=? WHERE rowid=?",
        table.name
    );
    let delete_sql = format!("DELETE FROM {} WHERE rowid=?", table.name);
    let mut totals = Totals::default();
    for key in &series {
        let mut from = f64::MIN;
        loop {
            let mut parameters: Vec<&dyn ToSql> =
                key.iter().map(|value| value as &dyn ToSql).collect();
            parameters.push(&from);
            parameters.push(&cutoff);
            let next: Option<f64> =
                connection.query_row(&next_sql, parameters.as_slice(), |row| row.get(0))?;
            let Some(next) = next else { break };
            let start = (next / policy.bucket_seconds).floor() * policy.bucket_seconds;
            let end = (start + policy.window_seconds).min(cutoff);
            if apply {
                connection.execute_batch("BEGIN IMMEDIATE")?;
            }
            let outcome = compact_window(
                connection,
                key,
                (start, end),
                (&rows_sql, &update_sql, &delete_sql),
                policy.bucket_seconds,
                apply,
            );
            let changed = match outcome {
                Ok(changed) => {
                    if apply {
                        connection.execute_batch("COMMIT")?;
                    }
                    changed
                }
                Err(error) => {
                    if apply {
                        let _ = connection.execute_batch("ROLLBACK");
                    }
                    return Err(error);
                }
            };
            totals.windows += u64::from(apply);
            totals.minutes += changed.0;
            totals.removed += changed.1;
            if apply && changed.0 > 0 && !policy.pause.is_zero() {
                std::thread::sleep(policy.pause);
            }
            from = end;
        }
    }
    Ok(totals)
}

/// Reduz uma janela; devolve `(minutos reduzidos, linhas apagadas)`.
fn compact_window(
    connection: &Connection,
    key: &[Sql],
    (start, end): (f64, f64),
    (rows_sql, update_sql, delete_sql): (&str, &str, &str),
    bucket_seconds: f64,
    apply: bool,
) -> rusqlite::Result<(u64, u64)> {
    let mut parameters: Vec<&dyn ToSql> = key.iter().map(|value| value as &dyn ToSql).collect();
    parameters.push(&start);
    parameters.push(&end);
    let mut statement = connection.prepare(rows_sql)?;
    let rows: Vec<(String, i64, Sample)> = statement
        .query_map(parameters.as_slice(), |row| {
            let timestamp: f64 = row.get(1)?;
            let source: Sql = row.get(2)?;
            let source = match source {
                Sql::Text(text) => text,
                other => format!("{other:?}"),
            };
            let bucket = (timestamp / bucket_seconds).floor() as i64;
            Ok((
                source,
                bucket,
                Sample {
                    rowid: row.get(0)?,
                    timestamp,
                    gpu: parse(&row.get(3)?),
                    miner: parse(&row.get(4)?),
                },
            ))
        })?
        .collect::<rusqlite::Result<_>>()?;
    // Mesma origem e mesmo minuto formam um grupo (ordem: tempo, então já agrupado por minuto).
    let mut groups: Vec<((String, i64), Vec<Sample>)> = Vec::new();
    for (source, bucket, sample) in rows {
        match groups
            .iter_mut()
            .find(|(group, _)| group.0 == source && group.1 == bucket)
        {
            Some((_, samples)) => samples.push(sample),
            None => groups.push(((source, bucket), vec![sample])),
        }
    }
    let (mut minutes, mut removed) = (0, 0);
    for (_, samples) in groups.iter().filter(|(_, samples)| samples.len() > 1) {
        minutes += 1;
        removed += samples.len() as u64 - 1;
        if !apply {
            continue;
        }
        let (gpu, miner) = merge(samples, bucket_seconds);
        let last = samples.last().expect("grupo com amostras");
        match gpu {
            Some(gpu) => {
                connection.execute(update_sql, rusqlite::params![gpu, miner, last.rowid])?
            }
            None => connection.execute(
                &update_sql.replace("gpu_json=?,", ""),
                rusqlite::params![miner, last.rowid],
            )?,
        };
        for sample in &samples[..samples.len() - 1] {
            connection.execute(delete_sql, [sample.rowid])?;
        }
    }
    Ok((minutes, removed))
}

/// Filtro das decisões apagáveis: `hold`, sem escrita, sem pausa, fora de emergência e
/// mais velhas que o corte. A última decisão de cada GPU é excluída à parte.
const PRUNABLE: &str = "id>=? AND id<? AND stamp<? AND CASE WHEN json_valid(payload_json) THEN \
     json_extract(payload_json,'$.action')='hold' \
     AND json_extract(payload_json,'$.writes_hardware')=0 \
     AND COALESCE(json_extract(payload_json,'$.pause_required'),0)=0 \
     AND COALESCE(json_extract(payload_json,'$.source'),'')!='emergency' \
     ELSE 0 END";

/// `AND id NOT IN (...)` com a última decisão de cada GPU (o que o `status` mostra).
///
/// Calculado uma vez por passada: um `EXISTS` correlacionado faria o SQLite montar um
/// índice temporário da tabela inteira a cada bloco (0,6 s por bloco no banco real).
fn keep_latest(connection: &Connection) -> rusqlite::Result<String> {
    let mut statement = connection.prepare("SELECT MAX(id) FROM decisions GROUP BY gpu_uuid")?;
    let latest: Vec<String> = statement
        .query_map([], |row| row.get::<_, i64>(0))?
        .map(|id| id.map(|id| id.to_string()))
        .collect::<rusqlite::Result<_>>()?;
    Ok(if latest.is_empty() {
        String::new()
    } else {
        format!(" AND id NOT IN ({})", latest.join(","))
    })
}

/// Apaga as decisões `hold` sem escrita mais velhas que `policy.max_age_seconds`.
pub fn prune_decisions(
    path: &Path,
    now: f64,
    policy: &Policy,
    apply: bool,
) -> Result<Value, String> {
    if !path.is_file() {
        return Ok(json!({"database": path, "exists": false}));
    }
    let connection = open(path, apply, policy)?;
    let failed = sql_error(path);
    if !table_exists(&connection, "decisions").map_err(&failed)? {
        return Ok(
            json!({"database": path, "exists": true, "applied": apply, "decisions_removed": 0}),
        );
    }
    let cutoff = now - policy.max_age_seconds;
    let bounds: (Option<i64>, Option<i64>) = connection
        .query_row("SELECT MIN(id),MAX(id) FROM decisions", [], |row| {
            Ok((row.get(0)?, row.get(1)?))
        })
        .map_err(&failed)?;
    let (mut removed, mut transactions) = (0_u64, 0_u64);
    if let (Some(first), Some(last)) = bounds {
        let mut keep: Option<String> = None;
        let mut low = first;
        while low <= last {
            let high = low.saturating_add(policy.decision_chunk.max(1));
            // Ids crescem com o tempo: o primeiro bloco todo dentro do prazo encerra.
            let oldest: Option<f64> = connection
                .query_row(
                    "SELECT MIN(stamp) FROM decisions WHERE id>=? AND id<?",
                    [low, high],
                    |row| row.get(0),
                )
                .map_err(&failed)?;
            if oldest.is_some_and(|oldest| oldest >= cutoff) {
                break;
            }
            if keep.is_none() {
                keep = Some(keep_latest(&connection).map_err(&failed)?);
            }
            let keep = keep.as_deref().unwrap_or_default();
            let count = if apply {
                connection
                    .execute(
                        &format!("DELETE FROM decisions WHERE {PRUNABLE}{keep}"),
                        rusqlite::params![low, high, cutoff],
                    )
                    .map_err(&failed)? as u64
            } else {
                connection
                    .query_row(
                        &format!("SELECT COUNT(*) FROM decisions WHERE {PRUNABLE}{keep}"),
                        rusqlite::params![low, high, cutoff],
                        |row| row.get::<_, i64>(0),
                    )
                    .map_err(&failed)? as u64
            };
            removed += count;
            transactions += u64::from(apply);
            if apply && count > 0 && !policy.pause.is_zero() {
                std::thread::sleep(policy.pause);
            }
            low = high;
        }
    }
    Ok(json!({
        "database": path, "exists": true, "applied": apply, "cutoff": cutoff,
        "decisions_removed": removed, "transactions": transactions,
    }))
}

/// Os `gpu-agent.sqlite3` do produto: o da pasta de estado (motor legado) e um por
/// GPU em `LOG_DIR/gpus/GPU-*/`. Só os que existem.
pub fn agent_databases(state_dir: &Path, log_dir: &Path) -> Vec<PathBuf> {
    let mut found = vec![state_dir.join("gpu-agent.sqlite3")];
    if let Ok(entries) = std::fs::read_dir(log_dir.join("gpus")) {
        let mut per_gpu: Vec<PathBuf> = entries
            .filter_map(Result::ok)
            .filter(|entry| entry.file_name().to_string_lossy().starts_with("GPU-"))
            .map(|entry| entry.path().join("gpu-agent.sqlite3"))
            .collect();
        per_gpu.sort();
        found.extend(per_gpu);
    }
    found.retain(|path| path.is_file());
    found.dedup();
    found
}

/// Retenção completa: histórico e cada banco do agente. Um banco com erro não impede
/// os outros; o erro fica no relatório (`errors`).
pub fn run(history: &Path, agents: &[PathBuf], now: f64, policy: &Policy, apply: bool) -> Value {
    let mut errors = Vec::new();
    let history = compact_telemetry(history, now, policy, apply).unwrap_or_else(|error| {
        errors.push(error.clone());
        json!({"database": history, "error": error})
    });
    let decisions: Vec<Value> = agents
        .iter()
        .map(|path| {
            prune_decisions(path, now, policy, apply).unwrap_or_else(|error| {
                errors.push(error.clone());
                json!({"database": path, "error": error})
            })
        })
        .collect();
    json!({
        "now": now,
        "applied": apply,
        "max_age_days": policy.max_age_seconds / 86_400.0,
        "bucket_seconds": policy.bucket_seconds,
        "history": history,
        "agents": decisions,
        "errors": errors,
    })
}
