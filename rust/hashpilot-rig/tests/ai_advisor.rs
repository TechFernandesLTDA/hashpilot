//! Paridade de `ai_advisor.py` e dos consultores de `gpu_agent.py` contra o golden
//! gerado pelo Python real (`tools/rust_parity/generate_goldens.py ai_advisor`).
//!
//! A nuvem e o cliente OpenCode são roteiros (como no gerador); o Ollama fala HTTP de
//! verdade com um servidor local de uma conexão.

use std::io::{BufRead, BufReader, Read, Write};
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use hashpilot_rig::ai_advisor::{
    self, Advice, AiError, AiResult, BackgroundAdvisor, Effects, OllamaAdvisor, RoutedAdvisor,
};
use serde_json::{Value, json};

fn golden() -> Value {
    let raw =
        std::fs::read_to_string("../hashpilot-core/tests/golden/ai_advisor.json").expect("golden");
    serde_json::from_str(&raw).expect("json")
}

fn cases<'a>(golden: &'a Value, kind: &str) -> Vec<&'a Value> {
    golden["cases"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|case| case["kind"] == kind)
        .collect()
}

/// `{"ok": ...}` ou `{"error": "Tipo: mensagem"}`. Mensagens do decodificador JSON são de
/// cada linguagem: só o tipo é comparado.
fn outcome(result: AiResult<Value>) -> Value {
    match result {
        Ok(value) => json!({"ok": value}),
        Err(error) => json!({"error": error.describe()}),
    }
}

fn same(actual: &Value, expected: &Value, what: &str) {
    fn decode(value: &Value) -> bool {
        value
            .get("error")
            .and_then(Value::as_str)
            .is_some_and(|text| text.starts_with("JSONDecodeError"))
    }
    if decode(actual) && decode(expected) {
        return;
    }
    let expected = if expected.get("ok").is_some() {
        json!({"ok": expected["ok"]})
    } else {
        json!({"error": expected["error"]})
    };
    assert_eq!(actual, &expected, "{what}");
}

#[test]
fn funcoes_puras() {
    let golden = golden();
    assert_eq!(golden["system"], json!(ai_advisor::SYSTEM));
    let providers: Vec<Value> = ai_advisor::PROVIDERS
        .iter()
        .map(|(name, label, url)| json!([name, label, url]))
        .collect();
    let expected = &golden["providers"];
    let expected: Vec<Value> = expected
        .as_object()
        .map(|map| {
            map.iter()
                .map(|(name, item)| json!([name, item[0], item[1]]))
                .collect()
        })
        .unwrap_or_else(|| expected.as_array().unwrap().clone());
    assert_eq!(providers, expected, "PROVIDERS");
    for case in cases(&golden, "configuration") {
        same(
            &outcome(ai_advisor::configuration(&case["in"])),
            case,
            &format!("configuration {}", case["in"]),
        );
    }
    for case in cases(&golden, "telemetry") {
        same(
            &outcome(ai_advisor::telemetry_context(&case["in"])),
            case,
            &format!("telemetry {}", case["in"]),
        );
    }
    for case in cases(&golden, "completion") {
        same(
            &outcome(ai_advisor::completion_text(&case["in"]).map(Value::from)),
            case,
            &format!("completion {}", case["in"]),
        );
    }
    for case in cases(&golden, "parse") {
        same(
            &outcome(ai_advisor::parse_advice(&case["in"])),
            case,
            &format!("parse {}", case["in"]),
        );
    }
    for case in cases(&golden, "ollama_init") {
        let input = &case["in"];
        let result = OllamaAdvisor::new(&input[0], input[1].as_str().unwrap(), &input[2])
            .map(|advisor| json!({"endpoint": advisor.endpoint, "model": advisor.model, "timeout": advisor.timeout_repr}));
        same(&outcome(result), case, &format!("ollama_init {input}"));
    }
}

fn scratch(name: &str) -> PathBuf {
    let path = std::env::temp_dir().join(format!(
        "hp-ai-{name}-{}",
        hashpilot_rig::manager::random_hex(8)
    ));
    std::fs::create_dir_all(&path).unwrap();
    path
}

#[test]
fn cota_local() {
    let golden = golden();
    let directory = scratch("cota");
    for row in cases(&golden, "budget")[0]["rows"].as_array().unwrap() {
        let result = ai_advisor::reserve_budget(&directory, &row["now"]).map(|()| Value::Null);
        same(
            &outcome(result),
            &row["out"],
            &format!("reserve {}", row["now"]),
        );
    }
    let _ = std::fs::remove_dir_all(directory);
}

type Log = Arc<Mutex<Vec<Value>>>;

