"""Verificação estrutural de scripts PowerShell sem PowerShell (aspas, comentários, () {} []).

Não substitui o parser real (`tools/check_powershell.ps1`, no Windows/pre-push);
serve onde não há `pwsh`, para pegar aspas ou chaves quebradas por uma edição.
Uso: python tools/check_powershell_structure.py [arquivo.ps1 ...]  (padrão: todos os versionados)
"""

from __future__ import annotations
import pathlib
import subprocess
import sys

PAIRS = {')': '(', '}': '{', ']': '['}


def check(text: str) -> None:
    """Levanta AssertionError com a linha do primeiro problema estrutural."""
    stack = []
    i = 0
    n = len(text)
    line = 1
    while i < n:
        ch = text[i]
        if ch == '\n':
            line += 1
            i += 1
            continue
        if text.startswith('<#', i):
            j = text.find('#>', i + 2)
            assert j >= 0, f'bloco de comentario aberto na linha {line}'
            line += text.count('\n', i, j)
            i = j + 2
            continue
        if text.startswith("@'", i) or text.startswith('@"', i):
            q = text[i + 1]
            j = text.find('\n' + q + '@', i + 2)
            assert j >= 0, f'here-string aberta na linha {line}'
            line += text.count('\n', i, j + 3)
            i = j + 3
            continue
        if ch == '#':
            j = text.find('\n', i)
            i = n if j < 0 else j
            continue
        if ch == "'":
            j = i + 1
            while True:
                k = text.find("'", j)
                assert k >= 0, f"aspas simples abertas na linha {line}"
                if text.startswith("''", k):
                    j = k + 2
                    continue
                break
            line += text.count('\n', i, k)
            i = k + 1
            continue
        if ch == '"':
            j = i + 1
            while j < n:
                if text[j] == '`':
                    j += 2
                    continue
                if text[j] == '"':
                    if j + 1 < n and text[j + 1] == '"':
                        j += 2
                        continue
                    break
                j += 1
            assert j < n, f'aspas duplas abertas na linha {line}'
            line += text.count('\n', i, j)
            i = j + 1
            continue
        if ch == '`':
            i += 2
            continue
        if ch in '({[':
            stack.append((ch, line))
        elif ch in ')}]':
            assert stack and stack[-1][0] == PAIRS[ch], f"'{ch}' sem par na linha {line} (pilha: {stack[-3:]})"
            stack.pop()
        i += 1
    assert not stack, f'nao fechado: {stack[-3:]}'


def main(argv: list[str]) -> int:
    root = pathlib.Path(__file__).resolve().parents[1]
    files = (
        argv
        or subprocess.run(
            ["git", "-C", str(root), "ls-files", "*.ps1"], capture_output=True, text=True, check=True
        ).stdout.split()
    )
    bad = 0
    for name in files:
        path = pathlib.Path(name) if pathlib.Path(name).is_absolute() else root / name
        try:
            check(path.read_text(encoding="utf-8-sig"))
        except AssertionError as error:
            bad += 1
            print(f"{name}: {error}")
    print(f"PowerShell (estrutura): {len(files)} arquivos, {bad} com problema")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
