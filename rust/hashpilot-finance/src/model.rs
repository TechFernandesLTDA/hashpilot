//! Parte pura do `finance.py`: normalização do estado, `financial_model`,
//! `portfolio_model` e `portfolio_snapshots`.
//!
//! O estado é JSON, como o dicionário do Python, e a ordem das chaves é preservada.
//! Os números seguem o Python: `fetched_at` ausente vira o inteiro `0`, a fração de
//! contribuição desligada é o inteiro `0`, e as somas usam o `sum()` compensado do
//! Python 3.12+ (Neumaier), para o resultado sair igual bit a bit.

use std::collections::{BTreeMap, BTreeSet};
use std::sync::LazyLock;

use hashpilot_core::numbers::{numeric, valuation_numeric};
use hashpilot_core::payout::payout_model;
use hashpilot_core::valuation::value_in_currencies;
use hashpilot_core::wallet::support_configuration;
use hashpilot_history::pyjson::{py_eq, truthy};
use serde_json::{Map, Value, json};

/// Fuso fixo do `finance.LOCAL_TZ` (UTC−3, sem horário de verão).
pub const LOCAL_OFFSET_SECONDS: f64 = -3.0 * 3600.0;
/// Taxa do SRBMiner para pearlhash, exibida no próprio log.
pub const DEV_FEE: f64 = 0.02;

static NULL: Value = Value::Null;
static EMPTY_OBJECT: LazyLock<Value> = LazyLock::new(|| Value::Object(Map::new()));
static EMPTY_LIST: Value = Value::Array(Vec::new());
static ZERO: LazyLock<Value> = LazyLock::new(|| json!(0));

/// `objeto.get(chave, padrão)`; um objeto que não é dicionário conta como vazio.
fn get_or<'a>(object: &'a Value, key: &str, default: &'a Value) -> &'a Value {
    object
        .as_object()
        .and_then(|map| map.get(key))
        .unwrap_or(default)
}

fn field<'a>(object: &'a Value, key: &str) -> &'a Value {
    get_or(object, key, &NULL)
}

fn sub<'a>(object: &'a Value, key: &str) -> &'a Value {
    get_or(object, key, &EMPTY_OBJECT)
}

fn number(value: Option<f64>) -> Value {
    value.map_or(Value::Null, |x| json!(x))
}

/// Número de um valor JSON para aritmética (`True` vale 1, sinal de -0.0 mantido).
fn as_number(value: &Value) -> Option<f64> {
    match value {
        Value::Bool(flag) => Some(f64::from(u8::from(*flag))),
        Value::Number(number) => number.as_f64(),
        _ => None,
    }
}

/// `source.get('fetched_at', 0)` como número (estado já normalizado).
fn fetched_at(source: &Value) -> f64 {
    as_number(get_or(source, "fetched_at", &ZERO)).unwrap_or(0.0)
}

/// `sum(valores)` do Python 3.12+: inteiros somados exatos até o primeiro float;
/// depois, soma compensada de Neumaier, com a correção aplicada no fim só se finita.
pub fn py_sum<'a>(values: impl IntoIterator<Item = &'a Value>) -> Value {
    let mut values = values.into_iter();
    let mut integer: i64 = 0;
    let mut total = loop {
        match values.next() {
            None => return json!(integer),
            Some(Value::Bool(flag)) => integer += i64::from(*flag),
            Some(Value::Number(number)) if !number.is_f64() => {
                integer += number.as_i64().unwrap_or(0);
            }
            Some(other) => break integer as f64 + as_number(other).unwrap_or(f64::NAN),
        }
    };
    let mut compensation = 0.0_f64;
    for value in values {
        match value {
            Value::Number(number) if number.is_f64() => {
                let x = number.as_f64().unwrap_or(f64::NAN);
                let sum = total + x;
                if total.abs() >= x.abs() {
                    compensation += (total - sum) + x;
                } else {
                    compensation += (x - sum) + total;
                }
                total = sum;
            }
            other => total += as_number(other).unwrap_or(f64::NAN),
        }
    }
    if compensation != 0.0 && compensation.is_finite() {
        total += compensation;
    }
    json!(total)
}

/// `min(valores)` do Python: o primeiro menor vence nos empates.
fn py_min<'a>(values: impl IntoIterator<Item = &'a Value>) -> Option<&'a Value> {
    let mut best: Option<(&Value, f64)> = None;
    for value in values {
        let x = as_number(value).unwrap_or(f64::NAN);
        if best.is_none_or(|(_, current)| x < current) {
            best = Some((value, x));
        }
    }
    best.map(|(value, _)| value)
}

