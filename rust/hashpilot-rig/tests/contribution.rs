//! Paridade do proxy de contribuiÃ§Ã£o com o `contribution.py` (golden `contribution`,
//! gerado pelo Python real):
//! - ledger: 20 arquivos (vÃ¡lidos, migraÃ§Ã£o da versÃ£o 1 e cada regra de recusa) e a mesma
//!   sequÃªncia de operaÃ§Ãµes em cada um, comparando resultado, tipo numÃ©rico (`int` ou
//!   `float`) e o arquivo gravado byte a byte;
//! - reescrita Stratum: 57 combinaÃ§Ãµes de mensagem, carteira ativa e autenticaÃ§Ã£o;
//! - conexÃµes: 14 roteiros com sockets reais e uma pool simulada, os mesmos que o
//!   gerador executou contra o Python.

use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use hashpilot_rig::contribution::{
    Ledger, Num, Proxy, dumps_ascii, record_route_value, rewrite_message,
};
use serde_json::{Value, json};

fn golden() -> Value {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../hashpilot-core/tests/golden/contribution.json");
    serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
}

fn scratch(name: &str) -> PathBuf {
    let directory = std::env::temp_dir().join(format!(
        "hashpilot-contribution-{}-{name}-{}",
        std::process::id(),
        Instant::now().elapsed().as_nanos()
    ));
    let _ = std::fs::remove_dir_all(&directory);
    std::fs::create_dir_all(&directory).unwrap();
    directory
}

fn num(value: Num) -> Value {
    value.value()
}

#[test]
fn ledger_matches_python() {
    let golden = golden();
    let wallet = golden["wallet"].as_str().unwrap();
    let cases: Vec<&Value> = golden["cases"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|case| case["kind"] == "ledger")
        .collect();
    assert!(cases.len() >= 20, "{}", cases.len());
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let directory = scratch(name);
        let path = directory.join("support-mining-ledger.json");
        if let Some(content) = case["content"].as_str() {
            std::fs::write(&path, content).unwrap();
        }
        let mut ledger = Ledger::open(&path, wallet).unwrap();
        assert_eq!(json!(ledger.healthy), case["loaded"]["healthy"], "{name}");
        if name != "json_quebrado" {
            // A mensagem de JSON malformado Ã© do decodificador de cada linguagem.
            assert_eq!(
                ledger.error.clone().map_or(Value::Null, Value::String),
                case["loaded"]["error"],
                "{name}"
            );
        }
        for expected in case["ops"].as_array().unwrap() {
            let op = expected["op"].as_array().unwrap();
            let outcome: Result<Value, String> = match op[0].as_str().unwrap() {
                "seconds" => Ok(json!(ledger.seconds())),
                "role" => Ok(json!(ledger.role(op[1] == true))),
                "until_switch" => Ok(json!(ledger.until_switch(op[1] == true))),
                "available_support" => Ok(num(ledger.available_support())),
                "advance" => ledger.advance(&op[1], op[2] == true).map(|()| Value::Null),
                "record_route" => record_route_value(&mut ledger, &op[1], op[2].as_str().unwrap())
                    .map(|()| Value::Null),
                "reserve_support" => ledger.reserve_support().map(num),
                "record_share" => {
                    ledger.record_share(op[1].as_str().unwrap(), op[2] == true);
                    Ok(Value::Null)
                }
                "save" => ledger.save().map(|()| Value::Null),
                other => panic!("operaÃ§Ã£o {other}"),
            };
            match outcome {
                Ok(result) => assert_eq!(&result, &expected["result"], "{name} {op:?}"),
                Err(error) => assert_eq!(json!(error), expected["error"], "{name} {op:?}"),
            }
        }
        assert_eq!(ledger.data, case["data"], "{name}");
        let saved = std::fs::read_to_string(&path).ok();
        let expected = case["saved"].as_str().map(str::to_owned);
        assert_eq!(saved, expected, "{name}: arquivo gravado");
        let _ = std::fs::remove_dir_all(directory);
    }
}

#[test]
fn stratum_rewrite_matches_python() {
    let golden = golden();
    let wallet = golden["wallet"].as_str().unwrap();
    let cases: Vec<&Value> = golden["cases"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|case| case["kind"] == "rewrite")
        .collect();
    assert!(cases.len() >= 57, "{}", cases.len());
    for case in cases {
        let outcome = rewrite_message(
            &case["message"],
            wallet,
            case["active"].as_str().unwrap(),
            case["authenticated"] == true,
        );
        match outcome {
            Ok((message, is_auth)) => {
                assert_eq!(json!(dumps_ascii(&message)), case["line"], "{case}");
                assert_eq!(json!(is_auth), case["auth"], "{case}");
            }
            Err(error) => assert_eq!(json!(error), case["error"], "{case}"),
        }
    }
}

fn pair() -> (TcpStream, TcpStream) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let near = TcpStream::connect(listener.local_addr().unwrap()).unwrap();
    let (far, _) = listener.accept().unwrap();
    near.set_nodelay(true).unwrap();
    far.set_nodelay(true).unwrap();
    (near, far)
}

fn text_lines(group: &Value) -> Vec<Vec<u8>> {
    group
        .as_array()
        .unwrap()
        .iter()
        .map(|item| item.as_str().unwrap().as_bytes().to_vec())
        .collect()
}

