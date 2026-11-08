//! Chave do job, seeds de ruído, partição em tiles e prova de matriz.
//!
//! Porte das funções privadas de `zk-pow/src/ffi/mine.rs` da referência oficial
//! (Pearl Research Labs, licença ISC — aviso completo em
//! `rust/hashpilot-miner/NOTICE-pearl.md`). Só o caso denso (sem MoE), que é o único que
//! `verify_plain_proof` aceita no commit fixado.

use anyhow::{Result, ensure};
use pearl_blake3::{MerkleTree, blake3_digest, pad_to_chunk_boundary};
use zk_pow::api::proof::{
    IncompleteBlockHeader, MiningConfiguration, PeriodicPattern, SeedDerivation,
};
use zk_pow::ffi::plain_proof::MatrixMerkleProof;

/// Faixa do sinal (`MMAType::Int7xInt7ToInt32`): o verificador recusa valores fora dela.
pub const SIGNAL_MIN: i8 = -64;
pub const SIGNAL_MAX: i8 = 64;

/// `compute_job_key`: BLAKE3 do cabeçalho incompleto seguido da configuração.
pub fn job_key(header: &IncompleteBlockHeader, config: &MiningConfiguration) -> [u8; 32] {
    let mut data = Vec::with_capacity(128);
    data.extend_from_slice(&header.to_bytes());
    data.extend_from_slice(&config.to_bytes());
    blake3_digest(&data, None)
}

/// Bytes com que a matriz é comprometida (i8 como u8, na ordem dada).
pub fn flatten(values: &[i8]) -> Vec<u8> {
    values.iter().map(|&value| value as u8).collect()
}

/// `compute_commitment_hash_with_offsets` (denso): devolve `(b_noise_seed, a_noise_seed)`.
/// `a_row_major` é A por linhas; `bt_row_major` é Bᵀ por linhas (B por colunas), ambos
/// já com `pad_to_chunk_boundary`.
pub fn noise_seeds(
    job_key: &[u8; 32],
    a_row_major: &[u8],
    bt_row_major: &[u8],
    seed_derivation: SeedDerivation,
    m: u32,
    n: u32,
) -> ([u8; 32], [u8; 32]) {
    let raw_hash_a = blake3_digest(a_row_major, Some(*job_key));
    let raw_hash_b = blake3_digest(bt_row_major, Some(*job_key));
    let (hash_a, hash_b) = seed_derivation.bind_roots(&raw_hash_a, &raw_hash_b, m, n);
    let b_noise_seed = blake3_digest(&[&job_key[..], &hash_b[..]].concat(), None);
    let a_noise_seed = blake3_digest(&[&b_noise_seed[..], &hash_a[..]].concat(), None);
    (b_noise_seed, a_noise_seed)
}

/// `threads_partition`: grupos de índices de linhas (ou colunas) que formam os tiles,
/// na mesma ordem do oficial. Erro (em vez de pânico) se a dimensão não for múltipla
/// do período do padrão.
pub fn partition(pattern: &PeriodicPattern, total: usize) -> Result<Vec<Vec<usize>>> {
    let period = pattern.period() as usize;
    ensure!(
        period > 0 && total % period == 0,
        "dimensão {total} não é múltipla do período {period} do padrão"
    );
    let base: Vec<usize> = pattern
        .to_list()
        .iter()
        .map(|&index| index as usize)
        .collect();
    Ok((0..total)
        .filter(|&offset| pattern.offset_is_valid(offset as u32))
        .map(|offset| base.iter().map(|&delta| offset + delta).collect())
        .collect())
}

/// `build_matrix_proof`: prova Merkle das linhas `row_indices` de uma matriz
/// `rows × cols` dada por linhas (`values`).
pub fn matrix_proof(
    values: &[i8],
    rows: usize,
    cols: usize,
    job_key: &[u8; 32],
    row_indices: &[usize],
) -> MatrixMerkleProof {
    let padded = pad_to_chunk_boundary(&flatten(values));
    let tree = MerkleTree::new(&padded, *job_key);
    let leaves = MerkleTree::compute_leaf_indices_from_rows(row_indices, (rows, cols));
    MatrixMerkleProof {
        proof: tree.get_multileaf_proof(&leaves),
        row_indices: row_indices.to_vec(),
    }
}
