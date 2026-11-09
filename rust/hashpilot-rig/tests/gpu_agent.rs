//! Paridade do `gpu_agent.rs` com o `gpu_agent.py` (golden `gpu_agent`, gerado pelo Python
//! real com relógio fixo e `uuid4` em sequência): funções puras, leituras com nvidia-smi
//! falso, seleção e coordenação, e quatro roteiros do `GPUAgent` comparando cada resposta
//! e o conteúdo final das cinco tabelas do SQLite.

use std::cell::RefCell;
use std::path::{Path, PathBuf};
use std::rc::Rc;

use hashpilot_rig::gpu_agent::{self as ga, Advisor, AgentError, GpuAgent};
use serde_json::{Value, json};

fn golden() -> Value {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../hashpilot-core/tests/golden/gpu_agent.json");
    serde_json::from_str(&std::fs::read_to_string(path).expect("golden gpu_agent"))
        .expect("JSON do golden")
}

fn scratch(label: &str) -> PathBuf {
    let directory = std::env::temp_dir().join(format!(
        "hp-gpu-agent-{label}-{}-{}",
        std::process::id(),
        ga::uuid4_hex()
    ));
    std::fs::create_dir_all(&directory).expect("pasta temporária");
    directory
}

fn opt_f64(value: &Value) -> Option<f64> {
    value.as_f64()
}

fn opt_i64(value: &Value) -> Option<i64> {
    value.as_i64()
}

fn outcome(result: Result<Value, AgentError>) -> Value {
    match result {
        Ok(value) => json!({"out": value}),
        Err(error) => json!({"error": error.describe()}),
    }
}

fn expected(case: &Value) -> Value {
    match case.get("error") {
        Some(error) => json!({"error": error}),
        None => json!({"out": case["out"]}),
    }
}

#[test]
fn funcoes_puras_iguais_ao_python() {
    let golden = golden();
    let gpu = &golden["gpu"];
    let mut checked = 0;
    let mut naive_wrong = 0;
    for case in golden["cases"].as_array().unwrap() {
        let input = &case["in"];
        let got = match case["kind"].as_str().unwrap() {
            "normalize_sample" => {
                json!({"out": ga::normalize_sample(&input["sample"], opt_f64(&input["now"]))})
            }
            "thermal_limits" => {
                let (soft, hard) = ga::thermal_limits(&input["gpu"]);
                json!({"out": [soft, hard]})
            }
            "thermal_window_summary" => {
                json!({"out": ga::thermal_window_summary(input["rows"].as_array().unwrap(), input["now"].as_f64().unwrap())})
            }
            "performance_probe_gate" => json!({"out": ga::performance_probe_gate(
                &input["sample"], &input["benchmark"], input["profile"].as_str().unwrap(), input["soft"].as_f64().unwrap())}),
            "validated_plan" => {
                let best = Some(&input["best"]).filter(|best| !best.is_null());
                outcome(ga::validated_plan(
                    &input["device"],
                    &input["sample"],
                    input["action"].as_str().unwrap(),
                    best,
                ))
            }
            "control_plan" => {
                let advice = Some(&input["advice"]).filter(|advice| !advice.is_null());
                outcome(ga::control_plan(
                    gpu,
                    &input["sample"],
                    input["objective"].as_str().unwrap(),
                    input["profile"].as_str().unwrap(),
                    input["soft"].as_f64().unwrap(),
                    advice,
                    None,
                ))
            }
            "mean" => {
                let values: Vec<f64> = input["values"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|v| v.as_f64().unwrap())
                    .collect();
                let exact = ga::exact_mean(&values);
                let want = case["out"].as_f64().unwrap();
                assert_eq!(exact.to_bits(), want.to_bits(), "média exata de {values:?}");
                if ga::naive_mean(&values).to_bits() != want.to_bits() {
                    naive_wrong += 1;
                }
                checked += 1;
                continue;
            }
            _ => continue,
        };
        assert_eq!(got, expected(case), "{} com {input}", case["kind"]);
        checked += 1;
    }
    assert!(checked > 200, "poucos casos puros: {checked}");
    // A soma ingênua de floats erra em pelo menos três dos casos da média.
    assert!(
        naive_wrong >= 3,
        "a soma ingênua só errou {naive_wrong} vezes"
    );
}

