//! `mining_health.py`: saúde pelo ACK da pool e orçamento persistente de recuperação
//! por GPU (`operations.sqlite3`, o mesmo arquivo e esquema do Python).
//!
//! Os números seguem os tipos do Python: `max(0, x)` devolve o inteiro `0`, e os
//! limites `900`/`7200` saem inteiros quando prevalecem sobre a mediana.

use std::path::{Path, PathBuf};
use std::time::Duration;

use hashpilot_history::pyjson::truthy;
use rusqlite::{Connection, TransactionBehavior, params};
use serde_json::{Value, json};

/// `finite(valor)`: número que não é booleano (o JSON não tem NaN nem infinito).
pub fn finite(value: &Value) -> bool {
    value.is_number()
}

fn number(value: &Value) -> f64 {
    value.as_f64().unwrap_or(f64::NAN)
}

/// `max(0, x)` do Python: o inteiro `0` quando `x` não é positivo.
fn non_negative(value: f64) -> Value {
    if value > 0.0 { json!(value) } else { json!(0) }
}

fn median(values: &[f64]) -> f64 {
    let mut sorted = values.to_vec();
    sorted.sort_by(f64::total_cmp);
    let middle = sorted.len() / 2;
    if sorted.len() % 2 == 1 {
        sorted[middle]
    } else {
        (sorted[middle - 1] + sorted[middle]) / 2.0
    }
}

const CONNECTED: &str = "Conectado à pool";
const RECONNECTING: &str = "Reconectando à pool";

/// `health(miner, running, now, started_at=, intervals=)`. Os intervalos são `float`,
/// como os que o `OperationalStore.assess` calcula a partir das colunas `REAL`.
pub fn health(
    miner: &Value,
    running: &Value,
    now: f64,
    started_at: &Value,
    intervals: &[f64],
) -> Value {
    let field = |key: &str| miner.get(key).unwrap_or(&Value::Null);
    let stamp = field("hashrate_at");
    let beginning = if finite(started_at) {
        started_at
    } else {
        field("started_at")
    };
    let begun = finite(beginning).then(|| number(beginning));
    let rate = field("hashrate");
    let paused = truthy(field("paused"));
    let hashing = running == &Value::Bool(true)
        && !paused
        && finite(stamp)
        && (0.0..=180.0).contains(&(now - number(stamp)))
        && begun.is_none_or(|start| number(stamp) >= start)
        && finite(rate)
        && number(rate) > 0.0;
    let mut last = Some(field("last_share_at").clone());
    if let Some(value) = &last
        && (!finite(value)
            || number(value) > now
            || begun.is_some_and(|start| number(value) < start))
    {
        last = None;
    }
    let gaps: Vec<f64> = intervals
        .iter()
        .copied()
        .filter(|gap| gap.is_finite() && *gap > 0.0 && *gap <= 86400.0)
        .collect();
    let threshold = if gaps.len() >= 3 {
        let scaled = median(&gaps) * 6.0;
        let capped = if scaled < 7200.0 {
            json!(scaled)
        } else {
            json!(7200)
        };
        if number(&capped) > 900.0 {
            capped
        } else {
            json!(900)
        }
    } else {
        json!(900)
    };
    let limit = number(&threshold);
    let reference = last.as_ref().map_or(begun, |value| Some(number(value)));
    let age = reference.map(|start| non_negative(now - start));
    let activity = field("last_job_at");
    let job_current = finite(activity)
        && (0.0..=180.0).contains(&(now - number(activity)))
        && begun.is_none_or(|start| number(activity) >= start);
    let connection = field("connection").as_str();
    let mut connected = connection == Some(CONNECTED)
        || job_current
        || last
            .as_ref()
            .is_some_and(|value| connection != Some(RECONNECTING) && now - number(value) <= limit);
    if connection == Some(RECONNECTING) {
        connected = false;
    }
    let warning = hashing
        && age.as_ref().is_some_and(|value| number(value) > limit)
        && begun.is_some_and(|start| now - start >= 300.0);
    let state = if running != &Value::Bool(true) {
        if running == &Value::Bool(false) {
            "stopped"
        } else {
            "unknown"
        }
    } else if paused {
        "cooling"
    } else if !connected {
        "connecting"
    } else if !hashing {
        "no_hashrate"
    } else if warning {
        "no_shares"
    } else if last
        .as_ref()
        .is_some_and(|value| now - number(value) <= limit)
    {
        "confirmed"
    } else {
        "awaiting_share"
    };
    let label = match state {
        "stopped" => "Minerador parado",
        "unknown" => "Processo não confirmado",
        "cooling" => "GPU em resfriamento",
        "connecting" => "Aguardando conexão com a pool",
        "no_hashrate" => "Sem hashrate recente",
        "no_shares" => "Alerta: sem shares aceitas no prazo esperado",
        "confirmed" => "Pool confirmou shares",
        _ => "Calculando · aguardando share aceita",
    };
    let basis = if gaps.len() >= 3 {
        "histórico de ACKs (margem 6×)"
    } else {
        "janela inicial conservadora de 15 min; dificuldade desconhecida"
    };
    json!({
        "state": state,
        "label": label,
        "hashing": hashing,
        "connected": connected,
        "last_accepted_at": last.unwrap_or(Value::Null),
        "seconds_without_share": age.unwrap_or(Value::Null),
        "warning": warning,
        "warning_after_seconds": threshold,
        "interval_samples": gaps.len(),
        "basis": basis,
        "ack_source": "resposta da pool registrada pelo minerador; não comprova pagamento",
    })
}

