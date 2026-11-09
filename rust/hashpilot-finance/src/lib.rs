//! Finanças do HashPilot (fase 3 do port): porta o `finance.py`.
//!
//! - [`model`]: parte pura (normalização, `financial_model`, `portfolio_model`,
//!   `portfolio_snapshots`), com o mesmo JSON do Python.
//! - [`fetch`]: consultas públicas da Kryptex e da CoinGecko com corpo limitado.
//! - [`collector`]: ciclo de 60 s, cache `finance-cache.json` e histórico SQLite.
//!
//! Só endereços públicos de carteira; nenhuma conta, chave ou credencial.

pub mod collector;
pub mod fetch;
pub mod model;

pub use collector::{
    FinanceCollector, FinanceHistory, PortfolioFinanceCollector, Samples, WalletsHandle, Worker,
    merge_archived,
};
pub use fetch::{Fetcher, fetch_json, network_fetcher, shared_market_fetcher, urls_for_wallet};
pub use model::{
    financial_model, fresh_hash, normalize_finance_state, portfolio_model, portfolio_snapshots,
    py_sum,
};
