"""Origem e atualidade dos dados; projeção não é saldo confirmado."""
import time
from mining_health import finite

def quality(state,now=None):
    now=time.time() if now is None else now
    wallets=state.get('wallets') if state.get('schema_version')==2 else {'wallet':state}
    wallets=wallets if isinstance(wallets,dict) else {}
    rows=[]
    for wallet in wallets.values():
        sources=wallet.get('sources',{})
        for key in ('balance','price','network'):
            source=sources.get(key,{})
            stamp=source.get('fetched_at')
            if key=='price':
                quoted=(source.get('data') or {}).get('pearl-2',{}).get('last_updated_at')
                if finite(quoted) and finite(stamp):stamp=min(stamp,quoted)
            age=now-stamp if finite(stamp) and stamp<=now else None
            status='indisponível' if age is None else 'desatualizado' if age>180 or wallet.get('errors') else 'atual'
            rows.append({'source':key,'age_seconds':age,'status':status})
    status='indisponível' if not rows or any(r['status']=='indisponível' for r in rows) else 'parcial / desatualizado' if any(r['status']!='atual' for r in rows) else 'fontes atuais'
    return {'status':status,'sources':rows,'oldest_age_seconds':max((r['age_seconds'] for r in rows if r['age_seconds'] is not None),default=None),
        'labels':'Confirmado e pendente: informados pela pool. BRL/USD/BTC: conversões. Futuro: projeção variável, sem garantia.'}
