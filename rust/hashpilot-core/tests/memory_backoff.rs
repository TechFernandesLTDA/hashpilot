//! Espaçamento crescente das reexplorações de memória: melhoria só do Rust, sem golden do
//! Python (o Python reexplora a cada 6 h, sempre; os goldens rodam com ela desligada).

use hashpilot_core::thermal_target::{MEMORY_REEXPLORE_MAX_S, MEMORY_REEXPLORE_S, MemoryExplorer};

const HOUR: f64 = 3600.0;

/// Roda o explorador com passos de 10 s e devolve os instantes em que ele passou para `probe`
/// (o candidato que não é o melhor), um por rodada. `rate` dá o hashrate de cada clock.
fn probes(
    explorer: &mut MemoryExplorer,
    until: f64,
    probe: f64,
    rate: &dyn Fn(f64, f64) -> f64,
) -> Vec<f64> {
    let mut seen = Vec::new();
    let mut now = 0.0;
    while now < until {
        let current = explorer.current_mhz.unwrap_or(810.0);
        let watts = if current > 1000.0 { 176.0 } else { 166.0 };
        if explorer.step(
            now,
            Some(74.0),
            Some(rate(current, now)),
            Some(0.0),
            Some(current),
            Some(watts),
        ) == Some(probe)
        {
            seen.push(now);
        }
        now += 10.0;
    }
    seen
}

fn gaps(times: &[f64]) -> Vec<f64> {
    times
        .windows(2)
        .map(|pair| ((pair[1] - pair[0]) / HOUR).round())
        .collect()
}

#[test]
fn sem_espacamento_reexplora_a_cada_6_horas() {
    let mut explorer = MemoryExplorer::new(&[6801.0, 810.0], 76.0);
    explorer.best_mhz = Some(810.0);
    let rate = |clock: f64, _: f64| if clock > 1000.0 { 77.4 } else { 78.5 };
    let times = probes(&mut explorer, 40.0 * HOUR, 6801.0, &rate);
    assert_eq!(gaps(&times), vec![6.0; gaps(&times).len()]);
    assert!(times.len() >= 6);
    assert_eq!(explorer.reexplore_after_s, MEMORY_REEXPLORE_S);
}

#[test]
fn mesmo_melhor_dobra_o_intervalo_ate_72_horas() {
    let mut explorer = MemoryExplorer::new(&[6801.0, 810.0], 76.0);
    explorer.best_mhz = Some(810.0);
    explorer.reexplore_backoff = true;
    let rate = |clock: f64, _: f64| if clock > 1000.0 { 77.4 } else { 78.5 };
    let times = probes(&mut explorer, 300.0 * HOUR, 6801.0, &rate);
    assert_eq!(gaps(&times), vec![12.0, 24.0, 48.0, 72.0, 72.0]);
    assert_eq!(explorer.best_mhz, Some(810.0));
    assert_eq!(explorer.reexplore_after_s, MEMORY_REEXPLORE_MAX_S);
}

#[test]
fn melhor_novo_volta_a_6_horas() {
    let mut explorer = MemoryExplorer::new(&[6801.0, 810.0], 76.0);
    explorer.best_mhz = Some(810.0);
    explorer.reexplore_backoff = true;
    // Depois de 30 h a memória alta passa a render mais: a rodada seguinte troca o melhor.
    let rate = |clock: f64, now: f64| match (clock > 1000.0, now > 30.0 * HOUR) {
        (true, true) => 80.0,
        (true, false) => 77.4,
        _ => 78.5,
    };
    let mut now = 0.0;
    let mut changed_at = None;
    while now < 60.0 * HOUR {
        let current = explorer.current_mhz.unwrap_or(810.0);
        explorer.step(
            now,
            Some(74.0),
            Some(rate(current, now)),
            Some(0.0),
            Some(current),
            None,
        );
        if changed_at.is_none() && explorer.best_mhz == Some(6801.0) && explorer.queue.is_empty() {
            changed_at = Some(now);
            // A troca do melhor zera o espaçamento.
            assert_eq!(explorer.reexplore_after_s, MEMORY_REEXPLORE_S);
        }
        now += 10.0;
    }
    assert!(
        changed_at.is_some(),
        "a rodada depois de 30 h devia escolher 6801 MHz"
    );
    // Rodadas de 6801 confirmando 6801 voltam a espaçar.
    assert!(explorer.reexplore_after_s > MEMORY_REEXPLORE_S);
}
