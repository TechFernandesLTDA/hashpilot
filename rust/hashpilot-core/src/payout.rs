//! Previsão de pagamento da pool — porte de `payout.py`.
//! Estimativa explícita, nunca um cronômetro do servidor.

use serde::Serialize;
use serde_json::{Map, Value};

use crate::numbers::{non_negative, py_float};
use crate::text::py_str;

/// Regra pública da Kryptex para Pearl: pagamentos uma vez por hora.
pub const PAYOUT_CYCLE_SECONDS: f64 = 3600.0;

static EMPTY: std::sync::LazyLock<Map<String, Value>> = std::sync::LazyLock::new(Map::new);
const NULL: Value = Value::Null;

/// `dict.get(chave, {})` tolerante: ausente ou não-objeto vira objeto vazio.
fn object<'a>(value: &'a Value, key: &str) -> &'a Map<String, Value> {
    value.get(key).and_then(Value::as_object).unwrap_or(&EMPTY)
}

fn field<'a>(map: &'a Map<String, Value>, key: &str) -> &'a Value {
    map.get(key).unwrap_or(&NULL)
}

/// Veracidade de um valor JSON como no Python (`bool(valor)`).
fn truthy(value: &Value) -> bool {
    match value {
        Value::Null => false,
        Value::Bool(flag) => *flag,
        Value::Number(number) => number.as_f64().is_some_and(|value| value != 0.0),
        Value::String(text) => !text.is_empty(),
        Value::Array(items) => !items.is_empty(),
        Value::Object(map) => !map.is_empty(),
    }
}

/// `payout.duration`: texto curto em português ("2 h 5 min", "mais de 1 ano").
pub fn duration(seconds: Option<f64>) -> String {
    let Some(value) = seconds.filter(|value| value.is_finite() && *value >= 0.0) else {
        return "não disponível".into();
    };
    let minutes = (value / 60.0).ceil().max(0.0);
    if minutes >= 1440.0 * 366.0 {
        return "mais de 1 ano".into();
    }
    let minutes = minutes as i64;
    let (days, remainder) = (minutes / 1440, minutes % 1440);
    let (hours, minutes) = (remainder / 60, remainder % 60);
    if days > 365 {
        return "mais de 1 ano".into();
    }
    let mut parts = Vec::new();
    if days > 0 {
        parts.push(format!("{days} d"));
    }
    if hours > 0 {
        parts.push(format!("{hours} h"));
    }
    if minutes > 0 || parts.is_empty() {
        parts.push(format!("{minutes} min"));
    }
    parts.join(" ")
}

/// `duration` sobre um valor JSON (mesma validação de `payout.number`).
pub fn duration_value(value: &Value) -> String {
    duration(non_negative(value))
}

/// `payout.observed_rate`: PRL/dia pela variação do total na última hora (mín. 30 min).
pub fn observed_rate(state: &Value, now: f64) -> Option<(f64, f64)> {
    let mut points: Vec<(f64, f64)> = Vec::new();
    for point in state
        .get("snapshots")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        let Some(point) = point.as_object() else {
            continue;
        };
        let (Some(stamp), Some(total)) = (
            non_negative(field(point, "timestamp")),
            non_negative(field(point, "total_prl")),
        ) else {
            continue;
        };
        if now - 3600.0 <= stamp && stamp <= now + 5.0 {
            match points.iter_mut().find(|(existing, _)| *existing == stamp) {
                Some(existing) => existing.1 = total,
                None => points.push((stamp, total)),
            }
        }
    }
    points.sort_by(|a, b| a.0.total_cmp(&b.0));
    let (first, last) = (points.first()?, points.last()?);
    if points.len() < 2 {
        return None;
    }
    let span = last.0 - first.0;
    let change = last.1 - first.1;
    if span < 1800.0 || change <= 0.0 {
        return None;
    }
    Some((change / span * 86400.0, span))
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct LastPayment {
    pub date: f64,
    pub timestamp: f64,
    pub amount_prl: Option<f64>,
    pub amount: Option<f64>,
    pub txid: String,
    pub status: String,
}

/// Mesmos campos do dicionário de `payout.payout_model`.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct PayoutModel {
    pub threshold_prl: Option<f64>,
    pub threshold_brl: Option<f64>,
    pub threshold_source: String,
    pub total_prl: Option<f64>,
    pub confirmed_prl: Option<f64>,
    pub unconfirmed_prl: Option<f64>,
    pub remaining_prl: Option<f64>,
    pub remaining_confirmed_prl: Option<f64>,
    pub remaining_brl: Option<f64>,
    pub progress_pct: Option<f64>,
    pub confirmed_progress_pct: Option<f64>,
    pub rate_prl_day: Option<f64>,
    pub rate_label: String,
    pub maturation_seconds: Option<f64>,
    pub maturation_label: String,
    pub maturation_blocks: Option<f64>,
    pub payout_cycle_seconds: f64,
    pub last_payment: Option<LastPayment>,
    pub source_at: Option<f64>,
    pub stale: bool,
    pub eta_min_seconds: Option<f64>,
    pub eta_max_seconds: Option<f64>,
    pub generation_seconds: Option<f64>,
    pub state_label: String,
    pub eta_label: String,
    pub eta_details: String,
}

