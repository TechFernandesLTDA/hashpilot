//! Paridade do laço do supervisor (`gpu_agent.control`) contra o golden gerado pelo Python
//! real com os mesmos roteiros: nvidia-smi falso, relógio virtual, processo do minerador e
//! leitura do minerador simulados. Nada aqui toca a GPU real.

use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use hashpilot_rig::ai_advisor::{Effects, RoutedAdvisor};
use hashpilot_rig::controller::{ControlArgs, Hooks, MinerSource, control};
use serde_json::{Value, json};

fn golden() -> Value {
    let raw = std::fs::read_to_string("../hashpilot-core/tests/golden/control_loop.json")
        .expect("golden");
    serde_json::from_str(&raw).expect("json")
}

struct State {
    now: f64,
    cycle: i64,
    clock: i64,
    limit: f64,
    memory: i64,
    log: Vec<Value>,
    trace: Vec<Value>,
}

/// `control_model_temp`: `round(base + 0.02 * (clock - 1600), 1)`.
fn model_temp(spec: &Value, cycle: i64, clock: i64) -> f64 {
    let temps = spec["temps"].as_array().unwrap();
    let base = temps[(cycle as usize).min(temps.len() - 1)]
        .as_f64()
        .unwrap();
    py_round(base + 0.02 * (clock - 1600) as f64, 1)
}

fn model_rate(clock: i64) -> f64 {
    py_round(70.0 + 0.03 * (clock - 1500) as f64, 2)
}

/// `round(x, n)` do Python: correto pelo valor decimal exato do `float`.
fn py_round(value: f64, digits: usize) -> f64 {
    format!("{value:.digits$}").parse().unwrap()
}

fn substitute(value: &Value, pid: u32, now: f64) -> Value {
    match value {
        Value::String(text) if text == "<pid>" => json!(pid),
        Value::String(text) if text == "<now>" => json!(now),
        Value::String(text) if text == "<now-30>" => json!(now - 30.0),
        Value::Object(map) => Value::Object(
            map.iter()
                .map(|(key, item)| (key.clone(), substitute(item, pid, now)))
                .collect(),
        ),
        other => other.clone(),
    }
}

fn scratch(name: &str) -> PathBuf {
    let path = std::env::temp_dir().join(format!(
        "hp-ctl-{name}-{}",
        hashpilot_rig::manager::random_hex(8)
    ));
    std::fs::create_dir_all(&path).unwrap();
    path
}

fn load(path: &Path) -> Value {
    std::fs::read_to_string(path)
        .ok()
        .and_then(|raw| serde_json::from_str(raw.trim_start_matches('\u{feff}')).ok())
        .unwrap_or(Value::Null)
}