#[test]
fn media_exata_em_casos_extremos() {
    // Subnormais, sinais mistos e cancelamento total, comparados com frações exatas.
    assert_eq!(ga::exact_mean(&[0.1, 0.2, 0.3]), 0.2);
    assert_eq!(ga::naive_mean(&[0.1, 0.2, 0.3]), 0.20000000000000004);
    assert_eq!(ga::exact_mean(&[1e16, 1.0, -1e16, 1.0]), 0.5);
    assert_eq!(ga::exact_mean(&[5e-324, 5e-324]), 5e-324);
    assert_eq!(ga::exact_mean(&[5e-324, 0.0]), 0.0);
    assert_eq!(ga::exact_mean(&[5e-324, 5e-324, 5e-324, 0.0]), 5e-324);
    assert_eq!(ga::exact_mean(&[1.0, -1.0]), 0.0);
    assert_eq!(ga::exact_mean(&[-3.0, -4.0]), -3.5);
    assert_eq!(ga::exact_mean(&[f64::MAX, f64::MAX]), f64::MAX);
    assert_eq!(ga::exact_mean(&[1.0, 2.0, 2.0]), 5.0 / 3.0);
    assert_eq!(ga::exact_mean(&[]), 0.0);
}

fn fake(output: &str) -> impl FnMut(&[String]) -> Result<String, String> + '_ {
    move |arguments: &[String]| {
        assert_eq!(arguments[0], "-i");
        assert_eq!(arguments[1], "GPU-abc-123");
        assert_eq!(arguments[3], "--format=csv,noheader,nounits");
        match output.strip_prefix("RuntimeError: ") {
            Some(message) => Err(message.to_owned()),
            None => Ok(output.to_owned()),
        }
    }
}

#[test]
fn leituras_com_nvidia_smi_falso() {
    let golden = golden();
    let gpu = &golden["gpu"];
    let folder = scratch("leituras");
    let (settings, finance) = (
        folder.join("settings.json"),
        folder.join("finance-latest.json"),
    );
    let mut checked = 0;
    for case in golden["cases"].as_array().unwrap() {
        let input = &case["in"];
        let output = input["output"].as_str().unwrap_or_default();
        let got = match case["kind"].as_str().unwrap() {
            "read_sample" => {
                let mut asked = Vec::new();
                let mut command = |arguments: &[String]| {
                    asked = arguments.to_vec();
                    fake(output)(arguments)
                };
                let result = outcome(ga::read_sample(gpu, &mut command, Some(1234.5)));
                assert_eq!(
                    asked[2],
                    "--query-gpu=temperature.gpu,power.draw,power.limit,clocks.gr,utilization.gpu"
                );
                result
            }
            "read_fan" => json!({"out": ga::read_fan(gpu, &mut fake(output))}),
            "read_memory_clocks" => json!({"out": ga::read_memory_clocks(gpu, &mut fake(output))}),
            "read_memory_clock" => json!({"out": ga::read_memory_clock(gpu, &mut fake(output))}),
            kind @ ("read_optimization" | "read_target_temperature") => {
                for (path, content) in [
                    (&settings, &input["settings"]),
                    (&finance, &input["finance"]),
                ] {
                    match content.as_str() {
                        Some(text) => std::fs::write(path, text).unwrap(),
                        None => {
                            let _ = std::fs::remove_file(path);
                        }
                    }
                }
                if kind == "read_optimization" {
                    let (objective, price, revenue) = ga::read_optimization(&settings, &finance);
                    json!({"out": [objective, price, revenue]})
                } else {
                    json!({"out": ga::read_target_temperature(&settings, "GPU-abc-123")})
                }
            }
            _ => continue,
        };
        assert_eq!(got, expected(case), "{} com {input}", case["kind"]);
        checked += 1;
    }
    assert_eq!(checked, 11 + 3 * 19 + 2 * 13);
    let _ = std::fs::remove_dir_all(folder);
}

