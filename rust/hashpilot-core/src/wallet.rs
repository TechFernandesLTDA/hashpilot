//! Carteira Pearl e contribuição opcional — partes puras de `contribution.py`.

use std::sync::LazyLock;

use regex::Regex;
use serde::Serialize;
use serde_json::Value;

/// Carteira do autor. É a padrão de propósito; pagamentos enviados a ela vão para o autor.
pub const AUTHOR_WALLET: &str = "prl1pl2km7u7uddqqfmry4px9c9c7lpfeskhtl0yvkylvgupcjkafxmaqc6cr8t";
/// Parcela aproximada do tempo conectado: 57 min para o usuário, 3 min para o autor.
pub const PERCENT: i64 = 5;
pub const CYCLE_SECONDS: i64 = 3600;
pub const PRIMARY_SECONDS: i64 = 3420;

static WALLET: LazyLock<Regex> =
    LazyLock::new(|| Regex::new(r"^prl1[ac-hj-np-z02-9]{30,100}$").expect("padrão fixo e válido"));

/// `contribution.valid_wallet`: endereço público Pearl (bech32, minúsculas). Nunca seed.
pub fn valid_wallet(wallet: &Value) -> bool {
    wallet.as_str().is_some_and(|text| WALLET.is_match(text))
}

/// `valid_wallet` sobre texto.
pub fn valid_wallet_str(wallet: &str) -> bool {
    WALLET.is_match(wallet)
}

/// Resultado de `contribution.support_configuration`.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct SupportConfiguration {
    pub enabled: bool,
    pub primary_wallet: Value,
    pub support_wallet: String,
    pub percent: i64,
    pub method: String,
    pub reason: String,
    pub configuration_valid: bool,
}

/// Contribuição só fica ativa com opt-in explícito, carteira válida e igual à
/// configurada, e diferente da do autor (sem divisão extra nesse caso).
pub fn support_configuration(settings: &Value, wallet: &Value) -> SupportConfiguration {
    let settings = settings.as_object();
    let valid = settings.is_some() && valid_wallet(wallet);
    let same_wallet = settings.is_some_and(|map| map.get("wallet").unwrap_or(wallet) == wallet);
    let requested =
        settings.is_some_and(|map| map.get("support_enabled") == Some(&Value::Bool(true)));
    let is_author = wallet.as_str() == Some(AUTHOR_WALLET);
    let enabled = valid && same_wallet && requested && !is_author;
    let reason = if enabled {
        "Contribuição opcional habilitada: 57 min para sua carteira / 3 min para o autor"
    } else if is_author {
        "Carteira do autor configurada; não existe contribuição adicional"
    } else if !valid || !same_wallet {
        "Configuração inválida ou carteira alterada; contribuição desligada"
    } else {
        "Contribuição opcional desligada"
    };
    SupportConfiguration {
        enabled,
        primary_wallet: wallet.clone(),
        support_wallet: AUTHOR_WALLET.into(),
        percent: PERCENT,
        method: "mining_time".into(),
        reason: reason.into(),
        configuration_valid: valid && same_wallet,
    }
}