/// `finance.normalize_finance_state`: ordena observações reais e tolera campos
/// malformados do cache ou da API.
pub fn normalize_finance_state(state: &Value, now: f64) -> Value {
    let mut result = state.as_object().cloned().unwrap_or_default();
    let mut sources = Map::new();
    if let Some(Value::Object(original)) = result.get("sources") {
        for (key, source) in original {
            let Value::Object(source) = source else {
                continue;
            };
            let mut source = source.clone();
            let valid = match (key.as_str(), source.get("data")) {
                ("rewards", Some(Value::Array(_))) => true,
                ("rewards", _) => false,
                (_, Some(Value::Object(_))) => true,
                _ => false,
            };
            if !valid {
                continue;
            }
            let fetched = numeric(source.get("fetched_at").unwrap_or(&NULL));
            source.insert(
                "fetched_at".into(),
                match fetched {
                    Some(x) if x != 0.0 => json!(x),
                    _ => json!(0),
                },
            );
            let Some(Value::Object(data)) = source.get_mut("data") else {
                sources.insert(key.clone(), Value::Object(source));
                continue;
            };
            if key == "price" {
                for asset in ["pearl-2", "bitcoin"] {
                    match data.get_mut(asset) {
                        Some(Value::Object(asset_data)) => {
                            for currency in ["brl", "usd"] {
                                let value = valuation_numeric(
                                    asset_data.get(currency).unwrap_or(&NULL),
                                    true,
                                );
                                asset_data.insert(currency.into(), number(value));
                            }
                            if let Some(stamp) = asset_data.get("last_updated_at") {
                                let value = number(numeric(stamp));
                                asset_data.insert("last_updated_at".into(), value);
                            }
                        }
                        _ => {
                            data.insert(asset.into(), Value::Object(Map::new()));
                        }
                    }
                }
            }
            let fields: &[&str] = match key.as_str() {
                "balance" => &[
                    "total",
                    "confirmed",
                    "unconfirmed",
                    "threshold",
                    "maturation_time",
                ],
                "payouts" => &["paid"],
                "network" => &[
                    "net_hashrate",
                    "block_time",
                    "block_reward",
                    "fee",
                    "minpay",
                    "blocks_for_maturation",
                ],
                _ => &[],
            };
            for name in fields {
                if let Some(value) = data.get(*name) {
                    let value = number(numeric(value).filter(|x| *x >= 0.0));
                    data.insert((*name).into(), value);
                }
            }
            sources.insert(key.clone(), Value::Object(source));
        }
    }
    result.insert("sources".into(), Value::Object(sources));

    // `points[stamp] = ...`: a última amostra de cada instante vence; -0.0 == 0.0.
    let mut points: Vec<(f64, f64)> = Vec::new();
    let mut positions: BTreeMap<u64, usize> = BTreeMap::new();
    if let Some(Value::Array(snapshots)) = result.get("snapshots") {
        for point in snapshots {
            if !point.is_object() {
                continue;
            }
            let stamp = numeric(field(point, "timestamp"));
            let total = numeric(field(point, "total_prl"));
            let (Some(stamp), Some(total)) = (stamp, total) else {
                continue;
            };
            if 0.0 <= stamp && stamp <= now + 5.0 && total >= 0.0 {
                let key = (stamp + 0.0).to_bits();
                match positions.get(&key) {
                    Some(&position) => points[position] = (stamp, total),
                    None => {
                        positions.insert(key, points.len());
                        points.push((stamp, total));
                    }
                }
            }
        }
    }
    points.sort_by(|a, b| (a.0 + 0.0).total_cmp(&(b.0 + 0.0)));
    let snapshots = points
        .into_iter()
        .map(|(stamp, total)| json!({"timestamp": stamp, "total_prl": total}))
        .collect();
    result.insert("snapshots".into(), Value::Array(snapshots));
    if !result.get("errors").is_some_and(Value::is_object) {
        result.insert("errors".into(), Value::Object(Map::new()));
    }
    Value::Object(result)
}

/// Meia-noite local (UTC−3) e início da semana (segunda-feira), como o
/// `datetime.fromtimestamp(now, LOCAL_TZ)` do Python, que arredonda ao microssegundo.
fn day_boundaries(now: f64) -> (f64, f64) {
    let mut seconds = now.floor();
    if ((now - seconds) * 1e6).round_ties_even() >= 1e6 {
        seconds += 1.0;
    }
    let local = seconds + LOCAL_OFFSET_SECONDS;
    let days = (local / 86400.0).floor();
    let midnight = days * 86400.0 - LOCAL_OFFSET_SECONDS;
    // 01/01/1970 foi quinta-feira (`weekday()` 3).
    let weekday = (days + 3.0).rem_euclid(7.0);
    (midnight, midnight - weekday * 86400.0)
}

