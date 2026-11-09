//! Gravação de um trabalho da fila — `HistoryStore.write` do Python.
//!
//! A ordem em que os campos são lidos é a do Python: com dois campos faltando, o
//! erro registrado é o do mesmo campo. Dado ausente vira `NULL`, nunca zero.

use std::sync::Arc;

use hashpilot_core::text::py_str;
use rusqlite::types::Value as Sql;
use rusqlite::{Connection, ErrorCode, params_from_iter};
use serde_json::{Map, Value, json};

use crate::pyjson::{encode, get, index, py_eq, truthy, type_name};

/// Relógio injetável (`time.time()` do Python), em segundos desde a época.
pub type Clock = Arc<dyn Fn() -> f64 + Send + Sync>;

/// Identidade de GPU copiada para `gpu_devices.identity_json`, na ordem do Python.
const IDENTITY_KEYS: [&str; 13] = [
    "uuid",
    "index",
    "name",
    "vendor",
    "pci_bus_id",
    "pci_device_id",
    "pci_sub_device_id",
    "pci_subdevice_id",
    "vbios_version",
    "driver_version",
    "serial",
    "architecture",
    "memory_mib",
];

/// Resultado de uma tentativa de gravação, com o mesmo destino do Python.
#[derive(Debug)]
pub(crate) enum Failure {
    /// `HistorySchemaError`: o histórico fecha e descarta a fila.
    Schema(String),
    /// Amostra malformada: vai para `history-rejected.jsonl` e o gravador segue.
    Rejected(String),
    /// Banco ocupado ou erro de disco: o mesmo trabalho é tentado de novo.
    Transient(String),
}

impl From<String> for Failure {
    fn from(message: String) -> Self {
        Failure::Rejected(message)
    }
}

/// `sqlite3.IntegrityError` é amostra rejeitada; os demais erros, transitórios.
pub(crate) fn sql_failure(error: rusqlite::Error) -> Failure {
    if let rusqlite::Error::SqliteFailure(code, _) = &error {
        if code.code == ErrorCode::ConstraintViolation {
            return Failure::Rejected(error.to_string());
        }
    }
    Failure::Transient(error.to_string())
}

/// Valor JSON → parâmetro do SQLite, como o adaptador do `sqlite3`.
///
/// Diferença consciente: lista ou objeto num parâmetro faz o Python repetir o
/// trabalho para sempre (`ProgrammingError` cai no ramo transitório); aqui a amostra
/// é rejeitada com a mesma mensagem.
fn bind(value: &Value, position: usize) -> Result<Sql, Failure> {
    Ok(match value {
        Value::Null => Sql::Null,
        Value::Bool(flag) => Sql::Integer(i64::from(*flag)),
        Value::Number(number) => {
            if let Some(integer) = number.as_i64() {
                Sql::Integer(integer)
            } else if number.is_u64() {
                return Err(Failure::Rejected(
                    "Python int too large to convert to SQLite INTEGER".into(),
                ));
            } else {
                Sql::Real(number.as_f64().unwrap_or(f64::NAN))
            }
        }
        Value::String(text) => Sql::Text(text.clone()),
        Value::Array(_) | Value::Object(_) => {
            return Err(Failure::Rejected(format!(
                "Error binding parameter {position}: type '{}' is not supported",
                type_name(value)
            )));
        }
    })
}

fn bind_all(values: &[Value]) -> Result<Vec<Sql>, Failure> {
    values
        .iter()
        .enumerate()
        .map(|(position, value)| bind(value, position + 1))
        .collect()
}

fn execute(connection: &Connection, sql: &str, values: &[Value]) -> Result<(), Failure> {
    let parameters = bind_all(values)?;
    connection
        .execute(sql, params_from_iter(parameters))
        .map(|_| ())
        .map_err(sql_failure)
}

/// `{k: obj.get(k) for k in chaves if obj.get(k)}`.
fn errors_of(object: &Value, keys: &[&str]) -> Result<Value, Failure> {
    let mut errors = Map::new();
    for key in keys {
        if let Some(value) = get(object, key)? {
            if truthy(value) {
                errors.insert((*key).to_string(), value.clone());
            }
        }
    }
    Ok(Value::Object(errors))
}

fn get_or_null(object: &Value, key: &str) -> Result<Value, Failure> {
    Ok(get(object, key)?.cloned().unwrap_or(Value::Null))
}

pub(crate) struct Writer {
    pub(crate) wallet: String,
    pub(crate) session: String,
    pub(crate) clock: Clock,
    last_status: Option<[Value; 6]>,
}

impl Writer {
    pub(crate) fn new(wallet: String, session: String, clock: Clock) -> Self {
        Writer {
            wallet,
            session,
            clock,
            last_status: None,
        }
    }

