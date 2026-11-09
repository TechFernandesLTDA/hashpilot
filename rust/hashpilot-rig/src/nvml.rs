//! Acesso ao NVML do driver NVIDIA (`System32\nvml.dll`), só as funções que o
//! `gpu_defaults.py` usa. ABI: `NVIDIA/go-nvml pkg/nvml/nvml.h`.
//!
//! [`Nvml`] é a interface por GPU: cada método é uma função do NVML, com o mesmo nome
//! no erro. [`Library`] é a implementação real; os testes usam uma falsa. As leituras
//! (`get_*`) não mudam nada; as escritas (`set_*`, `reset_*`) só são chamadas pela
//! restauração dos padrões.
#![allow(unsafe_code)]

use std::fmt;

/// `nvmlClockOffset_v1`: 24 bytes, versão `sizeof | 1 << 24`.
#[repr(C)]
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct ClockOffset {
    pub version: u32,
    pub kind: i32,
    pub pstate: i32,
    pub offset: i32,
    pub minimum: i32,
    pub maximum: i32,
}

pub const CLOCK_OFFSET_VERSION: u32 = std::mem::size_of::<ClockOffset>() as u32 | (1 << 24);

impl ClockOffset {
    pub fn query(kind: i32, pstate: i32) -> Self {
        Self {
            version: CLOCK_OFFSET_VERSION,
            kind,
            pstate,
            ..Self::default()
        }
    }
}

/// `gpu_defaults.NvmlError`: `"<função>: NVML <código>"`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct NvmlError {
    pub name: String,
    pub code: String,
}

impl NvmlError {
    pub fn new(name: &str, code: impl ToString) -> Self {
        Self {
            name: name.into(),
            code: code.to_string(),
        }
    }
}

impl fmt::Display for NvmlError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "{}: NVML {}", self.name, self.code)
    }
}

pub type NvmlResult<T> = Result<T, NvmlError>;

/// Uma GPU aberta no NVML (o handle já obtido pelo UUID).
pub trait Nvml {
    fn get_num_fans(&mut self) -> NvmlResult<u32>;
    fn get_fan_control_policy(&mut self, fan: u32) -> NvmlResult<u32>;
    fn set_default_fan_speed(&mut self, fan: u32) -> NvmlResult<()>;
    /// `nvmlDeviceGetSupportedPerformanceStates`: a lista crua (vazios valem 32).
    fn get_supported_performance_states(&mut self) -> NvmlResult<Vec<i32>>;
    fn get_clock_offsets(&mut self, kind: i32, pstate: i32) -> NvmlResult<ClockOffset>;
    fn set_clock_offsets(&mut self, value: &ClockOffset) -> NvmlResult<()>;
    fn reset_gpu_locked_clocks(&mut self) -> NvmlResult<()>;
    fn reset_memory_locked_clocks(&mut self) -> NvmlResult<()>;
    fn get_power_default_limit(&mut self) -> NvmlResult<u32>;
    fn get_power_limit(&mut self) -> NvmlResult<u32>;
    fn set_power_limit(&mut self, milliwatts: u32) -> NvmlResult<()>;
    /// `nvmlShutdown`.
    fn close(&mut self) -> NvmlResult<()>;
}

#[cfg(windows)]
mod imp {
    use super::{ClockOffset, Nvml, NvmlError, NvmlResult};
    use std::ffi::c_void;
    use windows_sys::Win32::Foundation::HMODULE;
    use windows_sys::Win32::System::LibraryLoader::{GetProcAddress, LoadLibraryW};

    type Handle = *mut c_void;
    /// O ponteiro cru que o `GetProcAddress` devolve.
    type Raw = unsafe extern "system" fn() -> isize;

    pub struct Library {
        module: HMODULE,
        handle: Handle,
        open: bool,
    }

    impl Library {
        fn symbol(&self, name: &str) -> NvmlResult<Raw> {
            let mut text = name.as_bytes().to_vec();
            text.push(0);
            // SAFETY: `module` é um módulo carregado e `text` termina em zero.
            unsafe { GetProcAddress(self.module, text.as_ptr()) }
                .ok_or_else(|| NvmlError::new(name, "API indisponível"))
        }

