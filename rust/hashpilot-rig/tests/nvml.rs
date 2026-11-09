//! Paridade da restauração pelo NVML com o `gpu_defaults.NvidiaDefaults.restore()`
//! (golden `rig_support`, tipo `nvml`). O Python real roda com um driver falso que só
//! troca o `call`; este teste usa um falso igual atrás da interface `Nvml` e compara o
//! relatório e a sequência completa de chamadas ao NVML, leituras incluídas.

use std::collections::BTreeMap;
use std::path::Path;

use hashpilot_rig::gpu_defaults::restore;
use hashpilot_rig::nvml::{ClockOffset, Nvml, NvmlError, NvmlResult};
use serde_json::{Value, json};

struct Fake {
    config: Value,
    calls: Vec<String>,
    fans: u32,
    policies: BTreeMap<u32, u32>,
    offsets: BTreeMap<(i32, i32), i32>,
    limits: (i32, i32),
    default: u32,
    power: u32,
}

impl Fake {
    fn new(config: &Value) -> Self {
        let fans = config.get("fans").and_then(Value::as_u64).unwrap_or(2) as u32;
        let offsets = match config.get("offsets").and_then(Value::as_object) {
            Some(map) => map
                .iter()
                .map(|(key, value)| {
                    let (domain, state) = key.split_once(',').unwrap();
                    (
                        (domain.parse().unwrap(), state.parse().unwrap()),
                        value.as_i64().unwrap() as i32,
                    )
                })
                .collect(),
            None => [0, 2, 8]
                .iter()
                .flat_map(|state| [((0, *state), 150), ((2, *state), 700)])
                .collect(),
        };
        let limits = config
            .get("limits")
            .and_then(Value::as_array)
            .map_or((-1000, 2000), |pair| {
                (
                    pair[0].as_i64().unwrap() as i32,
                    pair[1].as_i64().unwrap() as i32,
                )
            });
        Self {
            config: config.clone(),
            calls: Vec::new(),
            fans,
            policies: (0..fans).map(|index| (index, 1)).collect(),
            offsets,
            limits,
            default: config
                .get("default")
                .and_then(Value::as_u64)
                .unwrap_or(240_000) as u32,
            power: config
                .get("power")
                .and_then(Value::as_u64)
                .unwrap_or(170_000) as u32,
        }
    }

    fn listed(&self, key: &str, name: &str) -> bool {
        self.config
            .get(key)
            .and_then(Value::as_array)
            .is_some_and(|names| names.iter().any(|item| item == name))
    }

    /// Registra a chamada e aplica `fail`/`missing`; devolve se a escrita deve ser ignorada.
    fn enter(&mut self, name: &str) -> NvmlResult<bool> {
        self.calls.push(name.into());
        if self.listed("fail", name) {
            return Err(NvmlError::new(name, 4));
        }
        if self.listed("missing", name) {
            return Err(NvmlError::new(name, "API indisponível"));
        }
        Ok(self.listed("ignore", name))
    }
}

