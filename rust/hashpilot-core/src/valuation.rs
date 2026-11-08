//! Equivalentes monetários de PRL — porte de `valuation.py`.
//! Não representam pagamentos em USD ou BTC: o pagamento continua em PRL.

use serde::Serialize;
use serde_json::Value;

use crate::numbers::{finite, valuation_numeric};

#[derive(Clone, Copy, Debug, PartialEq, Serialize)]
pub struct CurrencyValues {
    pub prl: Option<f64>,
    pub brl: Option<f64>,
    pub usd: Option<f64>,
    pub btc: Option<f64>,
}

/// `valuation.value_in_currencies`: preços ausentes, zero ou negativos deixam o
/// equivalente ausente (nunca zero inventado).
pub fn value_in_currencies(
    prl: &Value,
    price_brl: &Value,
    price_usd: &Value,
    bitcoin_usd: &Value,
    bitcoin_brl: &Value,
) -> CurrencyValues {
    let amount = valuation_numeric(prl, false);
    let brl = valuation_numeric(price_brl, true);
    let usd = valuation_numeric(price_usd, true);
    let btc_usd = valuation_numeric(bitcoin_usd, true);
    let btc_brl = valuation_numeric(bitcoin_brl, true);
    let Some(amount) = amount else {
        return CurrencyValues {
            prl: None,
            brl: None,
            usd: None,
            btc: None,
        };
    };
    let btc = match (usd, btc_usd, brl, btc_brl) {
        (Some(usd), Some(btc_usd), _, _) => Some(amount * usd / btc_usd),
        (_, _, Some(brl), Some(btc_brl)) => Some(amount * brl / btc_brl),
        _ => None,
    };
    CurrencyValues {
        prl: finite(amount),
        brl: brl.and_then(|price| finite(amount * price)),
        usd: usd.and_then(|price| finite(amount * price)),
        btc: btc.and_then(finite),
    }
}