fn currencies(amounts: &[(&str, Value)], prices: [&Value; 4]) -> Value {
    let mut result = Map::new();
    for (key, amount) in amounts {
        let values = value_in_currencies(amount, prices[0], prices[1], prices[2], prices[3]);
        result.insert(
            (*key).into(),
            serde_json::to_value(values).unwrap_or(Value::Null),
        );
    }
    Value::Object(result)
}

/// `finance.financial_model`: créditos observados, projeção por rede e pagamento
/// para uma carteira. `hashrate_ths` ausente, zero ou inválido desliga a projeção.
pub fn financial_model(
    state: &Value,
    hashrate_ths: Option<f64>,
    now: f64,
    wallet: &str,
    settings: &Value,
) -> Value {
    let state = normalize_finance_state(state, now);
    let hashrate = hashrate_ths.filter(|x| x.is_finite() && *x > 0.0);
    let sources = sub(&state, "sources");
    let price_source = sub(sources, "price");
    let price_data = sub(sub(price_source, "data"), "pearl-2");
    let price = field(price_data, "brl").clone();
    let price_value = as_number(&price).filter(|_| truthy(&price));
    let errors = sub(&state, "errors");

    let mut result = Map::new();
    result.insert("price_brl".into(), price.clone());
    result.insert("price_usd".into(), field(price_data, "usd").clone());
    result.insert(
        "price_at".into(),
        get_or(
            price_data,
            "last_updated_at",
            field(price_source, "fetched_at"),
        )
        .clone(),
    );
    result.insert(
        "pool_at".into(),
        field(sub(sources, "balance"), "fetched_at").clone(),
    );
    result.insert("errors".into(), errors.clone());
    result.insert("periods".into(), json!({}));
    result.insert("forecast".into(), Value::Null);
    result.insert(
        "reward_history".into(),
        get_or(sub(sources, "rewards"), "data", &EMPTY_LIST).clone(),
    );
    let snapshots: Vec<(f64, f64)> = get_or(&state, "snapshots", &EMPTY_LIST)
        .as_array()
        .map(|items| {
            items
                .iter()
                .map(|point| {
                    (
                        as_number(field(point, "timestamp")).unwrap_or(f64::NAN),
                        as_number(field(point, "total_prl")).unwrap_or(f64::NAN),
                    )
                })
                .collect()
        })
        .unwrap_or_default();
    result.insert("snapshot_count".into(), json!(snapshots.len()));

    let (midnight, week) = day_boundaries(now);
    let mut periods = Map::new();
    for (period, boundary) in [
        ("day", midnight),
        ("week", week),
        ("month", now - 30.0 * 86400.0),
    ] {
        let before = snapshots.iter().rfind(|point| point.0 <= boundary);
        let after = snapshots.iter().find(|point| point.0 >= boundary);
        // Consulta nominal de 60 s: tolera um intervalo a mais na fronteira.
        let covered = before.is_some_and(|point| boundary - point.0 <= 120.0);
        let baseline = if covered { before } else { after };
        let end = snapshots.last();
        let amount = match (baseline, end) {
            (Some(start), Some(end)) if end.0 > start.0 => Some(end.1 - start.1),
            _ => None,
        };
        periods.insert(
            period.into(),
            json!({
                "prl": number(amount),
                "brl": number(amount.zip(price_value).map(|(a, p)| a * p)),
                "partial": !covered,
                "since": number(baseline.map(|point| point.0)),
            }),
        );
    }
    result.insert("periods".into(), Value::Object(periods));

    let balance = get_or(sub(sources, "balance"), "data", &EMPTY_OBJECT);
    let payouts = get_or(sub(sources, "payouts"), "data", &EMPTY_OBJECT);
    let total = field(balance, "total");
    let paid = field(payouts, "paid");
    result.insert("unpaid_prl".into(), total.clone());
    result.insert(
        "unconfirmed_prl".into(),
        field(balance, "unconfirmed").clone(),
    );
    result.insert("paid_prl".into(), paid.clone());
    let accumulated = match (as_number(total), as_number(paid)) {
        (Some(total), Some(paid)) if !total.is_nan() && !paid.is_nan() => {
            Some(total + paid).filter(|x| x.is_finite())
        }
        _ => None,
    };
    result.insert("accumulated_prl".into(), number(accumulated));
    result.insert(
        "accumulated_brl".into(),
        number(accumulated.zip(price_value).map(|(a, p)| a * p)),
    );

    let network_source = sub(sources, "network");
    let network = get_or(network_source, "data", &EMPTY_OBJECT);
    let mut contribution_settings = settings.as_object().cloned().unwrap_or_default();
    contribution_settings.insert("wallet".into(), json!(wallet));
    let contribution = support_configuration(&Value::Object(contribution_settings), &json!(wallet));
    result.insert(
        "support_contribution".into(),
        serde_json::to_value(&contribution).unwrap_or(Value::Null),
    );
    let current = [price_source, network_source]
        .iter()
        .all(|source| (-5.0..=180.0).contains(&(now - fetched_at(source))))
        && !["price", "network"]
            .iter()
            .any(|key| truthy(field(errors, key)));
    let pool_fee = numeric(get_or(network, "fee", &ZERO));
    let net_hashrate = field(network, "net_hashrate");
    let block_time = field(network, "block_time");
    let block_reward = field(network, "block_reward");
    let mut forecast = Value::Null;
    if let (true, Some(hashrate), Some(price), Some(fee)) =
        (current, hashrate, price_value, pool_fee)
        && truthy(net_hashrate)
        && truthy(block_time)
        && truthy(block_reward)
        && (0.0..=1.0).contains(&fee)
    {
        let (support_fraction, fraction) = if contribution.enabled {
            let fraction = contribution.percent as f64 / 100.0;
            (json!(fraction), fraction)
        } else {
            (json!(0), 0.0)
        };
        let prl_day = hashrate * 1e12 / as_number(net_hashrate).unwrap_or(f64::NAN)
            * as_number(block_reward).unwrap_or(f64::NAN)
            * 86400.0
            / as_number(block_time).unwrap_or(f64::NAN)
            * (1.0 - fee)
            * (1.0 - DEV_FEE)
            * (1.0 - fraction);
        let values = [
            prl_day,
            prl_day * price,
            prl_day * price * 7.0,
            prl_day * price * 30.0,
        ];
        if values.iter().all(|x| x.is_finite()) {
            forecast = json!({
                "prl_day": values[0],
                "brl_day": values[1],
                "brl_7days": values[2],
                "brl_30days": values[3],
                "hashrate_ths": hashrate,
                "pool_fee": fee,
                "dev_fee": DEV_FEE,
                "support_fraction": support_fraction,
                "support_assumption": "configured_mining_time",
                "network_at": field(network_source, "fetched_at").clone(),
            });
        }
    }
    let prl_day = as_number(field(&forecast, "prl_day"));
    result.insert(
        "forecast_unavailable_reason".into(),
        if forecast.is_null() {
            json!("Hashrate, preço ou rede ausentes, inválidos ou desatualizados")
        } else {
            Value::Null
        },
    );
    result.insert("forecast".into(), forecast.clone());
    result.insert("wallet".into(), json!(wallet));
    let payout = payout_model(
        &state,
        (!forecast.is_null()).then_some(&forecast),
        &price,
        now,
    );
    let payout = serde_json::to_value(&payout).unwrap_or(Value::Null);
    let bitcoin = get_or(sub(price_source, "data"), "bitcoin", &EMPTY_OBJECT);
    let price_btc_usd = field(bitcoin, "usd").clone();
    let price_btc_brl = field(bitcoin, "brl").clone();
    let btc_at = if truthy(bitcoin) {
        get_or(
            bitcoin,
            "last_updated_at",
            field(price_source, "fetched_at"),
        )
        .clone()
    } else {
        Value::Null
    };
    let stale = !truthy(&btc_at)
        || !as_number(&btc_at).is_some_and(|at| (-5.0..=180.0).contains(&(now - at)));
    let periods = &result["periods"];
    let amounts = [
        ("day", periods["day"]["prl"].clone()),
        ("week", periods["week"]["prl"].clone()),
        ("month", periods["month"]["prl"].clone()),
        ("accumulated", number(accumulated)),
        ("unpaid", total.clone()),
        ("confirmed", field(balance, "confirmed").clone()),
        ("pending", field(balance, "unconfirmed").clone()),
        ("paid", paid.clone()),
        ("forecast7", number(prl_day.map(|x| x * 7.0))),
        ("forecast30", number(prl_day.map(|x| x * 30.0))),
        ("threshold", field(&payout, "threshold_prl").clone()),
        ("remaining", field(&payout, "remaining_prl").clone()),
    ];
    let price_usd = result["price_usd"].clone();
    result.insert("payout".into(), payout);
    result.insert("price_btc_usd".into(), price_btc_usd.clone());
    result.insert("price_btc_brl".into(), price_btc_brl.clone());
    result.insert("btc_price_at".into(), btc_at);
    result.insert("btc_price_stale".into(), json!(stale));
    result.insert(
        "currencies".into(),
        currencies(
            &amounts,
            [&price, &price_usd, &price_btc_usd, &price_btc_brl],
        ),
    );
    Value::Object(result)
}

