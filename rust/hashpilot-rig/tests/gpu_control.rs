//! Paridade da trava de controle por GPU com o `gpu_agent.NvidiaActuator` (golden
//! `gpu_control`, só Windows). Cada cenário parte do mesmo `gpu-control-owner.json`,
//! com trava real no primeiro byte do `.lock`, e compara erro, conteúdo do arquivo de
//! posse e arquivos da pasta depois de cada passo.
#![cfg(windows)]

use std::path::Path;

use hashpilot_rig::gpu_defaults::ControlLease;
use serde_json::{Value, json};

#[test]
fn control_lease_matches_python_actuator() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../hashpilot-core/tests/golden/gpu_control.json");
    let golden: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    let cases = golden["cases"].as_array().unwrap();
    assert!(cases.len() >= 15);
    for (number, case) in cases.iter().enumerate() {
        let name = case["name"].as_str().unwrap();
        let folder = std::path::absolute(std::env::temp_dir().join(format!(
            "hashpilot-gpu-control-{}-{number}",
            std::process::id()
        )))
        .unwrap();
        let _ = std::fs::remove_dir_all(&folder);
        std::fs::create_dir_all(&folder).unwrap();
        let scenario = &case["scenario"];
        let owner_path = folder.join("gpu-control-owner.json");
        if let Some(text) = scenario["owner"].as_str() {
            std::fs::write(
                &owner_path,
                text.replace("\"{self}\"", &std::process::id().to_string()),
            )
            .unwrap();
        }
        let mut holder = None;
        if scenario.get("hold") == Some(&json!(true)) {
            let lock = folder.join("gpu-control-owner.lock");
            std::fs::write(&lock, b" ").unwrap();
            let file = std::fs::OpenOptions::new()
                .read(true)
                .write(true)
                .open(&lock)
                .unwrap();
            assert!(hashpilot_rig::win32::lock_first_byte(&file));
            holder = Some(file);
        }
        let mut lease = ControlLease::new(&folder).unwrap();
        let steps = scenario["steps"].as_array().unwrap();
        let records = case["records"].as_array().unwrap();
        assert_eq!(steps.len(), records.len());
        for (position, (step, expected)) in steps.iter().zip(records).enumerate() {
            let outcome = match step.as_str().unwrap() {
                "acquire" => lease.acquire("GPU-a"),
                "assert" => lease.assert_owner("GPU-a"),
                "assert_other" => lease.assert_owner("GPU-b"),
                "replace" => {
                    std::fs::write(&owner_path, r#"{"token": "outro", "gpu_uuid": "GPU-a"}"#)
                        .unwrap();
                    Ok(())
                }
                "delete" => {
                    std::fs::remove_file(&owner_path).unwrap();
                    Ok(())
                }
                "close" => {
                    lease.close();
                    Ok(())
                }
                other => panic!("passo {other}"),
            };
            let mut record = json!({"op": step});
            if let Err(error) = outcome {
                record["error"] = json!(error);
            }
            let mut content = match std::fs::read_to_string(&owner_path) {
                Err(_) => json!("ausente"),
                Ok(text) => serde_json::from_str(&text).unwrap_or_else(|_| json!({"text": text})),
            };
            if content.get("token").and_then(Value::as_str) == Some(lease.token()) {
                // Mesma normalização do gerador: pid, token e horário desta sessão.
                assert_eq!(content["pid"], json!(std::process::id()));
                assert!(content["at"].is_f64());
                assert_eq!(content["lease_version"], json!(2));
                for key in ["token", "pid", "at"] {
                    content[key] = json!(format!("{{{key}}}"));
                }
            } else if content.get("pid") == Some(&json!(std::process::id())) {
                content["pid"] = json!("{self}");
            }
            record["owner"] = content;
            let mut files: Vec<String> = std::fs::read_dir(&folder)
                .unwrap()
                .map(|item| item.unwrap().file_name().to_string_lossy().into_owned())
                .collect();
            files.sort();
            record["files"] = json!(files);
            assert_eq!(&record, expected, "cenário {name}, passo {position}");
        }
        lease.close();
        if let Some(file) = holder {
            hashpilot_rig::win32::unlock_first_byte(&file);
        }
        let _ = std::fs::remove_dir_all(&folder);
    }
}
