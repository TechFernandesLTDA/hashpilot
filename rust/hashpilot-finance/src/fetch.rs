//! Consultas públicas da Kryptex e da CoinGecko, como o `finance.fetch_json`:
//! GET com corpo limitado a 2 MB (resposta maior é erro explícito, nunca truncada),
//! `User-Agent` do HashPilot, tempo-limite de 9 s e JSON com BOM tolerado.
//!
//! Nenhuma consulta usa conta ou credencial: só endereços públicos da carteira.

use std::collections::HashMap;
use std::io::Read;
use std::sync::{Arc, LazyLock, Mutex};
use std::time::{Duration, Instant};

use serde_json::Value;

pub const BASE: &str = "https://pool.kryptex.com/prl/api/v1/";
pub const PRICE_URL: &str = "https://api.coingecko.com/api/v3/simple/price?ids=pearl-2,bitcoin&vs_currencies=brl,usd&include_last_updated_at=true";
pub const MAX_RESPONSE_BYTES: usize = 2_000_000;
pub const TIMEOUT: Duration = Duration::from_secs(9);

/// `app_version.VERSION`, lido do mesmo arquivo do Python na compilação.
pub static APP_VERSION: LazyLock<String> = LazyLock::new(|| {
    include_str!("../../../app_version.py")
        .lines()
        .find_map(|line| line.strip_prefix("VERSION = "))
        .map(|value| value.trim().trim_matches(['\'', '"']).to_owned())
        .unwrap_or_else(|| "v0.0.0".into())
});

/// `finance.USER_AGENT`.
pub static USER_AGENT: LazyLock<String> = LazyLock::new(|| {
    format!(
        "HashPilot/{} (Tech Fernandes; local read-only dashboard)",
        APP_VERSION.trim_start_matches('v')
    )
});

/// Uma consulta: URL → JSON, ou o texto do erro (`str(error)` no Python).
pub type Fetcher = Arc<dyn Fn(&str) -> Result<Value, String> + Send + Sync>;

/// `finance.URLS["network"]`.
pub fn network_url() -> String {
    format!("{BASE}pool/info")
}

/// `finance.urls_for_wallet`, na ordem do dicionário do Python: cotação e rede
/// primeiro, depois as consultas da carteira. A ordem vale para `sources` e `errors`.
pub fn urls_for_wallet(wallet: &str) -> Vec<(&'static str, String)> {
    let mut urls = vec![("price", PRICE_URL.to_owned()), ("network", network_url())];
    if !wallet.is_empty() {
        urls.push(("balance", format!("{BASE}miner/balance/{wallet}")));
        urls.push(("payouts", format!("{BASE}miner/payouts/{wallet}/stats")));
        urls.push(("rewards", format!("{BASE}miner/reward-chart/{wallet}")));
        urls.push(("payout_history", format!("{BASE}miner/payouts/{wallet}")));
    }
    urls
}

fn host(url: &str) -> &str {
    let rest = url.split_once("://").map_or(url, |(_, rest)| rest);
    rest.split(['/', '?', '#']).next().unwrap_or(rest)
}

/// Texto de um `HTTPError` do `urllib`: `HTTP Error 503: Service Unavailable`.
fn http_error(status: ureq::http::StatusCode) -> String {
    format!(
        "HTTP Error {}: {}",
        status.as_u16(),
        status.canonical_reason().unwrap_or("")
    )
}

/// Decodifica o corpo como o `json.loads(body.decode("utf-8-sig"))`.
pub fn decode_body(url: &str, body: &[u8]) -> Result<Value, String> {
    if body.len() > MAX_RESPONSE_BYTES {
        return Err(format!(
            "Resposta de {} excede {MAX_RESPONSE_BYTES} bytes",
            host(url)
        ));
    }
    let body = body.strip_prefix(b"\xef\xbb\xbf").unwrap_or(body);
    let text = std::str::from_utf8(body).map_err(|error| error.to_string())?;
    serde_json::from_str(text).map_err(|error| error.to_string())
}

fn agent() -> &'static ureq::Agent {
    static AGENT: LazyLock<ureq::Agent> = LazyLock::new(|| {
        ureq::Agent::config_builder()
            .timeout_global(Some(TIMEOUT))
            .http_status_as_error(false)
            .user_agent(USER_AGENT.as_str())
            .build()
            .into()
    });
    &AGENT
}

