//! Única parte com `unsafe` do port: as chamadas Win32 que dão a identidade de um
//! processo (`OpenProcess`, `GetExitCodeProcess`, `GetProcessTimes`,
//! `QueryFullProcessImageNameW`) e o encerramento (`TerminateProcess`).
//!
//! Cada função abre o handle, usa e fecha (`Handle` fecha no `Drop`). Os ponteiros
//! passados apontam para variáveis locais vivas durante a chamada. Fora do Windows as
//! funções existem e não encontram processo nenhum, para o workspace compilar.
#![allow(unsafe_code)]

/// Horário de criação (FILETIME, 100 ns desde 1601) e executável de um processo vivo.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Metadata {
    pub created_at: u64,
    pub executable: String,
}

/// `PROCESS_QUERY_LIMITED_INFORMATION`, o mesmo `0x1000` do Python.
pub const QUERY: u32 = 0x1000;
/// `PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE` (`0x1001`).
pub const QUERY_AND_TERMINATE: u32 = 0x1001;

#[cfg(windows)]
mod imp {
    use super::Metadata;
    use windows_sys::Win32::Foundation::{CloseHandle, FILETIME, HANDLE};
    use windows_sys::Win32::System::Threading::{
        GetExitCodeProcess, GetProcessTimes, OpenProcess, QueryFullProcessImageNameW,
        TerminateProcess,
    };

    /// `STILL_ACTIVE`: processo ainda em execução.
    const STILL_ACTIVE: u32 = 259;

    struct Handle(HANDLE);

    impl Drop for Handle {
        fn drop(&mut self) {
            // SAFETY: o handle veio de `OpenProcess`, não é nulo e é fechado uma vez só.
            unsafe {
                CloseHandle(self.0);
            }
        }
    }

    fn open(pid: u32, access: u32) -> Option<Handle> {
        // SAFETY: `OpenProcess` não recebe ponteiros; o retorno nulo é tratado.
        let handle = unsafe { OpenProcess(access, 0, pid) };
        (!handle.is_null()).then_some(Handle(handle))
    }

    fn metadata(handle: &Handle) -> Option<Metadata> {
        let mut code = 0u32;
        // SAFETY: `code` é uma variável local viva durante a chamada.
        if unsafe { GetExitCodeProcess(handle.0, &mut code) } == 0 || code != STILL_ACTIVE {
            return None;
        }
        let zero = FILETIME {
            dwLowDateTime: 0,
            dwHighDateTime: 0,
        };
        let (mut creation, mut exit, mut kernel, mut user) = (zero, zero, zero, zero);
        // SAFETY: as quatro estruturas são locais e vivas durante a chamada.
        let times =
            unsafe { GetProcessTimes(handle.0, &mut creation, &mut exit, &mut kernel, &mut user) };
        if times == 0 {
            return None;
        }
        let mut buffer = vec![0u16; 32768];
        let mut size = buffer.len() as u32;
        // SAFETY: `buffer` tem `size` unidades UTF-16; a API escreve no máximo isso e
        // devolve em `size` quantas usou (sem o nulo final).
        let named =
            unsafe { QueryFullProcessImageNameW(handle.0, 0, buffer.as_mut_ptr(), &mut size) };
        if named == 0 {
            return None;
        }
        Some(Metadata {
            created_at: (u64::from(creation.dwHighDateTime) << 32)
                | u64::from(creation.dwLowDateTime),
            executable: String::from_utf16_lossy(&buffer[..size as usize]),
        })
    }

    pub fn process_metadata(pid: u32) -> Option<Metadata> {
        metadata(&open(pid, super::QUERY)?)
    }

    pub fn terminate_if(pid: u32, expected: &dyn Fn(&Metadata) -> bool) -> bool {
        let Some(handle) = open(pid, super::QUERY_AND_TERMINATE) else {
            return false;
        };
        // A identidade é conferida de novo no mesmo handle que encerra: um PID
        // reutilizado entre a checagem e o encerramento não é atingido.
        match metadata(&handle) {
            // SAFETY: handle válido aberto com `PROCESS_TERMINATE`.
            Some(current) if expected(&current) => unsafe { TerminateProcess(handle.0, 1) != 0 },
            _ => false,
        }
    }
}

#[cfg(not(windows))]
mod imp {
    use super::Metadata;

    pub fn process_metadata(_pid: u32) -> Option<Metadata> {
        None
    }

    pub fn terminate_if(_pid: u32, _expected: &dyn Fn(&Metadata) -> bool) -> bool {
        false
    }
}

/// Identidade de um processo vivo; `None` se não existe, já saiu ou não pode ser lido.
pub fn process_metadata(pid: u32) -> Option<Metadata> {
    imp::process_metadata(pid)
}

/// Encerra `pid` só se, no handle de encerramento, a identidade ainda for a esperada.
pub fn terminate_if(pid: u32, expected: &dyn Fn(&Metadata) -> bool) -> bool {
    imp::terminate_if(pid, expected)
}

