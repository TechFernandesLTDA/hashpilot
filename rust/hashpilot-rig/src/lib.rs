//! Serviço do rig do HashPilot (fase 4 do port), etapa 4a: processos.
//!
//! - [`argv`]: `CommandLineToArgvW` em Rust puro, provado contra a API real.
//! - [`process`]: `process_backend.ProcessBackend`, com a mesma identidade do Python
//!   (PID, horário de criação em FILETIME, executável), para reconhecer processos que
//!   o serviço Python iniciou.
//! - [`win32`]: a única parte com `unsafe`, isolada e comentada.
//!
//! Nada aqui inicia mineração sozinho: `start` só cria o processo que o chamador pedir.

pub mod argv;
pub mod continuity;
pub mod fsutil;
pub mod health;
pub mod process;
pub mod reference;
pub mod successor;
pub mod win32;

pub use argv::windows_argv;
pub use process::{ProcessBackend, check_gpu_free, normalized_path, selected_gpus};
