//! `contribution.ProxyLease`: posse exclusiva do diretório pelo sistema operacional.
//!
//! Trava o primeiro byte do arquivo, como o `msvcrt.locking(LK_NBLCK, 1)` do Python,
//! então o serviço em Rust e o em Python se excluem mutuamente. O Windows libera a
//! trava mesmo se o processo morrer; o JSON dentro do arquivo é só informativo.

use std::fs::{File, OpenOptions};
use std::io::{Seek, SeekFrom, Write};
use std::path::Path;

use serde_json::json;

pub struct Lease {
    file: Option<File>,
}

fn now() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_or(0.0, |elapsed| elapsed.as_secs_f64())
}

fn rewrite(file: &mut File, text: &str) -> std::io::Result<()> {
    file.seek(SeekFrom::Start(0))?;
    file.write_all(text.as_bytes())?;
    file.set_len(text.len() as u64)
}

impl Lease {
    pub fn acquire(path: &Path, busy_message: &str) -> Result<Self, String> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).map_err(|error| error.to_string())?;
        }
        let mut file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(path)
            .map_err(|error| error.to_string())?;
        if file.metadata().map_err(|error| error.to_string())?.len() == 0 {
            file.write_all(b" ").map_err(|error| error.to_string())?;
        }
        if !crate::win32::lock_first_byte(&file) {
            return Err(busy_message.into());
        }
        let payload =
            json!({"pid": std::process::id(), "active": true, "started_at": now()}).to_string();
        let mut lease = Self { file: Some(file) };
        if let Err(error) = rewrite(lease.file.as_mut().expect("arquivo"), &payload) {
            lease.close();
            return Err(error.to_string());
        }
        Ok(lease)
    }

    pub fn close(&mut self) {
        if let Some(mut file) = self.file.take() {
            let payload = json!({"pid": std::process::id(), "active": false, "stopped_at": now()})
                .to_string();
            let _ = rewrite(&mut file, &payload);
            crate::win32::unlock_first_byte(&file);
        }
    }
}

impl Drop for Lease {
    fn drop(&mut self) {
        self.close();
    }
}