/// `rig_panel.fresh_hash`: hashrate recente (até 180 s) de uma GPU minerando.
pub fn fresh_hash(device: &Value, now: f64) -> Option<f64> {
    let miner = match field(device, "miner") {
        value if truthy(value) => value,
        _ => &EMPTY_OBJECT,
    };
    let stamp = numeric(field(miner, "hashrate_at"));
    let rate = numeric(field(miner, "hashrate"))?;
    (rate >= 0.0
        && truthy(field(device, "enabled"))
        && truthy(field(device, "miner_running"))
        && !truthy(field(miner, "paused"))
        && stamp.is_some_and(|stamp| (-5.0..=180.0).contains(&(now - stamp))))
    .then_some(rate)
}

fn wallet_text(value: &Value, default: &str) -> String {
    value
        .as_str()
        .map_or_else(|| default.to_owned(), str::to_owned)
}

/// `total(valores)` do `portfolio_model`: soma só se todos existirem.
fn total_of<'a>(values: impl IntoIterator<Item = &'a Value>) -> Option<Value> {
    let values: Vec<&Value> = values.into_iter().collect();
    (!values.is_empty() && values.iter().all(|value| !value.is_null()))
        .then(|| py_sum(values.iter().copied()))
}

fn times(value: &Value, price: &Value) -> Value {
    match (as_number(value), as_number(price)) {
        (Some(amount), Some(price)) if truthy(&json!(price)) => json!(amount * price),
        _ => Value::Null,
    }
}

