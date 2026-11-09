//! Paridade com o `process_backend.py` (golden `process`, gerado com a API real do
//! Windows) e ciclo de vida com processos reais e inofensivos (`ping` no loopback).
//! Nenhum teste toca no minerador ou na GPU.

use std::path::Path;
use std::time::Duration;

use hashpilot_rig::{ProcessBackend, normalized_path, windows_argv};
use serde_json::{Value, json};

fn golden() -> Value {
    let path =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../hashpilot-core/tests/golden/process.json");
    serde_json::from_str(&std::fs::read_to_string(path).expect("golden process.json"))
        .expect("JSON")
}

#[test]
fn windows_argv_matches_command_line_to_argv_w() {
    let golden = golden();
    let mut count = 0;
    for case in golden["cases"].as_array().unwrap() {
        if case["kind"] == "argv" {
            let line = case["line"].as_str().unwrap();
            assert_eq!(
                json!(windows_argv(line)),
                case["expected"],
                "linha {line:?}"
            );
            count += 1;
        }
    }
    assert!(count >= 30, "{count}");
}

#[cfg(windows)]
#[test]
fn normalized_path_matches_python() {
    for case in golden()["cases"].as_array().unwrap() {
        if case["kind"] == "path" {
            let path = case["path"].as_str().unwrap();
            assert_eq!(
                json!(normalized_path(path)),
                case["expected"],
                "caminho {path:?}"
            );
        }
    }
}

#[cfg(windows)]
fn ping_arguments() -> Vec<String> {
    let ping = std::env::var("SystemRoot").unwrap_or_else(|_| r"C:\Windows".into())
        + r"\System32\PING.EXE";
    vec![ping, "-n".into(), "60".into(), "127.0.0.1".into()]
}

#[cfg(windows)]
#[test]
fn start_identity_stop_and_wait_with_a_harmless_process() {
    let directory = std::env::temp_dir().join(format!("hashpilot-rig-test-{}", std::process::id()));
    let log = directory.join("logs").join("ping.log");
    let mut backend = ProcessBackend::new();
    let identity = backend
        .start(&ping_arguments(), &std::env::temp_dir(), Some(&log))
        .expect("start");
    assert!(
        identity["created_at"]
            .as_str()
            .unwrap()
            .parse::<u64>()
            .unwrap()
            > 0
    );
    assert_eq!(identity["launch_id"].as_str().unwrap().len(), 32);
    assert!(log.exists(), "log criado");
    assert!(backend.is_owned(&identity));

    // Um segundo backend (como o serviço depois de reiniciar) reconhece o processo
    // pela linha de comando real, lida do Windows.
    let mut restarted = ProcessBackend::new();
    assert!(
        restarted.is_owned(&identity),
        "reconhecido pela linha de comando"
    );
    let observed = restarted.inspect(&identity["pid"], true).unwrap();
    assert_eq!(observed["argv"], identity["argv"]);

    // Identidades adulteradas nunca são encerradas.
    let mut forged = identity.clone();
    forged["created_at"] = json!("1");
    assert!(!restarted.is_owned(&forged) && !restarted.stop(&forged));
    let mut other_argv = identity.clone();
    other_argv["argv"] = json!([identity["argv"][0], "-n", "61", "127.0.0.1"]);
    assert!(!restarted.is_owned(&other_argv) && !restarted.stop(&other_argv));
    let mut no_launch = identity.clone();
    no_launch["launch_id"] = json!("");
    assert!(!restarted.stop(&no_launch));
    assert!(backend.is_owned(&identity), "processo continua vivo");

    // Encerramento pelo backend que NÃO criou o processo (caminho TerminateProcess).
    assert!(restarted.stop(&identity));
    assert!(restarted.wait(&identity, Duration::from_secs(10)));
    assert!(!backend.is_owned(&identity));
    assert!(backend.inspect(&identity["pid"], false).is_none());
    let _ = std::fs::remove_dir_all(directory);
}

#[cfg(windows)]
#[test]
fn stop_through_the_launching_backend_and_invalid_pids() {
    let mut backend = ProcessBackend::new();
    let identity = backend
        .start(&ping_arguments(), &std::env::temp_dir(), None)
        .expect("start");
    assert!(backend.stop(&identity));
    assert!(backend.wait(&identity, Duration::from_secs(10)));
    for pid in [
        json!(0),
        json!(-1),
        json!(true),
        json!("123"),
        json!(null),
        json!(1.5),
    ] {
        assert!(backend.inspect(&pid, false).is_none(), "{pid}");
    }
    // O próprio processo de teste existe, mas não é nosso: nunca é encerrado.
    let me = backend.inspect(&json!(std::process::id()), false).unwrap();
    let stranger = json!({"pid": me["pid"], "created_at": me["created_at"], "executable": me["executable"], "argv": ["x"], "launch_id": "abc"});
    assert!(!backend.stop(&stranger));
}
