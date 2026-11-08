//! `hashpilot-miner` — autoteste e medição offline do minerador (fase M1).
//! Não conecta a nenhuma pool, não usa GPU e não altera clocks.

use std::process::ExitCode;
use std::time::Instant;

use hashpilot_miner::engine::CpuEngine;
use hashpilot_miner::{Job, attempt, random_signals, selftest};
use rand::SeedableRng;

const USAGE: &str = "\
hashpilot-miner — fase M1 (offline: sem pool, sem GPU)

  hashpilot-miner selftest [--attempts N] [--seed S]
      Minera com os parâmetros do teste oficial (dificuldade permissiva) e confere cada
      prova com o verify_plain_proof oficial; exige também recusar uma prova adulterada.
  hashpilot-miner bench-cpu [--attempts N]
      Mede o NoisyGEMM + transcript na CPU (dificuldade impossível: varre todos os tiles).
";

fn value(arguments: &[String], name: &str, default: u64) -> Result<u64, String> {
    match arguments.iter().position(|item| item == name) {
        None => Ok(default),
        Some(index) => arguments
            .get(index + 1)
            .and_then(|text| text.parse().ok())
            .ok_or_else(|| format!("{name} precisa de um inteiro")),
    }
}

fn run(arguments: &[String]) -> Result<bool, String> {
    let engine = CpuEngine::default();
    match arguments.first().map(String::as_str) {
        Some("selftest") => {
            let attempts = value(arguments, "--attempts", 20)? as usize;
            let seed = value(arguments, "--seed", 0xC0FFEE)?;
            let report = selftest(attempts, seed, &engine).map_err(|error| error.to_string())?;
            println!(
                "{}",
                serde_json::to_string_pretty(&report).map_err(|error| error.to_string())?
            );
            Ok(report.hits > 0 && report.verified == report.hits && report.tampered_rejected)
        }
        Some("bench-cpu") => {
            let attempts = value(arguments, "--attempts", 3)?.max(1) as usize;
            let mut job = Job::official_test().map_err(|error| error.to_string())?;
            job.header.nbits = 0x0300_0001; // alvo minúsculo: nenhum acerto, todos os tiles medidos
            let mut rng = rand_chacha::ChaCha20Rng::seed_from_u64(1);
            let (mut macs, mut seconds) = (0u64, 0f64);
            let started = Instant::now();
            for _ in 0..attempts {
                let (a, b) = random_signals(&mut rng, job.m, job.n, job.k());
                let result = attempt(&job, &a, &b, &engine).map_err(|error| error.to_string())?;
                macs += result.macs;
                seconds += result.gemm_time.as_secs_f64();
            }
            println!(
                "{}",
                serde_json::json!({
                    "engine": "cpu", "threads": engine.threads, "attempts": attempts,
                    "tmac_s": macs as f64 / seconds.max(1e-9) / 1e12,
                    "wall_seconds": started.elapsed().as_secs_f64(),
                    "note": "CPU serve para referência e verificação; minerar de verdade exige o motor de GPU (fase M2)",
                })
            );
            Ok(true)
        }
        _ => {
            print!("{USAGE}");
            Ok(arguments
                .first()
                .is_some_and(|item| item == "--help" || item == "-h"))
        }
    }
}

fn main() -> ExitCode {
    let arguments: Vec<String> = std::env::args().skip(1).collect();
    match run(&arguments) {
        Ok(true) => ExitCode::SUCCESS,
        Ok(false) => ExitCode::from(1),
        Err(message) => {
            eprintln!("erro: {message}");
            ExitCode::from(2)
        }
    }
}