fn run(case: &Value, gpu: &Value, t0: f64) -> Value {
    let name = case["name"].as_str().unwrap();
    let spec = case["spec"].clone();
    let root = scratch(name);
    let (state_dir, global_dir) = (root.join("gpu"), root.join("global"));
    std::fs::create_dir_all(&state_dir).unwrap();
    std::fs::create_dir_all(&global_dir).unwrap();
    let settings = global_dir.join("settings.json");
    let base = if spec["target"].is_null() {
        json!({})
    } else {
        json!({"target_temperature_c": spec["target"]})
    };
    std::fs::write(&settings, base.to_string()).unwrap();
    if let Some(files) = spec["files"].as_object() {
        for (file, payload) in files {
            std::fs::write(state_dir.join(file), payload.to_string()).unwrap();
        }
    }
    if let Some(remembered) = spec["remembered"].as_object() {
        let mut memory = json!({"gpu_uuid": gpu["uuid"]});
        for (key, value) in remembered {
            memory[key] = value.clone();
        }
        std::fs::write(state_dir.join("thermal-target.json"), memory.to_string()).unwrap();
    }
    let first_memory = spec["memory"]
        .as_array()
        .and_then(|items| items.first())
        .and_then(Value::as_i64)
        .unwrap_or(7001);
    let state = Arc::new(Mutex::new(State {
        now: t0,
        cycle: 0,
        clock: spec["start_clock"].as_i64().unwrap_or(1860),
        limit: 220.0,
        memory: first_memory,
        log: Vec::new(),
        trace: Vec::new(),
    }));
    let pid = std::process::id();

    let smi_state = state.clone();
    let smi_spec = spec.clone();
    let smi = move |arguments: &[String]| -> Result<String, String> {
        let mut state = smi_state.lock().unwrap();
        state.log.push(json!(arguments));
        let joined = arguments.join(" ");
        let value_after = |flag: &str| {
            arguments[arguments.iter().position(|item| item == flag).unwrap() + 1].clone()
        };
        if joined.contains("--query-gpu=temperature.gpu") {
            let temp = model_temp(&smi_spec, state.cycle, state.clock);
            let power = 150.0 + (state.clock - 1500) as f64 / 10.0;
            return Ok(format!(
                "{temp:.1}, {power:.1}, {:.2}, {}, 100",
                state.limit, state.clock
            ));
        }
        if joined.contains("--query-gpu=fan.speed") {
            return Ok("61".into());
        }
        if joined.contains("--query-supported-clocks=memory") {
            let items: Vec<String> = smi_spec["memory"]
                .as_array()
                .map(|items| items.iter().map(|item| item.to_string()).collect())
                .unwrap_or_default();
            return Ok(items.join("\n"));
        }
        if joined.contains("--query-gpu=clocks.mem") {
            return Ok(state.memory.to_string());
        }
        if arguments.iter().any(|item| item == "-lgc") {
            state.clock = value_after("-lgc")
                .split(',')
                .next()
                .unwrap()
                .parse()
                .unwrap();
        } else if arguments.iter().any(|item| item == "-pl") {
            state.limit = value_after("-pl").parse().unwrap();
        } else if arguments.iter().any(|item| item == "-lmc") {
            state.memory = value_after("-lmc")
                .split(',')
                .next()
                .unwrap()
                .parse()
                .unwrap();
        } else if arguments.iter().any(|item| item == "-rgc") {
            state.clock = 1860;
        }
        Ok(String::new())
    };

    let clock_state = state.clone();
    let sleep_state = state.clone();
    let (sleep_spec, sleep_dir, sleep_settings) =
        (spec.clone(), state_dir.clone(), settings.clone());
    let alive_state = state.clone();
    let alive_limit = spec["alive"].as_i64().unwrap();
    let miner_state = state.clone();
    let fresh = spec["fresh"].as_bool().unwrap_or(true);
    let executable = spec["executable"]
        .as_str()
        .unwrap_or("c:\\mineradores\\srbminer-multi.exe")
        .to_string();
    let mut counter = 0u64;
    let advisor = Arc::new(RoutedAdvisor::new(
        &global_dir,
        None,
        None,
        None,
        Effects::real(),
    ));
    let mut hooks = Hooks {
        smi: Box::new(smi),
        detect: Box::new({
            let gpu = gpu.clone();
            move || Ok(vec![gpu.clone()])
        }),
        now: Arc::new(move || clock_state.lock().unwrap().now),
        sleep: Box::new(move |seconds| {
            let current = load(&sleep_dir.join("gpu-agent-control.json"));
            let control = &current["target_control"];
            let entry = json!([
                current["mode"],
                current["writes"],
                control["action"],
                control["reason"],
                control["ceiling_mhz"]
            ]);
            let (cycle, now) = {
                let mut state = sleep_state.lock().unwrap();
                state.trace.push(entry);
                state.now += seconds;
                state.cycle += 1;
                (state.cycle, state.now)
            };
            if let Some(events) = sleep_spec["events"][cycle.to_string()].as_array() {
                for event in events {
                    let file = event[0].as_str().unwrap();
                    let target = if file == "<settings>" {
                        sleep_settings.clone()
                    } else {
                        sleep_dir.join(file)
                    };
                    let content = match &event[1] {
                        Value::String(text) => text.clone(),
                        payload => substitute(payload, pid, now).to_string(),
                    };
                    std::fs::write(target, content).unwrap();
                }
            }
        }),
        is_admin: Box::new({
            let admin = spec["admin"].as_bool().unwrap_or(true);
            move || admin
        }),
        identity: Box::new(move |miner| (miner == 4242).then(|| (1, executable.clone()))),
        alive: Box::new(move |miner| {
            miner == 4242 && alive_state.lock().unwrap().cycle < alive_limit
        }),
        read_miner: Box::new(move |_gpu, _count, _now| {
            let state = miner_state.lock().unwrap();
            Ok((
                json!({"hashrate": model_rate(state.clock), "hashrate_at": state.now - 5.0, "accepted": 2 * state.cycle,
                    "rejected": 0, "hw_errors": 0}),
                fresh,
            ))
        }),
        ids: Box::new(move || {
            counter += 1;
            format!("{counter:032x}")
        }),
        software: json!({"version": "teste"}),
        pid,
        settings_path: settings.clone(),
        advisor: Some(advisor),
        interrupted: Box::new(|| false),
        // Paridade: o Python grava toda decisão; a deduplicação do serviço fica desligada.
        hold_heartbeat: None,
    };
    let args = ControlArgs {
        miner_pid: 4242,
        uuid: gpu["uuid"].as_str().map(str::to_owned),
        index: Some(0),
        duration: spec["duration"].as_i64().unwrap_or(300),
        objective: "performance".into(),
        profile: spec["profile"].as_str().unwrap_or("performance").into(),
        source: MinerSource::default(),
        samples: spec["samples"].as_u64().unwrap_or(0),
    };
    let outcome = match control(&args, &state_dir, &state_dir, &mut hooks) {
        Ok(code) => json!({"code": code}),
        Err(error) => json!({"error": error.describe()}),
    };
    drop(hooks);
    let mut status = load(&state_dir.join("gpu-agent-control.json"));
    if status.get("controller_pid") == Some(&json!(pid)) {
        status["controller_pid"] = json!("<pid>");
    }
    let mut tables = serde_json::Map::new();
    let db_path = state_dir.join("gpu-agent.sqlite3");
    if db_path.exists() {
        let db = rusqlite::Connection::open(&db_path).unwrap();
        for table in [
            "benchmarks",
            "observations",
            "decisions",
            "trials",
            "adjustments",
        ] {
            let count: i64 = db
                .query_row(&format!("SELECT count(*) FROM {table}"), [], |row| {
                    row.get(0)
                })
                .unwrap();
            tables.insert(table.into(), json!(count));
        }
    }
    let state = state.lock().unwrap();
    let result = json!({"outcome": outcome, "cycles": state.cycle, "smi": state.log, "trace": state.trace, "status": status,
        "memory": load(&state_dir.join("thermal-target.json")),
        "owner_left": state_dir.join("gpu-control-owner.json").exists(), "tables": tables});
    let _ = std::fs::remove_dir_all(root);
    result
}

