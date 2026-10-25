"""Valida links locais/canônicos e JSONs públicos sem consultar a internet."""
from pathlib import Path
import json
import re
from urllib.parse import unquote

ROOT=Path(__file__).resolve().parents[1]
CANONICAL='https://raw.githubusercontent.com/TechFernandesLTDA/hashpilot/main/'
errors=[]
count=0
documents=[*ROOT.rglob('*.md'),ROOT/'llms.txt']
for file in documents:
    if '.git' in file.relative_to(ROOT).parts: continue
    text=file.read_text(encoding='utf-8-sig')
    for url in re.findall(r'\]\(([^\s)]+)',text):
        url=unquote(url.split('#')[0].split('?')[0])
        if not url: continue
        if url.startswith(CANONICAL): target=ROOT/url[len(CANONICAL):]
        elif re.match(r'[a-zA-Z]+:',url): continue
        else: target=file.parent/url
        count+=1
        if not target.exists(): errors.append(f'{file.relative_to(ROOT)} -> {url}')
for name in ('codemeta.json','data/nvidia-gpus.json','settings.example.json'):
    json.loads((ROOT/name).read_text(encoding='utf-8-sig'))
print(json.dumps({'checked_links':count,'errors':errors},ensure_ascii=False,indent=2))
raise SystemExit(bool(errors))
