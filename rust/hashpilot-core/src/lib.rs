//! Núcleo de lógica pura do HashPilot em Rust.
//!
//! Porte fiel dos módulos Python que decidem números e estados do painel —
//! parser do log do SRBMiner, política térmica, previsão de pagamento,
//! equivalentes monetários, carteira/contribuição, configuração do rig e
//! configuração local. Nada aqui acessa GPU, processos, rede ou inicia mineração.
//!
//! Regra herdada do projeto: dado ausente ou inválido é estado explícito
//! (`None`/erro), nunca zero inventado. A equivalência com o Python é conferida
//! por `tests/parity.rs`, contra respostas geradas pelo próprio Python
//! (`tools/rust_parity/generate_goldens.py`).

pub mod config;
pub mod miner_log;
pub mod numbers;
pub mod payout;
pub mod rig_config;
pub mod text;
pub mod thermal;
pub mod valuation;
pub mod wallet;
