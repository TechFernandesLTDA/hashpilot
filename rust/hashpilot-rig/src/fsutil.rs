//! `fsutil.atomic_json`: grava num temporário ao lado e troca pelo destino, com novas
//! tentativas só na troca (antivírus e leitores do Windows podem negar por instantes).

use std::io::Write;
use std::path::Path;
use std::time::Duration;

use hashpilot_history::pyjson::{encode, encode_indented};
use serde_json::Value;

/// `fsutil.torn_file`: vazio ou só NUL/espaços, o rastro de uma queda de energia (no NTFS
/// o rename sobrevive, mas os dados sem fsync voltam zerados). Nenhum JSON válido é assim.
pub fn torn_file(data: &[u8]) -> bool {
    data.iter()
        .all(|byte| matches!(byte, 0 | b' ' | b'\t' | b'\r' | b'\n'))
}

/// `fsutil.quarantine_torn`: move o arquivo danificado por queda para `corrompidos/` ao
/// lado e devolve o destino; `None`, sem mexer em nada, se ele não existe ou tem conteúdo.
pub fn quarantine_torn(path: &Path) -> std::io::Result<Option<std::path::PathBuf>> {
    let data = match std::fs::read(path) {
        Ok(data) => data,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error),
    };
    if !torn_file(&data) {
        return Ok(None);
    }
    let folder = path
        .parent()
        .unwrap_or_else(|| Path::new("."))
        .join("corrompidos");
    std::fs::create_dir_all(&folder)?;
    let name = path
        .file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_default();
    let stamp = chrono::Local::now().format("%Y%m%d-%H%M%S");
    let target = folder.join(format!("{name}.{stamp}.{}", std::process::id()));
    std::fs::rename(path, &target)?;
    Ok(Some(target))
}

/// `atomic_json(path, payload, durable=, compact=)`.
pub fn atomic_json(
    path: &Path,
    payload: &Value,
    durable: bool,
    compact: bool,
) -> Result<(), String> {
    let failed = |error: std::io::Error| format!("{}: {error}", path.display());
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(failed)?;
    }
    let name = path
        .file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_default();
    let temporary = path.with_file_name(format!("{name}.{}.tmp", std::process::id()));
    let text = if compact {
        encode(payload)
    } else {
        encode_indented(payload)
    };
    {
        let mut handle = std::fs::File::create(&temporary).map_err(failed)?;
        handle.write_all(text.as_bytes()).map_err(failed)?;
        if durable {
            handle.sync_all().map_err(failed)?;
        }
    }
    for attempt in 0..21 {
        match std::fs::rename(&temporary, path) {
            Ok(()) => return Ok(()),
            Err(error) if error.kind() == std::io::ErrorKind::PermissionDenied && attempt < 20 => {
                std::thread::sleep(Duration::from_millis(50));
            }
            Err(error) => return Err(failed(error)),
        }
    }
    unreachable!("a última tentativa devolve o erro")
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn writes_indented_and_compact_and_replaces() {
        let folder = std::env::temp_dir().join(format!("hashpilot-fsutil-{}", std::process::id()));
        let path = folder.join("a").join("x.json");
        atomic_json(
            &path,
            &json!({"b": [1, 2.0], "c": {}, "d": "ã"}),
            false,
            false,
        )
        .unwrap();
        assert_eq!(
            std::fs::read_to_string(&path).unwrap(),
            "{\n  \"b\": [\n    1,\n    2.0\n  ],\n  \"c\": {},\n  \"d\": \"ã\"\n}"
        );
        atomic_json(&path, &json!({"b": []}), true, true).unwrap();
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "{\"b\":[]}");
        assert_eq!(
            std::fs::read_dir(path.parent().unwrap()).unwrap().count(),
            1,
            "sem temporários"
        );
        let _ = std::fs::remove_dir_all(folder);
    }

    #[test]
    fn quarantines_only_files_torn_by_a_power_loss() {
        let folder =
            std::env::temp_dir().join(format!("hashpilot-fsutil-torn-{}", std::process::id()));
        std::fs::create_dir_all(&folder).unwrap();
        let path = folder.join("rig-manager.json");
        assert_eq!(
            quarantine_torn(&path).unwrap(),
            None,
            "ausente fica ausente"
        );
        std::fs::write(&path, b"{\"a\": 1}").unwrap();
        assert_eq!(quarantine_torn(&path).unwrap(), None);
        std::fs::write(&path, b"{\"a\": ").unwrap();
        assert_eq!(
            quarantine_torn(&path).unwrap(),
            None,
            "JSON truncado não é queda"
        );
        assert!(path.exists());
        std::fs::write(&path, vec![0u8; 11430]).unwrap();
        let target = quarantine_torn(&path)
            .unwrap()
            .expect("zerado vai para a quarentena");
        assert!(!path.exists());
        assert_eq!(target.parent().unwrap(), folder.join("corrompidos"));
        assert_eq!(
            std::fs::read(&target).unwrap().len(),
            11430,
            "preservado, não apagado"
        );
        assert!(torn_file(b"") && torn_file(b" \r\n\0") && !torn_file(b"0"));
        let _ = std::fs::remove_dir_all(folder);
    }
}
