"""Exporta observação RTX 3070 de SQLite somente leitura, sem comandos de GPU."""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import statistics
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config import STATE_DIR, LOG_DIR, WALLET

LOCAL_TZ = dt.timezone(dt.timedelta(hours=-3), "America/Sao_Paulo")
FIELDS = {"temperature_c":"temperature.gpu", "power_w":"power.draw",
          "power_limit_w":"power.limit", "core_clock_mhz":"clocks.gr",
          "memory_clock_mhz":"clocks.mem", "fan_percent":"fan.speed",
          "gpu_utilization_percent":"utilization.gpu"}

def number(value):
    return isinstance(value, (int,float)) and not isinstance(value,bool) and math.isfinite(value)

def stamp(value):
    return {"unix":value, "utc":dt.datetime.fromtimestamp(value,dt.timezone.utc).isoformat(),
            "local":dt.datetime.fromtimestamp(value,LOCAL_TZ).isoformat()}

def stats(values):
    values=[float(v) for v in values if number(v)]
    return ({"count":len(values),"min":min(values),"max":max(values),
             "median":statistics.median(values),"mean":statistics.mean(values)} if values else None)

def build(database, minutes, end, thermal_path):
    start=end-minutes*60
    sql="SELECT timestamp,gpu_json,miner_json,miner_running,log_path FROM telemetry WHERE wallet=? AND source='live' AND timestamp>=? AND timestamp<=? ORDER BY timestamp"
    with sqlite3.connect(database.resolve().as_uri()+"?mode=ro",uri=True,timeout=2) as db:
        rows=db.execute(sql,(WALLET,start,end)).fetchall()
    selected=[]
    excluded={"wrong_or_missing_gpu":0,"inactive_or_paused":0,"stale_gpu":0}
    for at,gpu_json,miner_json,running,log in rows:
        gpu=json.loads(gpu_json or 'null') or {}
        miner=json.loads(miner_json)
        name=str(gpu.get('name',''))
        if 'NVIDIA' not in name or 'RTX 3070' not in name or 'Ti' in name:
            excluded['wrong_or_missing_gpu']+=1; continue
        if not running or miner.get('paused'):
            excluded['inactive_or_paused']+=1; continue
        sampled=gpu.get('sampled_at')
        if not number(sampled) or not -2<=at-sampled<=10:
            excluded['stale_gpu']+=1; continue
        selected.append((at,gpu,miner,log))
    if not selected:
        raise ValueError('Nenhuma amostra live RTX 3070 ativa e recente na janela solicitada')
    hashes={}
    for at,gpu,miner,log in selected:
        rate_at=miner.get('hashrate_at'); rate=miner.get('hashrate')
        if number(rate_at) and start<=rate_at<=end and 0<=at-rate_at<=180 and number(rate) and rate>0:
            hashes.setdefault(rate_at,float(rate))
    times=[r[0] for r in selected]
    gaps=[b-a for a,b in zip(times,times[1:])]
    max_gap=max(gaps,default=0)
    # No claim of a complete requested window when start/end or internal observations are missing.
    covered_intervals=sum(gap for gap in gaps if gap<=10)
    complete=times[0]-start<=10 and end-times[-1]<=10 and max_gap<=10 and not any(excluded.values())
    starts={row[2].get('started_at') for row in selected}
    logs={row[3] for row in selected}
    counters={}
    stable=len(starts)==1 and None not in starts and len(logs)==1
    for key in ('accepted','rejected','hw_errors'):
        values=[row[2].get(key) for row in selected]
        monotonic=all(number(v) for v in values) and all(b>=a for a,b in zip(values,values[1:]))
        counters[key]=int(values[-1]-values[0]) if stable and monotonic else None
    # Hash a reproducible canonical evidence projection, not an actively changing SQLite file.
    evidence=[{"at":at,"gpu":{k:gpu.get(k) for k in ['name','driver_version','sampled_at',*FIELDS.values()]},
               "miner":{k:miner.get(k) for k in ['hashrate','hashrate_at','accepted','rejected','hw_errors','started_at','paused']}}
              for at,gpu,miner,log in selected]
    digest=hashlib.sha256(json.dumps(evidence,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    try:
        thermal=json.loads(thermal_path.read_text(encoding='utf-8-sig'))
        thermal={k:thermal.get(k) for k in ['enabled','mode','current_clock_mhz','desired_clock_mhz',
            'min_clock_mhz','max_clock_mhz','target_band_c','emergency_pause_c','sampled_at','error','dry_run']}
    except (OSError,ValueError):
        thermal=None
    return {"schema_version":1,"generated_at":stamp(time.time()),"device":{"index":0,
        "names":sorted({r[1]['name'] for r in selected}),
        "driver_versions":sorted({str(r[1].get('driver_version')) for r in selected})},
        "source":{"table":"telemetry","filter":"source=live, configured wallet, GPU0 parser",
        "sql":sql,"evidence_projection_sha256":digest,"digest_scope":"canonical filtered evidence projection; not full database hash"},
        "window":{"requested_minutes":minutes,"requested_start":stamp(start),"requested_end":stamp(end),
        "observed_start":stamp(times[0]),"observed_end":stamp(times[-1]),"observed_span_seconds":times[-1]-times[0],
        "covered_interval_seconds":covered_intervals,"max_sensor_gap_seconds":max_gap,"gap_threshold_seconds":10,
        "complete":complete,"queried_rows":len(rows),"included_sensor_rows":len(selected),"excluded":excluded},
        "sensors":{key:stats([row[1].get(field) for row in selected]) for key,field in FIELDS.items()},
        "hashrate_ths":stats(list(hashes.values())),"hashrate_method":"unique hashrate_at, age 0..180s, timestamp inside requested window, active miner; unweighted independent log observations",
        "shares":{"stable_miner_start_and_log":stable,"deltas":counters,"method":"last minus first only for monotonic counters with unchanged miner start and log; not lifetime totals"},
        "thermal_status_at_export":thermal,
        "reference_configuration":{"min_clock_mhz":1500,"max_clock_mhz":1800,"fan_percent":100,"board_power_limit_w":240},
        "limitations":["One author-owned GPU0 RTX 3070 instance; not all boards or environments.",
        "Observational telemetry, not comparative A/B or universal overclock validation.",
        "Generic gpu_agent benchmark/control was not validated by this observation; legacy thermal supervisor may be active.",
        "No revenue, profitability or guaranteed hashrate inference. Sensor means are sample-weighted; hashrate logs are independently deduplicated."]}

def markdown(report):
    w=report['window']; h=report['hashrate_ths']; policy=report['thermal_status_at_export']
    lines=['# RTX 3070: observação real de uma unidade Windows','',
      '> English: measured telemetry from one author-owned NVIDIA RTX 3070. This is an observation, not a universal overclock, profitability guarantee or A/B comparison. Hashrate log timestamps are deduplicated independently from GPU polling.','',
      f"Janela solicitada: {w['requested_start']['local']} a {w['requested_end']['local']} ({w['requested_minutes']:g} min).",
      f"Cobertura: **{'completa' if w['complete'] else 'parcial'}**; observações de {w['observed_start']['local']} a {w['observed_end']['local']}; {w['included_sensor_rows']} amostras de sensores; maior intervalo {w['max_sensor_gap_seconds']:.2f} s.",
      '', '[JSON público com agregados e metodologia](https://raw.githubusercontent.com/TechFernandesLTDA/hashpilot/main/docs/performance/rtx3070-observation.json).','',
      '| Medida | n | Mínimo | Mediana | Média | Máximo |','|---|---:|---:|---:|---:|---:|']
    measures=[('Hashrate (TH/s)',h)]+list(report['sensors'].items())
    for name,s in measures:
        if s: lines.append(f"| {name} | {s['count']} | {s['min']:.2f} | {s['median']:.2f} | {s['mean']:.2f} | {s['max']:.2f} |")
    lines+=['','## Shares e controle','',f"Deltas de contadores na observação: `{json.dumps(report['shares']['deltas'])}`. Só são calculados quando início do minerador/arquivo permanecem estáveis e contadores são monotônicos.",'',
      f"Driver: {', '.join(report['device']['driver_versions'])}. Política lida no momento da exportação: `{json.dumps(policy,ensure_ascii=False)}`.",'',
      'A referência desta unidade usa faixa 1500–1800 MHz, ventoinhas em 100% e limite de potência configurado de **240 W**. O consumo real é medido separadamente na tabela. O estado do supervisor acima é uma leitura no momento da exportação, não uma configuração garantida durante toda a janela. Temperatura ambiente e fabricante específico da placa não foram registrados.','',
      '## Método e limites','',
      "Consulta SQLite aberta com `mode=ro`, tabela `telemetry`, somente `source='live'` e carteira configurada. O parser atual identifica GPU0. Excluem-se GPU sem identidade RTX 3070 NVIDIA, minerador parado/pausado e sensores com idade acima de 10 s. Hashrate exige idade até 180 s, timestamp dentro da janela e deduplicação por `hashrate_at`.",'',
      'A cobertura é parcial se faltar início/fim em até 10 s, existir intervalo maior que 10 s ou alguma exclusão. A duração coberta soma somente intervalos entre amostras de até 10 s. Não se preenchem lacunas. Médias dos sensores usam amostras; as taxas usam observações independentes do log, sem repetir a mesma linha a cada coleta.','',
      'O SHA256 no JSON identifica uma projeção canônica dos registros filtrados, sem caminhos pessoais; não é hash do banco ativo. O JSON publica agregados, sem registros brutos, carteira ou caminhos locais.','',
      'Esta sessão é observação do hardware existente e do supervisor térmico legado. Não valida o benchmark/controlador genérico `gpu_agent`, todas as RTX 3070 ou os 158 modelos do catálogo. Não é comparação A/B e não estima rendimento financeiro.','',
      'Reproduzir, com o Python do mise, na raiz do projeto:','',
      '```powershell','mise exec -- python tools/build_performance_report.py --minutes 15 --output docs/performance/rtx3070-observation.json','```','',
      '[Código do relatório](../../tools/build_performance_report.py) · [Controle térmico](../../CONTROLE-TERMICO.md) · [Catálogo e limites](../gpu-catalog.md)']
    return '\n'.join(lines)+'\n'

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--minutes',type=float,default=15)
    parser.add_argument('--output',type=Path,default=Path('docs/performance/rtx3070-observation.json'))
    parser.add_argument('--database',type=Path,default=STATE_DIR/'history.sqlite3')
    parser.add_argument('--end',type=float,default=None,help='Unix timestamp; default current time')
    args=parser.parse_args()
    if not 0<args.minutes<=1440: parser.error('--minutes deve ser >0 e <=1440')
    report=build(args.database,args.minutes,args.end or time.time(),LOG_DIR/'thermal-control.json')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    args.output.with_name('rtx3070.md').write_text(markdown(report),encoding='utf-8')
    print(json.dumps({'output':str(args.output),'sensor_rows':report['window']['included_sensor_rows'],
        'hashrate':report['hashrate_ths'],'coverage_complete':report['window']['complete']}))

if __name__=='__main__': main()
