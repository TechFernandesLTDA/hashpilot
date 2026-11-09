//! `CommandLineToArgvW` em Rust puro (sem FFI), como o `process_backend.windows_argv`.
//!
//! O gerenciador decide se um minerador já ocupa uma GPU lendo o `--gpu-id` da linha
//! de comando. Se a divisão em argumentos errar, ele pode iniciar um minerador
//! duplicado ou bloquear um início válido. A paridade com a função real do Windows é
//! provada no golden `process`, gerado no Windows.

/// Divide uma linha de comando como o `shell32!CommandLineToArgvW`.
///
/// Linha vazia ou só com espaços devolve lista vazia: a API devolveria o caminho do
/// processo atual, e o Python trata esse caso antes de chamá-la.
pub fn windows_argv(command_line: &str) -> Vec<String> {
    if command_line.trim().is_empty() {
        return Vec::new();
    }
    let text: Vec<char> = command_line.chars().collect();
    let mut position = 0;
    let mut arguments = Vec::new();

    // O primeiro argumento (o executável) segue regras próprias: entre aspas vai até a
    // próxima aspa, sem tratar barras; sem aspas vai até o primeiro espaço ou tab.
    let mut program = String::new();
    if text.first() == Some(&'"') {
        position = 1;
        while position < text.len() {
            let character = text[position];
            position += 1;
            if character == '"' {
                break;
            }
            program.push(character);
        }
    } else {
        while position < text.len() && !matches!(text[position], ' ' | '\t') {
            program.push(text[position]);
            position += 1;
        }
    }
    arguments.push(program);
    while position < text.len() && matches!(text[position], ' ' | '\t') {
        position += 1;
    }
    if position >= text.len() {
        return arguments;
    }

    // Demais argumentos: `qcount` 0 fora de aspas, 1 dentro; três aspas seguidas
    // viram uma aspa literal; barras só contam antes de uma aspa.
    let mut current: Vec<char> = Vec::new();
    let mut backslashes = 0usize;
    let mut quotes = 0usize;
    while position < text.len() {
        let character = text[position];
        if matches!(character, ' ' | '\t') && quotes == 0 {
            arguments.push(current.drain(..).collect());
            backslashes = 0;
            while position < text.len() && matches!(text[position], ' ' | '\t') {
                position += 1;
            }
            if position >= text.len() {
                return arguments;
            }
            continue;
        }
        if character == '\\' {
            current.push('\\');
            backslashes += 1;
            position += 1;
            continue;
        }
        if character == '"' {
            if backslashes % 2 == 0 {
                // Par: metade das barras, e a aspa abre ou fecha o trecho.
                current.truncate(current.len() - backslashes / 2);
                quotes += 1;
            } else {
                // Ímpar: metade das barras e uma aspa literal.
                current.truncate(current.len() - backslashes / 2 - 1);
                current.push('"');
            }
            position += 1;
            backslashes = 0;
            while position < text.len() && text[position] == '"' {
                quotes += 1;
                if quotes == 3 {
                    current.push('"');
                    quotes = 0;
                }
                position += 1;
            }
            if quotes == 2 {
                quotes = 0;
            }
            continue;
        }
        current.push(character);
        backslashes = 0;
        position += 1;
    }
    arguments.push(current.into_iter().collect());
    arguments
}

#[cfg(test)]
mod tests {
    use super::windows_argv;

    #[test]
    fn documented_examples() {
        // Exemplos da documentação da Microsoft para o parsing de argumentos.
        let cases: &[(&str, &[&str])] = &[
            (r#"p "a b c" d e"#, &["p", "a b c", "d", "e"]),
            (r#"p "ab\"c" "\\" d"#, &["p", "ab\"c", "\\", "d"]),
            (r#"p a\\\b d"e f"g h"#, &["p", r"a\\\b", "de fg", "h"]),
            (r#"p a\\\"b c d"#, &["p", "a\\\"b", "c", "d"]),
            (r#"p a\\\\"b c" d e"#, &["p", r"a\\b c", "d", "e"]),
            (
                r#""C:\Program Files\x.exe" --gpu-id 0,1"#,
                &[r"C:\Program Files\x.exe", "--gpu-id", "0,1"],
            ),
            ("p a \"\"", &["p", "a", ""]),
            ("p   ", &["p"]),
            ("   ", &[]),
        ];
        for (line, expected) in cases {
            assert_eq!(windows_argv(line), *expected, "{line}");
        }
    }
}
