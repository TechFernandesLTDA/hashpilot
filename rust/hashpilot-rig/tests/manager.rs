//! Paridade do `RigManager` com o `rig.py` (golden `rig_manager`, só Windows).
//!
//! O gerador roda o `RigManager` Python real com o `FakeBackend` do `test_rig.py`
//! sobre 32 cenários descritos em dados. Este teste interpreta os mesmos passos com
//! o `RigManager` em Rust e um backend falso idêntico, e compara, depois de cada
//! passo, o resultado, o registro de GPUs, o estado do gerenciador e o que o backend
//! fez (processos iniciados, encerrados, esperados e restaurações). No fim, compara
//! os arquivos gravados e o `operations.sqlite3`. Nenhum processo real é criado.
#![cfg(windows)]

use std::cell::{Cell, RefCell};
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::rc::Rc;

use hashpilot_history::pyjson::py_eq;
use hashpilot_rig::continuity::IdleSleepGuard;
use hashpilot_rig::manager::{Backend, Paths, RigManager};
use serde_json::{Map, Value, json};

#[derive(Default)]
struct Fake {
    next_pid: i64,
    processes: BTreeMap<i64, (Value, bool)>,
    started: Vec<Value>,
    stopped: Vec<i64>,
    restored: Vec<Value>,
    waited: Vec<Value>,
    fail_uuid: Option<String>,
    blocked: Option<String>,
    verify_error: Option<String>,
    complete_all: bool,
    complete_pids: Vec<i64>,
    legacy: Vec<Value>,
}

impl Fake {
    fn inspect(&self, pid: &Value) -> Option<Value> {
        let (identity, alive) = self.processes.get(&pid.as_i64()?)?;
        alive.then(|| identity.clone())
    }
    fn is_owned(&self, identity: &Value) -> bool {
        identity.is_object()
            && self
                .inspect(&identity["pid"])
                .is_some_and(|current| py_eq(&current, identity))
    }
    fn start(&mut self, arguments: &[String]) -> Result<Value, String> {
        if let Some(uuid) = &self.fail_uuid
            && arguments.contains(uuid)
        {
            return Err("Failure isolated to selected GPU".into());
        }
        self.next_pid += 1;
        let pid = self.next_pid;
        let identity = json!({"pid": pid, "created_at": (pid * 1000).to_string(), "executable": arguments[0],
            "argv": arguments, "launch_id": pid.to_string()});
        self.processes.insert(pid, (identity.clone(), true));
        self.started.push(identity.clone());
        Ok(identity)
    }
}

struct Shared(Rc<RefCell<Fake>>);

impl Backend for Shared {
    fn verify_miner(&mut self, _miner_path: &Path) -> Result<(), String> {
        self.0.borrow().verify_error.clone().map_or(Ok(()), Err)
    }
    fn assert_gpu_available(&mut self, _miner_path: &Path, _index: &Value) -> Result<(), String> {
        self.0.borrow().blocked.clone().map_or(Ok(()), Err)
    }
    fn start(
        &mut self,
        arguments: &[String],
        _working_dir: &Path,
        _log_path: Option<&Path>,
    ) -> Result<Value, String> {
        self.0.borrow_mut().start(arguments)
    }
    fn inspect(&mut self, pid: &Value) -> Option<Value> {
        self.0.borrow().inspect(pid)
    }
    fn is_owned(&mut self, identity: &Value) -> bool {
        self.0.borrow().is_owned(identity)
    }
    fn stop(&mut self, identity: &Value) -> bool {
        let mut fake = self.0.borrow_mut();
        if !fake.is_owned(identity) {
            return false;
        }
        let pid = identity["pid"].as_i64().unwrap();
        fake.processes.get_mut(&pid).unwrap().1 = false;
        fake.stopped.push(pid);
        let target = json!(pid.to_string());
        for (identity, alive) in fake.processes.values_mut() {
            let argv = identity["argv"].as_array().cloned().unwrap_or_default();
            if let Some(position) = argv.iter().position(|item| item == "--miner-pid")
                && argv.get(position + 1) == Some(&target)
            {
                *alive = false;
            }
        }
        true
    }
    fn wait(&mut self, identity: &Value, _timeout: f64) -> bool {
        let mut fake = self.0.borrow_mut();
        fake.waited.push(identity["pid"].clone());
        let pid = identity["pid"].as_i64().unwrap_or(-1);
        if fake.complete_all || fake.complete_pids.contains(&pid) {
            fake.processes.get_mut(&pid).unwrap().1 = false;
            return true;
        }
        !fake.is_owned(identity)
    }
    fn legacy_candidates(&mut self, _miner_path: &Path) -> Result<Vec<Value>, String> {
        Ok(self.0.borrow().legacy.clone())
    }
    fn miner_device_map(
        &mut self,
        _miner_path: &Path,
        devices: &[Value],
    ) -> Result<Map<String, Value>, String> {
        Ok(devices
            .iter()
            .map(|gpu| {
                (
                    gpu["uuid"].as_str().unwrap().to_owned(),
                    gpu["index"].clone(),
                )
            })
            .collect())
    }
    fn restore_defaults(&mut self, gpu: &Value, _directory: &Path) -> Result<Value, String> {
        self.0.borrow_mut().restored.push(gpu["uuid"].clone());
        Ok(json!({"state": "restored", "at": 1000, "components": {}, "simulated": true}))
    }
}

