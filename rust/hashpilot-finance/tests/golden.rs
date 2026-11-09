//! Paridade com o `finance.py` real: `tools/rust_parity/generate_goldens.py finance`.
//!
//! Estado normalizado, amostras do coletor, `finance-cache.json` e chamadas ao
//! histórico são comparados como TEXTO (o JSON que o Python grava, byte a byte).
//! Os modelos para a interface (`financial_model`, `portfolio_model`) são comparados
//! por valor: números iguais bit a bit (inteiro ou float), chaves em qualquer ordem.

use std::collections::BTreeMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

use hashpilot_core::numbers::valuation_numeric;
use hashpilot_finance::{
    Fetcher, FinanceCollector, FinanceHistory, PortfolioFinanceCollector, financial_model,
    normalize_finance_state, portfolio_model, portfolio_snapshots, py_sum,
};
use hashpilot_history::Clock;
use hashpilot_history::pyjson::encode;
use serde_json::{Value, json};

fn golden() -> Value {
    let path =
        Path::new(env!("CARGO_MANIFEST_DIR")).join("../hashpilot-core/tests/golden/finance.json");
    serde_json::from_str(&fs::read_to_string(path).expect("golden finance.json")).expect("JSON")
}

/// Igualdade de valor: números iguais como f64 (sem tolerância) e do mesmo tipo
/// (inteiro ou float); objetos sem ordem. Só o bloco `payout`, que vem da struct da
/// fase 1 (sempre `f64`), aceita inteiro do Python como float.
fn assert_same(actual: &Value, expected: &Value, path: &str) {
    match (actual, expected) {
        (Value::Number(a), Value::Number(b)) => {
            let loose = path.contains(".payout.");
            assert!(
                loose || a.is_f64() == b.is_f64(),
                "{path}: tipo {a} (Rust) != {b} (Python)"
            );
            let (a, b) = (a.as_f64().unwrap(), b.as_f64().unwrap());
            assert!(
                a == b || (a.is_nan() && b.is_nan()),
                "{path}: {a:?} != {b:?}"
            );
        }
        (Value::Object(a), Value::Object(b)) => {
            let mut keys: Vec<&String> = a.keys().chain(b.keys()).collect();
            keys.sort();
            keys.dedup();
            for key in keys {
                assert!(a.contains_key(key), "{path}.{key}: falta no Rust");
                assert!(b.contains_key(key), "{path}.{key}: sobra no Rust");
                assert_same(&a[key], &b[key], &format!("{path}.{key}"));
            }
        }
        (Value::Array(a), Value::Array(b)) => {
            assert_eq!(a.len(), b.len(), "{path}: tamanho");
            for (index, (left, right)) in a.iter().zip(b).enumerate() {
                assert_same(left, right, &format!("{path}[{index}]"));
            }
        }
        _ => assert_eq!(actual, expected, "{path}"),
    }
}

fn assert_text(actual: &str, expected: &str, label: &str) {
    if actual != expected {
        let position = actual
            .chars()
            .zip(expected.chars())
            .take_while(|(a, b)| a == b)
            .count();
        let start = position.saturating_sub(80);
        let excerpt = |text: &str| text.chars().skip(start).take(200).collect::<String>();
        panic!(
            "{label}: texto diferente a partir do caractere {position}\n rust:   {}\n python: {}",
            excerpt(actual),
            excerpt(expected)
        );
    }
}

fn text(value: &Value) -> String {
    value.as_str().expect("texto no golden").to_owned()
}

struct FakeHistory {
    archived: Value,
    calls: Mutex<Vec<String>>,
}

impl FinanceHistory for FakeHistory {
    fn finance(&self, state: &Value) {
        self.calls
            .lock()
            .unwrap()
            .push(encode(&json!(["finance", state])));
    }

    fn finance_state(&self, since: f64, wallet: &str) -> Value {
        self.calls
            .lock()
            .unwrap()
            .push(encode(&json!(["finance_state", since, wallet])));
        self.archived
            .get(wallet)
            .cloned()
            .unwrap_or_else(|| json!({"wallet": wallet, "snapshots": [], "sources": {}}))
    }
}

struct Scenario {
    responses: Arc<Mutex<Value>>,
    calls: Arc<Mutex<Vec<String>>>,
    now: Arc<Mutex<f64>>,
    directory: PathBuf,
}

impl Scenario {
    fn new(name: &str) -> Self {
        let directory = std::env::temp_dir().join(format!(
            "hashpilot-finance-golden-{}-{name}",
            std::process::id()
        ));
        let _ = fs::remove_dir_all(&directory);
        fs::create_dir_all(&directory).unwrap();
        Self {
            responses: Arc::new(Mutex::new(json!({}))),
            calls: Arc::new(Mutex::new(Vec::new())),
            now: Arc::new(Mutex::new(0.0)),
            directory,
        }
    }