/// O mesmo roteiro do gerador: a pool responde cada linha recebida com o seu grupo de
/// respostas e fecha depois de 0,4 s; o minerador manda cada pedaÃ§o e espera `wait` linhas.
fn run_scenario(case: &Value, wallet: &str) -> Value {
    let scenario = &case["scenario"];
    let name = case["name"].as_str().unwrap();
    let root = scratch(name);
    let settings = root.join("proxy-settings.json");
    std::fs::write(
        &settings,
        json!({"wallet": wallet, "support_enabled": scenario["support"]}).to_string(),
    )
    .unwrap();
    let state = root.join("state");
    std::fs::create_dir_all(&state).unwrap();
    if !scenario["ledger"].is_null() {
        std::fs::write(
            state.join("support-mining-ledger.json"),
            scenario["ledger"].to_string(),
        )
        .unwrap();
    }
    let mut proxy = Proxy::new(
        wallet,
        &settings,
        &state,
        0,
        None,
        Some(Box::new(|| 1000.0)),
    )
    .unwrap();
    let (mut client_end, mut client_proxy) = pair();
    let (mut upstream_proxy, mut upstream_end) = pair();
    let received = Arc::new(Mutex::new(Vec::<String>::new()));
    let client_bytes = Arc::new(Mutex::new(Vec::<u8>::new()));

    let responses: Vec<Vec<Vec<u8>>> = scenario["responses"]
        .as_array()
        .unwrap()
        .iter()
        .map(text_lines)
        .collect();
    let pool_received = received.clone();
    let pool = std::thread::spawn(move || {
        let mut buffer = Vec::new();
        let mut chunk = [0u8; 65536];
        upstream_end
            .set_read_timeout(Some(Duration::from_secs(3)))
            .unwrap();
        let mut run = || -> std::io::Result<()> {
            for group in &responses {
                while !buffer.contains(&b'\n') {
                    let count = upstream_end.read(&mut chunk)?;
                    if count == 0 {
                        return Ok(());
                    }
                    buffer.extend_from_slice(&chunk[..count]);
                }
                let position = buffer.iter().position(|byte| *byte == b'\n').unwrap();
                let line: Vec<u8> = buffer.drain(..=position).take(position).collect();
                pool_received
                    .lock()
                    .unwrap()
                    .push(String::from_utf8(line).unwrap());
                for response in group {
                    upstream_end.write_all(response)?;
                }
            }
            std::thread::sleep(Duration::from_millis(400));
            upstream_end.set_read_timeout(Some(Duration::from_millis(200)))?;
            loop {
                let count = upstream_end.read(&mut chunk)?;
                if count == 0 {
                    return Ok(());
                }
                buffer.extend_from_slice(&chunk[..count]);
            }
        };
        let _ = run();
        for line in buffer.split(|byte| *byte == b'\n') {
            if !line.is_empty() {
                pool_received
                    .lock()
                    .unwrap()
                    .push(String::from_utf8(line.to_vec()).unwrap());
            }
        }
    });

    let chunks = text_lines(&scenario["client"]);
    let waits: Vec<usize> = scenario
        .get("wait")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .map(|item| item.as_u64().unwrap() as usize)
                .collect()
        })
        .unwrap_or_else(|| vec![0; chunks.len()]);
    let miner_bytes = client_bytes.clone();
    let miner = std::thread::spawn(move || {
        let mut chunk = [0u8; 65536];
        client_end
            .set_read_timeout(Some(Duration::from_millis(1500)))
            .unwrap();
        let mut seen = 0;
        let run = || -> std::io::Result<()> {
            for (data, wait) in chunks.iter().zip(waits) {
                client_end.write_all(data)?;
                seen += wait;
                while miner_bytes
                    .lock()
                    .unwrap()
                    .iter()
                    .filter(|byte| **byte == b'\n')
                    .count()
                    < seen
                {
                    let count = client_end.read(&mut chunk)?;
                    if count == 0 {
                        return Ok(());
                    }
                    miner_bytes
                        .lock()
                        .unwrap()
                        .extend_from_slice(&chunk[..count]);
                }
            }
            client_end.set_read_timeout(Some(Duration::from_secs(3)))?;
            loop {
                let count = client_end.read(&mut chunk)?;
                if count == 0 {
                    return Ok(());
                }
                miner_bytes
                    .lock()
                    .unwrap()
                    .extend_from_slice(&chunk[..count]);
            }
        };
        let _ = run();
    });

    let error = proxy
        .serve_connection(&mut client_proxy, &mut upstream_proxy)
        .err()
        .map(|failure| failure.describe());
    drop(client_proxy);
    drop(upstream_proxy);
    pool.join().unwrap();
    miner.join().unwrap();

    let mut status: Value =
        serde_json::from_str(&std::fs::read_to_string(state.join("support-mining.json")).unwrap())
            .unwrap();
    let status_map = status.as_object_mut().unwrap();
    status_map.shift_remove("pid");
    status_map.shift_remove("sampled_at");
    let ledger = std::fs::read_to_string(state.join("support-mining-ledger.json")).ok();
    let _ = std::fs::remove_dir_all(root);
    let received = received.lock().unwrap().clone();
    let client = String::from_utf8(client_bytes.lock().unwrap().clone()).unwrap();
    json!({"upstream_received": received, "client_received": client, "error": error,
        "safety_disabled": proxy.safety_disabled, "status": status, "ledger": ledger})
}

#[test]
fn proxy_connections_match_python() {
    let golden = golden();
    let wallet = golden["wallet"].as_str().unwrap().to_owned();
    let cases: Vec<&Value> = golden["cases"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|case| case["kind"] == "proxy")
        .collect();
    assert!(cases.len() >= 14, "{}", cases.len());
    for case in cases {
        let name = case["name"].as_str().unwrap();
        let actual = run_scenario(case, &wallet);
        for key in [
            "upstream_received",
            "client_received",
            "error",
            "safety_disabled",
            "status",
            "ledger",
        ] {
            assert_eq!(actual[key], case[key], "{name}: {key}");
        }
    }
}
