"""Escaneia bancos SQLite versionados sem imprimir o conteúdo dos registros."""
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import tempfile

ROOT=Path(__file__).resolve().parents[1]
objects=subprocess.check_output(['git','rev-list','--objects','--all'],cwd=ROOT,text=True).splitlines()
blobs={line.split(' ',1)[0] for line in objects if ' ' in line and line.split(' ',1)[1].endswith('.sqlite3')}
for file in (ROOT/'portable-data').glob('*.sqlite3'):
    with closing(sqlite3.connect(file.as_uri()+'?mode=ro',uri=True)) as database:
        dump='\n'.join(database.iterdump())
    scan=subprocess.run(['gitleaks','stdin','--redact','--no-banner'],input=dump,text=True,encoding='utf-8',capture_output=True,cwd=ROOT)
    if scan.returncode:
        print(scan.stdout,scan.stderr)
        raise SystemExit('Secret scan failed for a current database; publication blocked.')
with tempfile.TemporaryDirectory() as temporary:
    path=Path(temporary)/'snapshot.sqlite3'
    for blob in blobs:
        path.write_bytes(subprocess.check_output(['git','cat-file','blob',blob],cwd=ROOT))
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as database:
            dump='\n'.join(database.iterdump())
        scan=subprocess.run(['gitleaks','stdin','--redact','--no-banner'],input=dump,text=True,encoding='utf-8',capture_output=True,cwd=ROOT)
        if scan.returncode:
            print(scan.stdout,scan.stderr)
            raise SystemExit('Secret scan failed for a historical database; publication blocked.')
print(f'Historical SQLite blobs scanned: {len(blobs)}. Current database scan passed. No records printed.')