    fn fetcher(&self) -> Fetcher {
        let (responses, calls) = (self.responses.clone(), self.calls.clone());
        Arc::new(move |url: &str| {
            calls.lock().unwrap().push(url.to_owned());
            let response = responses.lock().unwrap().get(url).cloned();
            match response {
                Some(Value::Object(map)) if map.contains_key("__error__") => {
                    Err(map["__error__"].as_str().unwrap().to_owned())
                }
                Some(value) => Ok(value),
                None => Err(format!("sem resposta para {url}")),
            }
        })
    }

    fn clock(&self) -> Clock {
        let now = self.now.clone();
        Arc::new(move || *now.lock().unwrap())
    }

    fn set_round(&self, round: &Value) {
        *self.now.lock().unwrap() = round["now"].as_f64().unwrap();
        *self.responses.lock().unwrap() = round["responses"].clone();
    }

    /// Arquivos da pasta de estado, como o `fin_tree` do gerador.
    fn tree(&self) -> BTreeMap<String, String> {
        fn walk(root: &Path, directory: &Path, out: &mut BTreeMap<String, String>) {
            let mut entries: Vec<_> = fs::read_dir(directory).unwrap().flatten().collect();
            entries.sort_by_key(|entry| entry.path());
            for entry in entries {
                let path = entry.path();
                if path.is_dir() {
                    walk(root, &path, out);
                } else {
                    let relative = path
                        .strip_prefix(root)
                        .unwrap()
                        .to_string_lossy()
                        .replace('\\', "/");
                    out.insert(relative, fs::read_to_string(&path).unwrap());
                }
            }
        }
        let mut out = BTreeMap::new();
        walk(&self.directory, &self.directory, &mut out);
        out
    }

    fn assert_tree(&self, expected: &Value, label: &str) {
        let actual = self.tree();
        let expected: BTreeMap<String, String> = expected
            .as_object()
            .unwrap()
            .iter()
            .map(|(key, value)| (key.clone(), text(value)))
            .collect();
        assert_eq!(
            actual.keys().collect::<Vec<_>>(),
            expected.keys().collect::<Vec<_>>(),
            "{label}: arquivos"
        );
        for (name, content) in &expected {
            assert_text(&actual[name], content, &format!("{label}: {name}"));
        }
    }

    fn fetched(&self) -> Value {
        let mut calls = self.calls.lock().unwrap().clone();
        calls.sort();
        json!(calls)
    }
}

impl Drop for Scenario {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.directory);
    }
}

fn collector_case(case: &Value) {
    let name = text(&case["name"]);
    let scenario = Scenario::new(&name);
    if let Some(cache) = case["cache"].as_str() {
        fs::write(scenario.directory.join("finance-cache.json"), cache).unwrap();
    }
    let rounds = case["rounds"].as_array().unwrap();
    *scenario.now.lock().unwrap() = rounds
        .first()
        .map_or(1_794_189_600.5, |round| round["now"].as_f64().unwrap());
    let history = (!case["archived"].is_null()).then(|| {
        Arc::new(FakeHistory {
            archived: case["archived"].clone(),
            calls: Mutex::new(Vec::new()),
        })
    });
    let mut collector = FinanceCollector::new(
        Some(&scenario.directory),
        history.clone().map(|item| item as Arc<dyn FinanceHistory>),
        case["wallet"].as_str().unwrap(),
        scenario.fetcher(),
        scenario.clock(),
    );
    assert_text(
        &encode(collector.state()),
        &text(&case["initial"]),
        &format!("{name}: estado inicial"),
    );
    if case["prepare"] == json!(true) {
        let published: Vec<Value> = collector
            .prepare()
            .map(|state| json!(encode(&state)))
            .into_iter()
            .collect();
        assert_eq!(
            json!(published),
            case["prepared"],
            "{name}: publicado antes da 1ª consulta"
        );
        assert_text(
            &encode(collector.state()),
            &text(&case["prepared_state"]),
            &format!("{name}: estado fundido"),
        );
    }
    for (index, (round, expected)) in rounds
        .iter()
        .zip(case["outputs"].as_array().unwrap())
        .enumerate()
    {
        let label = format!("{name}: rodada {index}");
        scenario.set_round(round);
        match collector.sample() {
            Ok(state) => assert_text(&encode(&state), &text(&expected["sample"]), &label),
            Err(error) => {
                assert_eq!(json!(error), expected["raised"], "{label}: erro");
                assert_text(
                    &encode(collector.state()),
                    &text(&expected["state"]),
                    &label,
                );
            }
        }
        scenario.assert_tree(&expected["files"], &label);
    }
    assert_eq!(
        scenario.fetched(),
        case["fetched"],
        "{name}: URLs consultadas"
    );
    let calls: Vec<String> = history
        .map(|item| item.calls.lock().unwrap().clone())
        .unwrap_or_default();
    assert_eq!(
        json!(calls),
        case["history_calls"],
        "{name}: chamadas ao histórico"
    );
}

