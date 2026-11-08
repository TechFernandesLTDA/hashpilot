//! Levanta os dados de catálogo de uma release nova do SRBMiner, sem executar nada.
//!
//! Confere o SHA-256 do ZIP oficial contra o digest publicado pelo GitHub (e,
//! opcionalmente, o MD5 do texto da release, que vem por outro canal), localiza o
//! ÚNICO `SRBMiner-MULTI.exe` do pacote e calcula o SHA-256 dele lendo os bytes do
//! ZIP em memória — nada é extraído para o disco nem executado. A saída é a linha
//! pronta para `miner_update.CATALOG`, para revisão humana.

use std::io::{Cursor, Read};
use std::path::Path;
use std::process::Command;

use md5::Md5;
use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};

pub const RELEASES: &str = "https://github.com/doktor83/SRBMiner-Multi/releases/download";
pub const EXECUTABLE: &str = "SRBMiner-MULTI.exe";
pub const MAX_ARCHIVE_BYTES: u64 = 200_000_000;

fn is_version(version: &str) -> bool {
    let parts: Vec<&str> = version.split('.').collect();
    parts.len() == 3
        && parts
            .iter()
            .all(|part| !part.is_empty() && part.bytes().all(|b| b.is_ascii_digit()))
}

fn is_hex(text: &str, length: usize) -> bool {
    text.len() == length
        && text
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

pub fn folder(version: &str) -> String {
    format!("SRBMiner-Multi-{}", version.replace('.', "-"))
}

pub fn archive_url(version: &str) -> String {
    format!("{RELEASES}/{version}/{}-win64.zip", folder(version))
}

fn hex(bytes: &[u8]) -> String {
    bytes.iter().map(|byte| format!("{byte:02x}")).collect()
}

fn basename(name: &str) -> &str {
    name.rsplit(['/', '\\']).next().unwrap_or(name)
}

/// Confere os argumentos antes de qualquer download.
pub fn validate(version: &str, archive_sha256: &str, md5: Option<&str>) -> Result<(), String> {
    if !is_version(version) {
        return Err("Versão precisa ter o formato X.Y.Z".into());
    }
    if !is_hex(archive_sha256, 64) {
        return Err("--archive-sha256 precisa ter 64 dígitos hexadecimais minúsculos".into());
    }
    if md5.is_some_and(|text| !is_hex(&text.to_ascii_lowercase(), 32)) {
        return Err("--md5 precisa ter 32 dígitos hexadecimais".into());
    }
    Ok(())
}

/// Mesmo contrato de `tools/inspect_miner_release.py`.
pub fn inspect(
    version: &str,
    data: &[u8],
    archive_sha256: &str,
    md5: Option<&str>,
) -> Result<Value, String> {
    validate(version, archive_sha256, md5)?;
    let actual = hex(&Sha256::digest(data));
    if actual != archive_sha256 {
        return Err(format!(
            "SHA-256 do pacote divergente do publicado: {actual}"
        ));
    }
    if let Some(expected) = md5 {
        if hex(&Md5::digest(data)) != expected.to_ascii_lowercase() {
            return Err("MD5 do pacote divergente do texto da release".into());
        }
    }
    let mut archive = zip::ZipArchive::new(Cursor::new(data))
        .map_err(|error| format!("ZIP inválido: {error}"))?;
    let mut members = Vec::new();
    for index in 0..archive.len() {
        let entry = archive
            .by_index_raw(index)
            .map_err(|error| format!("ZIP inválido: {error}"))?;
        if !entry.is_dir() && basename(entry.name()) == EXECUTABLE {
            members.push((
                index,
                entry.name().to_string(),
                entry.enclosed_name().is_some(),
            ));
        }
    }
    let [(index, name, safe)] = members.as_slice() else {
        let names: Vec<&str> = members.iter().map(|(_, name, _)| name.as_str()).collect();
        return Err(format!(
            "Esperado exatamente um {EXECUTABLE} no pacote; encontrado: {names:?}"
        ));
    };
    if !safe || name.starts_with(['/', '\\']) || name.split(['/', '\\']).any(|part| part == "..") {
        return Err("Caminho do executável inseguro dentro do pacote".into());
    }
    let mut binary = Vec::new();
    archive
        .by_index(*index)
        .map_err(|error| format!("ZIP inválido: {error}"))?
        .take(MAX_ARCHIVE_BYTES + 1)
        .read_to_end(&mut binary)
        .map_err(|error| format!("Leitura do executável falhou: {error}"))?;
    if binary.len() as u64 > MAX_ARCHIVE_BYTES {
        return Err("Executável maior que o limite de revisão; recusado".into());
    }
    // Ordem fixa dos campos, a mesma das entradas existentes em `miner_update.CATALOG`.
    let mut fields = vec![
        ("archive_sha256", archive_sha256.to_string()),
        ("exe_sha256", hex(&Sha256::digest(&binary))),
    ];
    if name.as_str() != format!("{}/{EXECUTABLE}", folder(version)) {
        fields.push(("archive_member", name.clone()));
    }
    let entry: Map<String, Value> = fields
        .iter()
        .map(|(key, value)| ((*key).to_string(), json!(value)))
        .collect();
    // Linha no formato de dicionário Python: aspas simples, ", " e ": ".
    let pairs: Vec<String> = fields
        .iter()
        .map(|(key, value)| format!("'{key}': '{value}'"))
        .collect();
    let catalog_line = format!(" '{version}':{{{}}},", pairs.join(", "));
    Ok(json!({
        "version": version,
        "url": archive_url(version),
        "archive_bytes": data.len(),
        "executable_bytes": binary.len(),
        "catalog_entry": entry,
        "catalog_line": catalog_line,
        "note": "Nada foi extraído para o disco nem executado. Revise antes de incluir no catálogo.",
    }))
}

/// Baixa o pacote oficial com o `curl` do sistema (presente no Windows 10/11).
pub fn download(url: &str) -> Result<Vec<u8>, String> {
    let target = std::env::temp_dir().join(format!("hashpilot-release-{}.zip", std::process::id()));
    let status = Command::new("curl")
        .args([
            "-fL",
            "--silent",
            "--show-error",
            "--max-time",
            "180",
            "--max-filesize",
        ])
        .arg(MAX_ARCHIVE_BYTES.to_string())
        .arg("-o")
        .arg(&target)
        .arg(url)
        .status()
        .map_err(|error| format!("curl indisponível: {error}"))?;
    let result = if status.success() {
        std::fs::read(&target).map_err(|error| error.to_string())
    } else {
        Err(format!("Falha no download oficial ({status})"))
    };
    let _ = std::fs::remove_file(&target);
    result
}

pub fn read_file(path: &Path) -> Result<Vec<u8>, String> {
    let size = std::fs::metadata(path)
        .map_err(|error| format!("{}: {error}", path.display()))?
        .len();
    if size > MAX_ARCHIVE_BYTES {
        return Err("Pacote maior que o limite de revisão; recusado".into());
    }
    std::fs::read(path).map_err(|error| format!("{}: {error}", path.display()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use zip::write::SimpleFileOptions;

    const BINARY: &[u8] = b"MZ fixture only, never executed";

    fn package(members: &[(&str, &[u8])]) -> Vec<u8> {
        let mut writer = zip::ZipWriter::new(Cursor::new(Vec::new()));
        for (name, data) in members {
            writer
                .start_file(*name, SimpleFileOptions::default())
                .unwrap();
            writer.write_all(data).unwrap();
        }
        writer.finish().unwrap().into_inner()
    }

    fn sha(data: &[u8]) -> String {
        hex(&Sha256::digest(data))
    }

    #[test]
    fn nested_layout_yields_catalog_entry_with_member_and_hashes() {
        let name = "SRBMiner-Multi-3-8-6/Win/SRBMiner-Multi-3-8-6/SRBMiner-MULTI.exe";
        let data = package(&[(name, BINARY), ("readme.txt", b"x")]);
        let md5 = hex(&Md5::digest(&data));
        let report = inspect("3.8.6", &data, &sha(&data), Some(&md5)).unwrap();
        let entry = &report["catalog_entry"];
        assert_eq!(entry["archive_sha256"], json!(sha(&data)));
        assert_eq!(entry["exe_sha256"], json!(sha(BINARY)));
        assert_eq!(entry["archive_member"], json!(name));
        assert!(
            report["url"]
                .as_str()
                .unwrap()
                .ends_with("/3.8.6/SRBMiner-Multi-3-8-6-win64.zip")
        );
        assert_eq!(
            report["catalog_line"],
            json!(format!(
                " '3.8.6':{{'archive_sha256': '{}', 'exe_sha256': '{}', 'archive_member': '{name}'}},",
                sha(&data),
                sha(BINARY)
            ))
        );
    }

    #[test]
    fn default_layout_omits_archive_member() {
        let data = package(&[("SRBMiner-Multi-3-8-6/SRBMiner-MULTI.exe", BINARY)]);
        let report = inspect("3.8.6", &data, &sha(&data), None).unwrap();
        assert!(report["catalog_entry"].get("archive_member").is_none());
    }

    #[test]
    fn refuses_wrong_digest_md5_ambiguous_or_unsafe_packages() {
        let data = package(&[("SRBMiner-Multi-3-8-6/SRBMiner-MULTI.exe", BINARY)]);
        assert!(
            inspect("3.8.6", &data, &"0".repeat(64), None)
                .unwrap_err()
                .contains("SHA-256 do pacote divergente")
        );
        assert!(
            inspect("3.8.6", &data, &sha(&data), Some(&"0".repeat(32)))
                .unwrap_err()
                .contains("MD5")
        );
        assert!(
            inspect("3.8.6", &data, &sha(&data), Some("xyz"))
                .unwrap_err()
                .contains("--md5")
        );
        assert!(
            validate("3.8.6", &sha(&data).to_uppercase(), None)
                .unwrap_err()
                .contains("minúsculos")
        );
        let double = package(&[
            ("a/SRBMiner-MULTI.exe", BINARY),
            ("b/SRBMiner-MULTI.exe", BINARY),
        ]);
        assert!(
            inspect("3.8.6", &double, &sha(&double), None)
                .unwrap_err()
                .contains("exatamente um")
        );
        let unsafe_path = package(&[("../SRBMiner-MULTI.exe", BINARY)]);
        assert!(
            inspect("3.8.6", &unsafe_path, &sha(&unsafe_path), None)
                .unwrap_err()
                .contains("inseguro")
        );
        assert!(
            inspect("3.8", &data, &sha(&data), None)
                .unwrap_err()
                .contains("formato")
        );
        assert!(
            inspect("3.8.6", b"nao e zip", &sha(b"nao e zip"), None)
                .unwrap_err()
                .contains("ZIP inválido")
        );
    }

    #[test]
    fn reads_local_file_without_extracting_anything() {
        let data = package(&[("SRBMiner-Multi-3-8-6/SRBMiner-MULTI.exe", BINARY)]);
        let directory =
            std::env::temp_dir().join(format!("hashpilot-release-test-{}", std::process::id()));
        std::fs::create_dir_all(&directory).unwrap();
        let path = directory.join("pacote.zip");
        std::fs::write(&path, &data).unwrap();
        let read = read_file(&path).unwrap();
        assert!(inspect("3.8.6", &read, &sha(&data), None).is_ok());
        let names: Vec<_> = std::fs::read_dir(&directory)
            .unwrap()
            .map(|entry| entry.unwrap().file_name())
            .collect();
        assert_eq!(names, ["pacote.zip"]);
        std::fs::remove_dir_all(&directory).unwrap();
    }
}
