//! Formato do log de texto do SRBMiner — porte de `miner_log.py` e de
//! `gpu_telemetry._parse_gpu_log`.
//!
//! O SRBMiner não expõe API ao HashPilot: hashrate, shares, médias, conexão e
//! pausas saem destas linhas. Os testes de paridade comparam este módulo com o
//! Python sobre os logs reais de `portable-data/` e variantes sintéticas.

use std::collections::{HashMap, HashSet};
use std::sync::LazyLock;

use chrono::{FixedOffset, Local, LocalResult, NaiveDateTime, TimeZone};
use regex::Regex;
use serde::Serialize;
use serde_json::Value;

use crate::numbers::{finite, numeric_str};
use crate::text::{py_str, splitlines};

fn regex(pattern: &str) -> Regex {
    Regex::new(pattern).expect("padrão de regex fixo e válido")
}

pub static STAMP: LazyLock<Regex> =
    LazyLock::new(|| regex(r"^\[(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\]"));
// O Python termina com `(?!\d)`; como `\d+` é guloso, o grupo 1 é sempre a sequência
// inteira de dígitos e a busca nunca depende do lookahead (que o crate regex não tem).
pub static GPU_LABEL: LazyLock<Regex> = LazyLock::new(|| regex(r"\bGPU\s*(?:\[\s*)?(\d+)"));
pub static RATE: LazyLock<Regex> = LazyLock::new(|| {
    regex(r"\bGPU\s*(?:\[\s*)?(\d+)(?:\s*\])?\s+.*?:\s*([\d.]+)\s+([kMGTPE]?H)/s\s+\[(.*?)\]")
});
pub static AVERAGE: LazyLock<Regex> = LazyLock::new(|| {
    regex(
        r"(?i)Average hashrate:\s*1m\s+([\d.]+)\s+([kMGTPE]?H)/s\s*\|\s*15m\s+([\d.]+)\s+([kMGTPE]?H)/s",
    )
});
pub static COUNTERS: LazyLock<Regex> = LazyLock::new(|| regex(r"([A-Z]+):([\d.]+)"));
pub static DEVICE_BUS: LazyLock<Regex> =
    LazyLock::new(|| regex(r"(?i)\bBUS\s*:\s*([0-9a-f]{1,2})\b"));
pub static DEVICE_INDEX: LazyLock<Regex> = LazyLock::new(|| regex(r"(?i)\bGPU\s*(\d+)\s*[:\[]"));
static PCI: LazyLock<Regex> =
    LazyLock::new(|| regex(r"^[0-9a-fA-F]{4,8}:([0-9a-fA-F]{2}):[0-9a-fA-F]{2}\.[0-7]$"));

/// Multiplicador de cada unidade para TH/s.
pub fn unit_multiplier(unit: &str) -> Option<f64> {
    Some(match unit {
        "H" => 1e-12,
        "kH" => 1e-9,
        "MH" => 1e-6,
        "GH" => 1e-3,
        "TH" => 1.0,
        "PH" => 1e3,
        "EH" => 1e6,
        _ => return None,
    })
}

pub const CONNECTION_LOST: [&str; 4] = [
    "disconnected",
    "connection failed",
    "reconnecting",
    "connection lost",
];
const WAITING: &str = "Aguardando conexão";
const CONNECTED: &str = "Conectado à pool";
const RECONNECTING: &str = "Reconectando à pool";

/// Fuso usado para converter o carimbo `[AAAA-MM-DD hh:mm:ss]`, que o SRBMiner
/// grava em hora local. Produção usa `Local`; testes usam um deslocamento fixo.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StampZone {
    Local,
    FixedOffsetSeconds(i32),
}

impl StampZone {
    pub const UTC: StampZone = StampZone::FixedOffsetSeconds(0);

    /// Hora local ingênua → epoch. Na hora ambígua do fim do horário de verão vale a
    /// mais cedo (como `datetime.timestamp()` com `fold=0`); na hora inexistente, `None`.
    pub fn to_epoch(self, naive: NaiveDateTime) -> Option<f64> {
        let seconds = match self {
            StampZone::Local => match Local.from_local_datetime(&naive) {
                LocalResult::Single(moment) | LocalResult::Ambiguous(moment, _) => {
                    moment.timestamp()
                }
                LocalResult::None => return None,
            },
            StampZone::FixedOffsetSeconds(offset) => FixedOffset::east_opt(offset)?
                .from_local_datetime(&naive)
                .single()?
                .timestamp(),
        };
        Some(seconds as f64)
    }
}