        fn check(name: &str, code: i32) -> NvmlResult<()> {
            if code == 0 {
                Ok(())
            } else {
                Err(NvmlError::new(name, code))
            }
        }

        /// Carrega o `nvml.dll` do driver, inicializa e abre a GPU pelo UUID.
        pub fn open(uuid: &str) -> Result<Self, String> {
            let windir = std::env::var_os("WINDIR").ok_or_else(|| "'WINDIR'".to_owned())?;
            let path = std::path::Path::new(&windir)
                .join("System32")
                .join("nvml.dll");
            let wide: Vec<u16> = path
                .as_os_str()
                .to_string_lossy()
                .encode_utf16()
                .chain(std::iter::once(0))
                .collect();
            // SAFETY: caminho UTF-16 terminado em zero; o módulo fica carregado enquanto o processo vive.
            let module = unsafe { LoadLibraryW(wide.as_ptr()) };
            if module.is_null() {
                return Err(format!(
                    "Could not find module '{}' (or one of its dependencies)",
                    path.display()
                ));
            }
            let mut library = Self {
                module,
                handle: std::ptr::null_mut(),
                open: false,
            };
            let init = library
                .symbol("nvmlInit_v2")
                .map_err(|error| error.to_string())?;
            // SAFETY: assinatura `nvmlReturn_t nvmlInit_v2(void)` do nvml.h.
            let code =
                unsafe { std::mem::transmute::<Raw, unsafe extern "system" fn() -> i32>(init)() };
            Self::check("nvmlInit_v2", code).map_err(|error| error.to_string())?;
            library.open = true;
            let result = (|| -> NvmlResult<Handle> {
                let by_uuid = library.symbol("nvmlDeviceGetHandleByUUID")?;
                let mut text = uuid.as_bytes().to_vec();
                text.push(0);
                let mut handle: Handle = std::ptr::null_mut();
                // SAFETY: `(const char*, nvmlDevice_t*)`; o texto termina em zero e o ponteiro é válido.
                let code = unsafe {
                    std::mem::transmute::<
                        Raw,
                        unsafe extern "system" fn(*const u8, *mut Handle) -> i32,
                    >(by_uuid)(text.as_ptr(), &mut handle)
                };
                Self::check("nvmlDeviceGetHandleByUUID", code)?;
                Ok(handle)
            })();
            match result {
                Ok(handle) if !handle.is_null() => {
                    library.handle = handle;
                    Ok(library)
                }
                Ok(_) => {
                    let _ = library.close();
                    Err("Driver não confirmou a identidade da GPU".into())
                }
                Err(error) => {
                    let _ = library.close();
                    Err(error.to_string())
                }
            }
        }

        fn read_u32(&self, name: &str) -> NvmlResult<u32> {
            let function = self.symbol(name)?;
            let mut value = 0u32;
            // SAFETY: `(nvmlDevice_t, unsigned int*)`; handle aberto e ponteiro válido.
            let code = unsafe {
                std::mem::transmute::<Raw, unsafe extern "system" fn(Handle, *mut u32) -> i32>(
                    function,
                )(self.handle, &mut value)
            };
            Self::check(name, code).map(|()| value)
        }

        fn call_u32(&self, name: &str, argument: u32) -> NvmlResult<()> {
            let function = self.symbol(name)?;
            // SAFETY: `(nvmlDevice_t, unsigned int)`.
            let code = unsafe {
                std::mem::transmute::<Raw, unsafe extern "system" fn(Handle, u32) -> i32>(function)(
                    self.handle,
                    argument,
                )
            };
            Self::check(name, code)
        }

        fn call_plain(&self, name: &str) -> NvmlResult<()> {
            let function = self.symbol(name)?;
            // SAFETY: `(nvmlDevice_t)`.
            let code = unsafe {
                std::mem::transmute::<Raw, unsafe extern "system" fn(Handle) -> i32>(function)(
                    self.handle,
                )
            };
            Self::check(name, code)
        }

        fn offsets(&self, name: &str, value: &mut ClockOffset) -> NvmlResult<()> {
            let function = self.symbol(name)?;
            // SAFETY: `(nvmlDevice_t, nvmlClockOffset_t*)`; struct `repr(C)` de 24 bytes com a versão certa.
            let code = unsafe {
                std::mem::transmute::<Raw, unsafe extern "system" fn(Handle, *mut ClockOffset) -> i32>(
                    function,
                )(self.handle, value)
            };
            Self::check(name, code)
        }
    }