#[test]
fn selecao_e_coordenacao() {
    let golden = golden();
    let gpu = &golden["gpu"];
    let pid = std::process::id();
    let own = |text: &str| text.replace("\"{self}\"", &pid.to_string());
    let folder = scratch("coordenacao");
    let (first, second) = (folder.join("a"), folder.join("b"));
    std::fs::create_dir_all(&first).unwrap();
    std::fs::create_dir_all(&second).unwrap();
    let mut checked = 0;
    for case in golden["cases"].as_array().unwrap() {
        let input = &case["in"];
        let got = match case["kind"].as_str().unwrap() {
            "select_device" => outcome(ga::select_device(
                input["devices"].as_array().unwrap(),
                input["uuid"].as_str(),
                opt_i64(&input["index"]),
                opt_i64(&input["miner_index"]),
            )),
            "snapshot_device_payload" => json!({"out": ga::snapshot_device_payload(
                &input["snapshot"], gpu, input["count"].as_u64().unwrap() as usize)}),
            "requested_control_stop" => {
                let stop = folder.join("gpu-agent-stop.json");
                match input["content"].as_str() {
                    Some(text) => std::fs::write(&stop, own(text)).unwrap(),
                    None => {
                        let _ = std::fs::remove_file(&stop);
                    }
                }
                json!({"out": ga::requested_control_stop(
                    &folder, "GPU-abc-123", input["started"].as_f64().unwrap(), opt_f64(&input["now"]), pid)})
            }
            "external_supervisor_active" => {
                for directory in [&first, &second] {
                    for name in ["thermal-control.json", "gpu-agent-control.json"] {
                        let _ = std::fs::remove_file(directory.join(name));
                    }
                }
                for (name, payload) in input["files"].as_object().unwrap() {
                    let text = if payload == "texto" {
                        "{x".to_owned()
                    } else {
                        own(&payload.to_string())
                    };
                    std::fs::write(folder.join(name), text).unwrap();
                }
                json!({"out": ga::external_supervisor_active(
                    &[first.as_path(), second.as_path()], input["gpu_uuid"].as_str(), opt_i64(&input["gpu_index"]), pid)})
            }
            _ => continue,
        };
        assert_eq!(got, expected(case), "{} com {input}", case["kind"]);
        checked += 1;
    }
    assert_eq!(checked, 9 + 12 + 11 + 20);
    let _ = std::fs::remove_dir_all(folder);
}

/// O consultor de teste do gerador: `ok:<ação>` sugere a ação; o resto falha.
struct TestAdvisor {
    spec: String,
    calls: Rc<RefCell<Vec<Value>>>,
}

impl Advisor for TestAdvisor {
    fn advise(&self, context: &Value) -> Result<Value, String> {
        self.calls.borrow_mut().push(context.clone());
        match self.spec.strip_prefix("ok:") {
            Some(action) => Ok(json!({"action": action, "reason": "Sugestão da IA de teste"})),
            None => Err(format!("IA de teste indisponível {}", "x".repeat(200))),
        }
    }
}

fn arg<'a>(args: &'a Value, key: &str) -> &'a Value {
    static NULL: Value = Value::Null;
    args.get(key).unwrap_or(&NULL)
}