/// `finance.fetch_json`: lê no máximo 2 MB + 1 byte e recusa o excesso.
pub fn fetch_json(url: &str) -> Result<Value, String> {
    let response = agent()
        .get(url)
        .header("Accept", "application/json")
        .call()
        .map_err(|error| error.to_string())?;
    let status = response.status();
    if !status.is_success() {
        return Err(http_error(status));
    }
    let mut body = Vec::new();
    response
        .into_body()
        .into_reader()
        .take(MAX_RESPONSE_BYTES as u64 + 1)
        .read_to_end(&mut body)
        .map_err(|error| error.to_string())?;
    decode_body(url, &body)
}

/// O consultor real.
pub fn network_fetcher() -> Fetcher {
    Arc::new(fetch_json)
}

/// `finance.SharedMarketFetcher`: só cotação e rede (públicas) são reaproveitadas por
/// 45 s entre carteiras; saldos de carteira nunca passam pelo cache.
pub fn shared_market_fetcher(fetcher: Fetcher, ttl: Duration) -> Fetcher {
    let shared = [PRICE_URL.to_owned(), network_url()];
    let cache: Mutex<HashMap<String, (Instant, Value)>> = Mutex::new(HashMap::new());
    Arc::new(move |url: &str| {
        if !shared.iter().any(|item| item == url) {
            return fetcher(url);
        }
        // Como o Python, a trava fica com a consulta: duas carteiras não repetem a mesma.
        let mut cache = cache.lock().unwrap_or_else(|poison| poison.into_inner());
        if let Some((stamp, value)) = cache.get(url)
            && stamp.elapsed() < ttl
        {
            return Ok(value.clone());
        }
        let value = fetcher(url)?;
        cache.insert(url.to_owned(), (Instant::now(), value.clone()));
        Ok(value)
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    #[test]
    fn urls_match_python_order_and_empty_wallet() {
        let keys: Vec<&str> = urls_for_wallet("prl1x").iter().map(|(k, _)| *k).collect();
        assert_eq!(
            keys,
            [
                "price",
                "network",
                "balance",
                "payouts",
                "rewards",
                "payout_history"
            ]
        );
        let keys: Vec<&str> = urls_for_wallet("").iter().map(|(k, _)| *k).collect();
        assert_eq!(keys, ["price", "network"]);
        assert_eq!(
            urls_for_wallet("prl1x")[3].1,
            "https://pool.kryptex.com/prl/api/v1/miner/payouts/prl1x/stats"
        );
    }

    #[test]
    fn body_limit_bom_and_user_agent() {
        let big = vec![b' '; MAX_RESPONSE_BYTES + 1];
        assert_eq!(
            decode_body("https://pool.kryptex.com/x", &big).unwrap_err(),
            "Resposta de pool.kryptex.com excede 2000000 bytes"
        );
        assert_eq!(
            decode_body("u", b"\xef\xbb\xbf{\"a\":1}").unwrap(),
            serde_json::json!({"a": 1})
        );
        assert!(USER_AGENT.starts_with("HashPilot/0."), "{}", *USER_AGENT);
        assert!(!USER_AGENT.contains("/v0"));
        assert_eq!(
            http_error(ureq::http::StatusCode::SERVICE_UNAVAILABLE),
            "HTTP Error 503: Service Unavailable"
        );
    }

    #[test]
    fn shared_fetcher_caches_only_public_market_urls() {
        let calls = Arc::new(AtomicUsize::new(0));
        let counter = calls.clone();
        let inner: Fetcher = Arc::new(move |_url: &str| {
            Ok(serde_json::json!(counter.fetch_add(1, Ordering::SeqCst)))
        });
        let shared = shared_market_fetcher(inner, Duration::from_secs(45));
        assert_eq!(shared(PRICE_URL).unwrap(), serde_json::json!(0));
        assert_eq!(shared(PRICE_URL).unwrap(), serde_json::json!(0));
        let balance = format!("{BASE}miner/balance/prl1x");
        assert_eq!(shared(&balance).unwrap(), serde_json::json!(1));
        assert_eq!(shared(&balance).unwrap(), serde_json::json!(2));
        assert_eq!(calls.load(Ordering::SeqCst), 3);
    }
}