/// `SetThreadExecutionState(flags)`: devolve o estado anterior, ou 0 em falha. Vale
/// para a thread que chama, como no Python (o serviço chama sempre da mesma).
#[cfg(windows)]
pub fn set_thread_execution_state(flags: u32) -> u32 {
    // SAFETY: função sem ponteiros; qualquer combinação de flags é aceita pela API.
    unsafe { windows_sys::Win32::System::Power::SetThreadExecutionState(flags) }
}

#[cfg(not(windows))]
pub fn set_thread_execution_state(_flags: u32) -> u32 {
    0
}

/// Trava exclusiva e sem espera do primeiro byte (`msvcrt.locking(LK_NBLCK, 1)`).
#[cfg(windows)]
pub fn lock_first_byte(file: &std::fs::File) -> bool {
    use std::os::windows::io::AsRawHandle;
    // SAFETY: o handle é de um arquivo aberto e vivo durante a chamada; a API só lê
    // os inteiros de região. Retorno diferente de zero indica a trava obtida.
    unsafe {
        windows_sys::Win32::Storage::FileSystem::LockFile(file.as_raw_handle(), 0, 0, 1, 0) != 0
    }
}

#[cfg(not(windows))]
pub fn lock_first_byte(_file: &std::fs::File) -> bool {
    false
}

/// Libera a trava de [`lock_first_byte`].
#[cfg(windows)]
pub fn unlock_first_byte(file: &std::fs::File) {
    use std::os::windows::io::AsRawHandle;
    // SAFETY: mesmo handle vivo e mesma região travada por `lock_first_byte`.
    unsafe {
        windows_sys::Win32::Storage::FileSystem::UnlockFile(file.as_raw_handle(), 0, 0, 1, 0);
    }
}

#[cfg(not(windows))]
pub fn unlock_first_byte(_file: &std::fs::File) {}

/// `gpu_agent.process_state`: `"alive"`, `"dead"` (PID inexistente, erro 87, ou já
/// encerrado) ou `"unknown"` (acesso negado ou falha de leitura nunca provam a morte).
#[cfg(windows)]
pub fn process_state(pid: u32) -> &'static str {
    use windows_sys::Win32::Foundation::{CloseHandle, GetLastError};
    use windows_sys::Win32::System::Threading::{GetExitCodeProcess, OpenProcess};
    // SAFETY: OpenProcess só recebe inteiros; o handle devolvido é fechado abaixo.
    let handle = unsafe { OpenProcess(0x1000, 0, pid) };
    if handle.is_null() {
        // SAFETY: lê o último erro desta thread, logo após a chamada que falhou.
        return if unsafe { GetLastError() } == 87 {
            "dead"
        } else {
            "unknown"
        };
    }
    let mut code = 0u32;
    // SAFETY: handle válido e ponteiro para um u32 desta pilha.
    let read = unsafe { GetExitCodeProcess(handle, &mut code) };
    // SAFETY: handle aberto acima, fechado uma única vez.
    unsafe { CloseHandle(handle) };
    if read == 0 {
        "unknown"
    } else if code == 259 {
        "alive"
    } else {
        "dead"
    }
}

#[cfg(not(windows))]
pub fn process_state(_pid: u32) -> &'static str {
    "unknown"
}

/// `shell32.IsUserAnAdmin()`: o processo roda com privilégio de administrador.
#[cfg(windows)]
pub fn is_user_an_admin() -> bool {
    // SAFETY: função sem argumentos que só consulta o token do processo.
    unsafe { windows_sys::Win32::UI::Shell::IsUserAnAdmin() != 0 }
}

#[cfg(not(windows))]
pub fn is_user_an_admin() -> bool {
    false
}

/// Ligado por Ctrl+C, Ctrl+Break ou pelo fechamento do console.
pub static CTRL_STOP: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

#[cfg(windows)]
unsafe extern "system" fn ctrl_handler(event: u32) -> i32 {
    CTRL_STOP.store(true, std::sync::atomic::Ordering::SeqCst);
    // CTRL_CLOSE_EVENT (2), LOGOFF (5) e SHUTDOWN (6): o Windows encerra o processo
    // quando esta função volta; esperar até 4,5 s dá tempo ao laço de parar as GPUs.
    if matches!(event, 2 | 5 | 6) {
        std::thread::sleep(std::time::Duration::from_millis(4500));
    }
    1
}

/// Instala o tratamento de Ctrl+C que liga [`CTRL_STOP`].
#[cfg(windows)]
pub fn install_ctrl_handler() -> bool {
    // SAFETY: a função de tratamento é `extern "system"`, vive o programa inteiro e só
    // grava um atômico.
    unsafe {
        windows_sys::Win32::System::Console::SetConsoleCtrlHandler(Some(ctrl_handler), 1) != 0
    }
}

#[cfg(not(windows))]
pub fn install_ctrl_handler() -> bool {
    false
}
