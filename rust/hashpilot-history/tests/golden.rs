//! Paridade com o `history.HistoryStore` do Python.
//!
//! `tools/rust_parity/generate_goldens.py history` roda o Python REAL sobre cenários
//! fixos e despeja o banco inteiro. Aqui o Rust repete as mesmas operações, na mesma
//! ordem, e precisa produzir o mesmo banco: SQL do esquema, todas as linhas (o JSON
//! gravado é comparado como texto), `user_version`, offsets, rejeitadas e as respostas
//! das consultas. O relógio, os identificadores e o fuso são os mesmos do gerador.
//!
//! Único teste deste binário: ele muda o diretório atual (os arquivos dos cenários
//! têm nomes sem pasta, como no gerador).

use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

use hashpilot_core::miner_log::StampZone;
use hashpilot_history::read::sql_to_json;
use hashpilot_history::{HistoryStore, StoreOptions};
use rusqlite::Connection;
use rusqlite::types::Value as Sql;
use serde_json::{Map, Value, json};

const FLUSH: Duration = Duration::from_secs(10);

fn golden() -> Value {
    let path =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../hashpilot-core/tests/golden/history.json");
    let text =
        fs::read_to_string(&path).unwrap_or_else(|error| panic!("{}: {error}", path.display()));
    serde_json::from_str(&text).expect("golden em JSON")
}

fn options(counter: &Arc<AtomicU64>, now: f64) -> StoreOptions {
    let counter = counter.clone();
    StoreOptions {
        ids: Box::new(move || format!("{:032x}", counter.fetch_add(1, Ordering::SeqCst) + 1)),
        clock: Arc::new(move || now),
        stamp_zone: StampZone::UTC,
    }
}

fn hex(text: &str) -> Vec<u8> {
    (0..text.len())
        .step_by(2)
        .map(|at| u8::from_str_radix(&text[at..at + 2], 16).expect("hex"))
        .collect()
}

fn text<'a>(op: &'a Value, key: &str) -> Option<&'a str> {
    op.get(key).and_then(Value::as_str)
}

fn number(value: &Value) -> f64 {
    value.as_f64().expect("número")
}

/// Uma operação do cenário; devolve a saída registrada (como `history_apply`).
fn apply(
    store: &mut HistoryStore,
    op: &Value,
    counter: &Arc<AtomicU64>,
    now: f64,
    wallet: &str,
) -> Option<Value> {
    let raised = |message: String| Some(json!({"raised": message}));
    match op["op"].as_str().expect("op") {
        "telemetry" => store
            .telemetry_with(
                &op["data"],
                text(op, "source").unwrap_or("live"),
                op.get("archive").and_then(Value::as_bool).unwrap_or(true),
            )
            .err()
            .and_then(raised),
        "rig_telemetry" => store.rig_telemetry(&op["rig"]).err().and_then(raised),
        "event" => {
            store.event(
                op["timestamp"].clone(),
                op["kind"].as_str().expect("kind"),
                op["data"].clone(),
                text(op, "key"),
                text(op, "wallet"),
            );
            None
        }
        "finance" => {
            store.finance(&op["state"]);
            None
        }
        "preferences" => {
            store.preferences(&op["data"]);
            None
        }
        "write_file" => {
            let data = match text(op, "hex") {
                Some(digits) => hex(digits),
                None => op["text"].as_str().expect("text").as_bytes().to_vec(),
            };
            let append = op.get("append").and_then(Value::as_bool).unwrap_or(false);
            let mut file = OpenOptions::new()
                .create(true)
                .write(true)
                .append(append)
                .truncate(!append)
                .open(text(op, "path").expect("path"))
                .expect("arquivo do cenário");
            file.write_all(&data).expect("gravar arquivo do cenário");
            None
        }
        "archive_log" => {
            store
                .archive_log(
                    Path::new(text(op, "path").expect("path")),
                    text(op, "gpu_uuid"),
                    text(op, "wallet"),
                )
                .expect("archive_log");
            None
        }
        "archive_thermal" => {
            store
                .archive_thermal(Path::new(text(op, "path").expect("path")))
                .expect("archive_thermal");
            None
        }
        "reopen" => {
            assert!(store.flush(FLUSH));
            store.close();
            let wallet = text(op, "wallet").unwrap_or(wallet);
            *store = HistoryStore::with_options("history.sqlite3", wallet, options(counter, now))
                .expect("reabrir");
            None
        }
        "last_error" => {
            assert!(store.flush(FLUSH));
            Some(json!({"last_error": store.last_error()}))
        }
        "read" => {
            assert!(store.flush(FLUSH));
            let call = op["call"].as_str().expect("call");
            let args = op["args"].as_array().expect("args");
            let result = match call {
                "restore" => store.restore(number(&args[0])),
                "restore_gpu" => store.restore_gpu(
                    args[0].as_str().expect("uuid"),
                    number(&args[1]),
                    args[2].as_str().expect("wallet"),
                ),
                "gpu_stats" => Ok(store.gpu_stats(args.first().and_then(Value::as_str))),
                "finance_state" => {
                    store.finance_state(number(&args[0]), args.get(1).and_then(Value::as_str))
                }
                "stats" => store.stats(),
                other => panic!("consulta desconhecida: {other}"),
            };
            Some(match result {
                Ok(value) => json!({"read": call, "result": value}),
                Err(message) => json!({"raised": message}),
            })
        }
        other => panic!("operação desconhecida: {other}"),
    }
}

