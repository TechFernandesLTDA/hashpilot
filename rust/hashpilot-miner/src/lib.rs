//! Minerador Pearl do HashPilot — fase M1.
//!
//! Uma tentativa de mineração completa, construída sobre a referência oficial
//! (`zk-pow`, fixada num commit): compromisso de A e B, seeds e ruído de baixo
//! rank oficiais, NoisyGEMM + transcript por tile (`engine`), hash do jackpot,
//! limite de dificuldade e prova simples (`PlainProof`) com Merkle oficial.
//!
//! Nesta fase **não há rede nem GPU**: nada é enviado a uma pool e nenhum clock é
//! alterado. A paridade com o `try_mine_one` oficial e a aceitação pelo
//! `verify_plain_proof` oficial são conferidas em `tests/`.

pub mod engine;
pub mod job;

use std::time::{Duration, Instant};

use anyhow::{Result, ensure};
use primitive_types::U256;
use rand::Rng;
use serde::Serialize;
use zk_pow::api::proof::{
    IncompleteBlockHeader, MMAType, MiningConfiguration, PeriodicPattern, SeedDerivation,
};
use zk_pow::api::proof_utils::compute_jackpot_hash;
use zk_pow::api::sanity_checks::extract_difficulty_bound;
use zk_pow::circuit::pearl_noise::compute_noise_for_indices;
use zk_pow::ffi::plain_proof::PlainProof;

use crate::engine::{NoisedPair, Tile, TileEngine};

pub use zk_pow;

/// Um trabalho de mineração denso: cabeçalho, configuração e dimensões.
#[derive(Clone, Copy, Debug)]
pub struct Job {
    pub header: IncompleteBlockHeader,
    pub config: MiningConfiguration,
    pub m: usize,
    pub n: usize,
    pub seed_derivation: SeedDerivation,
}

impl Job {
    pub fn k(&self) -> usize {
        self.config.common_dim as usize
    }

    /// Parâmetros do teste oficial de CI (`zk-pow/bindings/go/src/plain.rs`), com
    /// dificuldade permissiva — para autoteste offline, nunca para minerar de verdade.
    pub fn official_test() -> Result<Self> {
        let k = 2048;
        Ok(Self {
            header: IncompleteBlockHeader {
                version: 0,
                prev_block: [0; 32],
                merkle_root: *b"0123456789abcdef0123456789abcdef",
                timestamp: 0x6666_6666,
                nbits: 0x207F_FFFF,
            },
            config: MiningConfiguration {
                common_dim: k as u32,
                rank: 128,
                mma_type: MMAType::Int7xInt7ToInt32,
                rows_pattern: PeriodicPattern::from_list(&[0, 8, 64, 72])?,
                cols_pattern: PeriodicPattern::from_list(&[0, 1, 8, 9, 32, 33, 40, 41])?,
                moe: None,
            },
            m: 1024,
            n: 128,
            seed_derivation: SeedDerivation::Legacy,
        })
    }
}

/// Matrizes de sinal sorteadas com as **mesmas chamadas** de RNG do `try_mine_one`
/// oficial (A por linhas, depois B por linhas), para a paridade poder usar o mesmo RNG.
pub fn random_signals<R: Rng>(rng: &mut R, m: usize, n: usize, k: usize) -> (Vec<i8>, Vec<i8>) {
    let a = (0..m * k)
        .map(|_| rng.random_range(job::SIGNAL_MIN..=job::SIGNAL_MAX))
        .collect();
    let b = (0..k * n)
        .map(|_| rng.random_range(job::SIGNAL_MIN..=job::SIGNAL_MAX))
        .collect();
    (a, b)
}

/// Resultado de uma tentativa: prova (se achou) e onde o tempo foi gasto.
#[derive(Debug)]
pub struct Attempt {
    pub proof: Option<PlainProof>,
    pub tiles: usize,
    pub tiles_checked: usize,
    pub macs: u64,
    pub commit_time: Duration,
    pub gemm_time: Duration,
}