/// Primeira diferença entre dois JSON, com o caminho, para o erro ser legível.
fn first_difference(path: &str, actual: &Value, expected: &Value) -> Option<String> {
    match (actual, expected) {
        (Value::Object(left), Value::Object(right)) => {
            let mut keys: Vec<&String> = left.keys().chain(right.keys()).collect();
            keys.sort();
            keys.dedup();
            keys.into_iter().find_map(|key| {
                first_difference(
                    &format!("{path}.{key}"),
                    left.get(key).unwrap_or(&Value::Null),
                    right.get(key).unwrap_or(&Value::Null),
                )
                .or_else(|| {
                    (left.contains_key(key) != right.contains_key(key))
                        .then(|| format!("{path}.{key}: chave só de um lado"))
                })
            })
        }
        (Value::Array(left), Value::Array(right)) => left
            .iter()
            .zip(right)
            .enumerate()
            .find_map(|(index, (left, right))| {
                first_difference(&format!("{path}[{index}]"), left, right)
            })
            .or_else(|| {
                (left.len() != right.len())
                    .then(|| format!("{path}: {} itens, esperado {}", left.len(), right.len()))
            }),
        _ => (actual != expected).then(|| format!("{path}: {actual} != esperado {expected}")),
    }
}

#[test]
fn laco_de_controle_igual_ao_python() {
    let golden = golden();
    let t0 = golden["t0"].as_f64().unwrap();
    let mut failures = Vec::new();
    for case in golden["cases"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let actual = run(case, &golden["gpu"], t0);
        let outcome = if case.get("code").is_some() {
            json!({"code": case["code"]})
        } else {
            json!({"error": case["error"]})
        };
        let expected = json!({"outcome": outcome, "cycles": case["cycles"], "smi": case["smi"], "trace": case["trace"], "status": case["status"],
            "memory": case["memory"], "owner_left": case["owner_left"], "tables": case["tables"]});
        if let Some(difference) = first_difference(name, &actual, &expected) {
            failures.push(difference);
        }
    }
    assert!(
        failures.is_empty(),
        "{} roteiro(s) diferem:\n{}",
        failures.len(),
        failures.join("\n")
    );
}
