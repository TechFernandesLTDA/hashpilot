"""Pacote determinístico a partir de blobs Git; nunca inclui arquivos locais extras."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = "RELEASE-MANIFEST.json"


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_name(name):
    parts = PurePosixPath(name).parts
    return bool(parts) and not name.startswith("/") and "\\" not in name and ":" not in name and all(p not in (".", "..") for p in parts) and str(PurePosixPath(name)) == name


def git(*args, root=ROOT):
    return subprocess.check_output(["git", *args], cwd=root)


def build(ref, version, output, root=ROOT):
    if not re.fullmatch(r"v\d+\.\d+\.\d+", version):
        raise ValueError("Use uma versão vX.Y.Z")
    commit = git("rev-parse", "--verify", "--end-of-options", ref + "^{commit}", root=root).decode().strip()
    files = {}
    for entry in git("ls-tree", "-rz", "--full-tree", commit, root=root).split(b"\0"):
        if not entry:
            continue
        metadata, encoded_name = entry.split(b"\t", 1)
        mode, kind, oid = metadata.split()
        name = encoded_name.decode("utf-8")
        if not safe_name(name) or kind != b"blob" or mode not in (b"100644", b"100755") or name == MANIFEST:
            raise ValueError("Árvore contém caminho/tipo não permitido")
        if name.startswith(".git/") or Path(name).name in (".env", "settings.json"):
            raise ValueError("Arquivo de configuração privada não permitido")
        files[name] = git("cat-file", "blob", oid.decode(), root=root)
    manifest = {"schema": 1, "version": version, "commit": commit,
                "source": "https://github.com/TechFernandesLTDA/hashpilot",
                "files": {name: {"sha256": digest(data), "size": len(data)} for name, data in sorted(files.items())}}
    raw = (json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode()
    files[MANIFEST] = raw
    output.mkdir(parents=True, exist_ok=True)
    archive = output / f"hashpilot-{version}.zip"
    # ZIP_STORED avoids compression-library differences across machines.
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as bundle:
        for name, data in sorted(files.items()):
            info = zipfile.ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            bundle.writestr(info, data)
    manifest_path = output / f"hashpilot-{version}.manifest.json"
    manifest_path.write_bytes(raw)
    (output / "SHA256SUMS").write_text("".join(f"{digest(path.read_bytes())}  {path.name}\n" for path in (archive, manifest_path)), encoding="ascii")
    return {"version": version, "commit": commit, "files": len(manifest["files"]), "sha256": digest(archive.read_bytes()), "package": str(archive)}


def verify(archive, checksums):
    expected = {}
    for line in checksums.read_text(encoding="ascii").splitlines():
        match = re.fullmatch(r"([a-f0-9]{64})  ([^/\\:]+)", line)
        if not match or match[2] in expected:
            raise ValueError("Lista de checksums inválida")
        expected[match[2]] = match[1]
    if expected.get(archive.name) != digest(archive.read_bytes()):
        raise ValueError("Checksum do pacote divergente")
    with zipfile.ZipFile(archive) as bundle:
        names = bundle.namelist()
        if len(names) != len({n.casefold() for n in names}) or any(not safe_name(n) for n in names):
            raise ValueError("Caminho duplicado ou inseguro no ZIP")
        if sum(info.file_size for info in bundle.infolist()) > 512 * 1024 * 1024:
            raise ValueError("Pacote excede limite de inspeção")
        manifest = json.loads(bundle.read(MANIFEST))
        if manifest.get("schema") != 1 or not re.fullmatch(r"[a-f0-9]{40}", manifest.get("commit", "")):
            raise ValueError("Manifesto inválido")
        if archive.name != f"hashpilot-{manifest.get('version')}.zip" or not re.fullmatch(r"v\d+\.\d+\.\d+", manifest.get("version", "")):
            raise ValueError("Versão/nome do pacote divergente")
        if set(names) != set(manifest["files"]) | {MANIFEST}:
            raise ValueError("Inventário do ZIP divergente")
        for name, metadata in manifest["files"].items():
            data = bundle.read(name)
            if len(data) != metadata["size"] or digest(data) != metadata["sha256"]:
                raise ValueError("Conteúdo divergente do manifesto")
    return {"verified": True, "commit": manifest["commit"], "version": manifest["version"], "files": len(names)-1}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    build_parser = sub.add_parser("build")
    build_parser.add_argument("--ref", default="HEAD")
    build_parser.add_argument("--version", required=True)
    build_parser.add_argument("--output", type=Path, default=ROOT / "dist")
    check = sub.add_parser("verify")
    check.add_argument("archive", type=Path)
    check.add_argument("--checksums", type=Path, required=True)
    args = parser.parse_args()
    result = build(args.ref, args.version, args.output) if args.action == "build" else verify(args.archive, args.checksums)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