/// Uma tentativa completa sobre `a` (m×k por linhas) e `b` (k×n por linhas).
pub fn attempt(job: &Job, a: &[i8], b: &[i8], engine: &dyn TileEngine) -> Result<Attempt> {
    let (m, n, k) = (job.m, job.n, job.k());
    let rank = job.config.rank as usize;
    ensure!(
        job.config.moe.is_none(),
        "só jobs densos nesta fase (MoE é recusado pelo verificador)"
    );
    ensure!(
        rank > 0 && a.len() == m * k && b.len() == k * n,
        "dimensões inconsistentes com o job"
    );
    let started = Instant::now();
    let bt: Vec<i8> = (0..n * k)
        .map(|index| b[(index % k) * n + index / k])
        .collect();
    let key = job::job_key(&job.header, &job.config);
    let a_bytes = pearl_blake3::pad_to_chunk_boundary(&job::flatten(a));
    let bt_bytes = pearl_blake3::pad_to_chunk_boundary(&job::flatten(&bt));
    let (b_seed, a_seed) = job::noise_seeds(
        &key,
        &a_bytes,
        &bt_bytes,
        job.seed_derivation,
        m as u32,
        n as u32,
    );
    let all_rows: Vec<usize> = (0..m).collect();
    let all_cols: Vec<usize> = (0..n).collect();
    let noise = compute_noise_for_indices(k, rank, (b_seed, a_seed), &all_rows, &all_cols);
    let pair = NoisedPair {
        m,
        n,
        k,
        a: (0..m * k)
            .map(|i| i16::from(a[i]) + i16::from(noise.a[i / k][i % k]))
            .collect(),
        bt: (0..n * k)
            .map(|i| i16::from(bt[i]) + i16::from(noise.b[i / k][i % k]))
            .collect(),
    };
    let mut tiles = Vec::new();
    for rows in job::partition(&job.config.rows_pattern, m)? {
        for cols in job::partition(&job.config.cols_pattern, n)? {
            tiles.push(Tile {
                rows: rows.clone(),
                cols,
            });
        }
    }
    let commit_time = started.elapsed();
    let gemm_started = Instant::now();
    let jackpots = engine.jackpots(&pair, rank, &tiles);
    let gemm_time = gemm_started.elapsed();
    let bound = extract_difficulty_bound(job.header.nbits, &job.config);
    let macs = tiles
        .iter()
        .map(|tile| (tile.rows.len() * tile.cols.len() * (k / rank) * rank) as u64)
        .sum();
    // Mesma ordem do oficial (linhas por fora, colunas por dentro): o primeiro acerto vence.
    for (checked, (tile, jackpot)) in tiles.iter().zip(&jackpots).enumerate() {
        let hash = compute_jackpot_hash(jackpot, a_seed);
        if U256::from_little_endian(&hash) <= bound {
            let proof = PlainProof {
                m,
                n,
                k,
                noise_rank: rank,
                a: job::matrix_proof(a, m, k, &key, &tile.rows),
                bt: job::matrix_proof(&bt, n, k, &key, &tile.cols),
                moe: None,
            };
            return Ok(Attempt {
                proof: Some(proof),
                tiles: tiles.len(),
                tiles_checked: checked + 1,
                macs,
                commit_time,
                gemm_time,
            });
        }
    }
    Ok(Attempt {
        proof: None,
        tiles: tiles.len(),
        tiles_checked: tiles.len(),
        macs,
        commit_time,
        gemm_time,
    })
}

/// Relatório do autoteste offline.
#[derive(Debug, Serialize)]
pub struct SelfTestReport {
    pub engine: String,
    pub attempts: usize,
    pub hits: usize,
    pub verified: usize,
    pub rejected: Vec<String>,
    pub tampered_rejected: bool,
    pub cpu_tmac_s: f64,
    pub note: &'static str,
}

/// Minera offline com dificuldade permissiva e confere cada prova com o
/// `verify_plain_proof` oficial; adultera uma prova e exige que seja recusada.
pub fn selftest(attempts: usize, seed: u64, engine: &dyn TileEngine) -> Result<SelfTestReport> {
    use rand::SeedableRng;
    let job = Job::official_test()?;
    let mut rng = rand_chacha::ChaCha20Rng::seed_from_u64(seed);
    let (mut hits, mut verified, mut macs, mut gemm) = (0, 0, 0u64, Duration::ZERO);
    let mut rejected = Vec::new();
    let mut tampered_rejected = false;
    for _ in 0..attempts {
        let (a, b) = random_signals(&mut rng, job.m, job.n, job.k());
        let result = attempt(&job, &a, &b, engine)?;
        macs += result.tiles_checked as u64 * (result.macs / result.tiles.max(1) as u64);
        gemm += result.gemm_time;
        let Some(proof) = result.proof else { continue };
        hits += 1;
        match zk_pow::api::verify::verify_plain_proof(
            &job.header,
            &proof,
            None,
            job.seed_derivation,
        ) {
            Ok(()) => verified += 1,
            Err(error) => rejected.push(error.to_string()),
        }
        if !tampered_rejected {
            // Outro cabeçalho muda a chave do job: a mesma prova não pode passar.
            let mut other = job.header;
            other.timestamp ^= 1;
            tampered_rejected =
                zk_pow::api::verify::verify_plain_proof(&other, &proof, None, job.seed_derivation)
                    .is_err();
        }
    }
    Ok(SelfTestReport {
        engine: engine.name().into(),
        attempts,
        hits,
        verified,
        rejected,
        tampered_rejected,
        cpu_tmac_s: if gemm.is_zero() {
            0.0
        } else {
            macs as f64 / gemm.as_secs_f64() / 1e12
        },
        note: "offline: parâmetros do teste oficial com dificuldade permissiva; nada foi enviado a uma pool",
    })
}