/// O mesmo despejo de `history_dump` no gerador.
fn dump(database: &Path) -> Map<String, Value> {
    let connection = Connection::open(database).expect("abrir banco do cenário");
    let mut schema_rows = connection
        .prepare("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")
        .expect("sqlite_master");
    let schema: Vec<Value> = schema_rows
        .query_map([], |row| {
            Ok(Value::Array(
                (0..4)
                    .map(|column| row.get::<_, Sql>(column).map(|value| sql_to_json(&value)))
                    .collect::<rusqlite::Result<_>>()?,
            ))
        })
        .expect("consulta")
        .collect::<rusqlite::Result<_>>()
        .expect("linhas");
    let tables: Vec<String> = connection
        .prepare("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        .expect("tabelas")
        .query_map([], |row| row.get(0))
        .expect("consulta")
        .collect::<rusqlite::Result<_>>()
        .expect("nomes");
    let mut table_map = Map::new();
    for table in tables {
        let columns: Vec<String> = connection
            .prepare(&format!("PRAGMA table_info({table})"))
            .expect("table_info")
            .query_map([], |row| row.get(1))
            .expect("consulta")
            .collect::<rusqlite::Result<_>>()
            .expect("colunas");
        let mut statement = connection
            .prepare(&format!(
                "SELECT * FROM {table} ORDER BY {}",
                columns.join(",")
            ))
            .expect("select");
        let count = columns.len();
        let rows: Vec<Value> = statement
            .query_map([], |row| {
                Ok(Value::Array(
                    (0..count)
                        .map(|column| row.get::<_, Sql>(column).map(|value| sql_to_json(&value)))
                        .collect::<rusqlite::Result<_>>()?,
                ))
            })
            .expect("consulta")
            .collect::<rusqlite::Result<_>>()
            .expect("linhas");
        table_map.insert(table, json!({"columns": columns, "rows": rows}));
    }
    let version: i64 = connection
        .query_row("PRAGMA user_version", [], |row| row.get(0))
        .expect("versão");
    let journal: String = connection
        .query_row("PRAGMA journal_mode", [], |row| row.get(0))
        .expect("journal");
    drop(schema_rows);
    drop(connection);
    let offsets = fs::read_to_string("history-log-offsets.json").ok();
    let rejected: Vec<Value> = fs::read_to_string("history-rejected.jsonl")
        .map(|text| text.lines().map(|line| json!(line)).collect())
        .unwrap_or_default();
    let mut out = Map::new();
    out.insert("schema".into(), Value::Array(schema));
    out.insert("tables".into(), Value::Object(table_map));
    out.insert("user_version".into(), json!(version));
    out.insert("journal_mode".into(), json!(journal));
    out.insert("offsets".into(), json!(offsets));
    out.insert("rejected".into(), Value::Array(rejected));
    out
}

fn scenario_dir(name: &str) -> PathBuf {
    let directory = std::env::temp_dir().join(format!(
        "hashpilot-history-rust-{}-{name}",
        std::process::id()
    ));
    let _ = fs::remove_dir_all(&directory);
    fs::create_dir_all(&directory).expect("pasta do cenário");
    directory
}

fn run_case(case: &Value, now: f64, wallet: &str) -> Map<String, Value> {
    for statement in case["prepare"].as_array().expect("prepare") {
        let connection = Connection::open("history.sqlite3").expect("preparar banco");
        connection
            .execute_batch(statement.as_str().expect("sql"))
            .expect("comando de preparo");
    }
    let counter = Arc::new(AtomicU64::new(0));
    let mut store = HistoryStore::with_options("history.sqlite3", wallet, options(&counter, now))
        .expect("abrir histórico");
    let mut outputs = Vec::new();
    for op in case["ops"].as_array().expect("ops") {
        if let Some(output) = apply(&mut store, op, &counter, now, wallet) {
            outputs.push(output);
        }
    }
    assert!(store.flush(FLUSH));
    store.close();
    let last_error = store.last_error();
    drop(store);
    let mut result = dump(Path::new("history.sqlite3"));
    result.insert("outputs".into(), Value::Array(outputs));
    result.insert("last_error".into(), json!(last_error));
    result
}

#[test]
fn rust_history_reproduces_python_history_store() {
    let golden = golden();
    let now = golden["now"].as_f64().expect("now");
    let wallet = golden["wallet"].as_str().expect("wallet");
    let cases = golden["cases"].as_array().expect("cases");
    assert!(
        cases.len() >= 6,
        "o golden do histórico precisa dos seis cenários"
    );
    let original = std::env::current_dir().expect("cwd");
    for case in cases {
        let name = case["name"].as_str().expect("name");
        let directory = scenario_dir(name);
        std::env::set_current_dir(&directory).expect("entrar na pasta do cenário");
        let result = run_case(case, now, wallet);
        std::env::set_current_dir(&original).expect("voltar");
        let _ = fs::remove_dir_all(&directory);
        for field in [
            "outputs",
            "schema",
            "user_version",
            "journal_mode",
            "offsets",
            "rejected",
            "last_error",
        ] {
            assert_eq!(result[field], case[field], "cenário {name}: campo {field}");
        }
        let expected = case["tables"].as_object().expect("tables");
        let actual = result["tables"].as_object().expect("tables");
        assert_eq!(
            actual.keys().collect::<Vec<_>>(),
            expected.keys().collect::<Vec<_>>(),
            "cenário {name}: tabelas"
        );
        for (table, expected_table) in expected {
            assert_eq!(
                actual[table]["columns"], expected_table["columns"],
                "cenário {name}: colunas de {table}"
            );
            let expected_rows = expected_table["rows"].as_array().expect("rows");
            let actual_rows = actual[table]["rows"].as_array().expect("rows");
            for (position, (got, want)) in actual_rows.iter().zip(expected_rows).enumerate() {
                assert_eq!(got, want, "cenário {name}: {table} linha {position}");
            }
            assert_eq!(
                actual_rows.len(),
                expected_rows.len(),
                "cenário {name}: número de linhas de {table}"
            );
        }
    }
}