/// `finance.portfolio_model`: soma carteiras únicas; ganho por GPU não é inferido
/// do saldo da pool.
///
/// `default_wallet` é o `config.WALLET`. Diferença consciente: o Python percorre as
/// carteiras na ordem de um `set` (aleatória por processo) ao montar
/// `forecast_missing_gpu_uuids` da visão consolidada; aqui a ordem é alfabética.
pub fn portfolio_model(
    state: &Value,
    devices: &[Value],
    now: f64,
    selected_wallet: Option<&str>,
    settings: &Value,
    default_wallet: &str,
) -> Value {
    let wallet_states: Vec<(String, Value)> = match (
        py_eq(field(state, "schema_version"), &json!(2)),
        field(state, "wallets"),
    ) {
        (true, Value::Object(map)) => map
            .iter()
            .map(|(key, value)| (key.clone(), value.clone()))
            .collect(),
        _ => vec![(
            wallet_text(
                get_or(state, "wallet", &json!(default_wallet)),
                default_wallet,
            ),
            state.clone(),
        )],
    };
    let settings_wallet = get_or(settings, "wallet", &json!(default_wallet)).clone();
    let device_wallet = |device: &Value| {
        let own = field(device, "wallet");
        let chosen = if truthy(own) { own } else { &settings_wallet };
        wallet_text(chosen, default_wallet)
    };
    let mut wallets: BTreeSet<String> = devices.iter().map(device_wallet).collect();
    if wallets.is_empty() {
        wallets.insert(wallet_text(
            get_or(state, "primary_wallet", &json!(default_wallet)),
            default_wallet,
        ));
    }
    let mut hashes: BTreeMap<String, f64> =
        wallets.iter().map(|wallet| (wallet.clone(), 0.0)).collect();
    let mut missing: BTreeMap<String, Vec<Value>> = wallets
        .iter()
        .map(|wallet| (wallet.clone(), Vec::new()))
        .collect();
    for device in devices {
        let miner = match field(device, "miner") {
            value if truthy(value) => value,
            _ => &EMPTY_OBJECT,
        };
        let wallet = device_wallet(device);
        let enabled = get_or(device, "enabled", &Value::Bool(true)).clone();
        let mut with_enabled = device.as_object().cloned().unwrap_or_default();
        with_enabled.insert("enabled".into(), enabled.clone());
        if let Some(rate) = fresh_hash(&Value::Object(with_enabled), now) {
            *hashes.entry(wallet).or_insert(0.0) += rate;
        } else if truthy(&enabled)
            && truthy(field(device, "miner_running"))
            && !truthy(field(miner, "paused"))
        {
            missing
                .entry(wallet)
                .or_default()
                .push(field(device, "uuid").clone());
        }
    }
    let wallet_states: Vec<(String, Value)> = wallet_states
        .into_iter()
        .map(|(wallet, cached)| {
            let normalized = normalize_finance_state(&cached, now);
            (wallet, normalized)
        })
        .collect();
    let mut common: Map<String, Value> = Map::new();
    for (_, cached) in &wallet_states {
        for key in ["price", "network"] {
            let value = field(sub(cached, "sources"), key);
            if truthy(value)
                && fetched_at(value) > fetched_at(common.get(key).unwrap_or(&EMPTY_OBJECT))
            {
                common.insert(key.into(), value.clone());
            }
        }
    }
    let mut models = Map::new();
    for wallet in &wallets {
        let mut cached = wallet_states
            .iter()
            .rev()
            .find(|(key, _)| key == wallet)
            .map(|(_, value)| value.clone())
            .unwrap_or_else(|| json!({"wallet": wallet, "sources": {}, "snapshots": []}));
        for (key, value) in &common {
            let current = fetched_at(get_or(sub(&cached, "sources"), key, &EMPTY_OBJECT));
            if fetched_at(value) > current
                && let Value::Object(map) = &mut cached
                && let Value::Object(sources) = map
                    .entry("sources")
                    .or_insert_with(|| Value::Object(Map::new()))
            {
                sources.insert(key.clone(), value.clone());
            }
        }
        let gaps = &missing[wallet];
        let rate = if gaps.is_empty() {
            Some(hashes[wallet]).filter(|x| *x != 0.0)
        } else {
            None
        };
        let mut model = financial_model(&cached, rate, now, wallet, settings);
        if let Value::Object(map) = &mut model {
            map.insert(
                "forecast_missing_gpu_uuids".into(),
                Value::Array(gaps.clone()),
            );
        }
        models.insert(wallet.clone(), model);
    }
    let hashrate_by_wallet: Map<String, Value> = hashes
        .iter()
        .map(|(wallet, rate)| (wallet.clone(), json!(rate)))
        .collect();

    let selected = selected_wallet.filter(|wallet| models.contains_key(*wallet));
    if selected.is_some() || models.len() == 1 {
        let key = selected
            .map(str::to_owned)
            .unwrap_or_else(|| models.keys().next().cloned().unwrap_or_default());
        let mut result = models[&key].as_object().cloned().unwrap_or_default();
        result.insert("scope".into(), json!("wallet"));
        result.insert("selected_wallet".into(), json!(key));
        result.insert("wallet_count".into(), json!(models.len()));
        result.insert("wallet_models".into(), Value::Object(models.clone()));
        result.insert(
            "hashrate_by_wallet".into(),
            Value::Object(hashrate_by_wallet),
        );
        result.insert("forecast_partial".into(), json!(!missing[&key].is_empty()));
        result.insert(
            "earnings_attribution".into(),
            json!("wallet_balance_not_individual_gpu"),
        );
        return Value::Object(result);
    }

    let first = models.values().next().cloned().unwrap_or(Value::Null);
    let mut result = Map::new();
    for key in [
        "price_brl",
        "price_usd",
        "price_at",
        "price_btc_usd",
        "price_btc_brl",
        "btc_price_at",
        "btc_price_stale",
    ] {
        result.insert(key.into(), field(&first, key).clone());
    }
    let price_brl = result["price_brl"].clone();
    let price_usd = result["price_usd"].clone();
    let price_btc_usd = result["price_btc_usd"].clone();
    let price_btc_brl = result["price_btc_brl"].clone();
    result.insert("scope".into(), json!("portfolio"));
    result.insert("wallet".into(), Value::Null);
    result.insert("selected_wallet".into(), Value::Null);
    result.insert("wallet_count".into(), json!(models.len()));
    result.insert("wallet_models".into(), Value::Object(models.clone()));
    result.insert(
        "hashrate_by_wallet".into(),
        Value::Object(hashrate_by_wallet),
    );
    result.insert(
        "forecast_partial".into(),
        json!(missing.values().any(|items| !items.is_empty())),
    );
    result.insert(
        "forecast_missing_gpu_uuids".into(),
        Value::Array(missing.values().flatten().cloned().collect()),
    );
    result.insert("earnings_attribution".into(), json!("unique_wallets"));
    result.insert("periods".into(), json!({}));
    result.insert("errors".into(), json!({}));
    result.insert("forecast".into(), Value::Null);
    let pool_at: Vec<Value> = models
        .values()
        .map(|model| {
            let value = field(model, "pool_at");
            if truthy(value) {
                value.clone()
            } else {
                json!(0)
            }
        })
        .collect();
    let pool_at = py_min(&pool_at).cloned().unwrap_or(json!(0));
    result.insert(
        "pool_at".into(),
        if truthy(&pool_at) {
            pool_at
        } else {
            Value::Null
        },
    );
    let counts: Vec<Value> = models
        .values()
        .map(|model| get_or(model, "snapshot_count", &ZERO).clone())
        .collect();
    result.insert("snapshot_count".into(), py_sum(&counts));
    let mut errors = Map::new();
    for (wallet, model) in &models {
        if let Value::Object(items) = sub(model, "errors") {
            for (key, error) in items {
                errors.insert(format!("{wallet}:{key}"), error.clone());
            }
        }
    }
    result.insert("errors".into(), Value::Object(errors));
    let mut periods = Map::new();
    for period in ["day", "week", "month"] {
        let rows: Vec<&Value> = models
            .values()
            .map(|model| field(sub(model, "periods"), period))
            .collect();
        let amount = total_of(rows.iter().map(|row| field(row, "prl")));
        let since: Vec<&Value> = rows
            .iter()
            .map(|row| field(row, "since"))
            .filter(|value| !value.is_null())
            .collect();
        periods.insert(
            period.into(),
            json!({
                "prl": amount.clone().unwrap_or(Value::Null),
                "brl": amount.as_ref().map_or(Value::Null, |amount| times(amount, &price_brl)),
                "partial": rows.iter().any(|row| truthy(field(row, "partial"))) || amount.is_none(),
                "since": py_min(since).cloned().unwrap_or(Value::Null),
            }),
        );
    }
    result.insert("periods".into(), Value::Object(periods));
    for key in [
        "unpaid_prl",
        "unconfirmed_prl",
        "paid_prl",
        "accumulated_prl",
    ] {
        let value = total_of(models.values().map(|model| field(model, key)));
        result.insert(key.into(), value.unwrap_or(Value::Null));
    }
    let accumulated_brl = times(&result["accumulated_prl"], &price_brl);
    result.insert("accumulated_brl".into(), accumulated_brl);
    let active: Vec<&Value> = models
        .iter()
        .filter(|(wallet, _)| hashes.get(*wallet).is_some_and(|rate| *rate > 0.0))
        .map(|(_, model)| field(model, "forecast"))
        .collect();
    if !active.is_empty() && active.iter().all(|forecast| truthy(forecast)) {
        let mut forecast = Map::new();
        for key in [
            "prl_day",
            "brl_day",
            "brl_7days",
            "brl_30days",
            "hashrate_ths",
        ] {
            forecast.insert(
                key.into(),
                py_sum(active.iter().map(|item| get_or(item, key, &ZERO))),
            );
        }
        forecast.insert("scope".into(), json!("unique_wallet_groups"));
        forecast.insert("support_fraction".into(), Value::Null);
        result.insert("forecast".into(), Value::Object(forecast));
    }
    let confirmed = total_of(
        models
            .values()
            .map(|model| field(sub(model, "payout"), "confirmed_prl")),
    )
    .unwrap_or(Value::Null);
    result.insert(
        "payout".into(),
        json!({
            "state": "multiple_wallets",
            "state_label": format!("{} carteiras · selecione uma para ver o pagamento", models.len()),
            "eta_label": "Estimativa individual por carteira",
            "threshold_prl": null,
            "remaining_prl": null,
            "confirmed_prl": confirmed.clone(),
            "unconfirmed_prl": result["unconfirmed_prl"].clone(),
            "total_prl": result["unpaid_prl"].clone(),
            "progress_pct": null,
            "confirmed_progress_pct": null,
            "eta_details": "Não existe um único mínimo ou prazo para carteiras diferentes.",
        }),
    );
    let prl_day = as_number(field(&result["forecast"], "prl_day"));
    let periods = &result["periods"];
    let amounts = [
        ("day", periods["day"]["prl"].clone()),
        ("week", periods["week"]["prl"].clone()),
        ("month", periods["month"]["prl"].clone()),
        ("accumulated", result["accumulated_prl"].clone()),
        ("unpaid", result["unpaid_prl"].clone()),
        ("confirmed", confirmed),
        ("pending", result["unconfirmed_prl"].clone()),
        ("paid", result["paid_prl"].clone()),
        ("forecast7", number(prl_day.map(|x| x * 7.0))),
        ("forecast30", number(prl_day.map(|x| x * 30.0))),
        ("threshold", Value::Null),
        ("remaining", Value::Null),
    ];
    result.insert(
        "currencies".into(),
        currencies(
            &amounts,
            [&price_brl, &price_usd, &price_btc_usd, &price_btc_brl],
        ),
    );
    result.insert(
        "support_contribution".into(),
        json!({
            "enabled": models
                .values()
                .any(|model| truthy(field(sub(model, "support_contribution"), "enabled"))),
            "scope": "per_wallet",
        }),
    );
    Value::Object(result)
}

