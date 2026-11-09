//! Paridade do `thermal_target.py` (golden `thermal_target`): os traços gravados do
//! Python real são reexecutados passo a passo, comparando cada decisão (ação, motivo e
//! teto) e, no fim, o estado interno do controlador e do explorador.

use std::path::Path;

use hashpilot_core::numbers::py_float;
use hashpilot_core::thermal_target::{MemoryExplorer, TargetTemperatureController, clamp_target};
use serde_json::{Value, json};

fn number(value: &Value) -> Option<f64> {
    value.as_f64()
}

/// `"-inf"`/`"inf"` do gerador.
fn stamp(value: f64) -> Value {
    if value == f64::NEG_INFINITY {
        json!("-inf")
    } else if value == f64::INFINITY {
        json!("inf")
    } else {
        json!(value)
    }
}

fn close(left: &Value, right: &Value) -> bool {
    match (left, right) {
        (Value::Number(a), Value::Number(b)) => {
            let (a, b) = (a.as_f64().unwrap(), b.as_f64().unwrap());
            a == b || (a - b).abs() <= 1e-9 * a.abs().max(b.abs())
        }
        (Value::Array(a), Value::Array(b)) => {
            a.len() == b.len() && a.iter().zip(b).all(|(x, y)| close(x, y))
        }
        (Value::Object(a), Value::Object(b)) => {
            a.len() == b.len()
                && a.iter()
                    .all(|(key, value)| b.get(key).is_some_and(|other| close(value, other)))
        }
        _ => left == right,
    }
}

fn golden() -> Value {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/golden/thermal_target.json");
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

#[test]
fn controller_traces_match_python() {
    let golden = golden();
    let cases: Vec<&Value> = golden["cases"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|case| case["kind"] == "controller")
        .collect();
    assert!(cases.len() >= 10, "{}", cases.len());
    for case in cases {
        let name = &case["name"];
        let reasons = case["reasons"].as_array().unwrap();
        let mut controller = TargetTemperatureController::new(case["target"].as_f64().unwrap());
        for (position, op) in case["ops"].as_array().unwrap().iter().enumerate() {
            let input = op["in"].as_array().unwrap();
            if op["op"] == "notify" {
                controller.notify_applied(
                    input[0].as_str().unwrap(),
                    number(&input[1]),
                    number(&input[2]),
                    input[3].as_f64().unwrap(),
                    number(&input[4]),
                );
                continue;
            }
            let decision = controller.step(
                number(&input[0]),
                number(&input[1]),
                number(&input[2]),
                input[3].as_f64().unwrap(),
                number(&input[4]),
            );
            let out = op["out"].as_array().unwrap();
            let reason = reasons[out[1].as_u64().unwrap() as usize].as_str().unwrap();
            assert_eq!(
                (
                    decision.action,
                    decision.reason.as_str(),
                    decision.ceiling_mhz
                ),
                (out[0].as_str().unwrap(), reason, number(&out[2])),
                "{name} passo {position}, entrada {input:?}"
            );
        }
        let state = &case["state"];
        let pending = controller.pending_up.as_ref().map(|pending| json!({"from": pending.from, "to": pending.to, "at": pending.at, "rate_before": pending.rate_before}));
        let mut rates: Vec<(i64, f64)> = controller
            .rates
            .iter()
            .map(|(clock, rate)| (*clock, *rate))
            .collect();
        rates.sort_unstable_by_key(|(clock, _)| *clock);
        let rust = json!({
            "last_down_at": stamp(controller.last_down_at),
            "last_up_at": stamp(controller.last_up_at),
            "headroom_since": controller.headroom_since,
            "ceiling_mhz": controller.ceiling_mhz,
            "ceiling_until": stamp(controller.ceiling_until),
            "pending_up": pending,
            "rates": rates.into_iter().map(|(clock, rate)| (clock.to_string(), json!(rate))).collect::<serde_json::Map<_, _>>(),
        });
        assert!(
            close(&rust, state),
            "{name}: estado\nrust={rust}\npython={state}"
        );
    }
}

#[test]
fn memory_explorer_traces_match_python() {
    let golden = golden();
    let cases: Vec<&Value> = golden["cases"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|case| case["kind"] == "explorer")
        .collect();
    assert!(cases.len() >= 10, "{}", cases.len());
    for case in cases {
        let name = &case["name"];
        let scenario = &case["scenario"];
        let candidates: Vec<f64> = scenario["candidates"]
            .as_array()
            .unwrap()
            .iter()
            .map(|value| value.as_f64().unwrap())
            .collect();
        let mut explorer = MemoryExplorer::new(&candidates, 76.0);
        if let Some(objective) = scenario.get("objective").and_then(Value::as_str) {
            explorer.objective = objective.into();
            explorer.revenue_per_ths_day = scenario["revenue"].as_f64();
            explorer.price_kwh = scenario["price"].as_f64();
        }
        for (position, row) in case["rows"].as_array().unwrap().iter().enumerate() {
            let row = row.as_array().unwrap();
            let out = explorer.step(
                row[0].as_f64().unwrap(),
                number(&row[1]),
                number(&row[2]),
                number(&row[3]),
                number(&row[4]),
                number(&row[5]),
            );
            assert_eq!(
                out,
                number(&row[6]),
                "{name} passo {position}, entrada {row:?}"
            );
        }
        let state = &case["state"];
        let unreachable: Vec<f64> = explorer
            .unreachable
            .iter()
            .map(|bits| f64::from_bits(*bits))
            .collect();
        let rust = json!({
            "best_mhz": explorer.best_mhz,
            "scores": explorer.scores.iter().map(|(clock, score)| json!([clock, score])).collect::<Vec<_>>(),
            "queue": explorer.queue,
            "current_mhz": explorer.current_mhz,
            "unreachable": unreachable,
            "power": explorer.power.iter().map(|(clock, watts)| (format!("{:?}", f64::from_bits(*clock)), json!(watts))).collect::<serde_json::Map<_, _>>(),
            "explored_at": stamp(explorer.explored_at),
            "candidates": explorer.candidates,
        });
        assert!(
            close(&rust, state),
            "{name}: estado\nrust={rust}\npython={state}"
        );
    }
}

#[test]
fn clamp_target_matches_python() {
    let golden = golden();
    let case = golden["cases"]
        .as_array()
        .unwrap()
        .iter()
        .find(|case| case["kind"] == "clamp")
        .unwrap();
    for item in case["cases"].as_array().unwrap() {
        let value = match &item["in"][0] {
            Value::String(text) if text == "nan" => Some(f64::NAN),
            other => py_float(other),
        };
        let result = clamp_target(value, item["in"][1].as_f64().unwrap());
        assert_eq!(result, item["out"].as_f64(), "{item}");
    }
}