fn scripted(case: &Value, log: &Log) -> Effects {
    let (log1, log2, log3, log4) = (log.clone(), log.clone(), log.clone(), log.clone());
    let catalog = case["catalog"].clone();
    let catalog_error = case["catalog_error"].as_str().map(str::to_owned);
    let completion = case["completion"].clone();
    let completion_error = case["completion_error"].as_str().map(str::to_owned);
    let opencode_catalog = case["opencode_catalog"].clone();
    let credential = case["credential"].as_str().map(str::to_owned);
    let which = case["which"].as_str().map(PathBuf::from);
    let raw = case["opencode_raw"].as_str().unwrap().as_bytes().to_vec();
    let code = case["opencode_code"].as_i64().unwrap() as i32;
    let budget_error = case["budget_error"].as_str().map(str::to_owned);
    let ollama_result = case["ollama_result"].clone();
    let ollama_error = case["ollama_error"].as_str().map(str::to_owned);
    let or_empty = |value: &Value| {
        if value.is_null() {
            json!([])
        } else {
            value.clone()
        }
    };
    let (catalog, opencode_catalog) = (or_empty(&catalog), or_empty(&opencode_catalog));
    Effects {
        request_json: Box::new(move |url, payload, key| {
            log1.lock()
                .unwrap()
                .push(json!({"url": url, "payload": payload, "key": key.is_some()}));
            if url.ends_with("/api/v1/models") {
                if let Some(error) = &catalog_error {
                    return Err(AiError::value(error.clone()));
                }
                return Ok(json!({"data": catalog}));
            }
            if url.ends_with("/zen/v1/models") {
                return Ok(json!({"data": opencode_catalog}));
            }
            match &completion_error {
                Some(error) => Err(AiError::value(error.clone())),
                None => Ok(completion.clone()),
            }
        }),
        credential: Box::new(move |_| Ok(credential.clone())),
        which: Box::new(move |_| which.clone()),
        run: Box::new(move |request| {
            let arguments = &request.arguments;
            let prompt = arguments.last().unwrap();
            let tail: String = {
                let chars: Vec<char> = prompt.chars().collect();
                chars[chars.len().saturating_sub(60)..].iter().collect()
            };
            let mut env = serde_json::Map::new();
            let mut pairs = request.environment.clone();
            pairs.sort();
            for (key, value) in pairs {
                let value = if key == "OPENCODE_CONFIG_DIR" {
                    "<temp>".to_string()
                } else {
                    value
                };
                env.insert(key, json!(value));
            }
            let in_temp = request
                .directory
                .to_string_lossy()
                .contains("HashPilot-advice-")
                && request.directory.is_dir();
            log2.lock().unwrap().push(
                json!({"run": arguments[..arguments.len() - 1], "prompt_tail": tail,
                "cwd_is_temp": in_temp, "env": env, "timeout": request.timeout.as_secs()}),
            );
            Ok((code, raw.clone()))
        }),
        reserve: Box::new(move |_| {
            log3.lock().unwrap().push(json!({"budget": true}));
            match &budget_error {
                Some(error) => Err(AiError::value(error.clone())),
                None => Ok(()),
            }
        }),
        ollama: Box::new(move |advisor, context| {
            log4.lock()
                .unwrap()
                .push(json!({"ollama_model": advisor.model, "context": context}));
            match &ollama_error {
                Some(error) => Err(AiError::value(error.clone())),
                None => Ok(ollama_result.clone()),
            }
        }),
        now: Box::new(|| 1_794_000_000.0),
    }
}

#[test]
fn consultor_roteado() {
    let golden = golden();
    let context = json!({"sample": {"temperature_c": 70.0, "power_w": 150.5, "nome": "x"}, "benchmark": {"validated": false, "x": 1}});
    for case in cases(&golden, "routed") {
        let name = case["name"].as_str().unwrap();
        let log: Log = Arc::new(Mutex::new(Vec::new()));
        // `key=""` no gerador conta como chave passada (`is not None`), mas vazia.
        let key = case["key"].as_bool().unwrap().then(|| {
            if name == "openrouter_chave_vazia" {
                String::new()
            } else {
                "sk-or-v1-teste-sem-valor-real".to_string()
            }
        });
        let advisor = RoutedAdvisor::new(
            Path::new("C:/estado"),
            None,
            Some(case["settings"].clone()),
            key,
            scripted(case, &log),
        );
        for (index, expected) in case["results"].as_array().unwrap().iter().enumerate() {
            same(
                &outcome(advisor.advise(&context)),
                expected,
                &format!("{name} chamada {index}"),
            );
        }
        assert_eq!(json!(advisor.label()), case["label"], "{name} label");
        assert_eq!(json!(advisor.enabled()), case["enabled"], "{name} enabled");
        assert_eq!(
            Value::Array(log.lock().unwrap().clone()),
            case["log"],
            "{name} log"
        );
    }
}

