//! Paridade Rust × Python: cada caso de `tests/golden/*.json` foi produzido pelo
//! próprio Python do HashPilot (`tools/rust_parity/generate_goldens.py`, com
//! TZ=UTC). O Rust precisa devolver o mesmo resultado — números, textos em
//! português, ausências (`null`) e mensagens de erro.

use std::collections::HashMap;
use std::path::{Path, PathBuf};

use hashpilot_core::config::{expandvars_nt, expandvars_posix, log_dir, resolve_state_dir};
use hashpilot_core::miner_log::{StampZone, parse_gpu_log, parse_miner_device_map, parse_stamp};
use hashpilot_core::numbers::{non_negative, numeric, valuation_numeric};
use hashpilot_core::payout::{duration_value, payout_model};
use hashpilot_core::rig_config::{MAX_GPUS, resolve_devices, update_gpu_settings};
use hashpilot_core::thermal::{self, ThermalPolicy};
use hashpilot_core::valuation::value_in_currencies;
use hashpilot_core::wallet::{support_configuration, valid_wallet};
use serde_json::{Value, json};

fn manifest_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn repository_root() -> PathBuf {
    manifest_dir().join("../..")
}

fn golden(name: &str) -> Value {
    let path = manifest_dir()
        .join("tests/golden")
        .join(format!("{name}.json"));
    let text = std::fs::read_to_string(&path)
        .unwrap_or_else(|error| panic!("{}: {error}", path.display()));
    serde_json::from_str(&text).expect("golden JSON válido")
}

fn cases<'a>(value: &'a Value, key: &str) -> &'a Vec<Value> {
    value[key]
        .as_array()
        .unwrap_or_else(|| panic!("golden sem lista `{key}`"))
}

/// Compara valores JSON como resultados: números com tolerância relativa mínima
/// (inteiro e float equivalentes), chave ausente equivale a `null`.
fn assert_same(actual: &Value, expected: &Value, path: &str) {
    match (actual, expected) {
        (Value::Number(a), Value::Number(b)) => {
            let (a, b) = (a.as_f64().unwrap(), b.as_f64().unwrap());
            let tolerance = 1e-12 * a.abs().max(b.abs()).max(1.0);
            assert!((a - b).abs() <= tolerance, "{path}: {a} != {b}");
        }
        (Value::Object(a), Value::Object(b)) => {
            let mut keys: Vec<&String> = a.keys().chain(b.keys()).collect();
            keys.sort();
            keys.dedup();
            for key in keys {
                let left = a.get(key).unwrap_or(&Value::Null);
                let right = b.get(key).unwrap_or(&Value::Null);
                assert_same(left, right, &format!("{path}.{key}"));
            }
        }
        (Value::Array(a), Value::Array(b)) => {
            assert_eq!(
                a.len(),
                b.len(),
                "{path}: tamanho {} != {}",
                a.len(),
                b.len()
            );
            for (index, (left, right)) in a.iter().zip(b).enumerate() {
                assert_same(left, right, &format!("{path}[{index}]"));
            }
        }
        _ => assert_eq!(actual, expected, "{path}"),
    }
}

fn option(value: Option<f64>) -> Value {
    value.map_or(Value::Null, |number| json!(number))
}

#[test]
fn numbers_follow_python_float_rules() {
    let data = golden("numbers");
    for case in cases(&data, "cases") {
        let input = &case["input"];
        let name = format!("numbers({input})");
        assert_same(
            &option(numeric(input)),
            &case["numeric"],
            &format!("{name}.numeric"),
        );
        assert_same(
            &option(non_negative(input)),
            &case["payout_number"],
            &format!("{name}.payout_number"),
        );
        assert_same(
            &option(valuation_numeric(input, false)),
            &case["valuation_numeric"],
            &format!("{name}.valuation"),
        );
        assert_same(
            &option(valuation_numeric(input, true)),
            &case["valuation_positive"],
            &format!("{name}.positive"),
        );
    }
}

#[test]
fn miner_log_matches_python_on_real_and_synthetic_logs() {
    let data = golden("miner_log");
    let mut real = 0;
    for case in cases(&data, "cases") {
        let name = case["name"].as_str().unwrap();
        let text = match case.get("fixture").and_then(Value::as_str) {
            Some(fixture) => {
                real += 1;
                let bytes = std::fs::read(repository_root().join(fixture))
                    .expect("log real de portable-data");
                String::from_utf8_lossy(&bytes)
                    .trim_start_matches('\u{feff}')
                    .to_string()
            }
            None => case["text"].as_str().unwrap().to_string(),
        };
        let (state, future) = parse_gpu_log(
            &text,
            Some("GPU-test"),
            case["index"].as_i64().unwrap(),
            case["now"].as_f64().unwrap(),
            case["allow_local_gpu0"].as_bool().unwrap(),
            StampZone::UTC,
        );
        assert_same(
            &serde_json::to_value(&state).unwrap(),
            &case["result"],
            &format!("{name}.result"),
        );
        assert_same(&option(future), &case["future"], &format!("{name}.future"));
    }
    assert!(
        real >= 5,
        "os logs reais de portable-data/ precisam participar ({real})"
    );
    for case in cases(&data, "stamps") {
        let text = case["text"].as_str().unwrap();
        assert_same(
            &option(parse_stamp(text, StampZone::UTC)),
            &case["epoch_utc"],
            &format!("stamp({text})"),
        );
    }
}