fn truncate(text: &str, limit: usize) -> String {
    text.chars().take(limit).collect()
}

/// `OperationalStore`: o orçamento de reinícios sobrevive ao reinício do serviço.
pub struct OperationalStore {
    path: PathBuf,
}

type Sql<T> = Result<T, String>;

fn sql<T>(result: rusqlite::Result<T>) -> Sql<T> {
    result.map_err(|error| error.to_string())
}

impl OperationalStore {
    pub fn new(path: &Path) -> Sql<Self> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        let store = Self {
            path: path.to_owned(),
        };
        let db = store.connect()?;
        sql(db.execute_batch(
            "CREATE TABLE IF NOT EXISTS acks(gpu TEXT, stamp REAL, PRIMARY KEY(gpu,stamp));
             CREATE TABLE IF NOT EXISTS recoveries(id INTEGER PRIMARY KEY,gpu TEXT,stamp REAL,reason TEXT);
             CREATE INDEX IF NOT EXISTS recovery_gpu_stamp ON recoveries(gpu,stamp);
             CREATE TABLE IF NOT EXISTS incidents(id INTEGER PRIMARY KEY,gpu TEXT,stamp REAL,kind TEXT,message TEXT);
             CREATE INDEX IF NOT EXISTS incident_gpu_stamp ON incidents(gpu,stamp);
             CREATE TABLE IF NOT EXISTS component_attempts(id INTEGER PRIMARY KEY,gpu TEXT,component TEXT,stamp REAL);
             CREATE INDEX IF NOT EXISTS component_gpu_stamp ON component_attempts(gpu,component,stamp);",
        ))?;
        Ok(store)
    }

    fn connect(&self) -> Sql<Connection> {
        let db = sql(Connection::open(&self.path))?;
        sql(db.busy_timeout(Duration::from_secs(3)))?;
        sql(db.pragma_update(None, "synchronous", "FULL"))?;
        Ok(db)
    }

    fn stamps(db: &Connection, query: &str, values: &[&dyn rusqlite::ToSql]) -> Sql<Vec<f64>> {
        let mut statement = sql(db.prepare(query))?;
        let rows = sql(statement.query_map(values, |row| row.get::<_, f64>(0)))?;
        sql(rows.collect())
    }

    /// `assess`: grava o ACK novo e avalia a saúde com os intervalos entre ACKs.
    pub fn assess(
        &self,
        identifier: &str,
        miner: &Value,
        running: &Value,
        now: f64,
        started_at: &Value,
    ) -> Sql<Value> {
        let last = miner.get("last_share_at").unwrap_or(&Value::Null);
        let db = self.connect()?;
        if finite(last) && (0.0..=86400.0).contains(&(now - number(last))) {
            sql(db.execute(
                "INSERT OR IGNORE INTO acks VALUES(?,?)",
                params![identifier, number(last)],
            ))?;
        }
        let stamps = Self::stamps(
            &db,
            "SELECT stamp FROM acks WHERE gpu=? AND stamp<=? ORDER BY stamp DESC LIMIT 33",
            &[&identifier, &now],
        )?;
        drop(db);
        let mut miner = miner.clone();
        if last.is_null()
            && let Some(first) = stamps.first()
            && finite(started_at)
            && *first >= number(started_at)
            && let Some(object) = miner.as_object_mut()
        {
            object.insert("last_share_at".into(), json!(first));
        }
        let gaps: Vec<f64> = stamps.windows(2).map(|pair| pair[0] - pair[1]).collect();
        Ok(health(&miner, running, now, started_at, &gaps))
    }

    fn recoveries(db: &Connection, identifier: &str, now: f64) -> Sql<Vec<(f64, Option<String>)>> {
        let mut statement = sql(db.prepare(
            "SELECT stamp,reason FROM recoveries WHERE gpu=? AND stamp>? AND stamp<=? ORDER BY stamp DESC",
        ))?;
        let rows = sql(
            statement.query_map(params![identifier, now - 3600.0, now], |row| {
                Ok((row.get(0)?, row.get(1)?))
            }),
        )?;
        sql(rows.collect())
    }

    fn delay(count: usize) -> f64 {
        // min(900, 60*2**max(0, n-1)); a partir de 5 tentativas o limite prevalece.
        if count == 0 {
            0.0
        } else {
            (60.0 * 2f64.powi((count - 1).min(16) as i32)).min(900.0)
        }
    }

    /// `budget`: tentativas na última hora, bloqueio a partir de 3 e próximo horário.
    pub fn budget(&self, identifier: &str, now: f64) -> Sql<Value> {
        let rows = Self::recoveries(&self.connect()?, identifier, now)?;
        let mut next = rows
            .first()
            .map_or(now, |row| row.0 + Self::delay(rows.len()));
        let blocked = rows.len() >= 3;
        if blocked {
            let oldest = rows.iter().map(|row| row.0).fold(f64::INFINITY, f64::min) + 3600.0;
            if oldest > next {
                next = oldest;
            }
        }
        Ok(json!({
            "attempts_last_hour": rows.len(),
            "limit_per_hour": 3,
            "blocked": blocked,
            "next_allowed_at": next,
            "last_reason": rows.first().map_or(Value::Null, |row| json!(row.1)),
        }))
    }

    /// `reserve`: reserva uma recuperação (transação `IMMEDIATE`) se o orçamento deixar.
    pub fn reserve(&self, identifier: &str, reason: &str, now: f64) -> Sql<bool> {
        let mut db = self.connect()?;
        let transaction = sql(db.transaction_with_behavior(TransactionBehavior::Immediate))?;
        let stamps: Vec<f64> = Self::recoveries(&transaction, identifier, now)?
            .into_iter()
            .map(|row| row.0)
            .collect();
        if stamps.len() >= 3
            || stamps
                .first()
                .is_some_and(|first| now < first + Self::delay(stamps.len()))
        {
            sql(transaction.commit())?;
            return Ok(false);
        }
        sql(transaction.execute(
            "INSERT INTO recoveries(gpu,stamp,reason) VALUES(?,?,?)",
            params![identifier, now, truncate(reason, 120)],
        ))?;
        sql(transaction.commit())?;
        Ok(true)
    }

    /// `reserve_component(…, delay=60, limit=3)`.
    pub fn reserve_component(
        &self,
        identifier: &str,
        component: &str,
        now: f64,
        delay: f64,
        limit: usize,
    ) -> Sql<bool> {
        let mut db = self.connect()?;
        let transaction = sql(db.transaction_with_behavior(TransactionBehavior::Immediate))?;
        let rows = Self::stamps(
            &transaction,
            "SELECT stamp FROM component_attempts WHERE gpu=? AND component=? AND stamp>? AND stamp<=? ORDER BY stamp DESC",
            &[&identifier, &component, &(now - 3600.0), &now],
        )?;
        if rows.len() >= limit || rows.first().is_some_and(|first| now - first < delay) {
            sql(transaction.commit())?;
            return Ok(false);
        }
        sql(transaction.execute(
            "INSERT INTO component_attempts(gpu,component,stamp) VALUES(?,?,?)",
            params![identifier, component, now],
        ))?;
        sql(transaction.commit())?;
        Ok(true)
    }

    /// `event`: registra o incidente, sem repetir o mesmo em menos de 60 s.
    pub fn event(&self, identifier: &str, kind: &str, message: &str, now: f64) -> Sql<()> {
        let db = self.connect()?;
        let message = truncate(message, 300);
        let recent: Option<(f64, Option<String>, Option<String>)> = db
            .query_row(
                "SELECT stamp,kind,message FROM incidents WHERE gpu=? ORDER BY id DESC LIMIT 1",
                [identifier],
                |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
            )
            .map(Some)
            .or_else(|error| {
                if error == rusqlite::Error::QueryReturnedNoRows {
                    Ok(None)
                } else {
                    Err(error)
                }
            })
            .map_err(|error| error.to_string())?;
        let repeated = recent.is_some_and(|(stamp, previous_kind, previous_message)| {
            previous_kind.as_deref() == Some(kind)
                && previous_message.as_deref() == Some(message.as_str())
                && (0.0..60.0).contains(&(now - stamp))
        });
        if !repeated {
            sql(db.execute(
                "INSERT INTO incidents(gpu,stamp,kind,message) VALUES(?,?,?,?)",
                params![identifier, now, kind, message],
            ))?;
        }
        Ok(())
    }

    /// `recent_events(limit=5)`, limitado entre 1 e 20.
    pub fn recent_events(&self, identifier: &str, limit: i64) -> Sql<Value> {
        let db = self.connect()?;
        let mut statement = sql(db.prepare(
            "SELECT stamp,kind,message FROM incidents WHERE gpu=? ORDER BY id DESC LIMIT ?",
        ))?;
        let rows = sql(statement.query_map(params![identifier, limit.clamp(1, 20)], |row| {
            Ok(json!({"at": row.get::<_, f64>(0)?, "kind": row.get::<_, Option<String>>(1)?, "message": row.get::<_, Option<String>>(2)?}))
        }))?;
        Ok(Value::Array(sql(rows.collect())?))
    }
}
