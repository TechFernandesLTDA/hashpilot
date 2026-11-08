"""Levanta os dados de catálogo de uma release nova do SRBMiner, sem executar nada.

Baixa (ou lê de --file) o ZIP oficial Windows, confere o SHA-256 contra o digest
publicado pelo GitHub (e, opcionalmente, o MD5 do texto da release, que vem por
outro canal), localiza o ÚNICO `SRBMiner-MULTI.exe` dentro do pacote e calcula o
SHA-256 dele lendo os bytes do ZIP em memória — nada é extraído para o disco nem
executado. A saída é o trecho pronto para `miner_update.CATALOG` e para
`Permissao-Defender.ps1`, para revisão humana antes de entrar no repositório.

Uso:
    python tools/inspect_miner_release.py 3.8.6 --archive-sha256 <hex> [--md5 <hex>] [--file pacote.zip]
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import urllib.request
import zipfile
import io

RELEASES = "https://github.com/doktor83/SRBMiner-Multi/releases/download"
EXECUTABLE = "SRBMiner-MULTI.exe"
MAX_ARCHIVE_BYTES = 200_000_000


def folder(version: str) -> str:
    return "SRBMiner-Multi-" + version.replace(".", "-")


def archive_url(version: str) -> str:
    return f"{RELEASES}/{version}/{folder(version)}-win64.zip"


def download(url: str, opener=urllib.request.urlopen) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "HashPilot release inspector"})
    with opener(request, timeout=120) as response:
        data = response.read(MAX_ARCHIVE_BYTES + 1)
    if len(data) > MAX_ARCHIVE_BYTES:
        raise ValueError("Pacote maior que o limite de revisão; recusado")
    return data


def inspect(version: str, data: bytes, archive_sha256: str, md5: str | None = None) -> dict:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Versão precisa ter o formato X.Y.Z")
    if not re.fullmatch(r"[0-9a-f]{64}", archive_sha256):
        raise ValueError("--archive-sha256 precisa ter 64 dígitos hexadecimais minúsculos")
    actual = hashlib.sha256(data).hexdigest()
    if actual != archive_sha256:
        raise ValueError(f"SHA-256 do pacote divergente do publicado: {actual}")
    if md5 is not None and hashlib.md5(data).hexdigest() != md5.lower():
        raise ValueError("MD5 do pacote divergente do texto da release")
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = [info for info in archive.infolist() if Path(info.filename).name == EXECUTABLE and not info.is_dir()]
        if len(members) != 1:
            names = [info.filename for info in members]
            raise ValueError(f"Esperado exatamente um {EXECUTABLE} no pacote; encontrado: {names}")
        member = members[0]
        if member.filename.startswith(("/", "\\")) or ".." in Path(member.filename).parts:
            raise ValueError("Caminho do executável inseguro dentro do pacote")
        binary = archive.read(member)
    item = {"archive_sha256": archive_sha256, "exe_sha256": hashlib.sha256(binary).hexdigest()}
    default_member = f"{folder(version)}/{EXECUTABLE}"
    if member.filename != default_member:
        item["archive_member"] = member.filename
    return {
        "version": version,
        "url": archive_url(version),
        "archive_bytes": len(data),
        "executable_bytes": len(binary),
        "catalog_entry": item,
        "catalog_line": f" {version!r}:{json.dumps(item).replace(chr(34), chr(39))},",
        "note": "Nada foi extraído para o disco nem executado. Revise antes de incluir no catálogo.",
    }


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("version")
    parser.add_argument("--archive-sha256", required=True, help="digest sha256 exibido pelo GitHub para o ZIP win64")
    parser.add_argument("--md5", help="MD5 do bloco 'Archive MD5 hashes' da release (segundo canal, opcional)")
    parser.add_argument("--file", type=Path, help="ZIP já baixado; sem ele, baixa da release oficial")
    args = parser.parse_args(argv)
    data = args.file.read_bytes() if args.file else download(archive_url(args.version))
    print(json.dumps(inspect(args.version, data, args.archive_sha256, args.md5), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(f"erro: {error}", file=sys.stderr)
        raise SystemExit(1)