    /// `x or self.wallet`.
    fn or_wallet(&self, value: Option<&Value>) -> Value {
        match value {
            Some(value) if truthy(value) => value.clone(),
            _ => Value::String(self.wallet.clone()),
        }
    }

    fn session(&self) -> Value {
        Value::String(self.session.clone())
    }

    pub(crate) fn write(
        &mut self,
        connection: &Connection,
        kind: &str,
        payload: &Value,
    ) -> Result<(), Failure> {
        match kind {
            "telemetry" => self.telemetry(connection, payload),
            "rig_telemetry" => self.rig_telemetry(connection, payload),
            "event" => self.event(connection, payload),
            "events" => {
                let items = index(payload, "events")?;
                let Value::Array(items) = items else {
                    return Err(Failure::Rejected(format!(
                        "'{}' object is not iterable",
                        type_name(items)
                    )));
                };
                // executemany monta todas as linhas antes de gravar a primeira.
                let rows = items
                    .iter()
                    .map(|item| self.event_row(item))
                    .collect::<Result<Vec<_>, _>>()?;
                for row in rows {
                    execute(
                        connection,
                        "INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?)",
                        &row,
                    )?;
                }
                Ok(())
            }
            "finance" => self.finance(connection, payload),
            "preferences" => {
                let Value::Object(map) = payload else {
                    return Err(Failure::Rejected(format!(
                        "'{}' object has no attribute 'items'",
                        type_name(payload)
                    )));
                };
                for (name, value) in map {
                    execute(
                        connection,
                        "INSERT OR REPLACE INTO preferences VALUES(?,?,?)",
                        &[
                            Value::String(self.wallet.clone()),
                            Value::String(name.clone()),
                            Value::String(encode(value)),
                        ],
                    )?;
                }
                Ok(())
            }
            _ => Ok(()),
        }
    }

    fn telemetry(&mut self, connection: &Connection, payload: &Value) -> Result<(), Failure> {
        let data = index(payload, "data")?;
        let source = index(payload, "source")?;
        let errors = errors_of(data, &["gpu_error", "process_error", "log_error"])?;
        let wallet = self.or_wallet(get(data, "wallet")?);
        let collected_at = index(data, "collected_at")?.clone();
        let miner = get(data, "miner")?.cloned().unwrap_or_else(|| json!({}));
        let miner_running = get_or_null(data, "miner_running")?;
        let log_path = get_or_null(data, "log_path")?;
        execute(
            connection,
            "INSERT OR IGNORE INTO telemetry VALUES(?,?,?,?,?,?,?,?,?)",
            &[
                wallet.clone(),
                collected_at.clone(),
                self.session(),
                source.clone(),
                Value::String(encode(&get_or_null(data, "gpu")?)),
                Value::String(encode(&miner)),
                miner_running.clone(),
                Value::String(encode(&errors)),
                log_path.clone(),
            ],
        )?;
        let has_errors = errors.as_object().is_some_and(|map| !map.is_empty());
        let status = [
            miner_running,
            get_or_null(&miner, "paused")?,
            get_or_null(&miner, "connection")?,
            Value::Bool(has_errors),
            log_path,
            wallet,
        ];
        let changed = match &self.last_status {
            None => true,
            Some(previous) => !previous.iter().zip(&status).all(|(a, b)| py_eq(a, b)),
        };
        if source.as_str() == Some("live") && changed {
            let [running, paused, connection_state, _, path, wallet] = &status;
            let event = json!({
                "timestamp": collected_at,
                "key": format!("status:{}", py_str(&collected_at)),
                "kind": "status_changed",
                "wallet": wallet,
                "data": {"miner_running": running, "paused": paused, "connection": connection_state,
                         "errors": errors, "log_path": path},
            });
            self.write(connection, "event", &event)?;
            self.last_status = Some(status);
        }
        Ok(())
    }

