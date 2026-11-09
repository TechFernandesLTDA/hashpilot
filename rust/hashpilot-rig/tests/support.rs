//! Paridade com `mining_health`, `mining_continuity`, `miner_identity`,
//! `reference_profile`, `gpu_profiles`, o catálogo do `miner_update` e o `json.dumps(indent=2)` do `atomic_json`
//! (golden `rig_support`; o `match_successor` está no golden `process`, só do Windows).
//!
//! A comparação é por valor **com tipo**: `0` e `0.0` são diferentes, porque o
//! Python grava cada um de um jeito no JSON que o painel lê.

use std::path::Path;

use hashpilot_history::pyjson::encode_indented;
use hashpilot_rig::continuity::{ContinuityWindow, safe_sensor};
use hashpilot_rig::health::{OperationalStore, health};
use hashpilot_rig::reference::{
    profile, reference_device_confirmed, reference_memory_clock, reference_start_clock,
};
use serde_json::{Value, json};

fn golden(name: &str) -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join(format!("../hashpilot-core/tests/golden/{name}.json"));
    serde_json::from_str(&std::fs::read_to_string(path).expect("golden")).expect("JSON")
}

fn cases(golden: &Value, kind: &str) -> Vec<Value> {
    golden["cases"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|case| case["kind"] == kind)
        .cloned()
        .collect()
}

/// Igualdade do Python com o tipo numérico conferido (int x float).
fn strict(left: &Value, right: &Value) -> bool {
    match (left, right) {
        (Value::Number(a), Value::Number(b)) => {
            a.is_f64() == b.is_f64() && a.as_f64() == b.as_f64()
        }
        (Value::Array(a), Value::Array(b)) => {
            a.len() == b.len() && a.iter().zip(b).all(|(x, y)| strict(x, y))
        }
        (Value::Object(a), Value::Object(b)) => {
            a.len() == b.len()
                && a.iter()
                    .zip(b)
                    .all(|((ka, va), (kb, vb))| ka == kb && strict(va, vb))
        }
        _ => left == right,
    }
}

fn assert_strict(actual: &Value, expected: &Value, context: &str) {
    assert!(
        strict(actual, expected),
        "{context}\n  rust:   {actual}\n  python: {expected}"
    );
}

fn f(value: &Value) -> f64 {
    value.as_f64().unwrap()
}

#[test]
fn health_matches_python() {
    let golden = golden("rig_support");
    let all = cases(&golden, "health");
    assert!(all.len() > 1500, "{}", all.len());
    for case in &all {
        let intervals: Vec<f64> = case["intervals"]
            .as_array()
            .unwrap()
            .iter()
            .map(f)
            .collect();
        let actual = health(
            &case["miner"],
            &case["running"],
            f(&case["now"]),
            &case["started_at"],
            &intervals,
        );
        assert_strict(
            &actual,
            &case["expected"],
            &format!(
                "{} running={} started={} intervals={:?}",
                case["name"], case["running"], case["started_at"], intervals
            ),
        );
    }
}