fn non_zero(value: Option<f64>) -> Option<f64> {
    value.filter(|number| *number != 0.0)
}

/// `payout.payout_model`: progresso até o mínimo confirmado e janela estimada de envio.
pub fn payout_model(
    state: &Value,
    forecast: Option<&Value>,
    price_brl: &Value,
    now: f64,
) -> PayoutModel {
    let sources = state.get("sources").unwrap_or(&NULL);
    let balance_source = sources.get("balance").unwrap_or(&NULL);
    let balance = object(balance_source, "data");
    let network = object(sources.get("network").unwrap_or(&NULL), "data");

    let mut threshold = non_negative(field(balance, "threshold"));
    let mut threshold_source = "carteira";
    if non_zero(threshold).is_none() {
        threshold = non_zero(non_negative(field(network, "minpay")));
        threshold_source = "mínimo geral da pool";
    }
    let total = non_negative(field(balance, "total"));
    let confirmed = non_negative(field(balance, "confirmed"));
    let pending = non_negative(field(balance, "unconfirmed"));
    let price = non_negative(price_brl);

    let observed = observed_rate(state, now);
    let mut rate = observed.map(|(rate, _)| rate);
    let mut rate_label = match observed {
        Some((rate, span)) if rate != 0.0 => {
            format!("Ritmo observado na pool nos últimos {:.0} min", span / 60.0)
        }
        _ => "Ritmo ainda não estimável".to_string(),
    };
    if let Some(forecast) = forecast
        .and_then(Value::as_object)
        .filter(|map| !map.is_empty())
    {
        if rate.is_none() {
            rate = non_zero(non_negative(field(forecast, "prl_day")));
            if rate.is_some() {
                rate_label = "Estimativa pela rede e pelo hashrate atual".into();
            }
        }
    }

    let mut maturation = non_negative(field(balance, "maturation_time"));
    if maturation.is_none() {
        if let (Some(blocks), Some(block_time)) = (
            non_negative(field(network, "blocks_for_maturation")),
            non_negative(field(network, "block_time")),
        ) {
            maturation = Some(blocks * block_time);
        }
    }
    let source_at = non_negative(balance_source.get("fetched_at").unwrap_or(&NULL));
    let balance_error = truthy(field(object(state, "errors"), "balance"));
    let stale = source_at.is_none_or(|at| !(-5.0..=180.0).contains(&(now - at))) || balance_error;
    let missing = threshold
        .zip(total)
        .map(|(threshold, total)| (threshold - total).max(0.0));
    let missing_confirmed = threshold
        .zip(confirmed)
        .map(|(threshold, confirmed)| (threshold - confirmed).max(0.0));

    let history = object(sources.get("payout_history").unwrap_or(&NULL), "data");
    let mut last: Option<(&Map<String, Value>, f64)> = None;
    for payment in field(history, "results").as_array().into_iter().flatten() {
        let Some(payment) = payment.as_object() else {
            continue;
        };
        let Some(date) = non_negative(field(payment, "date")).filter(|date| *date <= now + 5.0)
        else {
            continue;
        };
        let date = py_float(field(payment, "date")).unwrap_or(date);
        // `max` do Python devolve o primeiro máximo: só substitui quando estritamente maior.
        if last.is_none_or(|(_, best)| date > best) {
            last = Some((payment, date));
        }
    }
    let last_payment = last.map(|(payment, date)| LastPayment {
        date,
        timestamp: date,
        amount_prl: non_negative(field(payment, "received")),
        amount: non_negative(field(payment, "received")),
        txid: payment.get("txid").map_or_else(String::new, py_str),
        status: payment.get("status").map_or_else(String::new, py_str),
    });

    let positive_threshold = non_zero(threshold);
    let mut output = PayoutModel {
        threshold_prl: threshold,
        threshold_brl: threshold.zip(price).map(|(threshold, price)| threshold * price),
        threshold_source: threshold_source.into(),
        total_prl: total,
        confirmed_prl: confirmed,
        unconfirmed_prl: pending,
        remaining_prl: missing,
        remaining_confirmed_prl: missing_confirmed,
        remaining_brl: missing.zip(price).map(|(missing, price)| missing * price),
        progress_pct: total.zip(positive_threshold).map(|(total, threshold)| (total / threshold * 100.0).min(100.0)),
        confirmed_progress_pct: confirmed
            .zip(positive_threshold)
            .map(|(confirmed, threshold)| (confirmed / threshold * 100.0).min(100.0)),
        rate_prl_day: rate,
        rate_label,
        maturation_seconds: maturation,
        maturation_label: duration(maturation),
        maturation_blocks: non_negative(field(network, "blocks_for_maturation")),
        payout_cycle_seconds: PAYOUT_CYCLE_SECONDS,
        last_payment,
        source_at,
        stale,
        eta_min_seconds: None,
        eta_max_seconds: None,
        generation_seconds: None,
        state_label: "Consultando saldo da pool".into(),
        eta_label: "Aguardando dados da pool".into(),
        eta_details: "A pool paga automaticamente após atingir o mínimo confirmado. Depois do envio, a carteira depende da confirmação e sincronização da rede.".into(),
    };
    if stale {
        output.state_label = "Saldo ainda não atualizado".into();
        output.eta_label = "Estimativa indisponível: aguarde a atualização da pool".into();
        return output;
    }
    let (Some(threshold), Some(total), Some(confirmed)) = (positive_threshold, total, confirmed)
    else {
        return output;
    };
    if confirmed >= threshold - 1e-9 {
        output.state_label = "Mínimo confirmado atingido; aguardando envio".into();
        output.eta_min_seconds = Some(0.0);
        output.eta_max_seconds = Some(PAYOUT_CYCLE_SECONDS);
        output.eta_label = "Próximo ciclo automático: aproximadamente até 1 h".into();
        output.eta_details = "O mínimo já está confirmado. A pool processa pagamentos uma vez por hora, sem publicar o instante exato da próxima rodada. Após o envio, aguarde confirmação/sincronização da carteira.".into();
    } else if total >= threshold - 1e-9 {
        output.state_label = "Mínimo gerado; aguardando confirmação".into();
        output.eta_min_seconds = Some(0.0);
        output.eta_max_seconds = maturation.map(|maturation| maturation + PAYOUT_CYCLE_SECONDS);
        output.eta_label = match maturation {
            Some(maturation) => {
                format!(
                    "Janela estimada: agora até cerca de {}",
                    duration(Some(maturation + PAYOUT_CYCLE_SECONDS))
                )
            }
            None => "Aguardando confirmação; prazo exato não disponível".into(),
        };
        output.eta_details = "O total inclui créditos pendentes e ainda não habilita o envio. A maturação indicada pela pool é uma duração típica, não o tempo restante individual de cada crédito. A janela pode variar; após confirmar, há o próximo ciclo de pagamento e a confirmação da transação.".into();
    } else if let Some(rate) = non_zero(rate) {
        let missing = missing.unwrap_or(0.0);
        let generation = missing / rate * 86400.0;
        let maturation_delay = maturation.unwrap_or(0.0);
        let earliest = generation + maturation_delay;
        output.state_label = "Acumulando até o mínimo de pagamento".into();
        output.generation_seconds = Some(generation);
        output.eta_min_seconds = Some(earliest);
        output.eta_max_seconds = Some(earliest + PAYOUT_CYCLE_SECONDS);
        output.eta_label = format!(
            "Estimativa de envio: {} a {}",
            duration(Some(earliest)),
            duration(Some(earliest + PAYOUT_CYCLE_SECONDS))
        );
        let maturation_text = if maturation_delay != 0.0 {
            format!(
                "Foi incluída uma maturação típica de {}, ",
                duration(Some(maturation_delay))
            )
        } else {
            String::new()
        };
        output.eta_details = format!(
            "Faltam cerca de {} para gerar o mínimo, se o ritmo continuar. {maturation_text}mais até aproximadamente 1 h de espera pelo ciclo. Não é um cronômetro da pool; dificuldade, créditos pendentes e confirmação/sincronização da carteira podem mudar o prazo.",
            duration(Some(generation))
        );
        if maturation.is_none() {
            output.eta_min_seconds = None;
            output.eta_max_seconds = None;
            output.eta_label = format!(
                "Para gerar o mínimo: cerca de {}; confirmação sem prazo disponível",
                duration(Some(generation))
            );
            output.eta_details = "O prazo de geração usa o ritmo atual. Sem uma duração de maturação informada pela pool/rede, ainda não é possível estimar o envio completo. Após confirmar, há o ciclo horário e a confirmação/sincronização da carteira.".into();
        }
    } else {
        output.state_label = "Acumulando até o mínimo de pagamento".into();
        output.eta_label = "Sem ritmo suficiente para estimar o prazo".into();
        output.eta_details = "Ainda faltam créditos para o mínimo. A estimativa aparece quando houver ritmo observado suficiente ou hashrate/rede atuais. A confirmação dos créditos e o ciclo de pagamento são etapas adicionais.".into();
    }
    output
}
