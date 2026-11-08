//! Contrato do log do SRBMiner com o log público e autorizado do autor
//! (`portable-data/`, SRBMiner 3.8.0, RTX 3070 física) — espelha `test_miner_log.py`.

use std::path::PathBuf;

use hashpilot_core::miner_log::{AVERAGE, RATE, STAMP, StampZone, log_timestamp, parse_gpu_log};
use hashpilot_core::text::splitlines;

fn real_log() -> String {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("../../portable-data/miner-logs/maximum-20261024-200354.log");
    std::fs::read_to_string(path).expect("log real de portable-data")
}

#[test]
fn last_sample_of_the_real_380_log() {
    let text = real_log();
    let last = splitlines(&text).last().copied().unwrap();
    let now = log_timestamp(last, StampZone::UTC).unwrap() + 1.0;
    let (state, future) = parse_gpu_log(&text, Some("GPU-fixture"), 0, now, true, StampZone::UTC);
    assert_eq!(state.version.as_deref(), Some("3.8.0"));
    assert_eq!(state.connection, "Conectado à pool");
    assert_eq!(state.hashrate, Some(76.08));
    assert_eq!((state.accepted, state.rejected, state.hw_errors), (8, 0, 0));
    assert_eq!(state.average_1m, Some(76.22));
    assert_eq!(state.average_15m, Some(74.10));
    assert!(state.rate_points.len() > 5);
    assert_eq!(future, None);
}

#[test]
fn every_rate_line_matches_except_total() {
    let text = real_log();
    let lines = splitlines(&text);
    let rates: Vec<&&str> = lines
        .iter()
        .filter(|line| line.contains(" TH/s [") && !line.contains("Total:"))
        .collect();
    assert!(!rates.is_empty());
    for line in rates {
        assert!(STAMP.is_match(line), "{line}");
        assert!(RATE.is_match(line), "{line}");
    }
    assert!(
        lines
            .iter()
            .filter(|line| line.contains("] Total:"))
            .all(|line| !RATE.is_match(line))
    );
    assert!(lines.iter().any(|line| AVERAGE.is_match(line)));
}