#[test]
fn miner_device_map_matches_python() {
    let data = golden("miner_log");
    for case in cases(&data, "device_map") {
        let text = case["text"].as_str().unwrap();
        let devices = case["devices"].as_array().unwrap();
        match (
            parse_miner_device_map(text, devices),
            case.get("ok"),
            case.get("error"),
        ) {
            (Ok(map), Some(expected), _) => {
                let actual: serde_json::Map<String, Value> =
                    map.into_iter().map(|(k, v)| (k, json!(v))).collect();
                assert_same(
                    &Value::Object(actual),
                    expected,
                    &format!("device_map({text})"),
                );
            }
            (Err(error), _, Some(expected)) => {
                assert_eq!(error, expected.as_str().unwrap(), "device_map({text})")
            }
            (actual, ok, error) => {
                panic!("device_map({text}): Rust {actual:?}, Python ok={ok:?} erro={error:?}")
            }
        }
    }
}

#[test]
fn thermal_policy_matches_python_step_by_step() {
    let data = golden("thermal");
    for (name, value) in data["constants"].as_object().unwrap() {
        let rust = match name.as_str() {
            "EMERGENCY_C" => thermal::EMERGENCY_C,
            "FAST_COOLING_C" => thermal::FAST_COOLING_C,
            "COOLING_C" => thermal::COOLING_C,
            "ANTICIPATE_C" => thermal::ANTICIPATE_C,
            "RECOVERY_C" => thermal::RECOVERY_C,
            "MINER_OFF_TEMPERATURE_C" => thermal::MINER_OFF_TEMPERATURE_C,
            other => panic!("constante térmica sem par no Rust: {other}"),
        };
        assert_eq!(rust, value.as_i64().unwrap(), "{name}");
    }
    for case in cases(&data, "cases") {
        let name = case["name"].as_str().unwrap();
        let arg = |key: &str, default: i64| {
            case["policy"]
                .get(key)
                .and_then(Value::as_i64)
                .unwrap_or(default)
        };
        let policy = ThermalPolicy::new(
            arg("min_clock", 1500),
            arg("max_clock", 1800),
            arg("initial_clock", 1740),
        );
        if let Some(expected) = case.get("constructor_error") {
            assert_eq!(policy.unwrap_err(), expected.as_str().unwrap(), "{name}");
            continue;
        }
        let mut policy = policy.unwrap_or_else(|error| panic!("{name}: {error}"));
        for (index, step) in cases(case, "steps").iter().enumerate() {
            let result = policy.step(
                step["temp"].as_f64().unwrap(),
                step["now"].as_f64().unwrap(),
            );
            let label = format!("{name}[{index}]");
            match (result, step.get("error")) {
                (Err(error), Some(expected)) => {
                    assert_eq!(error, expected.as_str().unwrap(), "{label}")
                }
                (Ok(decision), None) => {
                    assert_eq!(
                        decision.clock_mhz,
                        step["clock_mhz"].as_i64().unwrap(),
                        "{label}.clock"
                    );
                    assert_eq!(
                        decision.mode,
                        step["mode"].as_str().unwrap(),
                        "{label}.mode"
                    );
                    assert_eq!(
                        decision.reason,
                        step["reason"].as_str().unwrap(),
                        "{label}.reason"
                    );
                }
                (actual, expected) => panic!("{label}: Rust {actual:?}, Python erro={expected:?}"),
            }
        }
    }
}

#[test]
fn valuation_matches_python() {
    let data = golden("valuation");
    for case in cases(&data, "cases") {
        let prices = case["prices"].as_array().unwrap();
        let result =
            value_in_currencies(&case["prl"], &prices[0], &prices[1], &prices[2], &prices[3]);
        assert_same(
            &serde_json::to_value(result).unwrap(),
            &case["result"],
            &format!("valuation({case})"),
        );
    }
}

#[test]
fn payout_model_matches_python() {
    let data = golden("payout");
    let now = data["now"].as_f64().unwrap();
    let mut real = 0;
    for case in cases(&data, "cases") {
        let name = case["name"].as_str().unwrap();
        let state = match case.get("fixture").and_then(Value::as_str) {
            Some(fixture) => {
                real += 1;
                let text = std::fs::read_to_string(repository_root().join(fixture))
                    .expect("estado financeiro público");
                serde_json::from_str(text.trim_start_matches('\u{feff}')).expect("JSON válido")
            }
            None => case["state"].clone(),
        };
        let moment = case.get("now").and_then(Value::as_f64).unwrap_or(now);
        let forecast = case.get("forecast").filter(|value| !value.is_null());
        let result = payout_model(&state, forecast, &case["price_brl"], moment);
        assert_same(
            &serde_json::to_value(&result).unwrap(),
            &case["result"],
            name,
        );
    }
    assert_eq!(
        real, 2,
        "o estado financeiro real de portable-data/ precisa participar"
    );
    for case in cases(&data, "durations") {
        assert_eq!(
            duration_value(&case["input"]),
            case["result"].as_str().unwrap(),
            "duration({})",
            case["input"]
        );
    }
}