/// Servidor HTTP de uma conexão, como o `http.server` do gerador.
fn serve_once(status: u16, body: Vec<u8>) -> (u16, std::thread::JoinHandle<Value>) {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    let handle = std::thread::spawn(move || {
        let (stream, _) = listener.accept().unwrap();
        let mut reader = BufReader::new(stream.try_clone().unwrap());
        let mut request_line = String::new();
        reader.read_line(&mut request_line).unwrap();
        let path = request_line.split(' ').nth(1).unwrap().to_string();
        let (mut length, mut content_type) = (0usize, String::new());
        loop {
            let mut line = String::new();
            reader.read_line(&mut line).unwrap();
            let line = line.trim_end();
            if line.is_empty() {
                break;
            }
            let (name, value) = line.split_once(':').unwrap();
            match name.to_ascii_lowercase().as_str() {
                "content-length" => length = value.trim().parse().unwrap(),
                "content-type" => content_type = value.trim().to_string(),
                _ => {}
            }
        }
        let mut raw = vec![0u8; length];
        reader.read_exact(&mut raw).unwrap();
        let reason = match status {
            200 => "OK",
            404 => "Not Found",
            _ => "Internal Server Error",
        };
        let mut stream = stream;
        write!(
            stream,
            "HTTP/1.0 {status} {reason}\r\nContent-Length: {}\r\n\r\n",
            body.len()
        )
        .unwrap();
        stream.write_all(&body).unwrap();
        json!({"path": path, "content_type": content_type, "body": serde_json::from_slice::<Value>(&raw).unwrap()})
    });
    (port, handle)
}

#[test]
fn ollama_http_real() {
    let golden = golden();
    for case in cases(&golden, "ollama_http") {
        let name = case["name"].as_str().unwrap();
        let (port, server) = serve_once(
            case["status"].as_u64().unwrap() as u16,
            case["body"].as_str().unwrap().as_bytes().to_vec(),
        );
        let advisor = OllamaAdvisor::new(
            &json!("qwen2.5:0.5b"),
            &format!("http://127.0.0.1:{port}"),
            &json!(5),
        )
        .unwrap();
        let result = advisor.advise(&case["context"]);
        let received = server.join().unwrap();
        same(&outcome(result), &case["out"], name);
        assert_eq!(received, case["received"], "{name} pedido");
    }
}

struct Scripted {
    answers: Mutex<Vec<Value>>,
    contexts: Mutex<Vec<Value>>,
    routed: Option<Mutex<Vec<Value>>>,
    label: Mutex<String>,
}

fn answer(value: &Value) -> AiResult<Value> {
    match value.get("raise") {
        Some(error) => {
            let (kind, message) = error.as_str().unwrap().split_once(": ").unwrap();
            let kind: &'static str = Box::leak(kind.to_string().into_boxed_str());
            Err(AiError {
                kind,
                message: message.into(),
            })
        }
        None => Ok(value.clone()),
    }
}

impl Advice for Scripted {
    fn advise(&self, context: &Value) -> AiResult<Value> {
        self.contexts.lock().unwrap().push(context.clone());
        let next = self.answers.lock().unwrap().remove(0);
        let result = answer(&next);
        if result.is_ok() && self.routed.is_some() {
            *self.label.lock().unwrap() = "OpenRouter · Cloud Free".into();
        }
        result
    }

    fn label(&self) -> String {
        self.label.lock().unwrap().clone()
    }

    fn routed_settings(&self) -> Option<AiResult<Value>> {
        let configs = self.routed.as_ref()?;
        let mut configs = configs.lock().unwrap();
        let config = if configs.len() == 1 {
            configs[0].clone()
        } else {
            configs.remove(0)
        };
        Some(answer(&config))
    }
}

/// Os roteiros do gerador (`ai_background_cases`), com exceções como `{"raise": ...}`.
fn background_script(name: &str) -> (Vec<Value>, Option<Vec<Value>>) {
    let ok = json!({"action": "hold", "reason": "ok"});
    let fail = |text: &str| json!({"raise": text});
    let cloud =
        json!({"ai_enabled": true, "ai": {"provider": "openrouter", "cloud_consent": true}});
    let local = json!({"ai_enabled": true, "ai": {"provider": "ollama"}});
    match name {
        "local" => (
            vec![
                ok.clone(),
                fail("ValueError: falhou"),
                ok,
                json!({"action": "clock_down", "reason": "quarta"}),
            ],
            None,
        ),
        "intervalo_minimo" => (vec![ok.clone(), ok.clone(), ok], None),
        "intervalo_maior" => (vec![ok.clone(), ok], None),
        "roteado_nuvem" => (vec![ok.clone(), ok.clone(), ok], Some(vec![cloud])),
        "roteado_troca" => (
            vec![ok.clone(), ok.clone(), ok],
            Some(vec![local.clone(), local, cloud.clone(), cloud]),
        ),
        "roteado_config_invalida" => (vec![ok], Some(vec![fail("ValueError: x")])),
        "erro_tipo" => (
            vec![fail("TypeError: tipo"), fail("KeyError: 'response'"), ok],
            None,
        ),
        "erro_longo" => (
            vec![json!({"raise": format!("ValueError: {}fim", "é".repeat(170))})],
            None,
        ),
        other => panic!("roteiro desconhecido {other}"),
    }
}