/// Um instante do `portfolio_snapshots`: valor numérico, carimbo original e
/// `{carteira: total}` na ordem de inserção.
type StampGroup = (f64, Value, Vec<(String, Value)>);

/// `finance.portfolio_snapshots`: totais "até o instante" só depois que cada
/// carteira selecionada tem a primeira amostra.
pub fn portfolio_snapshots(state: &Value, wallets: Option<&[String]>) -> Value {
    if !py_eq(field(state, "schema_version"), &json!(2)) {
        return get_or(state, "snapshots", &EMPTY_LIST).clone();
    }
    let states = sub(state, "wallets");
    let mut identifiers: Vec<String> = Vec::new();
    let source: Vec<String> = match wallets {
        Some(items) if !items.is_empty() => items.to_vec(),
        _ => states
            .as_object()
            .map(|map| map.keys().cloned().collect())
            .unwrap_or_default(),
    };
    for wallet in source {
        if !identifiers.contains(&wallet) {
            identifiers.push(wallet);
        }
    }
    match identifiers.len() {
        0 => return json!([]),
        1 => {
            return get_or(sub(states, &identifiers[0]), "snapshots", &EMPTY_LIST).clone();
        }
        _ => {}
    }
    // grouped[stamp][wallet] = total, preservando a ordem de inserção do Python.
    let mut grouped: Vec<StampGroup> = Vec::new();
    let mut positions: BTreeMap<u64, usize> = BTreeMap::new();
    for wallet in &identifiers {
        let snapshots = get_or(sub(states, wallet), "snapshots", &EMPTY_LIST);
        let Some(points) = snapshots.as_array().filter(|items| !items.is_empty()) else {
            return json!([]);
        };
        for point in points {
            let stamp = field(point, "timestamp");
            let key_value = as_number(stamp).unwrap_or(f64::NAN);
            let key = (key_value + 0.0).to_bits();
            let position = *positions.entry(key).or_insert_with(|| {
                grouped.push((key_value, stamp.clone(), Vec::new()));
                grouped.len() - 1
            });
            let values = &mut grouped[position].2;
            let total = field(point, "total_prl").clone();
            match values.iter_mut().find(|(name, _)| name == wallet) {
                Some(entry) => entry.1 = total,
                None => values.push((wallet.clone(), total)),
            }
        }
    }
    grouped.sort_by(|a, b| a.0.total_cmp(&b.0));
    let mut seen: Vec<(String, Value)> = Vec::new();
    let mut result = Vec::new();
    for (_, stamp, values) in grouped {
        for (wallet, total) in values {
            match seen.iter_mut().find(|(name, _)| *name == wallet) {
                Some(entry) => entry.1 = total,
                None => seen.push((wallet, total)),
            }
        }
        if seen.len() == identifiers.len() {
            result.push(json!({
                "timestamp": stamp,
                "total_prl": py_sum(seen.iter().map(|(_, total)| total)),
            }));
        }
    }
    Value::Array(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn py_sum_matches_python_312_compensated_sum() {
        // Python 3.12+: sum([0.1, 0.2, 0.3]) == 0.6 (a soma ingênua dá 0.6000000000000001).
        let values = [json!(0.1), json!(0.2), json!(0.3)];
        assert_eq!(py_sum(&values), json!(0.6));
        assert_eq!(py_sum(&[json!(1), json!(2)]), json!(3));
        assert_eq!(py_sum(&[json!(1), json!(0.5)]), json!(1.5));
        assert_eq!(py_sum(std::iter::empty()), json!(0));
        let big = [json!(1e100), json!(1.0), json!(-1e100)];
        assert_eq!(py_sum(&big), json!(1.0));
    }

    #[test]
    fn local_day_and_week_boundaries() {
        // 2026-11-08 21:00 em São Paulo (domingo) = 2026-11-09 00:00 UTC.
        let now = 1_794_182_400.0;
        let (midnight, week) = day_boundaries(now);
        assert_eq!(midnight, 1_794_106_800.0); // 2026-11-08 00:00 −03
        assert_eq!(week, midnight - 6.0 * 86400.0); // segunda-feira 02/11
    }
}