/// Pasta real e canônica, como `Path.resolve()` no gerador.
fn scratch_root() -> PathBuf {
    let path = std::env::temp_dir().join(format!("hashpilot-parity-{}", std::process::id()));
    std::fs::create_dir_all(&path).unwrap();
    hashpilot_core::rig_config::resolve_path(&path)
}

fn scrub(value: Value, root: &Path) -> Value {
    let text = value
        .to_string()
        .replace(&root.to_string_lossy().replace('\\', "\\\\"), "<ROOT>");
    // Goldens gerados no Linux: normaliza separadores para comparar também no Windows.
    serde_json::from_str(&text.replace("\\\\", "/")).unwrap()
}

#[test]
fn rig_config_matches_python() {
    let data = golden("rig_config");
    assert_eq!(MAX_GPUS as i64, data["max_gpus"].as_i64().unwrap());
    let root = scratch_root();
    for case in cases(&data, "cases") {
        let name = case["name"].as_str().unwrap();
        let detected = case["detected"].as_array().unwrap();
        match (
            resolve_devices(detected, &case["settings"], &root),
            case.get("ok"),
            case.get("error"),
        ) {
            (Ok(devices), Some(expected), _) => {
                let actual = scrub(
                    Value::Array(devices.into_iter().map(Value::Object).collect()),
                    &root,
                );
                assert_same(&actual, expected, name);
            }
            (Err(error), _, Some(expected)) => {
                assert_eq!(error, expected.as_str().unwrap(), "{name}")
            }
            (actual, ok, error) => {
                panic!("{name}: Rust {actual:?}, Python ok={ok:?} erro={error:?}")
            }
        }
    }
    for case in cases(&data, "updates") {
        let name = case["name"].as_str().unwrap();
        let kwargs = &case["kwargs"];
        let result = update_gpu_settings(
            &case["settings"],
            &case["gpu_uuid"],
            kwargs.get("enabled"),
            kwargs.get("wallet_override"),
            kwargs
                .get("change_wallet")
                .and_then(Value::as_bool)
                .unwrap_or(false),
        );
        match (result, case.get("ok"), case.get("error")) {
            (Ok(settings), Some(expected), _) => assert_same(&settings, expected, name),
            (Err(error), _, Some(expected)) => {
                assert_eq!(error, expected.as_str().unwrap(), "{name}")
            }
            (actual, ok, error) => {
                panic!("{name}: Rust {actual:?}, Python ok={ok:?} erro={error:?}")
            }
        }
    }
    for case in cases(&data, "wallets") {
        assert_eq!(
            valid_wallet(&case["input"]),
            case["valid"].as_bool().unwrap(),
            "wallet({})",
            case["input"]
        );
    }
    for case in cases(&data, "support") {
        let result = support_configuration(&case["settings"], &case["wallet"]);
        assert_same(
            &serde_json::to_value(&result).unwrap(),
            &case["result"],
            &format!("support({case})"),
        );
    }
}

#[test]
fn config_matches_python() {
    let data = golden("config");
    for case in cases(&data, "cases") {
        let environ: HashMap<String, String> =
            serde_json::from_value(case["environ"].clone()).unwrap();
        let platform = case["platform"].as_str().unwrap();
        let result = resolve_state_dir(platform, &|name| environ.get(name).cloned());
        match (result, case.get("ok"), case.get("error")) {
            (Ok(path), Some(expected), _) => {
                assert_eq!(
                    path.to_string_lossy().replace('\\', "/"),
                    expected.as_str().unwrap(),
                    "{platform}"
                )
            }
            (Err(error), _, Some(expected)) => {
                assert_eq!(error, expected.as_str().unwrap(), "{platform}")
            }
            (actual, ok, error) => {
                panic!("{platform}: Rust {actual:?}, Python ok={ok:?} erro={error:?}")
            }
        }
    }
    let env: HashMap<String, String> = serde_json::from_value(data["env"].clone()).unwrap();
    let lookup = |name: &str| env.get(name).cloned();
    if !cfg!(windows) {
        for case in cases(&data, "log_dir") {
            let settings = case["settings"].as_object().unwrap();
            let result = log_dir(Path::new("/s/Local/PearlMonitor"), settings, &lookup);
            assert_eq!(
                result.to_string_lossy(),
                case["result"].as_str().unwrap(),
                "log_dir({settings:?})"
            );
        }
    }
    for case in cases(&data, "expandvars_posix") {
        let input = case["input"].as_str().unwrap();
        assert_eq!(
            expandvars_posix(input, &lookup),
            case["result"].as_str().unwrap(),
            "posix({input})"
        );
    }
    for case in cases(&data, "expandvars_nt") {
        let input = case["input"].as_str().unwrap();
        assert_eq!(
            expandvars_nt(input, &lookup),
            case["result"].as_str().unwrap(),
            "nt({input})"
        );
    }
}
