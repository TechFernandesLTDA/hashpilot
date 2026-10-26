"""Leitura histórica da ajuda, inclusive com proxy parado. Nunca modifica cotas."""
import json
import math
from pathlib import Path
from contribution import read_support_status

COUNTERS=('verified_primary_seconds','reserved_support_seconds','verified_support_seconds')
SHARES=('accepted_primary_shares','accepted_support_shares','rejected_primary_shares','rejected_support_shares')

def audit(directory,wallet,settings):
    result=read_support_status(directory,wallet,settings)
    if result.get('ready'):return result
    try:
        ledger=json.loads((Path(directory)/'support-mining-ledger.json').read_text(encoding='utf-8-sig'))
        values=ledger['wallets'][wallet]
        if ledger.get('schema_version')!=2:raise ValueError('Schema')
        def number(key):
            value=values.get(key)
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:raise ValueError('Counter')
            return value
        counters={key:number(key) for key in COUNTERS}
        shares={key:number(key) for key in SHARES}
        result.update(counters,share_responses=shares,audit_source='ledger persistente; rota atual não confirmada')
    except (OSError,ValueError,TypeError,KeyError):
        result['audit_source']='sem histórico auditável desta carteira'
    return result

def audit_text(entry,status):
    def value(key):
        item=status.get(key)
        return f'{item:.0f} s' if isinstance(item,(int,float)) else 'não disponível'
    shares=status.get('share_responses') or {}
    active=status.get('routing_active') is True
    return '\n'.join([
        'Ajuda opcional pela ferramenta: '+('habilitada' if status.get('enabled') else 'desativada / sem adicional'),
        'Carteira configurada: '+str(entry.get('wallet','não disponível')),
        'Carteira da rota atual: '+str(status.get('active_wallet') if active else 'não confirmada'),
        'Fase confirmada: '+str(status.get('active_role') if active else 'não confirmada'),
        'Próxima troca: '+(value('seconds_until_switch') if active else 'não disponível'),
        '',
        'Tempo primário verificado: '+value('verified_primary_seconds'),
        'Ajuda reservada (inclusive reservas já usadas): '+value('reserved_support_seconds'),
        'Ajuda verificada: '+value('verified_support_seconds'),
        f"Shares aceitas: sua carteira {shares.get('accepted_primary_shares','N/D')} · autor {shares.get('accepted_support_shares','N/D')}",
        f"Shares rejeitadas: sua carteira {shares.get('rejected_primary_shares','N/D')} · autor {shares.get('rejected_support_shares','N/D')}",
        'Origem: '+status.get('audit_source','proxy atual; contadores persistentes'),
        '',
        'A proporção é de tempo autorizado: até 3 min para 57 min primários verificados. Não movimenta o saldo do usuário e não garante 5% das moedas. Créditos dependem das shares e da pool.',
        '',
        'A carteira padrão do autor recebe diretamente; nesse caso não há 5% adicional. Desative a ajuda em Configurar quando desejar.',
    ])
