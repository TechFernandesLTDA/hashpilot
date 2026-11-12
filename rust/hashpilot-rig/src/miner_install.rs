//! `miner_update`: catálogo revisado do SRBMiner e seleção da versão instalada.
//!
//! Só a parte que o serviço do rig usa: descritor, versão selecionada, executável
//! conferido pelo SHA-256 do catálogo e troca de seleção. Baixar e extrair pacotes
//! continua no Python. O catálogo embutido precisa ser idêntico ao `CATALOG` do
//! `miner_update.py`; o golden `rig_support` confere isso.

use std::path::{Component, Path, PathBuf};

use serde_json::{Map, Value, json};
use sha2::{Digest, Sha256};

use crate::fsutil::atomic_json;

pub const DEFAULT_VERSION: &str = "3.8.0";
const EMBEDDED: [(&str, &str, &str, Option<&str>); 6] = [
    (
        "3.8.0",
        "33eef5f8946259290013ce370a746a85f91c2bbcd81fcefa1aad904dd9a912f8",
        "6eb4e0bc37d65fdf7644ab620737e14f4c7dfa9666acde43d5f38ab1e9094ac1",
        None,
    ),
    (
        "3.8.1",
        "16bd455557bd3698e004d1f6b5d19290b9b59e9f9c8ef9245acafdaeb2413e79",
        "2f7e60a17fcc65ddd3c7f5821666a6ae11ef6c1f2c95331bb7e46793c032b688",
        None,
    ),
    (
        "3.8.3",
        "92af8d2db5bc96df62825487e7f384b8ab04e5db26a634e29c2cd86dcbb87d0e",
        "1a58995232c96501a8c17139db5404527d9ec0b57863ae03605dd477e181ffdf",
        None,
    ),
    (
        "3.8.4",
        "68ec24cbe24e795dd4677e981967563dc7eaea21706289df6b4b0af667789baa",
        "5d235cfa8ff75659f5264817a356e689e087eb0f9b94218a80c5a3f25ffd1171",
        Some("SRBMiner-Multi-3-8-4/Win/SRBMiner-Multi-3-8-4/SRBMiner-MULTI.exe"),
    ),
    (
        "3.8.6",
        "d44aa90bf857e526805c95e153b8c089c2188dae5ba0c4fad83d516168fb424b",
        "d072fc7012fe1d56f27253d7e41931afd0e3df349fbcc947f2243151baa6ab29",
        None,
    ),
    (
        "3.8.7",
        "cbfc6fa4daf22641e0f845922f8fbccff7ff1dc144af7316061bca06b76cfb1f",
        "dde3740fd6ee9e7fd3832edd98c9fb838df2344ef05b5ecbcd87997dfb5d3348",
        None,
    ),
];
const UNREVIEWED: &str = "Versão ainda não revisada pelo HashPilot; atualize o aplicativo antes de instalar esse minerador.";
const TAMPERED: &str = "Executável ausente, bloqueado ou SHA-256 divergente. Confira o Histórico de proteção do Windows; nenhuma exceção será criada automaticamente. A versão atual foi preservada se a ativação ainda não começou.";

/// Versões revisadas: `versão -> {archive_sha256, exe_sha256[, archive_member]}`.
#[derive(Clone, Debug)]
pub struct Catalog {
    pub entries: Map<String, Value>,
    pub default_version: String,
}

impl Default for Catalog {
    fn default() -> Self {
        let entries = EMBEDDED
            .iter()
            .map(|(version, archive, exe, member)| {
                let mut item = Map::new();
                item.insert("archive_sha256".into(), json!(archive));
                item.insert("exe_sha256".into(), json!(exe));
                if let Some(member) = member {
                    item.insert("archive_member".into(), json!(member));
                }
                ((*version).to_owned(), Value::Object(item))
            })
            .collect();
        Self {
            entries,
            default_version: DEFAULT_VERSION.into(),
        }
    }
}