#[test]
fn operational_store_matches_python() {
    let golden = golden("rig_support");
    let case = &cases(&golden, "operations")[0];
    let folder = std::env::temp_dir().join(format!("hashpilot-operations-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&folder);
    let path = folder.join("operations.sqlite3");
    let store = OperationalStore::new(&path).unwrap();
    for (position, op) in case["ops"].as_array().unwrap().iter().enumerate() {
        let args = op["args"].as_array().unwrap();
        let kwargs = &op["kwargs"];
        let text = |index: usize| args[index].as_str().unwrap().to_owned();
        let actual = match op["op"].as_str().unwrap() {
            "assess" => store
                .assess(&text(0), &args[1], &args[2], f(&args[3]), &args[4])
                .unwrap(),
            "budget" => store.budget(&text(0), f(&args[1])).unwrap(),
            "reserve" => json!(store.reserve(&text(0), &text(1), f(&args[2])).unwrap()),
            "reserve_component" => json!(
                store
                    .reserve_component(
                        &text(0),
                        &text(1),
                        f(&args[2]),
                        kwargs.get("delay").map_or(60.0, f),
                        kwargs.get("limit").and_then(Value::as_u64).unwrap_or(3) as usize,
                    )
                    .unwrap()
            ),
            "event" => {
                store
                    .event(&text(0), &text(1), &text(2), f(&args[3]))
                    .unwrap();
                Value::Null
            }
            "recent_events" => store
                .recent_events(&text(0), args.get(1).and_then(Value::as_i64).unwrap_or(5))
                .unwrap(),
            other => panic!("operação {other}"),
        };
        assert_strict(
            &actual,
            &op["result"],
            &format!("op #{position} {}", op["op"]),
        );
    }
    let db = rusqlite::Connection::open(&path).unwrap();
    for (table, expected) in case["dump"].as_object().unwrap() {
        let mut statement = db
            .prepare(&format!("SELECT * FROM {table} ORDER BY rowid"))
            .unwrap();
        let columns = statement.column_count();
        let rows: Vec<Value> = statement
            .query_map([], |row| {
                Ok(Value::Array(
                    (0..columns)
                        .map(|index| match row.get_ref(index).unwrap() {
                            rusqlite::types::ValueRef::Integer(value) => json!(value),
                            rusqlite::types::ValueRef::Real(value) => json!(value),
                            rusqlite::types::ValueRef::Text(value) => {
                                json!(String::from_utf8_lossy(value))
                            }
                            rusqlite::types::ValueRef::Null => Value::Null,
                            rusqlite::types::ValueRef::Blob(_) => panic!("blob"),
                        })
                        .collect(),
                ))
            })
            .unwrap()
            .collect::<Result<_, _>>()
            .unwrap();
        assert_strict(&Value::Array(rows), expected, &format!("tabela {table}"));
    }
    drop(db);
    let _ = std::fs::remove_dir_all(folder);
}

#[test]
fn continuity_matches_python() {
    let golden = golden("rig_support");
    let mut window = ContinuityWindow::new();
    for step in cases(&golden, "continuity")[0]["steps"].as_array().unwrap() {
        let now = f(&step["now"]);
        if step["op"] == "inventory" {
            let actual = json!(
                window
                    .inventory(step["devices"].as_array().unwrap(), now)
                    .unwrap()
            );
            assert_strict(&actual, &step["result"], &format!("inventory {now}"));
        } else {
            assert_eq!(json!(window.tick(now)), step["result"], "tick {now}");
            assert_strict(
                &window.grace_until(),
                &step["grace_until"],
                &format!("grace {now}"),
            );
        }
    }
    for case in cases(&golden, "safe_sensor")[0]["cases"]
        .as_array()
        .unwrap()
    {
        assert_eq!(
            json!(safe_sensor(&case["sensor"], f(&case["now"]), 70.0)),
            case["expected"],
            "{}",
            case["sensor"]
        );
    }
}

#[test]
fn reference_profile_and_profiles_match_python() {
    let golden = golden("rig_support");
    for case in cases(&golden, "reference_confirmed")[0]["cases"]
        .as_array()
        .unwrap()
    {
        assert_eq!(
            json!(reference_device_confirmed(&case["gpu"])),
            case["expected"],
            "{}",
            case["gpu"]
        );
    }
    for case in cases(&golden, "reference_memory")[0]["cases"]
        .as_array()
        .unwrap()
    {
        let actual =
            reference_memory_clock(case["miner_path"].as_str().unwrap(), &case["settings"]);
        assert_strict(
            &json!(actual),
            &case["expected"],
            &format!("{} {}", case["miner_path"], case["settings"]),
        );
    }
    for case in cases(&golden, "reference_start")[0]["cases"]
        .as_array()
        .unwrap()
    {
        let actual = reference_start_clock(&case["gpu"], &case["settings"]);
        assert_strict(
            &json!(actual),
            &case["expected"],
            &format!("{}", case["settings"]),
        );
    }
    for case in cases(&golden, "profile")[0]["cases"].as_array().unwrap() {
        match profile(&case["value"]) {
            Ok(value) => assert_strict(&value, &case["ok"], &case["value"].to_string()),
            Err(error) => assert_eq!(json!(error), case["error"], "{}", case["value"]),
        }
    }
}

#[test]
fn indented_json_matches_python() {
    let golden = golden("rig_support");
    for case in cases(&golden, "indent") {
        assert_eq!(
            json!(encode_indented(&case["value"])),
            case["expected"],
            "{}",
            case["value"]
        );
    }
}

#[cfg(windows)]
#[test]
fn match_successor_matches_python() {
    let golden = golden("process");
    let all = cases(&golden, "successor");
    assert!(all.len() >= 20);
    for case in all {
        let actual = hashpilot_rig::successor::match_successor(
            &case["candidate"],
            &case["previous"],
            &case["device"],
            case["miner_path"].as_str().unwrap(),
        );
        assert_eq!(json!(actual), case["expected"], "{}", case["name"]);
    }
}

#[test]
fn embedded_miner_catalog_matches_python() {
    let golden = golden("rig_support");
    let found = cases(&golden, "catalog");
    assert_eq!(found.len(), 1);
    let case = &found[0];
    let catalog = hashpilot_rig::miner_install::Catalog::default();
    assert!(
        strict(&Value::Object(catalog.entries.clone()), &case["catalog"]),
        "catálogo difere do miner_update.CATALOG"
    );
    assert_eq!(json!(catalog.default_version), case["default_version"]);
    for (version, expected) in case["descriptors"].as_object().unwrap() {
        assert!(
            strict(&catalog.descriptor(&json!(version)).unwrap(), expected),
            "{version}"
        );
    }
    assert_eq!(
        json!(catalog.descriptor(&json!("9.9.9")).unwrap_err()),
        case["unknown"]
    );
}
