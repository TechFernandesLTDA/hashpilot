//! Leitura somente leitura dos logs do SRBMiner — porte de `gpu_telemetry.read_log_tail`.

use std::fs::File;
use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};

pub const TAIL_LIMIT: u64 = 200_000;

/// Últimos `limit` bytes do log, decodificados como o Python: UTF-16 pelo BOM do
/// arquivo, senão UTF-8 com BOM opcional, com substituição de bytes inválidos.
/// Se o corte caiu no meio, a primeira linha (parcial) é descartada.
pub fn read_log_tail(path: &Path, limit: u64) -> std::io::Result<String> {
    let mut file = File::open(path)?;
    let size = file.seek(SeekFrom::End(0))?;
    file.seek(SeekFrom::Start(0))?;
    let mut signature = [0u8; 2];
    let signature_length = file.read(&mut signature)?;
    let signature = &signature[..signature_length];
    let utf16_le = signature == [0xff, 0xfe];
    let utf16_be = signature == [0xfe, 0xff];
    let mut start = size.saturating_sub(limit);
    if utf16_le || utf16_be {
        start -= start % 2;
    }
    file.seek(SeekFrom::Start(start))?;
    let mut body = Vec::new();
    file.read_to_end(&mut body)?;
    let text = if utf16_le || utf16_be {
        let units: Vec<u16> = body
            .chunks_exact(2)
            .map(|pair| {
                if utf16_le {
                    u16::from_le_bytes([pair[0], pair[1]])
                } else {
                    u16::from_be_bytes([pair[0], pair[1]])
                }
            })
            .collect();
        String::from_utf16_lossy(&units)
    } else {
        String::from_utf8_lossy(&body).into_owned()
    };
    let text = text.trim_start_matches('\u{feff}');
    Ok(if start > 0 || size > limit {
        text.split_once('\n')
            .map_or(String::new(), |(_, rest)| rest.to_string())
    } else {
        text.to_string()
    })
}

/// `mining-*.log` mais recente da pasta, pela data de modificação.
pub fn newest_mining_log(directory: &Path) -> Option<PathBuf> {
    std::fs::read_dir(directory)
        .ok()?
        .filter_map(Result::ok)
        .filter(|entry| {
            let name = entry.file_name();
            let name = name.to_string_lossy();
            name.starts_with("mining-") && name.ends_with(".log")
        })
        .filter_map(|entry| Some((entry.metadata().ok()?.modified().ok()?, entry.path())))
        .max_by_key(|(modified, _)| *modified)
        .map(|(_, path)| path)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scratch(name: &str, bytes: &[u8]) -> PathBuf {
        let directory =
            std::env::temp_dir().join(format!("hashpilot-cli-{}-{name}", std::process::id()));
        std::fs::create_dir_all(&directory).unwrap();
        let path = directory.join("mining-1.log");
        std::fs::write(&path, bytes).unwrap();
        path
    }

    #[test]
    fn tail_drops_partial_first_line_and_reads_utf16() {
        let path = scratch("tail", "linha-a\n".repeat(50).as_bytes());
        assert_eq!(
            read_log_tail(&path, 100).unwrap().lines().next(),
            Some("linha-a")
        );
        assert_eq!(read_log_tail(&path, 10_000).unwrap().len(), 400);
        let mut utf16 = vec![0xff, 0xfe];
        for unit in "[2026-10-24 20:00:00] Miner version: 3.8.0\n".encode_utf16() {
            utf16.extend(unit.to_le_bytes());
        }
        let path = scratch("utf16", &utf16);
        assert!(
            read_log_tail(&path, TAIL_LIMIT)
                .unwrap()
                .contains("Miner version: 3.8.0")
        );
        assert_eq!(newest_mining_log(path.parent().unwrap()), Some(path));
    }
}