    fn rig_telemetry(&mut self, connection: &Connection, payload: &Value) -> Result<(), Failure> {
        let devices = get(payload, "devices")?
            .cloned()
            .unwrap_or_else(|| json!([]));
        let Value::Array(devices) = devices else {
            return Err(Failure::Rejected(format!(
                "'{}' object is not iterable",
                type_name(&devices)
            )));
        };
        for device in &devices {
            let identifier = index(device, "uuid")?.clone();
            // `device.get('collected_at', payload.get('collected_at', time.time()))`.
            let fallback = match get(payload, "collected_at")? {
                Some(value) => value.clone(),
                None => json!((self.clock)()),
            };
            let stamp = get(device, "collected_at")?.cloned().unwrap_or(fallback);
            let mut identity = match get(device, "identity")? {
                Some(Value::Object(map)) => map.clone(),
                Some(value) if truthy(value) => {
                    return Err(Failure::Rejected(format!(
                        "identidade da GPU não é um objeto: '{}'",
                        type_name(value)
                    )));
                }
                _ => Map::new(),
            };
            for key in IDENTITY_KEYS {
                if let Some(value) = get(device, key)? {
                    if !value.is_null() {
                        identity.insert(key.to_string(), value.clone());
                    }
                }
            }
            execute(
                connection,
                "INSERT INTO gpu_devices VALUES(?,?,?,?) ON CONFLICT(gpu_uuid) DO UPDATE SET last_seen=excluded.last_seen,identity_json=excluded.identity_json",
                &[
                    identifier.clone(),
                    stamp.clone(),
                    stamp.clone(),
                    Value::String(encode(&Value::Object(identity))),
                ],
            )?;
            let errors = errors_of(
                device,
                &[
                    "gpu_error",
                    "log_error",
                    "process_error",
                    "configuration_error",
                ],
            )?;
            let miner = match get(device, "miner")? {
                Some(value) if truthy(value) => value.clone(),
                _ => json!({}),
            };
            let enabled = get(device, "enabled")?
                .cloned()
                .unwrap_or(Value::Bool(true));
            execute(
                connection,
                "INSERT OR IGNORE INTO gpu_telemetry (gpu_uuid,timestamp,wallet,session,source,gpu_json,miner_json,miner_running,enabled,errors_json,log_path) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                &[
                    identifier,
                    stamp,
                    self.or_wallet(get(device, "wallet")?),
                    self.session(),
                    Value::String("live".into()),
                    Value::String(encode(&get_or_null(device, "gpu")?)),
                    Value::String(encode(&miner)),
                    get_or_null(device, "miner_running")?,
                    enabled,
                    Value::String(encode(&errors)),
                    get_or_null(device, "log_path")?,
                ],
            )?;
        }
        Ok(())
    }

    fn event_row(&self, item: &Value) -> Result<Vec<Value>, Failure> {
        let wallet = self.or_wallet(get(item, "wallet")?);
        let key = index(item, "key")?.clone();
        let timestamp = index(item, "timestamp")?.clone();
        let kind = index(item, "kind")?.clone();
        let data = Value::String(encode(index(item, "data")?));
        Ok(vec![wallet, key, timestamp, self.session(), kind, data])
    }

    fn event(&self, connection: &Connection, payload: &Value) -> Result<(), Failure> {
        let row = self.event_row(payload)?;
        execute(
            connection,
            "INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?)",
            &row,
        )
    }

    fn finance(&mut self, connection: &Connection, payload: &Value) -> Result<(), Failure> {
        let wallet = self.or_wallet(get(payload, "wallet")?);
        let snapshots = get(payload, "snapshots")?
            .cloned()
            .unwrap_or_else(|| json!([]));
        let Value::Array(snapshots) = snapshots else {
            return Err(Failure::Rejected(format!(
                "'{}' object is not iterable",
                type_name(&snapshots)
            )));
        };
        let rows = snapshots
            .iter()
            .map(|point| {
                Ok(vec![
                    wallet.clone(),
                    index(point, "timestamp")?.clone(),
                    index(point, "total_prl")?.clone(),
                ])
            })
            .collect::<Result<Vec<_>, String>>()?;
        for row in rows {
            execute(
                connection,
                "INSERT OR IGNORE INTO finance_snapshots VALUES(?,?,?)",
                &row,
            )?;
        }
        let sources = get(payload, "sources")?
            .cloned()
            .unwrap_or_else(|| json!({}));
        let Value::Object(sources) = sources else {
            return Err(Failure::Rejected(format!(
                "'{}' object has no attribute 'items'",
                type_name(&sources)
            )));
        };
        for (name, source) in &sources {
            execute(
                connection,
                "INSERT OR IGNORE INTO finance_sources VALUES(?,?,?,?)",
                &[
                    wallet.clone(),
                    Value::String(name.clone()),
                    index(source, "fetched_at")?.clone(),
                    Value::String(encode(index(source, "data")?)),
                ],
            )?;
        }
        if get(payload, "errors")?.is_some_and(truthy) {
            let stamp = match get(payload, "attempted_at")? {
                Some(value) => value.clone(),
                None => json!((self.clock)()),
            };
            let event = json!({
                "timestamp": stamp,
                "key": format!("finance-error:{}", py_str(&stamp)),
                "kind": "finance_error",
                "wallet": wallet,
                "data": index(payload, "errors")?,
            });
            self.write(connection, "event", &event)?;
        }
        Ok(())
    }
}