/// Igualdade do Python com o tipo numérico conferido; chaves em qualquer ordem.
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

/// Primeira diferença, para a mensagem de erro.
fn difference(left: &Value, right: &Value, at: &str) -> Option<String> {
    match (left, right) {
        (Value::Object(a), Value::Object(b)) => {
            // A ordem das GPUs é a ordem em que o serviço as percorre e publica.
            if at.ends_with("devices") && !a.keys().eq(b.keys()) {
                return Some(format!(
                    "{at}: ordem rust={:?} python={:?}",
                    a.keys().collect::<Vec<_>>(),
                    b.keys().collect::<Vec<_>>()
                ));
            }
            for key in a.keys().chain(b.keys()) {
                match (a.get(key), b.get(key)) {
                    (Some(x), Some(y)) => {
                        if let Some(found) = difference(x, y, &format!("{at}.{key}")) {
                            return Some(found);
                        }
                    }
                    (x, y) => return Some(format!("{at}.{key}: rust={x:?} python={y:?}")),
                }
            }
            None
        }
        (Value::Array(a), Value::Array(b)) if a.len() == b.len() => a
            .iter()
            .zip(b)
            .enumerate()
            .find_map(|(index, (x, y))| difference(x, y, &format!("{at}[{index}]"))),
        _ => (!strict(left, right)).then(|| format!("{at}: rust={left} python={right}")),
    }
}

struct Normalizer {
    root: String,
    project: String,
    stamp: regex::Regex,
}

impl Normalizer {
    fn text(&self, text: &str) -> String {
        let replaced = text
            .replace(&self.root, "{root}")
            .replace(&self.project, "{project}");
        self.stamp
            .replace_all(&replaced, "mining-{stamp}.log")
            .into_owned()
    }
    fn value(&self, value: &Value) -> Value {
        match value {
            Value::String(text) => json!(self.text(text)),
            Value::Array(items) => {
                Value::Array(items.iter().map(|item| self.value(item)).collect())
            }
            Value::Object(map) => Value::Object(
                map.iter()
                    .map(|(key, item)| (self.text(key), self.value(item)))
                    .collect(),
            ),
            other => other.clone(),
        }
    }
}

struct Run {
    root: PathBuf,
    project: PathBuf,
    fake: Rc<RefCell<Fake>>,
    clock: Rc<Cell<f64>>,
    detected: Rc<RefCell<Vec<Value>>>,
}