    impl Nvml for Library {
        fn get_num_fans(&mut self) -> NvmlResult<u32> {
            self.read_u32("nvmlDeviceGetNumFans")
        }
        fn get_fan_control_policy(&mut self, fan: u32) -> NvmlResult<u32> {
            let name = "nvmlDeviceGetFanControlPolicy_v2";
            let function = self.symbol(name)?;
            let mut value = 0u32;
            // SAFETY: `(nvmlDevice_t, unsigned int, nvmlFanControlPolicy_t*)`.
            let code = unsafe {
                std::mem::transmute::<Raw, unsafe extern "system" fn(Handle, u32, *mut u32) -> i32>(
                    function,
                )(self.handle, fan, &mut value)
            };
            Self::check(name, code).map(|()| value)
        }
        fn set_default_fan_speed(&mut self, fan: u32) -> NvmlResult<()> {
            self.call_u32("nvmlDeviceSetDefaultFanSpeed_v2", fan)
        }
        fn get_supported_performance_states(&mut self) -> NvmlResult<Vec<i32>> {
            let name = "nvmlDeviceGetSupportedPerformanceStates";
            let function = self.symbol(name)?;
            // O Python passa `sizeof` (64) para um vetor de 16; aqui o vetor tem 64
            // posições, então nem um driver que use o tamanho em elementos escreve fora.
            let mut values = [32i32; 64];
            // SAFETY: `(nvmlDevice_t, nvmlPstates_t*, unsigned int)`; o vetor tem 64 elementos.
            let code = unsafe {
                std::mem::transmute::<Raw, unsafe extern "system" fn(Handle, *mut i32, u32) -> i32>(
                    function,
                )(self.handle, values.as_mut_ptr(), 64)
            };
            Self::check(name, code).map(|()| values[..16].to_vec())
        }
        fn get_clock_offsets(&mut self, kind: i32, pstate: i32) -> NvmlResult<ClockOffset> {
            let mut value = ClockOffset::query(kind, pstate);
            self.offsets("nvmlDeviceGetClockOffsets", &mut value)
                .map(|()| value)
        }
        fn set_clock_offsets(&mut self, value: &ClockOffset) -> NvmlResult<()> {
            let mut copy = *value;
            self.offsets("nvmlDeviceSetClockOffsets", &mut copy)
        }
        fn reset_gpu_locked_clocks(&mut self) -> NvmlResult<()> {
            self.call_plain("nvmlDeviceResetGpuLockedClocks")
        }
        fn reset_memory_locked_clocks(&mut self) -> NvmlResult<()> {
            self.call_plain("nvmlDeviceResetMemoryLockedClocks")
        }
        fn get_power_default_limit(&mut self) -> NvmlResult<u32> {
            self.read_u32("nvmlDeviceGetPowerManagementDefaultLimit")
        }
        fn get_power_limit(&mut self) -> NvmlResult<u32> {
            self.read_u32("nvmlDeviceGetPowerManagementLimit")
        }
        fn set_power_limit(&mut self, milliwatts: u32) -> NvmlResult<()> {
            self.call_u32("nvmlDeviceSetPowerManagementLimit", milliwatts)
        }
        fn close(&mut self) -> NvmlResult<()> {
            if !self.open {
                return Ok(());
            }
            self.open = false;
            let function = self.symbol("nvmlShutdown")?;
            // SAFETY: `nvmlReturn_t nvmlShutdown(void)`, pareado com o `nvmlInit_v2`.
            let code = unsafe {
                std::mem::transmute::<Raw, unsafe extern "system" fn() -> i32>(function)()
            };
            Self::check("nvmlShutdown", code)
        }
    }

    impl Drop for Library {
        fn drop(&mut self) {
            let _ = self.close();
        }
    }
}

#[cfg(windows)]
pub use imp::Library;

/// Fora do Windows não há `nvml.dll`: abrir sempre falha.
#[cfg(not(windows))]
pub struct Library;

#[cfg(not(windows))]
impl Library {
    pub fn open(_uuid: &str) -> Result<Self, String> {
        Err("NVML do Windows indisponível nesta plataforma".into())
    }
}
