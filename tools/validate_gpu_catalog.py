"""Valida o catálogo NVIDIA; --refresh atualiza nomes/PCI a partir da fonte oficial.

O catálogo identifica produtos. Especificações de referência nunca autorizam
comandos de overclock e não substituem capacidades lidas do driver local.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
from urllib.parse import urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "data/nvidia-gpus.json"
DOCUMENTATION = ROOT / "docs/gpu-catalog.md"
DRIVER = "https://download.nvidia.com/XFree86/Linux-x86_64/580.95.05/README/supportedchips.html"
COMPARE = "https://www.nvidia.com/en-us/geforce/graphics-cards/compare/"
LAPTOP = "https://www.nvidia.com/en-us/geforce/laptops/compare/"
LEGACY = "https://developer.nvidia.com/cuda/gpus/legacy"


class Rows(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell = [], None, None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = []
        elif tag in ("br", "p", "div") and self.cell is not None:
            self.cell.append("\n")

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.cell is not None:
            self.row.append("".join(self.cell).strip())
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None


def normalize_name(name):
    name = re.sub(r"\s+", " ", name.strip())
    name = re.sub(r"^NVIDIA\s+", "", name, flags=re.I)
    name = re.sub(r"(?<=\d)Ti\b", " Ti", name)
    name = re.sub(r"\bsuper\b", "SUPER", name, flags=re.I)
    return "NVIDIA " + name


def family(name):
    kind, digits = re.search(r"\b(GTX|RTX) (\d+)", name).groups()
    prefix = digits[:2] if len(digits) == 4 else digits[0]
    return f"GeForce {kind} {prefix}{'00' if len(digits)==3 else ''} Series"


def form_factor(name):
    if "Laptop GPU" in name or "Max-Q" in name or re.search(r"\dM(?:X)?\b", name):
        return "laptop"
    if re.search(r"\dA\b", name):
        return "all_in_one"
    # The driver reuses these names for desktop and notebook PCI devices.
    ambiguous = ("GTX 1050", "GTX 1060", "GTX 1070", "GTX 1080", "GTX 1650", "GTX 1660 Ti",
                 "RTX 2050", "RTX 2060", "RTX 2070", "RTX 2080")
    bare = name.removeprefix("NVIDIA GeForce ")
    if bare in ambiguous or bare in ("RTX 2070 SUPER", "RTX 2080 SUPER", "GTX 1650 Ti"):
        return "unspecified"
    return "desktop"


# Curated factual reference rows, verified in NVIDIA's comparison tables.
# Arrays preserve published variants; these values are NEVER control limits.
# Key: model, memory GB variants, published board power W variants.
DESKTOP_REFERENCE = {
    "RTX 5090": ([32], [575]), "RTX 5080": ([16], [360]), "RTX 5070 Ti": ([16], [300]),
    "RTX 5070": ([12], [250]), "RTX 5060 Ti": ([8,16], [180]), "RTX 5060": ([8], [145]), "RTX 5050": ([8], [130]),
    "RTX 4090": ([24], [450]), "RTX 4080 SUPER": ([16], [320]), "RTX 4080": ([16], [320]),
    "RTX 4070 Ti SUPER": ([16], [285]), "RTX 4070 Ti": ([12], [285]), "RTX 4070 SUPER": ([12], [220]),
    "RTX 4070": ([12], [200]), "RTX 4060 Ti": ([8,16], [160,165]), "RTX 4060": ([8], [115]),
    "RTX 3090 Ti": ([24], [450]), "RTX 3090": ([24], [350]), "RTX 3080 Ti": ([12], [350]),
    "RTX 3080": ([10,12], [320,350]), "RTX 3070 Ti": ([8], [290]), "RTX 3070": ([8], [220]),
    "RTX 3060 Ti": ([8], [200]), "RTX 3060": ([8,12], [170]), "RTX 3050": ([6,8], [70,130]),
    "RTX 2080 Ti": ([11], [260]), "RTX 2080 SUPER": ([8], [250]), "RTX 2080": ([8], [225]),
    "RTX 2070 SUPER": ([8], [215]), "RTX 2070": ([8], [185]), "RTX 2060 SUPER": ([8], [175]),
    "RTX 2060": ([6,12], [160,185]),
    "GTX 1660 Ti": ([6], [120]), "GTX 1660 SUPER": ([6], [125]), "GTX 1660": ([6], [120]),
    "GTX 1650 SUPER": ([4], [100]), "GTX 1650": ([4], [75]), "GTX 1630": ([4], [75]),
}
LAPTOP_REFERENCE = {
    "RTX 5090 Laptop GPU": ([24], [95,150]), "RTX 5080 Laptop GPU": ([16], [80,150]),
    "RTX 5070 Ti Laptop GPU": ([12], [60,115]), "RTX 5070 Laptop GPU": ([8,12], [50,100]),
    "RTX 5060 Laptop GPU": ([8], [45,100]), "RTX 5050 Laptop GPU": ([8], [35,100]),
}


def fetch_rows(url):
    request = Request(url, headers={"User-Agent": "HashPilot-GPU-Catalog/1.0"})
    with urlopen(request, timeout=40) as response:
        body = response.read()
    parser = Rows()
    parser.feed(body.decode("utf-8", errors="replace"))
    return parser.rows, hashlib.sha256(body).hexdigest()


def build_catalog():
    rows, digest = fetch_rows(DRIVER)
    legacy_rows, legacy_digest = fetch_rows(LEGACY)
    capabilities = {}
    for row in legacy_rows:
        if not row or not re.fullmatch(r"\d+\.\d+", row[0]):
            continue
        for cell in row[1:]:
            for match in re.finditer(r"(?:NVIDIA )?GeForce GTX[^\n]+", cell):
                capabilities.setdefault(normalize_name(match.group().strip()), set()).add(row[0])
    records = {}
    for row in rows:
        if len(row) < 2:
            continue
        raw = re.sub(r"\s+", " ", row[0]).strip()
        name = normalize_name(raw)
        match = re.fullmatch(r"NVIDIA GeForce (GTX|RTX) (\d+).*", name)
        if not match or int(match[2]) < 400:
            continue
        pci = row[1].strip().upper()
        if not re.fullmatch(r"[0-9A-F]{4}(?:\s+[0-9A-F]{4}){0,2}", pci):
            continue
        if name not in records:
            short = name.removeprefix("NVIDIA GeForce ")
            modern = re.search(r"(GTX|RTX) (\d\d)\d\d", short)
            architecture = None
            arch_source = None
            if modern:
                architecture = {("GTX","10"):"Pascal",("GTX","16"):"Turing",("RTX","20"):"Turing",
                                ("RTX","30"):"Ampere",("RTX","40"):"Ada Lovelace",("RTX","50"):"Blackwell"}.get(modern.groups())
                arch_source = LAPTOP if form_factor(name)=="laptop" else COMPARE
            if short == "RTX 3050 A Laptop GPU":
                architecture, arch_source = None, None  # Naming alone is insufficient for this exceptional SKU.
            record = {
                "id":re.sub(r"[^a-z0-9]+","-",name.lower()).strip("-"),
                "vendor":"NVIDIA", "name":name, "family":family(name),
                "form_factor":form_factor(name), "architecture":architecture,
                "compute_capabilities":sorted(capabilities.get(name,[])),
                "memory_gb":None, "published_power_w":None,
                "pci_device_ids":[], "pci_identifiers":[], "aliases":[],
                "sources":[DRIVER],
                "field_sources":{"name":DRIVER,"pci_device_ids":DRIVER},
                "evidence_scope":"Product identity and PCI mapping only; driver appendix includes current and legacy products.",
                "tuning_validated":short=="RTX 3070",
                "tuning_scope":"author_single_desktop_board_reference" if short=="RTX 3070" else "not_hardware_validated",
            }
            if architecture:
                record["sources"].append(arch_source)
                record["field_sources"]["architecture"] = arch_source
            if record["compute_capabilities"]:
                record["sources"].append(LEGACY)
                record["field_sources"]["compute_capabilities"] = LEGACY
            # Expose desktop reference specs separately for names that can also denote laptops.
            if short in DESKTOP_REFERENCE:
                memory, power = DESKTOP_REFERENCE[short]
                record["reference_variants"] = {"scope":"NVIDIA desktop reference design; partner cards may differ",
                    "memory_gb":memory,"published_board_power_w":power,"source":COMPARE}
                if record["form_factor"] == "desktop":
                    record["memory_gb"] = memory[0] if len(memory)==1 else None
                    record["published_power_w"] = power[0] if len(power)==1 else None
                    record["field_sources"].update(memory_gb=COMPARE,published_power_w=COMPARE)
                record["sources"].append(COMPARE)
            elif short in LAPTOP_REFERENCE:
                memory,power = LAPTOP_REFERENCE[short]
                record["reference_variants"] = {"scope":"NVIDIA laptop subsystem power range; OEM implementation varies",
                    "memory_gb":memory,"published_subsystem_power_range_w":power,"source":LAPTOP}
                record["memory_gb"] = memory[0] if len(memory)==1 else None
                record["field_sources"]["memory_gb"] = LAPTOP
                record["sources"].append(LAPTOP)
            explicit_memory = re.search(r"\b(\d+)GB\b", short)
            if explicit_memory:
                record["memory_gb"] = int(explicit_memory[1])
                record["field_sources"]["memory_gb"] = DRIVER
            records[name] = record
        record = records[name]
        record["pci_device_ids"].append(pci.split()[0])
        record["pci_identifiers"].append(pci)
        record["aliases"].extend([raw,name.removeprefix("NVIDIA "),name.removeprefix("NVIDIA GeForce ")])
    for record in records.values():
        for key in ("pci_device_ids","pci_identifiers","aliases","sources"):
            record[key] = sorted(set(record[key]))
    gpus = sorted(records.values(), key=lambda x:(x["family"],x["name"]))
    return {
        "schema_version":1,
        "metadata":{
            "checked_at":datetime.now(timezone.utc).isoformat(), "model_count":len(gpus),
            "publisher":"Tech Fernandes / HashPilot", "source_quality":"NVIDIA primary sources only",
            "purpose":"Identification and reference facts, not safe overclock presets or mining compatibility guarantees",
            "unknown_values":"null means not established for the exact identity; never fill by interpolation",
            "matching_policy":"Prefer PCI device/subsystem plus actual model and form factor; name-only matches can be ambiguous",
            "tuning_policy":"Read supported capabilities and limits from the actual GPU/driver; never use published reference power as a control limit",
            "legacy_policy":"Presence in this catalogue does not imply support in a current driver, CUDA version, miner, or Pearl algorithm",
        },
        "sources":[
            {"id":"nvidia-driver-580-appendix", "url":DRIVER,"sha256":digest,"evidence_scope":"Official product names, PCI and subsystem identifiers; includes legacy appendix"},
            {"id":"nvidia-desktop-comparison", "url":COMPARE,"evidence_scope":"Modern architecture by family and curated desktop reference memory/power variants"},
            {"id":"nvidia-laptop-comparison", "url":LAPTOP,"evidence_scope":"Modern architecture by family and RTX50 laptop memory/subsystem power ranges"},
            {"id":"nvidia-legacy-compute", "url":LEGACY,"sha256":legacy_digest,"evidence_scope":"Legacy product compute capability where exact names match"},
        ],
        "gpus":gpus,
    }


def validate(catalog):
    models = catalog["gpus"]
    assert len(models)>=100, f"Expected at least 100 models, found {len(models)}"
    assert len({x["id"] for x in models})==len(models), "Duplicate ids"
    assert len({x["name"].casefold() for x in models})==len(models), "Duplicate names"
    assert catalog["metadata"]["model_count"]==len(models)
    for model in models:
        assert model["vendor"]=="NVIDIA"
        assert re.fullmatch(r"NVIDIA GeForce (GTX|RTX) \d+.*",model["name"]), model["name"]
        assert model["form_factor"] in ("desktop","laptop","all_in_one","unspecified")
        assert model["sources"] and model["pci_device_ids"]
        for url in model["sources"]:
            parsed=urlparse(url)
            assert parsed.scheme=="https" and (parsed.hostname=="nvidia.com" or parsed.hostname.endswith(".nvidia.com")), url
        for pci in model["pci_device_ids"]:
            assert re.fullmatch(r"[0-9A-F]{4}",pci)
        assert all("recommended" not in key and "safe_clock" not in key for key in model), model["name"]
        if model["tuning_validated"]:
            assert model["name"]=="NVIDIA GeForce RTX 3070"
            assert model["tuning_scope"]=="author_single_desktop_board_reference"
        for key in ("memory_gb","published_power_w"):
            assert model[key] is None or (isinstance(model[key],(int,float)) and model[key]>0)
    return {"models":len(models),"families":dict(Counter(x["family"] for x in models)),
            "form_factors":dict(Counter(x["form_factor"] for x in models)),
            "models_with_published_reference_variants":sum("reference_variants" in x for x in models),
            "models_with_compute_capability":sum(bool(x["compute_capabilities"]) for x in models),
            "hardware_validated_reference_models":sum(x["tuning_validated"] for x in models)}


def render_documentation(catalog):
    statistics=validate(catalog)
    lines=[
        "# Catálogo de GPUs NVIDIA GeForce GTX e RTX", "",
        f"O [dataset legível por máquinas](../data/nvidia-gpus.json) identifica **{statistics['models']} modelos únicos** das séries GTX 400 até RTX 50, incluindo desktop, notebook e all-in-one. Os nomes e identificadores PCI foram extraídos do apêndice oficial da NVIDIA; nomes alternativos como `750Ti`/`750 Ti` e `3050Ti`/`3050 Ti` foram consolidados e não contam como placas adicionais.", "",
        "## O que foi pesquisado", "",
        "| Informação | Cobertura e significado |", "| --- | --- |",
        f"| Nomes, família, aliases e PCI | Todos os {statistics['models']} registros; fonte primária NVIDIA por registro |",
        f"| Formato físico | {statistics['form_factors']['desktop']} desktop, {statistics['form_factors']['laptop']} notebook, {statistics['form_factors']['all_in_one']} all-in-one e {statistics['form_factors']['unspecified']} com nome ambíguo |",
        f"| Memória e potência de referência | {statistics['models_with_published_reference_variants']} registros com variantes publicadas nas tabelas oficiais; demais campos sem evidência ficam `null` |",
        f"| CUDA compute capability | {statistics['models_with_compute_capability']} correspondências exatas com a tabela oficial de produtos legados |",
        "| Arquitetura | Famílias modernas sustentadas pelos comparativos NVIDIA; variantes excepcionais e modelos antigos sem evidência direta ficam `null` |",
        "| Overclock, desempenho Pearl e estabilidade | Somente a unidade RTX 3070 do autor foi medida; o catálogo não contém presets de overclock |", "",
        "## Como interpretar os dados", "",
        "- `sources` e `field_sources` registram a fonte de cada fato, com o escopo da evidência. `metadata.checked_at` informa quando a pesquisa foi atualizada.",
        "- `pci_device_ids` guarda o identificador do chip. `pci_identifiers` conserva também os identificadores de subsistema quando publicados. Um chip pode aparecer em projetos diferentes; não basta o PCI de quatro dígitos para garantir memória, formato ou limites elétricos.",
        "- `form_factor: unspecified` significa que a NVIDIA usa o mesmo nome em produtos desktop e notebook. Por exemplo, uma GTX 1060 identificada apenas pelo nome não autoriza aplicar limites de uma placa desktop.",
        "- `memory_gb` e `published_power_w` só recebem um número quando a identidade e a fonte permitem um valor único. `reference_variants` preserva as alternativas publicadas. Nos nomes ambíguos, variantes de desktop ficam explicitamente separadas.",
        "- A potência publicada é uma especificação do projeto de referência, não o limite configurável da placa detectada. A RTX 3070 de referência tem 220 W na tabela NVIDIA, enquanto a unidade do autor informa 240 W de limite de fábrica. O controlador deve consultar o hardware.",
        "- `tuning_validated: true` na RTX 3070 representa uma única unidade desktop medida pelo autor. Não valida todos os fabricantes, BIOS, notebooks ou outras unidades do mesmo modelo.",
        "- A inclusão no catálogo não garante suporte no driver atual, em uma versão do CUDA, no SRBMiner ou no algoritmo Pearl. A fonte do driver contém também seções de produtos legados.", "",
        "## Benchmark e ajuste dinâmico", "",
        "O catálogo serve à identificação. A decisão de aplicar um ajuste depende das capacidades e limites informados pelo driver da GPU real, do algoritmo executado e das medições do benchmark local. Modelos conhecidos sem suporte de escrita devem continuar em observação. Não há hashrate presumido nem preset universal de clock, temperatura ou potência nesta pesquisa.", "",
        "## Reproduzir a pesquisa e validar", "", "```powershell",
        "mise exec -- python tools/validate_gpu_catalog.py",
        "mise exec -- python tools/validate_gpu_catalog.py --refresh", "```", "",
        "O primeiro comando valida o JSON local sem rede. O segundo busca novamente as tabelas oficiais de identificação e compute capability, reconstrói o dataset com as especificações de referência curadas e atualiza esta página. A revisão humana das tabelas de comparação continua necessária quando essas especificações mudarem. A soma SHA256 dos documentos de identificação fica em `sources` para rastreabilidade; não é um checksum de firmware ou de programa executável.", "",
        "## Fontes primárias", "",
        f"1. [NVIDIA — Supported GPU Products, driver 580.95.05]({DRIVER}): nomes e PCI, inclusive produtos legados.",
        f"2. [NVIDIA — comparação GeForce desktop]({COMPARE}): arquitetura das famílias recentes, memória e potência dos projetos de referência.",
        f"3. [NVIDIA — comparação GeForce notebooks]({LAPTOP}): arquitetura das famílias recentes e variantes RTX 50 para notebook.",
        f"4. [NVIDIA Developer — compute capability legado]({LEGACY}): capacidade CUDA por nome exato.", "",
        "## Índice dos modelos", "",
        "Valores `—` indicam informação não estabelecida para a identidade exata. A coluna de memória apresenta variantes de referência quando disponíveis, com o formato explicitado no JSON.", "",
        "| Modelo | Família | Formato | Memória (GB) | PCI IDs |", "| --- | --- | --- | --- | --- |",
    ]
    translated={"desktop":"Desktop","laptop":"Notebook","all_in_one":"All-in-one","unspecified":"Ambíguo"}
    for model in catalog["gpus"]:
        memory=model.get("reference_variants",{}).get("memory_gb")
        if memory:
            display=" / ".join(map(str,memory))
        else:
            display=str(model["memory_gb"]) if model["memory_gb"] is not None else "—"
        lines.append(f"| {model['name'].removeprefix('NVIDIA GeForce ')} | {model['family'].removeprefix('GeForce ')} | {translated[model['form_factor']]} | {display} | {', '.join(model['pci_device_ids'])} |")
    return "\n".join(lines)+"\n"


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refresh",action="store_true",help="Fetch official NVIDIA identity tables and rebuild curated dataset")
    args=parser.parse_args()
    if args.refresh:
        result=build_catalog()
        validate(result)
        CATALOG.parent.mkdir(parents=True,exist_ok=True)
        CATALOG.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
        DOCUMENTATION.parent.mkdir(parents=True,exist_ok=True)
        DOCUMENTATION.write_text(render_documentation(result),encoding="utf-8")
    print(json.dumps(validate(json.loads(CATALOG.read_text(encoding="utf-8"))),ensure_ascii=False,indent=2))