/// `Path.resolve()` do Windows: resolve junções e links da parte que existe.
pub fn resolve(path: &Path) -> PathBuf {
    let absolute = std::path::absolute(path).unwrap_or_else(|_| path.to_path_buf());
    let mut existing = absolute.clone();
    let mut rest = Vec::new();
    loop {
        if let Ok(real) = std::fs::canonicalize(&existing) {
            let text = real.to_string_lossy();
            let mut out = PathBuf::from(text.strip_prefix(r"\\?\UNC\").map_or_else(
                || text.strip_prefix(r"\\?\").unwrap_or(&text).to_owned(),
                |unc| format!(r"\\{unc}"),
            ));
            for part in rest.iter().rev() {
                out.push(part);
            }
            return out;
        }
        match (
            existing.file_name().map(std::ffi::OsStr::to_os_string),
            existing.parent(),
        ) {
            (Some(name), Some(parent)) => {
                rest.push(name);
                existing = parent.to_path_buf();
            }
            _ => return absolute,
        }
    }
}

fn sha256_hex(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

impl Catalog {
    /// `descriptor(version)`.
    pub fn descriptor(&self, version: &Value) -> Result<Value, String> {
        let Some(item) = version.as_str().and_then(|text| self.entries.get(text)) else {
            return Err(UNREVIEWED.into());
        };
        let version = version.as_str().unwrap_or_default();
        let folder = format!("SRBMiner-Multi-{}", version.replace('.', "-"));
        let mut out = item.as_object().cloned().unwrap_or_default();
        out.insert("version".into(), json!(version));
        out.insert("folder".into(), json!(folder));
        out.insert("asset".into(), json!(format!("{folder}-win64.zip")));
        out.insert("url".into(), json!(format!("https://github.com/doktor83/SRBMiner-Multi/releases/download/{version}/{folder}-win64.zip")));
        Ok(Value::Object(out))
    }

    /// `selected_version(base)`: a versão de `miner-install.json`, ou a padrão.
    pub fn selected_version(&self, base: &Path) -> Result<String, String> {
        let record = read(&base.join("miner-install.json"));
        let version = record
            .get("version")
            .cloned()
            .unwrap_or_else(|| json!(self.default_version));
        self.descriptor(&version)?;
        Ok(version.as_str().unwrap_or_default().to_owned())
    }

    /// `executable(version, base, verify)`: caminho fixo, sem junção, com SHA-256 conferido.
    pub fn executable(
        &self,
        version: &Value,
        base: &Path,
        verify: bool,
    ) -> Result<PathBuf, String> {
        let item = self.descriptor(version)?;
        let target = resolve(base)
            .join(item["folder"].as_str().unwrap_or_default())
            .join("SRBMiner-MULTI.exe");
        if resolve(&target) != target
            || target.components().any(|part| part == Component::ParentDir)
        {
            return Err("Caminho redirecionado do minerador recusado".into());
        }
        if verify {
            let valid = target.is_file()
                && std::fs::read(&target)
                    .is_ok_and(|bytes| json!(sha256_hex(&bytes)) == item["exe_sha256"]);
            if !valid {
                return Err(TAMPERED.into());
            }
        }
        Ok(target)
    }

    pub fn active_executable(&self, base: &Path, verify: bool) -> Result<PathBuf, String> {
        let version = self.selected_version(base)?;
        self.executable(&json!(version), base, verify)
    }

    /// `select(version, base)`: troca a versão selecionada (o executável precisa conferir).
    pub fn select(&self, version: &Value, base: &Path, now: f64) -> Result<(), String> {
        self.executable(version, base, true)?;
        let old = self.selected_version(base)?;
        atomic_json(
            &base.join("miner-install.json"),
            &json!({"version": version, "previous_version": old, "selected_at": now}),
            false,
            false,
        )
    }
}

/// `read(path)`: objeto JSON do arquivo; ausente, vazio ou parcial vale `{}`.
pub fn read(path: &Path) -> Map<String, Value> {
    std::fs::read_to_string(path)
        .ok()
        .and_then(|text| serde_json::from_str::<Value>(text.trim_start_matches('\u{feff}')).ok())
        .and_then(|value| value.as_object().cloned())
        .unwrap_or_default()
}

#[cfg(all(test, windows))]
mod tests {
    use super::*;

    /// Uma junção dentro da pasta de instalação apontando para fora é recusada, mesmo
    /// com o executável certo do outro lado (como o `target.resolve() != target`).
    #[test]
    fn junction_inside_the_install_folder_is_refused() {
        let root = std::path::absolute(
            std::env::temp_dir().join(format!("hashpilot-juncao-{}", std::process::id())),
        )
        .unwrap();
        let _ = std::fs::remove_dir_all(&root);
        let outside = root.join("fora");
        std::fs::create_dir_all(&outside).unwrap();
        std::fs::write(outside.join("SRBMiner-MULTI.exe"), b"binario").unwrap();
        let base = root.join("base");
        std::fs::create_dir_all(&base).unwrap();
        let link = base.join("SRBMiner-Multi-3-8-0");
        let made = std::process::Command::new("cmd")
            .args(["/c", "mklink", "/J"])
            .arg(&link)
            .arg(&outside)
            .output()
            .unwrap();
        assert!(
            made.status.success(),
            "{}",
            String::from_utf8_lossy(&made.stderr)
        );
        let mut catalog = Catalog::default();
        catalog.entries.insert(
            "3.8.0".into(),
            json!({"archive_sha256": "0".repeat(64), "exe_sha256": sha256_hex(b"binario")}),
        );
        let error = catalog
            .executable(&json!("3.8.0"), &base, true)
            .unwrap_err();
        assert_eq!(error, "Caminho redirecionado do minerador recusado");
        std::fs::remove_dir(&link).unwrap();
        std::fs::create_dir_all(&link).unwrap();
        std::fs::write(link.join("SRBMiner-MULTI.exe"), b"binario").unwrap();
        assert_eq!(
            catalog.executable(&json!("3.8.0"), &base, true).unwrap(),
            resolve(&base)
                .join("SRBMiner-Multi-3-8-0")
                .join("SRBMiner-MULTI.exe")
        );
        let _ = std::fs::remove_dir_all(root);
    }
}