impl Nvml for Fake {
    fn get_num_fans(&mut self) -> NvmlResult<u32> {
        self.enter("nvmlDeviceGetNumFans")?;
        Ok(self.fans)
    }
    fn get_fan_control_policy(&mut self, fan: u32) -> NvmlResult<u32> {
        self.enter("nvmlDeviceGetFanControlPolicy_v2")?;
        Ok(self
            .config
            .get("stuck_policy")
            .and_then(Value::as_u64)
            .map_or_else(
                || self.policies.get(&fan).copied().unwrap_or(1),
                |stuck| stuck as u32,
            ))
    }
    fn set_default_fan_speed(&mut self, fan: u32) -> NvmlResult<()> {
        if !self.enter("nvmlDeviceSetDefaultFanSpeed_v2")? {
            self.policies.insert(fan, 0);
        }
        Ok(())
    }
    fn get_supported_performance_states(&mut self) -> NvmlResult<Vec<i32>> {
        self.enter("nvmlDeviceGetSupportedPerformanceStates")?;
        let mut values = vec![32; 16];
        let reported = self
            .config
            .get("states")
            .and_then(Value::as_array)
            .map_or_else(
                || vec![0, 2, 8],
                |items| {
                    items
                        .iter()
                        .map(|item| item.as_i64().unwrap() as i32)
                        .collect()
                },
            );
        for (position, value) in reported.into_iter().take(16).enumerate() {
            values[position] = value;
        }
        Ok(values)
    }
    fn get_clock_offsets(&mut self, kind: i32, pstate: i32) -> NvmlResult<ClockOffset> {
        self.enter("nvmlDeviceGetClockOffsets")?;
        let mut value = ClockOffset::query(kind, pstate);
        value.offset = self.offsets.get(&(kind, pstate)).copied().unwrap_or(0);
        (value.minimum, value.maximum) = self.limits;
        Ok(value)
    }
    fn set_clock_offsets(&mut self, value: &ClockOffset) -> NvmlResult<()> {
        assert_eq!(value.version, 0x0100_0018);
        if !self.enter("nvmlDeviceSetClockOffsets")? {
            self.offsets
                .insert((value.kind, value.pstate), value.offset);
        }
        Ok(())
    }
    fn reset_gpu_locked_clocks(&mut self) -> NvmlResult<()> {
        self.enter("nvmlDeviceResetGpuLockedClocks").map(|_| ())
    }
    fn reset_memory_locked_clocks(&mut self) -> NvmlResult<()> {
        self.enter("nvmlDeviceResetMemoryLockedClocks").map(|_| ())
    }
    fn get_power_default_limit(&mut self) -> NvmlResult<u32> {
        self.enter("nvmlDeviceGetPowerManagementDefaultLimit")?;
        Ok(self.default)
    }
    fn get_power_limit(&mut self) -> NvmlResult<u32> {
        self.enter("nvmlDeviceGetPowerManagementLimit")?;
        Ok(self.power)
    }
    fn set_power_limit(&mut self, milliwatts: u32) -> NvmlResult<()> {
        if !self.enter("nvmlDeviceSetPowerManagementLimit")? {
            self.power = milliwatts;
        }
        Ok(())
    }
    fn close(&mut self) -> NvmlResult<()> {
        Ok(())
    }
}

/// Igualdade com tipo (`240` ≠ `240.0`) e com a ordem das chaves.
fn strict(left: &Value, right: &Value) -> bool {
    match (left, right) {
        (Value::Number(a), Value::Number(b)) => {
            a.is_f64() == b.is_f64() && a.as_f64() == b.as_f64()
        }
        (Value::Array(a), Value::Array(b)) => {
            a.len() == b.len() && a.iter().zip(b).all(|(x, y)| strict(x, y))
        }
        (Value::Object(a), Value::Object(b)) => {
            a.len() == b.len()
                && a.iter()
                    .zip(b)
                    .all(|((ka, va), (kb, vb))| ka == kb && strict(va, vb))
        }
        _ => left == right,
    }
}

#[test]
fn restore_matches_python_report_and_nvml_call_sequence() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../hashpilot-core/tests/golden/rig_support.json");
    let golden: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
    let cases: Vec<&Value> = golden["cases"]
        .as_array()
        .unwrap()
        .iter()
        .filter(|case| case["kind"] == "nvml")
        .collect();
    assert!(cases.len() >= 25, "{}", cases.len());
    for case in cases {
        let name = &case["config"]["name"];
        let mut fake = Fake::new(&case["config"]);
        let report = restore(&mut fake, 0.0);
        assert_eq!(json!(fake.calls), case["calls"], "{name}: chamadas");
        assert!(
            strict(&report, &case["report"]),
            "{name}:\nrust={report}\npython={}",
            case["report"]
        );
    }
}

#[test]
fn clock_offset_struct_has_the_nvidia_abi_size() {
    assert_eq!(std::mem::size_of::<ClockOffset>(), 24);
    assert_eq!(hashpilot_rig::nvml::CLOCK_OFFSET_VERSION, 0x0100_0018);
}