#[test]
fn consultor_em_segundo_plano() {
    let golden = golden();
    for case in cases(&golden, "background") {
        let name = case["name"].as_str().unwrap();
        let (answers, routed) = background_script(name);
        let label = if routed.is_some() {
            "IA opcional"
        } else {
            "local_ollama"
        };
        let advisor = Arc::new(Scripted {
            answers: Mutex::new(answers),
            contexts: Mutex::new(Vec::new()),
            routed: routed.map(Mutex::new),
            label: Mutex::new(label.into()),
        });
        let clock = Arc::new(Mutex::new(0.0f64));
        let reader = clock.clone();
        let mut background = BackgroundAdvisor::new(
            advisor.clone(),
            case["interval"].as_f64().unwrap(),
            Arc::new(move || *reader.lock().unwrap()),
        );
        for poll in case["polls"].as_array().unwrap() {
            let now = poll["now"].as_f64().unwrap();
            *clock.lock().unwrap() = now;
            let result = background.poll(&json!({"sample": {"t": poll["now"]}}), Some(now));
            background.join();
            assert_eq!(result, poll["out"], "{name} em {now}");
        }
        assert_eq!(
            Value::Array(advisor.contexts.lock().unwrap().clone()),
            case["contexts"],
            "{name} contextos"
        );
    }
}

/// Consultor que só responde quando o teste libera (para trocar a configuração com a
/// consulta em andamento).
struct Held {
    release: Mutex<std::sync::mpsc::Receiver<()>>,
    configs: Mutex<Value>,
}

impl Advice for Held {
    fn advise(&self, _context: &Value) -> AiResult<Value> {
        self.release.lock().unwrap().recv().unwrap();
        Ok(json!({"action": "hold", "reason": "antiga"}))
    }

    fn label(&self) -> String {
        "OpenRouter · Cloud Free".into()
    }

    fn routed_settings(&self) -> Option<AiResult<Value>> {
        Some(Ok(self.configs.lock().unwrap().clone()))
    }
}

/// `BackgroundAdvisor`: a resposta de uma consulta iniciada antes da troca de configuração
/// é descartada (`revision`), como no Python.
#[test]
fn troca_de_configuracao_descarta_resposta_antiga() {
    let (release, receiver) = std::sync::mpsc::channel();
    let advisor = Arc::new(Held {
        release: Mutex::new(receiver),
        configs: Mutex::new(json!({"ai_enabled": true, "ai": {"provider": "ollama"}})),
    });
    let mut background = BackgroundAdvisor::new(advisor.clone(), 60.0, Arc::new(|| 10.0));
    assert_eq!(
        background.poll(&json!({}), Some(0.0))["in_flight"],
        json!(true)
    );
    *advisor.configs.lock().unwrap() =
        json!({"ai_enabled": true, "ai": {"provider": "openrouter"}});
    let during = background.poll(&json!({}), Some(5.0));
    assert_eq!(during["status"], json!("pending: IA em segundo plano"));
    release.send(()).unwrap();
    background.join();
    let after = background.poll(&json!({}), Some(10.0));
    assert_eq!(
        after["advice"],
        Value::Null,
        "resposta antiga descartada: {after}"
    );
    assert_eq!(after["response_age_seconds"], Value::Null);
}

/// Uso real: o Ollama deste computador (`cargo test -- --ignored ollama_local_real`).
/// A inferência roda na CPU (`num_gpu: 0`) e não toca na GPU nem na mineração.
#[test]
#[ignore = "precisa do Ollama local com qwen2.5:0.5b"]
fn ollama_local_real() {
    let advisor = OllamaAdvisor::local("qwen2.5:0.5b").unwrap();
    let context = json!({"sample": {"temperature_c": 74.0, "power_w": 160.0, "clock_mhz": 1650},
        "benchmark": {"validated": true, "mean_hashrate_ths": 74.8}});
    let advice = advisor.advise(&context).expect("resposta do Ollama local");
    assert!(
        ai_advisor::ACTIONS.contains(&advice["action"].as_str().unwrap()),
        "{advice}"
    );
    eprintln!("Ollama local sugeriu: {advice}");
}
