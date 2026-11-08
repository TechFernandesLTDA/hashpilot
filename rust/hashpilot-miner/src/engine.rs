//! Motor de tiles: o NoisyGEMM e o transcript "jackpot" de cada tile.
//!
//! É a parte que um kernel de GPU vai substituir. O contrato é o laço de
//! `try_mine_one` da referência oficial: para cada fatia de `rank` posições de K,
//! soma os produtos no acumulador do tile, faz XOR de **todo** o tile (valores
//! acumulados até ali) e mistura no jackpot com rotação de 13 bits.

use zk_pow::circuit::pearl_program::{JACKPOT_SIZE, LROT_PER_TILE};

/// Matrizes já com ruído, em layout contíguo: `a` é m×k por linhas; `bt` é n×k por
/// linhas (as colunas de B). Sinal [-64, 64] + ruído i8 sempre cabe em i16.
#[derive(Clone, Debug)]
pub struct NoisedPair {
    pub m: usize,
    pub n: usize,
    pub k: usize,
    pub a: Vec<i16>,
    pub bt: Vec<i16>,
}

impl NoisedPair {
    fn a_row(&self, row: usize) -> &[i16] {
        &self.a[row * self.k..(row + 1) * self.k]
    }

    fn b_col(&self, col: usize) -> &[i16] {
        &self.bt[col * self.k..(col + 1) * self.k]
    }
}

/// Um tile: as linhas de A e as colunas de B que ele cobre (saída de `job::partition`).
#[derive(Clone, Debug)]
pub struct Tile {
    pub rows: Vec<usize>,
    pub cols: Vec<usize>,
}

/// Quem calcula os jackpots: a CPU agora; a GPU numa fase seguinte, sob o mesmo contrato.
pub trait TileEngine {
    fn name(&self) -> &'static str;
    /// Jackpot de cada tile, na mesma ordem de `tiles`.
    fn jackpots(&self, pair: &NoisedPair, rank: usize, tiles: &[Tile]) -> Vec<[u32; JACKPOT_SIZE]>;
}

fn dot(a: &[i16], b: &[i16]) -> i32 {
    // Produtos de |v| <= 128 somados em i32: sem estouro para os K do protocolo.
    a.iter()
        .zip(b)
        .map(|(&x, &y)| i32::from(x) * i32::from(y))
        .sum()
}

/// Jackpot de um tile — reordena o laço oficial (u, v por fora, fatias por dentro)
/// sem mudar o resultado: o XOR do tile em cada fatia é associativo e comutativo.
pub fn tile_jackpot(pair: &NoisedPair, rank: usize, tile: &Tile) -> [u32; JACKPOT_SIZE] {
    let chunks = if rank == 0 { 0 } else { pair.k / rank };
    let mut xored = vec![0u32; chunks];
    for &row in &tile.rows {
        let a = pair.a_row(row);
        for &col in &tile.cols {
            let b = pair.b_col(col);
            let mut running = 0i32;
            for (chunk, slot) in xored.iter_mut().enumerate() {
                let range = chunk * rank..(chunk + 1) * rank;
                running = running.wrapping_add(dot(&a[range.clone()], &b[range]));
                *slot ^= running as u32;
            }
        }
    }
    let mut jackpot = [0u32; JACKPOT_SIZE];
    for (chunk, value) in xored.into_iter().enumerate() {
        let slot = chunk % JACKPOT_SIZE;
        jackpot[slot] = jackpot[slot].rotate_left(LROT_PER_TILE) ^ value;
    }
    jackpot
}

/// Motor de CPU com uma thread por núcleo; ordem do resultado sempre igual à dos tiles.
#[derive(Clone, Copy, Debug)]
pub struct CpuEngine {
    pub threads: usize,
}

impl Default for CpuEngine {
    fn default() -> Self {
        Self {
            threads: std::thread::available_parallelism().map_or(1, usize::from),
        }
    }
}

impl TileEngine for CpuEngine {
    fn name(&self) -> &'static str {
        "cpu"
    }

    fn jackpots(&self, pair: &NoisedPair, rank: usize, tiles: &[Tile]) -> Vec<[u32; JACKPOT_SIZE]> {
        let threads = self.threads.clamp(1, tiles.len().max(1));
        let per_thread = tiles.len().div_ceil(threads).max(1);
        let mut output = vec![[0u32; JACKPOT_SIZE]; tiles.len()];
        std::thread::scope(|scope| {
            for (chunk_tiles, chunk_out) in
                tiles.chunks(per_thread).zip(output.chunks_mut(per_thread))
            {
                scope.spawn(move || {
                    for (tile, slot) in chunk_tiles.iter().zip(chunk_out) {
                        *slot = tile_jackpot(pair, rank, tile);
                    }
                });
            }
        });
        output
    }
}