fn replay(trace: &Value, gpus: &Value) {
    let folder = scratch("roteiro");
    let mut agent = GpuAgent::open(&folder, None).expect("abrir o banco");
    let mut counter = 0u128;
    agent.id_source = Box::new(move || {
        counter += 1;
        format!("{counter:032x}")
    });
    agent.clock = Box::new(|| 5000.0);
    let calls = Rc::new(RefCell::new(Vec::new()));
    let zero = json!(0);
    let policy = folder.join("policy");
    for op in trace["ops"].as_array().unwrap() {
        let args = &op["args"];
        let gpu = &gpus[op["gpu"].as_str().unwrap()];
        let now = opt_f64(arg(args, "now"));
        let name = op["op"].as_str().unwrap();
        let result: Result<Value, AgentError> = match name {
            "set_advisor" => {
                let spec = args["spec"].as_str().unwrap();
                agent.advisor = (!spec.is_empty()).then(|| {
                    Box::new(TestAdvisor {
                        spec: spec.to_owned(),
                        calls: calls.clone(),
                    }) as Box<dyn Advisor>
                });
                Ok(Value::Null)
            }
            "status" => agent.status(args["gpu_uuid"].as_str().unwrap(), now),
            "start_benchmark" => agent.start_benchmark(
                gpu,
                &args["duration_seconds"],
                args["objective"].as_str().unwrap(),
                now,
            ),
            "observe" => agent.observe(
                gpu,
                &args["sample"],
                arg(args, "hashrate_ths"),
                args.get("accepted").unwrap_or(&zero),
                args.get("rejected").unwrap_or(&zero),
                now,
                arg(args, "hash_sampled_at"),
                args.get("hw_errors").unwrap_or(&zero),
            ),
            "recommend" => agent.recommend(
                gpu,
                &args["sample"],
                arg(args, "ollama") == &json!(true),
                now,
            ),
            "assert_no_pending_trial" => agent.assert_no_pending_trial(gpu).map(|()| Value::Null),
            "record_application" => agent
                .record_application(
                    gpu,
                    &args["plan"],
                    &args["before"],
                    &args["baseline_hashrate"],
                    now,
                )
                .map(|()| Value::Null),
            "trial_feedback" => agent.trial_feedback(
                gpu,
                &args["sample"],
                arg(args, "hashrate_ths"),
                arg(args, "hash_sampled_at"),
                arg(args, "new_rejected_shares").as_i64().unwrap_or(0),
                now,
            ),
            "cancel_trial" => agent
                .cancel_trial(gpu, &args["sample"], now)
                .map(|plan| plan.unwrap_or(Value::Null)),
            "recover_interrupted_trials" => agent
                .recover_interrupted_trials(gpu, &mut || Ok(()), now)
                .map(|count| json!(count)),
            "record_adjustment" => agent
                .record_adjustment(
                    gpu,
                    &args["before"],
                    &args["after"],
                    &args["plan"],
                    arg(args, "hashrate"),
                    now,
                )
                .map(|()| Value::Null),
            "export_policy" => {
                let result = agent
                    .export_policy(gpu, &policy, now)
                    .map(|payload| payload.unwrap_or(Value::Null));
                let file = std::fs::read_to_string(policy.join("gpu-agent-policy.json")).ok();
                assert_eq!(
                    json!(file),
                    op["file"],
                    "arquivo da política em {}",
                    trace["name"]
                );
                result
            }
            other => panic!("operação desconhecida {other}"),
        };
        assert_eq!(
            outcome(result),
            expected(op),
            "{} / {name} com {args}",
            trace["name"]
        );
        let context = calls.borrow_mut().pop();
        if name == "recommend" {
            assert_eq!(
                json!(context),
                *arg(op, "advisor_context"),
                "contexto da IA em {}",
                trace["name"]
            );
        }
    }
    for (table, rows) in trace["tables"].as_object().unwrap() {
        let got = agent.table(table).expect("ler a tabela");
        assert_eq!(json!(got), *rows, "tabela {table} em {}", trace["name"]);
    }
    drop(agent);
    let _ = std::fs::remove_dir_all(folder);
}

#[test]
fn roteiros_do_agente_iguais_ao_python() {
    let golden = golden();
    let gpus = json!({"gpu": golden["gpu"], "ruim": golden["bad_gpu"]});
    let traces: Vec<&Value> = golden["cases"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|case| case["kind"] == "agent")
        .collect();
    assert_eq!(traces.len(), 7);
    for trace in traces {
        replay(trace, &gpus);
    }
}

#[test]
fn banco_do_rust_e_lido_pelo_mesmo_esquema() {
    // O arquivo é o mesmo do Python: WAL, as cinco tabelas e o índice das observações.
    let folder = scratch("esquema");
    let agent = GpuAgent::open(&folder, None).unwrap();
    assert_eq!(agent.path, folder.join("gpu-agent.sqlite3"));
    drop(agent);
    let db = rusqlite::Connection::open(folder.join("gpu-agent.sqlite3")).unwrap();
    let mode: String = db
        .query_row("PRAGMA journal_mode", [], |row| row.get(0))
        .unwrap();
    assert_eq!(mode, "wal");
    let mut names: Vec<String> = db
        .prepare("SELECT name FROM sqlite_master WHERE type IN ('table','index') ORDER BY name")
        .unwrap()
        .query_map([], |row| row.get(0))
        .unwrap()
        .map(Result::unwrap)
        .collect();
    names.sort();
    assert_eq!(
        names,
        [
            "adjustments",
            "benchmarks",
            "decisions",
            "observations",
            "observations_session",
            "sqlite_autoindex_benchmarks_1",
            "trials"
        ]
    );
    drop(db);
    let _ = std::fs::remove_dir_all(Path::new(&folder));
}

#[test]
fn json_ascii_como_o_python() {
    let text = json!({"a": "\u{e7}\u{2713}\u{1f600}\u{7f}", "b": [1, 2.5]});
    let expected =
        r#"{"a": "BSu00e7BSu2713BSud83dBSude00BSu007f", "b": [1, 2.5]}"#.replace("BS", "\\");
    assert_eq!(ga::py_dumps(&text, true), expected);
    assert_eq!(
        ga::py_dumps(&json!({"a": "\u{e7}"}), false),
        "{\"a\": \"\u{e7}\"}"
    );
}