#[test]
fn restore_defaults_holds_the_lease_until_the_driver_is_closed() {
    use hashpilot_rig::gpu_defaults::restore_defaults;
    let folder = std::env::temp_dir().join(format!("hashpilot-nvml-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&folder);
    let order = std::rc::Rc::new(std::cell::RefCell::new(Vec::new()));
    struct Closing(
        Fake,
        std::rc::Rc<std::cell::RefCell<Vec<String>>>,
        std::path::PathBuf,
    );
    impl Nvml for Closing {
        fn get_num_fans(&mut self) -> NvmlResult<u32> {
            // A posse precisa existir enquanto o driver é usado.
            assert!(self.2.join("gpu-control-owner.json").exists());
            self.1.borrow_mut().push("restore".into());
            self.0.get_num_fans()
        }
        fn get_fan_control_policy(&mut self, fan: u32) -> NvmlResult<u32> {
            self.0.get_fan_control_policy(fan)
        }
        fn set_default_fan_speed(&mut self, fan: u32) -> NvmlResult<()> {
            self.0.set_default_fan_speed(fan)
        }
        fn get_supported_performance_states(&mut self) -> NvmlResult<Vec<i32>> {
            self.0.get_supported_performance_states()
        }
        fn get_clock_offsets(&mut self, kind: i32, pstate: i32) -> NvmlResult<ClockOffset> {
            self.0.get_clock_offsets(kind, pstate)
        }
        fn set_clock_offsets(&mut self, value: &ClockOffset) -> NvmlResult<()> {
            self.0.set_clock_offsets(value)
        }
        fn reset_gpu_locked_clocks(&mut self) -> NvmlResult<()> {
            self.0.reset_gpu_locked_clocks()
        }
        fn reset_memory_locked_clocks(&mut self) -> NvmlResult<()> {
            self.0.reset_memory_locked_clocks()
        }
        fn get_power_default_limit(&mut self) -> NvmlResult<u32> {
            self.0.get_power_default_limit()
        }
        fn get_power_limit(&mut self) -> NvmlResult<u32> {
            self.0.get_power_limit()
        }
        fn set_power_limit(&mut self, milliwatts: u32) -> NvmlResult<()> {
            self.0.set_power_limit(milliwatts)
        }
        fn close(&mut self) -> NvmlResult<()> {
            assert!(
                self.2.join("gpu-control-owner.json").exists(),
                "driver fechado depois de soltar a posse"
            );
            self.1.borrow_mut().push("shutdown".into());
            Ok(())
        }
    }
    let log = order.clone();
    let dir = folder.clone();
    let mut factory = move |uuid: &str| -> Result<Box<dyn Nvml>, String> {
        log.borrow_mut().push(format!("open {uuid}"));
        Ok(Box::new(Closing(
            Fake::new(&json!({})),
            log.clone(),
            dir.clone(),
        )))
    };
    let report = restore_defaults(&json!({"uuid": "GPU-qa"}), &folder, &mut factory).unwrap();
    assert_eq!(report["state"], "restored");
    assert_eq!(*order.borrow(), vec!["open GPU-qa", "restore", "shutdown"]);
    assert!(
        !folder.join("gpu-control-owner.json").exists(),
        "posse liberada no fim"
    );
    let _ = std::fs::remove_dir_all(&folder);
}

#[cfg(windows)]
#[test]
fn lease_refusal_never_opens_the_driver() {
    use hashpilot_rig::gpu_defaults::{ControlLease, restore_defaults};
    let folder = std::env::temp_dir().join(format!("hashpilot-nvml-recusa-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&folder);
    let mut holder = ControlLease::new(&folder).unwrap();
    holder.acquire("GPU-qa").unwrap();
    let mut opened = false;
    let mut factory = |_: &str| -> Result<Box<dyn Nvml>, String> {
        opened = true;
        Err("não deveria abrir".into())
    };
    let report = restore_defaults(&json!({"uuid": "GPU-qa"}), &folder, &mut factory).unwrap();
    assert!(!opened);
    assert_eq!(report["state"], "failed");
    assert_eq!(report["error"], "Outra sessão detém o controle da GPU");
    assert_eq!(report["failed_components"], json!(["driver_or_ownership"]));
    holder.close();
    let _ = std::fs::remove_dir_all(&folder);
}
