//! Paridade com a referência oficial da Pearl (`zk-pow`, commit fixado).
//!
//! 1. Mesmo RNG → mesma `PlainProof` (bytes bincode, como um minerador envia) que o
//!    `try_mine_one` oficial, e o RNG consumido na mesma posição.
//! 2. Jackpot de **todos** os tiles igual a uma porta literal do laço oficial,
//!    inclusive com K que não é múltiplo do rank.
//! 3. Provas aceitas pelo `verify_plain_proof` oficial; adulteradas, recusadas.

use bincode::Options;
use hashpilot_miner::engine::{CpuEngine, NoisedPair, Tile, TileEngine, tile_jackpot};
use hashpilot_miner::{Job, attempt, random_signals, selftest};
use rand::{Rng, SeedableRng};
use rand_chacha::ChaCha20Rng;
use zk_pow::ffi::mine::try_mine_one;
use zk_pow::ffi::plain_proof::PlainProof;

fn bytes(proof: &PlainProof) -> Vec<u8> {
    bincode::options()
        .with_fixint_encoding()
        .serialize(proof)
        .expect("PlainProof serializável")
}

#[test]
fn same_rng_gives_the_same_proof_as_official_try_mine_one() {
    let job = Job::official_test().unwrap();
    let mut hits = 0;
    for seed in 0..12u64 {
        let mut official_rng = ChaCha20Rng::seed_from_u64(seed);
        let mut our_rng = official_rng.clone();
        let official = try_mine_one(
            &mut official_rng,
            job.m,
            job.n,
            job.k(),
            job.header,
            job.config,
            None,
            false,
            job.seed_derivation,
        )
        .unwrap();
        let (a, b) = random_signals(&mut our_rng, job.m, job.n, job.k());
        let ours = attempt(&job, &a, &b, &CpuEngine::default()).unwrap().proof;
        assert_eq!(
            official_rng.get_word_pos(),
            our_rng.get_word_pos(),
            "seed {seed}: consumo do RNG"
        );
        match (official, ours) {
            (Some(official), Some(ours)) => {
                hits += 1;
                assert_eq!(
                    bytes(&official),
                    bytes(&ours),
                    "seed {seed}: prova diferente do oficial"
                );
            }
            (None, None) => {}
            (official, ours) => panic!(
                "seed {seed}: oficial {:?}, nosso {:?}",
                official.is_some(),
                ours.is_some()
            ),
        }
    }
    assert!(
        hits > 0,
        "a dificuldade permissiva do teste oficial deveria produzir acertos"
    );
}

/// Porta literal do laço de `try_mine_one` (zk-pow/src/ffi/mine.rs).
fn official_jackpot(pair: &NoisedPair, rank: usize, tile: &Tile) -> [u32; 16] {
    let a = |row: usize, l: usize| i32::from(pair.a[row * pair.k + l]);
    let b = |col: usize, l: usize| i32::from(pair.bt[col * pair.k + l]);
    let mut accumulator = vec![vec![0i32; tile.cols.len()]; tile.rows.len()];
    let mut jackpot = [0u32; 16];
    for ll in (rank..=pair.k).step_by(rank) {
        for (u, &row) in tile.rows.iter().enumerate() {
            for (v, &col) in tile.cols.iter().enumerate() {
                for l in ll - rank..ll {
                    accumulator[u][v] += a(row, l) * b(col, l);
                }
            }
        }
        let xored = accumulator
            .iter()
            .flatten()
            .fold(0u32, |acc, &value| acc ^ value as u32);
        let slot = (ll / rank - 1) % 16;
        jackpot[slot] = jackpot[slot].rotate_left(13) ^ xored;
    }
    jackpot
}

#[test]
fn every_tile_jackpot_matches_the_literal_official_loop() {
    let mut rng = ChaCha20Rng::seed_from_u64(7);
    for (m, n, k, rank) in [
        (16, 8, 256, 32),
        (24, 16, 300, 64),
        (8, 8, 2048, 128),
        (4, 4, 130, 128),
    ] {
        let pair = NoisedPair {
            m,
            n,
            k,
            a: (0..m * k)
                .map(|_| rng.random_range(-128i16..=128))
                .collect(),
            bt: (0..n * k)
                .map(|_| rng.random_range(-128i16..=128))
                .collect(),
        };
        let tiles: Vec<Tile> = (0..6)
            .map(|_| Tile {
                rows: (0..4).map(|_| rng.random_range(0..m)).collect(),
                cols: (0..3).map(|_| rng.random_range(0..n)).collect(),
            })
            .collect();
        let cpu = CpuEngine { threads: 3 }.jackpots(&pair, rank, &tiles);
        let single = CpuEngine { threads: 1 }.jackpots(&pair, rank, &tiles);
        assert_eq!(cpu, single, "ordem/threads não podem mudar o resultado");
        for (tile, ours) in tiles.iter().zip(&cpu) {
            assert_eq!(
                *ours,
                official_jackpot(&pair, rank, tile),
                "m={m} n={n} k={k} rank={rank}"
            );
            assert_eq!(*ours, tile_jackpot(&pair, rank, tile));
        }
    }
}

#[test]
fn official_verifier_accepts_our_proofs_and_rejects_tampering() {
    let report = selftest(8, 0xC0FFEE, &CpuEngine::default()).unwrap();
    assert!(report.hits > 0, "{report:?}");
    assert_eq!(report.verified, report.hits, "{report:?}");
    assert!(report.tampered_rejected, "{report:?}");
}
