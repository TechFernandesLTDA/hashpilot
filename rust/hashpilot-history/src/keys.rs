//! Chaves de deduplicação dos eventos: as mesmas do `history.py`, para um banco
//! gravado pelo Python continuar sem duplicatas quando o Rust arquivar o mesmo log.

use sha2::{Digest, Sha256};

/// `history.log_key`: `"log:" + sha256(str(path) + "\n" + linha)`.
pub fn log_key(path: &str, line: &str) -> String {
    let mut digest = Sha256::new();
    digest.update(path.as_bytes());
    digest.update(b"\n");
    digest.update(line.as_bytes());
    format!("log:{}", hex(&digest.finalize()))
}

/// Chave de uma linha do `thermal.jsonl`: `"thermal:" + sha256(str(path) + "\n" + linha_bruta)`.
///
/// A linha bruta inclui o `\n` (e o `\r`, se houver), exatamente como o Python a lê.
pub fn thermal_key(path: &str, raw_line: &[u8]) -> String {
    let mut digest = Sha256::new();
    digest.update(path.as_bytes());
    digest.update(b"\n");
    digest.update(raw_line);
    format!("thermal:{}", hex(&digest.finalize()))
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn keys_are_sha256_of_path_newline_and_line() {
        // sha256("a.log\nx") e sha256("t.jsonl\n{}\n"), conferidos com o hashlib do Python.
        assert_eq!(
            log_key("a.log", "x"),
            "log:8336c029c41275cfcdedd8580b8faaa4c6e7765d663a7bda8ac04fb010bebbca"
        );
        assert_eq!(
            thermal_key("t.jsonl", b"{}\n"),
            "thermal:6631a639ae938516d522e5d7d667c17492de1f137ac38833c7786304751a4158"
        );
    }
}