/// `miner_log.parse_stamp`: data inválida (mês 99, 30/02, segundo 60) vira `None`.
pub fn parse_stamp(text: &str, zone: StampZone) -> Option<f64> {
    // O chrono aceita segundo 60 (bissexto); o `strptime` do Python não.
    let second: u32 = text.get(17..19)?.parse().ok()?;
    if second >= 60 {
        return None;
    }
    let naive = NaiveDateTime::parse_from_str(text, "%Y-%m-%d %H:%M:%S").ok()?;
    zone.to_epoch(naive)
}

/// `miner_log.log_timestamp`: carimbo no início da linha, ou `None`.
pub fn log_timestamp(line: &str, zone: StampZone) -> Option<f64> {
    let captures = STAMP.captures(line)?;
    parse_stamp(&captures[1], zone)
}

fn label_of(line: &str) -> Option<i64> {
    GPU_LABEL
        .captures(line)
        .and_then(|captures| captures[1].parse().ok())
}

/// Estado de uma GPU extraído do log — mesmos campos do dicionário do Python.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct GpuLogState {
    pub gpu_uuid: Option<String>,
    pub gpu_index: i64,
    pub log_gpu_index: Option<i64>,
    pub hashrate: Option<f64>,
    pub hashrate_at: Option<f64>,
    pub accepted: i64,
    pub rejected: i64,
    pub hw_errors: i64,
    pub average_1m: Option<f64>,
    pub average_15m: Option<f64>,
    pub started_at: Option<f64>,
    pub last_share_at: Option<f64>,
    pub connection: String,
    pub connection_at: Option<f64>,
    pub last_job_at: Option<f64>,
    pub last_log_at: Option<f64>,
    pub paused: bool,
    pub pause_at: Option<f64>,
    pub rate_points: Vec<(f64, f64)>,
    pub isolated_log: bool,
    pub recent_logs: String,
    pub version: Option<String>,
}

impl GpuLogState {
    fn reset_session(&mut self) {
        self.hashrate = None;
        self.hashrate_at = None;
        self.accepted = 0;
        self.rejected = 0;
        self.hw_errors = 0;
        self.last_share_at = None;
        self.average_1m = None;
        self.average_15m = None;
        self.rate_points.clear();
        self.paused = false;
        self.connection = WAITING.into();
        self.connection_at = None;
        self.last_job_at = None;
        self.pause_at = None;
    }
}

fn not_before(current: Option<f64>, stamp: f64) -> bool {
    current.is_none_or(|value| stamp >= value)
}