impl Run {
    fn manager(&self) -> RigManager {
        let detected = self.detected.clone();
        let clock = self.clock.clone();
        RigManager::new(
            Paths {
                state_dir: self.root.join("state"),
                log_dir: self.root.join("logs"),
                settings_path: self.root.join("settings.json"),
                miner_path: self.root.join("SRBMiner-MULTI.exe"),
                python_path: self.root.join("python.exe"),
                project_dir: self.project.clone(),
            },
            Box::new(Shared(self.fake.clone())),
            Box::new(move || Ok(detected.borrow().clone())),
            Box::new(move || clock.get()),
            Some(Box::new(|_, _| Ok(json!(43210)))),
            IdleSleepGuard::with(Box::new(|_| 1)),
        )
        .expect("RigManager")
    }
}

fn devices_of(value: &Value) -> Option<Vec<Value>> {
    value.as_array().cloned()
}

fn run_case(case: &Value, clock: f64, number: usize) {
    let name = case["name"].as_str().unwrap();
    let root = std::path::absolute(std::env::temp_dir().join(format!(
        "hashpilot-rig-golden-{}-{number}",
        std::process::id()
    )))
    .unwrap();
    let _ = std::fs::remove_dir_all(&root);
    std::fs::create_dir_all(&root).unwrap();
    let project =
        std::path::absolute(Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("..")).unwrap();
    hashpilot_rig::fsutil::atomic_json(
        &root.join("settings.json"),
        &json!({"wallet": hashpilot_core::wallet::AUTHOR_WALLET}),
        false,
        false,
    )
    .unwrap();
    let run = Run {
        root: root.clone(),
        project: project.clone(),
        fake: Rc::new(RefCell::new(Fake { next_pid: 100, ..Fake::default() })),
        clock: Rc::new(Cell::new(clock)),
        detected: Rc::new(RefCell::new(
            (0..6)
                .map(|index| {
                    json!({"vendor": "NVIDIA", "uuid": format!("GPU-test-{index}"), "index": index,
                    "name": if index % 2 == 0 { "NVIDIA GeForce RTX 3070" } else { "NVIDIA GeForce GTX 1660 SUPER" }})
                })
                .collect(),
        )),
    };
    let normalizer = Normalizer {
        root: root.to_string_lossy().into_owned(),
        project: project.to_string_lossy().into_owned(),
        stamp: regex::Regex::new(r"mining-\d{8}-\d{6}-[0-9a-f]{6}\.log").unwrap(),
    };
    let mut manager = run.manager();
    let mut seen = 0;
    let expand = |text: &str, manager: &RigManager| {
        text.replace("{root}", &root.to_string_lossy())
            .replace("{miner}", &manager.miner_path.to_string_lossy())
            .replace(
                "{log_parent}",
                &manager.log_dir.parent().unwrap().to_string_lossy(),
            )
    };
    let target = |reference: &Value, manager: &RigManager, fake: &Fake| -> i64 {
        if reference[0] == "device" {
            manager.devices[reference[1].as_str().unwrap()][reference[2].as_str().unwrap()]["pid"]
                .as_i64()
                .unwrap()
        } else {
            fake.started[reference[1].as_u64().unwrap() as usize]["pid"]
                .as_i64()
                .unwrap()
        }
    };
    let steps = case["steps"].as_array().unwrap();
    let records = case["records"].as_array().unwrap();
    assert_eq!(steps.len(), records.len());
    for (position, (step, expected)) in steps.iter().zip(records).enumerate() {
        let op = step["op"].as_str().unwrap();
        let mut record = json!({"op": op});
        let outcome: Result<Option<Value>, String> = match op {
            "reconcile" => manager
                .reconcile(
                    step.get("detected").and_then(devices_of),
                    step.get("settings").cloned(),
                )
                .map(|desired| Some(json!(desired))),
            "new_manager" => {
                manager = run.manager();
                Ok(None)
            }
            "set_detector" => {
                *run.detected.borrow_mut() = devices_of(&step["detected"]).unwrap();
                Ok(None)
            }
            "set_clock" => {
                run.clock.set(step["value"].as_f64().unwrap());
                Ok(None)
            }
            "backend" => {
                let mut fake = run.fake.borrow_mut();
                for (key, value) in step["set"].as_object().unwrap() {
                    let text = value.as_str().map(str::to_owned);
                    match key.as_str() {
                        "fail_uuid" => fake.fail_uuid = text,
                        "blocked" => fake.blocked = text,
                        "verify_error" => fake.verify_error = text,
                        "wait_mode" => fake.complete_all = value == "complete",
                        "complete_pids" => {
                            fake.complete_pids = value
                                .as_array()
                                .unwrap()
                                .iter()
                                .map(|pid| pid.as_i64().unwrap())
                                .collect()
                        }
                        other => panic!("ajuste {other}"),
                    }
                }
                Ok(None)
            }
            "kill" => {
                let mut fake = run.fake.borrow_mut();
                let pid = target(&step["target"], &manager, &fake);
                fake.processes.get_mut(&pid).unwrap().1 = false;
                Ok(None)
            }
            "mutate" => {
                let mut fake = run.fake.borrow_mut();
                let pid = target(&step["target"], &manager, &fake);
                let identity = &mut fake.processes.get_mut(&pid).unwrap().0;
                if let Some(fields) = step.get("fields").and_then(Value::as_object) {
                    for (key, value) in fields {
                        identity[key.as_str()] = value.clone();
                    }
                }
                if let Some(extra) = step.get("append_argv") {
                    identity["argv"].as_array_mut().unwrap().push(extra.clone());
                }
                Ok(None)
            }
            "backend_start" => {
                let arguments: Vec<String> = step["args"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|arg| expand(arg.as_str().unwrap(), &manager))
                    .collect();
                run.fake.borrow_mut().start(&arguments).map(|_| None)
            }
            "add_legacy" => {
                let mut fake = run.fake.borrow_mut();
                let extra: Vec<Value> = step["started"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|index| fake.started[index.as_u64().unwrap() as usize].clone())
                    .collect();
                fake.legacy.extend(extra);
                Ok(None)
            }
            "set_legacy" => {
                let mut fake = run.fake.borrow_mut();
                fake.legacy = step["started"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|index| fake.started[index.as_u64().unwrap() as usize].clone())
                    .collect();
                Ok(None)
            }
            "spawn_successor" => {
                let old = manager.devices[step["uuid"].as_str().unwrap()]["miner"].clone();
                let arguments: Vec<String> = old["argv"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|arg| arg.as_str().unwrap().to_owned())
                    .collect();
                let mut fake = run.fake.borrow_mut();
                let mut candidate = fake.start(&arguments).unwrap();
                candidate["parent_pid"] = if step["parent"] == true {
                    old["pid"].clone()
                } else {
                    json!(999999)
                };
                fake.legacy = vec![candidate];
                Ok(None)
            }
            "write_request" => hashpilot_rig::fsutil::atomic_json(
                &manager
                    .state_dir
                    .join("rig-requests")
                    .join(step["file"].as_str().unwrap()),
                &step["payload"],
                false,
                false,
            )
            .map(|()| None),
            "process_requests" => {
                manager.process_requests();
                Ok(None)
            }
            "stop_gpu" => manager
                .stop_gpu(
                    step["uuid"].as_str().unwrap(),
                    step.get("restore") == Some(&json!(true)),
                )
                .map(|done| Some(json!(done))),
            "adopt_legacy" => manager.adopt_legacy(None).map(|done| Some(json!(done))),
            "migrate_legacy" => manager
                .migrate_legacy(step["uuid"].as_str().unwrap(), step["reference"] == true)
                .map(|done| Some(json!(done))),
            "observe_health" => manager
                .observe_health(step["snapshots"].as_object().unwrap())
                .map(|()| None),
            "publish" => manager.publish().map(|()| None),
            "set_attr" => {
                let value = &step["value"];
                match step["name"].as_str().unwrap() {
                    "reference_uuid" => manager.reference_uuid = value.as_str().map(str::to_owned),
                    "miner_path" => {
                        manager.miner_path =
                            PathBuf::from(expand(value.as_str().unwrap(), &manager))
                    }
                    "require_recovery_sensors" => manager.require_recovery_sensors = value == true,
                    "shutting_down" => manager.shutting_down = value == true,
                    other => panic!("atributo {other}"),
                }
                Ok(None)
            }
            "corrupt_registry" => {
                std::fs::create_dir_all(&manager.state_dir).unwrap();
                std::fs::write(&manager.registry_path, step["text"].as_str().unwrap()).unwrap();
                Ok(None)
            }
            other => panic!("passo {other}"),
        };
        match outcome {
            Ok(Some(result)) => record["result"] = result,
            Ok(None) => {}
            Err(error) => record["error"] = json!(error),
        }
        record["devices"] = Value::Object(manager.devices.clone());
        record["manager"] = json!({
            "error": manager.error, "ignored": manager.ignored, "processed_ids": manager.processed_ids,
            "reference_uuid": manager.reference_uuid, "registry_valid": manager.registry_valid,
            "shutting_down": manager.shutting_down, "miner_path": manager.miner_path.to_string_lossy(),
        });
        {
            let fake = run.fake.borrow();
            record["backend"] = json!({
                "started": fake.started[seen..].iter().map(|identity| identity["argv"].clone()).collect::<Vec<_>>(),
                "stopped": fake.stopped, "restored": fake.restored, "waited": fake.waited,
            });
            seen = fake.started.len();
        }
        let actual = normalizer.value(&record);
        if let Some(found) = difference(&actual, expected, "") {
            panic!("cenário {name}, passo {position} ({op}): {found}");
        }
    }
    let mut files = Map::new();
    let mut stack = vec![root.clone()];
    while let Some(folder) = stack.pop() {
        for item in std::fs::read_dir(&folder).unwrap().filter_map(Result::ok) {
            let path = item.path();
            if path.is_dir() {
                stack.push(path);
                continue;
            }
            let name = path.file_name().unwrap().to_string_lossy().into_owned();
            let extension = path
                .extension()
                .map(|ext| ext.to_string_lossy().into_owned())
                .unwrap_or_default();
            if extension == "sqlite3" || extension == "log" || name.contains("-journal") {
                continue;
            }
            let relative = path
                .strip_prefix(&root)
                .unwrap()
                .to_string_lossy()
                .replace('\\', "/");
            let text = std::fs::read_to_string(&path).unwrap();
            let text = text.trim_start_matches('\u{feff}');
            let mut content = if extension == "jsonl" {
                json!(
                    text.lines()
                        .filter(|line| !line.is_empty())
                        .map(|line| serde_json::from_str::<Value>(line).unwrap())
                        .collect::<Vec<_>>()
                )
            } else {
                serde_json::from_str::<Value>(text).unwrap_or_else(|_| json!({"text": text}))
            };
            if relative == "state/rig-manager.json"
                && let Some(object) = content.as_object_mut()
                && object.contains_key("pid")
            {
                object.insert("pid".into(), json!(0));
            }
            files.insert(normalizer.text(&relative), normalizer.value(&content));
        }
    }
    if let Some(found) = difference(&Value::Object(files), &case["files"], "arquivos") {
        panic!("cenário {name}: {found}");
    }
    let db = rusqlite::Connection::open(root.join("state").join("operations.sqlite3")).unwrap();
    let mut dump = Map::new();
    for table in ["acks", "recoveries", "incidents", "component_attempts"] {
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
                            _ => Value::Null,
                        })
                        .collect(),
                ))
            })
            .unwrap()
            .collect::<Result<_, _>>()
            .unwrap();
        dump.insert(table.into(), Value::Array(rows));
    }
    drop(db);
    if let Some(found) = difference(
        &normalizer.value(&Value::Object(dump)),
        &case["operations"],
        "operations",
    ) {
        panic!("cenário {name}: {found}");
    }
    let _ = std::fs::remove_dir_all(root);
}

#[test]
fn rig_manager_matches_python_scenarios() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../hashpilot-core/tests/golden/rig_manager.json");
    let golden: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    let cases = golden["cases"].as_array().unwrap();
    assert!(cases.len() >= 30, "{}", cases.len());
    let only = std::env::var("HASHPILOT_CASE").ok();
    for (number, case) in cases.iter().enumerate() {
        if only.as_deref().is_some_and(|name| case["name"] != name) {
            continue;
        }
        run_case(case, golden["clock"].as_f64().unwrap(), number);
    }
}
