//! Retenção em bancos temporários: redução da telemetria antiga a um minuto e limpeza
//! dos `hold` velhos do agente. Nenhum banco real é aberto.

use std::path::{Path, PathBuf};
use std::time::Duration;

use hashpilot_history::retention::{self, Policy};
use hashpilot_history::{HistoryReader, schema};
use rusqlite::Connection;
use serde_json::{Value, json};

const DAY: f64 = 86_400.0;
/// "Agora" dos testes; tudo antes de `NOW - 30 dias` é velho.
const NOW: f64 = 1_800_000_000.0;
const GPU: &str = "GPU-aaaa";

fn scratch(label: &str) -> PathBuf {
    let directory = std::env::temp_dir().join(format!(
        "hp-retention-{label}-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    std::fs::create_dir_all(&directory).unwrap();
    directory
}

fn policy() -> Policy {
    Policy {
        pause: Duration::ZERO,
        ..Policy::default()
    }
}

fn history(folder: &Path) -> (PathBuf, Connection) {
    let path = folder.join("history.sqlite3");
    let connection = Connection::open(&path).unwrap();
    schema::ensure(&connection).unwrap();
    // Só acelera o preparo do teste (milhares de inserções); a retenção usa a própria conexão.
    connection.execute_batch("PRAGMA synchronous=OFF").unwrap();
    (path, connection)
}

fn gpu_row(connection: &Connection, stamp: f64, temperature: Option<f64>, hashrate: (f64, f64)) {
    let gpu = match temperature {
        Some(value) => {
            json!({"uuid": GPU, "index": 0, "name": "RTX 3070", "temperature.gpu": value,
                               "power.draw": value * 2.0, "pstate": "P2", "sampled_at": stamp})
        }
        None => json!({"uuid": GPU, "index": 0, "name": "RTX 3070", "temperature.gpu": null,
                        "sampled_at": stamp, "error": "sem sensor"}),
    };
    let miner =
        json!({"hashrate": hashrate.1, "hashrate_at": hashrate.0, "accepted": stamp as i64 % 1000});
    connection
        .execute(
            "INSERT INTO gpu_telemetry (gpu_uuid,timestamp,wallet,session,source,gpu_json,miner_json,miner_running,enabled,errors_json,log_path) \
             VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            rusqlite::params![GPU, stamp, "prl1w", "s1", "live", gpu.to_string(), miner.to_string(), 1, 1, "{}", "log"],
        )
        .unwrap();
}

fn legacy_row(connection: &Connection, stamp: f64, source: &str) {
    connection
        .execute(
            "INSERT INTO telemetry VALUES(?,?,?,?,?,?,?,?,?)",
            rusqlite::params![
                "prl1w",
                stamp,
                "s1",
                source,
                json!({"temperature.gpu": 60.0}).to_string(),
                json!({"hashrate": 70.0, "hashrate_at": stamp}).to_string(),
                1,
                "{}",
                "log"
            ],
        )
        .unwrap();
}

fn count(connection: &Connection, sql: &str) -> i64 {
    connection.query_row(sql, [], |row| row.get(0)).unwrap()
}

#[test]
fn telemetria_velha_vira_uma_media_por_minuto_e_a_recente_fica_intacta() {
    let folder = scratch("media");
    let (path, connection) = history(&folder);
    // Minuto velho alinhado: 30 amostras a cada 2 s, uma sem sensor, duas medições de hashrate.
    let old = ((NOW - 40.0 * DAY) / 60.0).floor() * 60.0;
    for step in 0..30 {
        let stamp = old + f64::from(step) * 2.0;
        let temperature = if step == 7 {
            None
        } else {
            Some(60.0 + f64::from(step % 3))
        };
        let rate = if step < 15 {
            (old - 5.0, 70.0)
        } else {
            (old + 29.0, 74.0)
        };
        gpu_row(&connection, stamp, temperature, rate);
    }
    // Minuto velho com uma amostra só: não muda.
    gpu_row(&connection, old + 600.0, Some(50.0), (old + 590.0, 60.0));
    // Recente (10 dias): nada muda.
    let recent = NOW - 10.0 * DAY;
    for step in 0..5 {
        gpu_row(
            &connection,
            recent + f64::from(step) * 2.0,
            Some(70.0),
            (recent, 80.0),
        );
    }
    let before_recent: Vec<String> = connection
        .prepare(
            "SELECT gpu_json||miner_json FROM gpu_telemetry WHERE timestamp>=? ORDER BY timestamp",
        )
        .unwrap()
        .query_map([recent], |row| row.get(0))
        .unwrap()
        .map(Result::unwrap)
        .collect();

    // Sem apply: só conta, nada é escrito.
    let preview = retention::compact_telemetry(&path, NOW, &policy(), false).unwrap();
    assert_eq!(preview["tables"]["gpu_telemetry"]["minutes_reduced"], 1);
    assert_eq!(preview["tables"]["gpu_telemetry"]["rows_removed"], 29);
    assert_eq!(count(&connection, "SELECT COUNT(*) FROM gpu_telemetry"), 36);

    let report = retention::compact_telemetry(&path, NOW, &policy(), true).unwrap();
    assert_eq!(report["tables"]["gpu_telemetry"]["rows_removed"], 29);
    assert_eq!(count(&connection, "SELECT COUNT(*) FROM gpu_telemetry"), 7);
    let (stamp, gpu, miner): (f64, String, String) = connection
        .query_row(
            "SELECT timestamp,gpu_json,miner_json FROM gpu_telemetry WHERE timestamp<? ORDER BY timestamp LIMIT 1",
            [old + 60.0],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
        )
        .unwrap();
    // Fica o carimbo da última amostra do minuto (chave primária real, sem colisão).
    assert_eq!(stamp, old + 58.0);
    let gpu: Value = serde_json::from_str(&gpu).unwrap();
    let miner: Value = serde_json::from_str(&miner).unwrap();
    // Média só dos 29 sensores presentes (a amostra sem sensor não vira zero).
    let temperatures: Vec<f64> = (0..30)
        .filter(|step| *step != 7)
        .map(|step| 60.0 + f64::from(step % 3))
        .collect();
    let expected = temperatures.iter().sum::<f64>() / temperatures.len() as f64;
    assert!((gpu["temperature.gpu"].as_f64().unwrap() - expected).abs() < 1e-9);
    assert!((gpu["power.draw"].as_f64().unwrap() - expected * 2.0).abs() < 1e-9);
    assert_eq!(
        gpu["pstate"], "P2",
        "campo não numérico vem da última amostra"
    );
    // Hashrate: média das duas medições independentes, carimbo da mais nova.
    assert_eq!(miner["hashrate"], 72.0);
    assert_eq!(miner["hashrate_at"], old + 29.0);
    assert_eq!(miner["retention"]["samples"], 30);
    assert_eq!(miner["retention"]["hashrate_samples"], 2);
    assert_eq!(miner["retention"]["from"], old);
    let after_recent: Vec<String> = connection
        .prepare(
            "SELECT gpu_json||miner_json FROM gpu_telemetry WHERE timestamp>=? ORDER BY timestamp",
        )
        .unwrap()
        .query_map([recent], |row| row.get(0))
        .unwrap()
        .map(Result::unwrap)
        .collect();
    assert_eq!(before_recent, after_recent);

    // Idempotente: rodar de novo não muda nada.
    let again = retention::compact_telemetry(&path, NOW, &policy(), true).unwrap();
    assert_eq!(again["tables"]["gpu_telemetry"]["rows_removed"], 0);
    assert_eq!(count(&connection, "SELECT COUNT(*) FROM gpu_telemetry"), 7);

    // O leitor continua entendendo a linha reduzida.
    drop(connection);
    let restored = HistoryReader::open(&path)
        .unwrap()
        .restore_gpu(GPU, 0.0, "prl1w")
        .unwrap();
    assert_eq!(restored["count"], 7);
    let _ = std::fs::remove_dir_all(&folder);
}

#[test]
fn tabela_legada_separa_origens_e_banco_sem_dados_velhos_nao_muda() {
    let folder = scratch("legada");
    let (path, connection) = history(&folder);
    let old = ((NOW - 31.0 * DAY) / 60.0).floor() * 60.0;
    for step in 0..10 {
        legacy_row(&connection, old + f64::from(step) * 2.0, "live");
    }
    legacy_row(&connection, old + 30.5, "imported_miner_log");
    legacy_row(&connection, old + 31.5, "imported_miner_log");
    let report = retention::compact_telemetry(&path, NOW, &policy(), true).unwrap();
    assert_eq!(report["tables"]["telemetry"]["minutes_reduced"], 2);
    let sources: Vec<(String, i64)> = connection
        .prepare("SELECT source,COUNT(*) FROM telemetry GROUP BY source ORDER BY source")
        .unwrap()
        .query_map([], |row| Ok((row.get(0)?, row.get(1)?)))
        .unwrap()
        .map(Result::unwrap)
        .collect();
    assert_eq!(
        sources,
        [("imported_miner_log".into(), 1), ("live".into(), 1)]
    );

    // Hoje (dados com menos de 30 dias) a retenção não apaga nada.
    let fresh = scratch("fresca");
    let (fresh_path, fresh_connection) = history(&fresh);
    for step in 0..50 {
        gpu_row(
            &fresh_connection,
            NOW - 17.0 * DAY + f64::from(step) * 2.0,
            Some(70.0),
            (NOW, 70.0),
        );
    }
    let report = retention::compact_telemetry(&fresh_path, NOW, &policy(), true).unwrap();
    assert_eq!(report["tables"]["gpu_telemetry"]["rows_removed"], 0);
    assert_eq!(
        count(&fresh_connection, "SELECT COUNT(*) FROM gpu_telemetry"),
        50
    );
    let _ = std::fs::remove_dir_all(&folder);
    let _ = std::fs::remove_dir_all(&fresh);
}

#[test]
fn banco_ausente_ou_de_versao_futura_nao_e_tocado() {
    let folder = scratch("ausente");
    let missing = folder.join("history.sqlite3");
    let report = retention::compact_telemetry(&missing, NOW, &policy(), true).unwrap();
    assert_eq!(report["exists"], false);
    assert!(!missing.exists(), "a retenção nunca cria o banco");
    let report =
        retention::prune_decisions(&folder.join("gpu-agent.sqlite3"), NOW, &policy(), true)
            .unwrap();
    assert_eq!(report["exists"], false);

    let connection = Connection::open(&missing).unwrap();
    connection.execute_batch("PRAGMA user_version=4").unwrap();
    drop(connection);
    let error = retention::compact_telemetry(&missing, NOW, &policy(), true).unwrap_err();
    assert!(error.contains("versão futura"), "{error}");
    let _ = std::fs::remove_dir_all(&folder);
}

/// O mesmo DDL do `gpu_agent.py` para `decisions`.
fn agent(folder: &Path) -> (PathBuf, Connection) {
    let path = folder.join("gpu-agent.sqlite3");
    let connection = Connection::open(&path).unwrap();
    connection
        .execute_batch(
            "PRAGMA journal_mode=WAL;
             PRAGMA synchronous=OFF;
             CREATE TABLE IF NOT EXISTS decisions(id INTEGER PRIMARY KEY,gpu_uuid TEXT NOT NULL,stamp REAL NOT NULL,
               session TEXT,payload_json TEXT NOT NULL);",
        )
        .unwrap();
    (path, connection)
}

fn decision(connection: &Connection, gpu: &str, stamp: f64, payload: &Value) {
    connection
        .execute(
            "INSERT INTO decisions(gpu_uuid,stamp,session,payload_json) VALUES(?,?,?,?)",
            rusqlite::params![gpu, stamp, Value::Null.to_string(), payload.to_string()],
        )
        .unwrap();
}

fn hold(reason: &str) -> Value {
    json!({"action": "hold", "clock_mhz": null, "power_limit_w": null, "reason": reason,
           "source": "adaptive", "pause_required": false, "writes_hardware": false})
}

#[test]
fn so_hold_velho_sem_escrita_e_apagado_e_a_ultima_decisao_fica() {
    let folder = scratch("decisoes");
    let (path, connection) = agent(&folder);
    let old = NOW - 40.0 * DAY;
    for step in 0..5000 {
        decision(
            &connection,
            GPU,
            old + f64::from(step) * 2.0,
            &hold("Validando o ajuste anterior"),
        );
    }
    let clock_down = json!({"action": "clock_down", "clock_mhz": 1635, "reason": "quente",
                             "source": "adaptive", "pause_required": false, "writes_hardware": false});
    decision(&connection, GPU, old + 10_001.0, &clock_down);
    let emergency = json!({"action": "hold", "blocked": "sem degrau", "reason": "Emergência",
                            "source": "emergency", "pause_required": true, "writes_hardware": false});
    decision(&connection, GPU, old + 10_002.0, &emergency);
    let mut written = hold("escreveu");
    written["writes_hardware"] = json!(true);
    decision(&connection, GPU, old + 10_003.0, &written);
    connection
        .execute(
            "INSERT INTO decisions(gpu_uuid,stamp,session,payload_json) VALUES(?,?,?,?)",
            rusqlite::params![GPU, old + 10_004.0, "s", "não é JSON"],
        )
        .unwrap();
    // Outra GPU cuja última decisão é um hold velho: essa linha fica.
    decision(&connection, "GPU-bbbb", old, &hold("Sensor ausente"));
    // Recentes: nada muda.
    for step in 0..10 {
        decision(
            &connection,
            GPU,
            NOW - DAY + f64::from(step),
            &hold("Validando o ajuste anterior"),
        );
    }
    let total = count(&connection, "SELECT COUNT(*) FROM decisions");
    assert_eq!(total, 5000 + 4 + 1 + 10);

    let preview = retention::prune_decisions(&path, NOW, &policy(), false).unwrap();
    assert_eq!(preview["decisions_removed"], 5000);
    assert_eq!(count(&connection, "SELECT COUNT(*) FROM decisions"), total);

    let small_chunks = Policy {
        decision_chunk: 700,
        ..policy()
    };
    let report = retention::prune_decisions(&path, NOW, &small_chunks, true).unwrap();
    assert_eq!(report["decisions_removed"], 5000);
    let left: Vec<String> = connection
        .prepare("SELECT payload_json FROM decisions WHERE stamp<? ORDER BY id")
        .unwrap()
        .query_map([NOW - 30.0 * DAY], |row| row.get(0))
        .unwrap()
        .map(Result::unwrap)
        .collect();
    assert_eq!(left.len(), 5);
    assert!(left[0].contains("clock_down"));
    assert!(left[1].contains("emergency"));
    assert!(left[2].contains("\"writes_hardware\":true"));
    assert_eq!(left[3], "não é JSON");
    assert!(left[4].contains("Sensor ausente"));
    assert_eq!(
        count(
            &connection,
            "SELECT COUNT(*) FROM decisions WHERE stamp>=1799000000"
        ),
        10
    );
    let again = retention::prune_decisions(&path, NOW, &policy(), true).unwrap();
    assert_eq!(again["decisions_removed"], 0);
    let _ = std::fs::remove_dir_all(&folder);
}

#[test]
fn run_encontra_os_bancos_do_agente_e_junta_os_relatorios() {
    let folder = scratch("run");
    let logs = folder.join("logs");
    std::fs::create_dir_all(logs.join("gpus").join(GPU)).unwrap();
    std::fs::create_dir_all(logs.join("gpus").join("outra-pasta")).unwrap();
    let (_, global) = agent(&folder);
    let (_, per_gpu) = agent(&logs.join("gpus").join(GPU));
    for connection in [&global, &per_gpu] {
        decision(connection, GPU, NOW - 40.0 * DAY, &hold("x"));
        decision(connection, GPU, NOW - 39.0 * DAY, &hold("x"));
    }
    let agents = retention::agent_databases(&folder, &logs);
    assert_eq!(
        agents,
        [
            folder.join("gpu-agent.sqlite3"),
            logs.join("gpus").join(GPU).join("gpu-agent.sqlite3")
        ]
    );
    let report = retention::run(
        &folder.join("history.sqlite3"),
        &agents,
        NOW,
        &policy(),
        true,
    );
    assert_eq!(report["errors"], json!([]));
    assert_eq!(report["history"]["exists"], false);
    assert_eq!(report["agents"][0]["decisions_removed"], 1);
    assert_eq!(report["agents"][1]["decisions_removed"], 1);
    let _ = std::fs::remove_dir_all(&folder);
}