fn portfolio_collector_case(case: &Value) {
    let scenario = Scenario::new("portfolio");
    let history = Arc::new(FakeHistory {
        archived: case["archived"].clone(),
        calls: Mutex::new(Vec::new()),
    });
    let wallets: Vec<String> = serde_json::from_value(case["wallets"].clone()).unwrap();
    let default_wallet = case["default_wallet"].as_str().unwrap();
    *scenario.now.lock().unwrap() = case["rounds"][0]["now"].as_f64().unwrap();
    let mut collector = PortfolioFinanceCollector::new(
        &scenario.directory,
        Some(history.clone()),
        Some(wallets[..2].to_vec()),
        scenario.fetcher(),
        default_wallet,
        scenario.clock(),
    );
    let handle = collector.wallets_handle();
    handle.configure(&wallets);
    for (index, round) in case["rounds"].as_array().unwrap().iter().enumerate() {
        let label = format!("portfólio: rodada {index}");
        scenario.set_round(round);
        assert_text(
            &encode(&collector.sample()),
            &text(&round["sample"]),
            &label,
        );
        scenario.assert_tree(&round["files"], &label);
    }
    handle.configure(&["invalida".to_owned()]);
    assert_eq!(json!(handle.current()), case["after_invalid"]);
    assert_eq!(
        scenario.fetched(),
        case["fetched"],
        "portfólio: URLs consultadas"
    );
    let mut calls = history.calls.lock().unwrap().clone();
    calls.sort();
    assert_eq!(
        json!(calls),
        case["history_calls"],
        "portfólio: chamadas ao histórico"
    );
}

#[test]
fn rust_finance_reproduces_python_finance() {
    let golden = golden();
    let cases = golden["cases"].as_array().unwrap();
    let mut counts: BTreeMap<String, usize> = BTreeMap::new();
    for case in cases {
        let kind = text(&case["kind"]);
        let name = case
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_owned();
        *counts.entry(kind.clone()).or_default() += 1;
        match kind.as_str() {
            "normalize" => {
                let actual = normalize_finance_state(&case["state"], case["now"].as_f64().unwrap());
                assert_text(
                    &encode(&actual),
                    &encode(&case["expected"]),
                    &format!("normalize {name}"),
                );
            }
            "financial" => {
                let hashrate = valuation_numeric(&case["hashrate"], true);
                let actual = financial_model(
                    &case["state"],
                    hashrate,
                    case["now"].as_f64().unwrap(),
                    case["wallet"].as_str().unwrap(),
                    &case["settings"],
                );
                assert_same(&actual, &case["expected"], &format!("financial {name}"));
            }
            "portfolio" => {
                let devices: Vec<Value> = case["devices"].as_array().unwrap().clone();
                let actual = portfolio_model(
                    &case["state"],
                    &devices,
                    case["now"].as_f64().unwrap(),
                    case["selected"].as_str(),
                    &case["settings"],
                    case["default_wallet"].as_str().unwrap(),
                );
                assert_same(&actual, &case["expected"], &format!("portfolio {name}"));
                // A ordem da lista consolidada só é estável com uma carteira faltando GPU.
                assert_eq!(
                    actual["forecast_missing_gpu_uuids"],
                    case["expected"]["forecast_missing_gpu_uuids"],
                    "portfolio {name}: ordem das GPUs sem leitura"
                );
            }
            "snapshots" => {
                let wallets: Option<Vec<String>> =
                    serde_json::from_value(case["wallets"].clone()).unwrap();
                let actual = portfolio_snapshots(&case["state"], wallets.as_deref());
                assert_text(
                    &encode(&actual),
                    &encode(&case["expected"]),
                    &format!("snapshots {name}"),
                );
            }
            "sum" => {
                let values = case["values"].as_array().unwrap();
                assert_text(
                    &encode(&py_sum(values)),
                    &encode(&case["expected"]),
                    &format!("sum {:?}", case["values"]),
                );
            }
            "collector" => collector_case(case),
            "portfolio_collector" => portfolio_collector_case(case),
            other => panic!("tipo de caso desconhecido: {other}"),
        }
    }
    // Garante que o golden não encolheu sem alguém perceber.
    assert!(
        counts["financial"] >= 25 && counts["portfolio"] >= 10 && counts["collector"] >= 9,
        "{counts:?}"
    );
}