/// `gpu_telemetry._parse_gpu_log`: seleciona o índice físico, ou a GPU0 de um log
/// comprovadamente isolado. Em log compartilhado, médias e shares sem rótulo não
/// são atribuídos a uma placa, e `Total:` nunca entra no hashrate por GPU.
///
/// Devolve o estado e o carimbo futuro mais próximo que foi ignorado (relógio adiantado).
pub fn parse_gpu_log(
    text: &str,
    gpu_uuid: Option<&str>,
    index: i64,
    now: f64,
    allow_local_gpu0: bool,
    zone: StampZone,
) -> (GpuLogState, Option<f64>) {
    let lines = splitlines(text);
    let labels: HashSet<i64> = lines.iter().filter_map(|line| label_of(line)).collect();
    let target = if labels.contains(&index) {
        Some(index)
    } else if allow_local_gpu0 && labels.len() == 1 && labels.contains(&0) {
        Some(0)
    } else {
        None
    };
    let single_label = labels.len() == 1;
    let recent_start = lines.len().saturating_sub(35);
    let mut state = GpuLogState {
        gpu_uuid: gpu_uuid.map(str::to_string),
        gpu_index: index,
        log_gpu_index: target,
        hashrate: None,
        hashrate_at: None,
        accepted: 0,
        rejected: 0,
        hw_errors: 0,
        average_1m: None,
        average_15m: None,
        started_at: None,
        last_share_at: None,
        connection: WAITING.into(),
        connection_at: None,
        last_job_at: None,
        last_log_at: None,
        paused: false,
        pause_at: None,
        rate_points: Vec::new(),
        isolated_log: labels.len() <= 1,
        recent_logs: lines[recent_start..].join("\n"),
        version: None,
    };
    let mut future: Option<f64> = None;

    for line in &lines {
        let Some(stamp) = log_timestamp(line, zone) else {
            continue;
        };
        if stamp > now + 5.0 {
            future = Some(future.map_or(stamp, |value| value.min(stamp)));
            continue;
        }
        if state.started_at.is_some_and(|started| stamp < started) {
            continue;
        }
        if not_before(state.last_log_at, stamp) {
            state.last_log_at = Some(stamp);
        }
        let label = label_of(line);
        let belongs = label.is_some() && target.is_some() && label == target;

        if let Some((_, version)) = line.split_once("Miner version:") {
            // O SRBMiner pode anexar um novo processo ao mesmo arquivo de log.
            state.reset_session();
            state.started_at = Some(stamp);
            state.version = Some(version.trim().to_string());
        }
        if line.contains("Job received") && not_before(state.last_job_at, stamp) {
            state.last_job_at = Some(stamp);
        }
        if not_before(state.connection_at, stamp) {
            if line.contains("Connected to ") || line.contains("Job received") {
                state.connection = CONNECTED.into();
                state.connection_at = Some(stamp);
            }
            let lower = line.to_lowercase();
            if CONNECTION_LOST.iter().any(|word| lower.contains(word)) {
                state.connection = RECONNECTING.into();
                state.connection_at = Some(stamp);
            }
        }
        if line.contains("share accepted")
            && (belongs || (label.is_none() && single_label && target.is_some()))
            && not_before(state.last_share_at, stamp)
        {
            state.last_share_at = Some(stamp);
        }
        if belongs && not_before(state.pause_at, stamp) {
            if line.contains("turned off") {
                state.paused = true;
                state.pause_at = Some(stamp);
            } else if line.contains("turned on") {
                state.paused = false;
                state.pause_at = Some(stamp);
            }
        }
        if let Some(captures) = RATE.captures(line) {
            let rate_gpu: Option<i64> = captures[1].parse().ok();
            if target.is_some() && rate_gpu == target {
                let rate = numeric_str(&captures[2]).and_then(|value| {
                    unit_multiplier(&captures[3]).and_then(|unit| finite(value * unit))
                });
                if let Some(rate) = rate {
                    // Entrega fora de ordem pode somar pontos ao gráfico, mas não troca um valor mais novo.
                    if now - 900.0 <= stamp && stamp <= now + 5.0 {
                        state.rate_points.push((stamp, rate));
                    }
                    if rate >= 0.0 && not_before(state.hashrate_at, stamp) {
                        state.hashrate = Some(rate);
                        state.hashrate_at = Some(stamp);
                        // O SRBMiner pode retomar depois de "turned off" sem registrar "turned on";
                        // hashrate positivo desta GPU após a pausa prova a retomada.
                        if rate > 0.0 && state.paused && state.pause_at.is_none_or(|at| stamp > at)
                        {
                            state.paused = false;
                            state.pause_at = Some(stamp);
                        }
                        let counters: HashMap<&str, &str> = COUNTERS
                            .captures_iter(&captures[4])
                            .map(|pair| {
                                (
                                    pair.get(1).map_or("", |m| m.as_str()),
                                    pair.get(2).map_or("", |m| m.as_str()),
                                )
                            })
                            .collect();
                        for (code, slot) in [
                            ("A", &mut state.accepted),
                            ("R", &mut state.rejected),
                            ("HW", &mut state.hw_errors),
                        ] {
                            if let Some(value) =
                                counters.get(code).and_then(|text| numeric_str(text))
                            {
                                if value >= 0.0 && value.fract() == 0.0 {
                                    *slot = value as i64;
                                }
                            }
                        }
                    }
                }
            }
        }
        if let Some(captures) = AVERAGE.captures(line) {
            if target.is_some() && (belongs || (label.is_none() && single_label)) {
                if let (Some(first_unit), Some(last_unit)) =
                    (unit_multiplier(&captures[2]), unit_multiplier(&captures[4]))
                {
                    if let Some(first) = numeric_str(&captures[1]) {
                        state.average_1m = finite(first * first_unit);
                    }
                    if let Some(last) = numeric_str(&captures[3]) {
                        state.average_15m = finite(last * last_unit);
                    }
                }
            }
        }
    }

    // `sorted(dict(pontos).items())`: o último valor de cada carimbo vence.
    let mut latest: Vec<(f64, f64)> = Vec::new();
    for (stamp, rate) in state.rate_points.drain(..) {
        match latest.iter_mut().find(|(existing, _)| *existing == stamp) {
            Some(point) => point.1 = rate,
            None => latest.push((stamp, rate)),
        }
    }
    latest.sort_by(|a, b| a.0.total_cmp(&b.0));
    state.rate_points = latest;
    (state, future)
}

