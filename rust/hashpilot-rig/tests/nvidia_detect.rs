//! Paridade de `detect_nvidia_gpus` e `read_gpu_samples` com o Python (golden
//! `nvidia_detect`). O `nvidia-smi` é simulado pelas mesmas respostas gravadas, e o
//! teste compara o resultado, o erro e cada chamada ao comando.

use std::path::Path;

use hashpilot_rig::nvidia::{CapabilityCache, detect_nvidia_gpus, read_gpu_samples};
use serde_json::{Map, Value, json};

/// Igualdade com tipo (`240` ≠ `240.0`); a ordem das chaves não importa.
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
                    .all(|(key, value)| b.get(key).is_some_and(|other| strict(value, other)))
        }
        _ => left == right,
    }
}

fn fake<'a>(
    script: &'a Map<String, Value>,
    calls: &'a mut Vec<Value>,
) -> impl FnMut(&[String]) -> Result<String, String> + 'a {
    move |arguments: &[String]| {
        calls.push(json!(arguments));
        let key = arguments.join(" ");
        match script.get(&key) {
            None => Err(format!(
                "sem resposta para {}",
                key.chars().take(60).collect::<String>()
            )),
            Some(Value::Object(error)) => Err(error["error"].as_str().unwrap().to_owned()),
            Some(value) => Ok(value.as_str().unwrap().to_owned()),
        }
    }
}

#[test]
fn detector_and_samples_match_python() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../hashpilot-core/tests/golden/nvidia_detect.json");
    let golden: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    let cases = golden["cases"].as_array().unwrap();
    assert!(cases.len() >= 18);
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let script = case["script"].as_object().unwrap();
        if case["kind"] == "detect" {
            let mut cache = CapabilityCache::new();
            for (position, step) in case["steps"].as_array().unwrap().iter().enumerate() {
                let mut calls = Vec::new();
                let outcome = {
                    let mut command = fake(script, &mut calls);
                    detect_nvidia_gpus(
                        &mut command,
                        step.get("max_devices").and_then(Value::as_i64),
                        (case["cache"] == true).then_some(&mut cache),
                        step["now"].as_f64().unwrap(),
                    )
                };
                assert_eq!(
                    json!(calls),
                    step["calls"],
                    "{name} passo {position}: chamadas"
                );
                match outcome {
                    Ok(devices) => {
                        let devices = Value::Array(devices);
                        assert!(
                            strict(&devices, &step["result"]),
                            "{name} passo {position}:\nrust={devices}\npython={}",
                            step["result"]
                        );
                    }
                    Err(error) => {
                        assert_eq!(json!(error), step["error"], "{name} passo {position}")
                    }
                }
            }
        } else {
            let mut calls = Vec::new();
            let devices = case["devices"].as_array().unwrap();
            let outcome = {
                let mut command = fake(script, &mut calls);
                read_gpu_samples(devices, &mut command, case["now"].as_f64().unwrap())
            };
            assert_eq!(json!(calls), case["calls"], "{name}: chamadas");
            match outcome {
                Ok(samples) => {
                    let samples = Value::Object(samples);
                    assert!(
                        strict(&samples, &case["result"]),
                        "{name}:\nrust={samples}\npython={}",
                        case["result"]
                    );
                }
                Err(error) => assert_eq!(json!(error), case["error"], "{name}"),
            }
        }
    }
}

#[test]
fn embedded_catalog_is_the_repository_file() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../data/nvidia-gpus.json");
    let file: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    let identity =
        hashpilot_rig::nvidia::catalog_identity("NVIDIA GeForce RTX 3070", &json!("0x248410DE"));
    let entry = file["gpus"]
        .as_array()
        .unwrap()
        .iter()
        .find(|item| item["id"] == "nvidia-geforce-rtx-3070")
        .unwrap();
    assert_eq!(identity["catalog_id"], entry["id"]);
    assert_eq!(identity["catalog_sources"], entry["sources"]);
}