/// `miner_log.parse_miner_device_map`: casa IDs do SRBMiner com UUIDs NVIDIA pelo
/// barramento PCI único, nunca pelo nome do modelo.
pub fn parse_miner_device_map(
    text: &str,
    devices: &[Value],
) -> Result<HashMap<String, i64>, String> {
    let mut buses: HashMap<i64, HashSet<i64>> = HashMap::new();
    for line in splitlines(text) {
        let index = DEVICE_INDEX.captures(line);
        let bus = DEVICE_BUS.captures(line);
        if let (Some(index), Some(bus)) = (index, bus) {
            if line.to_lowercase().contains("nvidia") {
                if let (Ok(bus), Ok(index)) =
                    (i64::from_str_radix(&bus[1], 16), index[1].parse::<i64>())
                {
                    buses.entry(bus).or_default().insert(index);
                }
            }
        }
    }
    let mut result = HashMap::new();
    let mut seen = HashSet::new();
    for gpu in devices {
        let pci_text = gpu.get("pci_bus_id").map_or_else(String::new, py_str);
        let Some(pci) = PCI.captures(&pci_text) else {
            return Err(
                "Endereço PCI da NVIDIA ausente; seleção do minerador não confirmada".into(),
            );
        };
        let bus = i64::from_str_radix(&pci[1], 16).map_err(|error| error.to_string())?;
        let candidates = buses.get(&bus).cloned().unwrap_or_default();
        if seen.contains(&bus) || candidates.len() != 1 {
            return Err(
                "Mapeamento PCI/ID do SRBMiner ambíguo ou ausente; mineração não iniciada".into(),
            );
        }
        seen.insert(bus);
        let uuid = gpu
            .get("uuid")
            .map(py_str)
            .ok_or("GPU sem UUID no mapeamento do minerador")?;
        result.insert(uuid, *candidates.iter().next().expect("um candidato"));
    }
    let distinct: HashSet<_> = result.values().collect();
    if distinct.len() != result.len() {
        return Err("Um ID do minerador correspondeu a mais de uma GPU".into());
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stamp_rejects_invalid_dates_and_leap_seconds() {
        assert_eq!(
            parse_stamp("1970-01-01 00:00:10", StampZone::UTC),
            Some(10.0)
        );
        assert_eq!(parse_stamp("2026-99-25 00:00:00", StampZone::UTC), None);
        assert_eq!(parse_stamp("2026-11-03 10:00:60", StampZone::UTC), None);
        assert_eq!(log_timestamp("sem carimbo", StampZone::UTC), None);
        assert_eq!(
            parse_stamp("1970-01-01 01:00:00", StampZone::FixedOffsetSeconds(3600)),
            Some(0.0),
            "hora local com UTC+1"
        );
    }

    #[test]
    fn positive_hashrate_after_pause_clears_it_without_turned_on() {
        // Caso real (SRBMiner 3.8.4, 07/11/2026), espelho de test_gpu_telemetry.py.
        let paused =
            "[1970-01-01 00:00:03] GPU0 turned off - waiting to cool down [temperature > 83]";
        let rate = |value: &str, stamp: &str| {
            format!(
                "[1970-01-01 00:00:{stamp}] GPU0 RTX: {value} TH/s [T:65C P:150W A:10 R:0 HW:0]"
            )
        };
        let parse = |text: String| parse_gpu_log(&text, None, 0, 40.0, true, StampZone::UTC).0;
        let still = parse(format!("{paused}\n{}", rate("0", "10")));
        assert!(still.paused);
        let resumed = parse(format!("{paused}\n{}", rate("75", "30")));
        assert!(!resumed.paused);
        assert_eq!(resumed.pause_at, Some(30.0));
        assert_eq!(resumed.hashrate, Some(75.0));
        let stale = parse(format!("{}\n{paused}", rate("75", "01")));
        assert!(stale.paused);
    }

    #[test]
    fn total_line_never_counts_as_a_gpu() {
        let text = "[1970-01-01 00:00:10] Total: 99.00 TH/s [A:1 R:0 HW:0]\n";
        let (state, _) = parse_gpu_log(text, None, 0, 20.0, true, StampZone::UTC);
        assert_eq!(state.hashrate, None);
    }
}
