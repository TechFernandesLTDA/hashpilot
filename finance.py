"""Receitas observadas da pool e projeções, sem acesso a contas ou credenciais."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import json
from pathlib import Path
import queue
import threading
import time
import urllib.request
import copy

from config import WALLET, SETTINGS
from contribution import support_configuration
from payout import payout_model
from valuation import value_in_currencies, numeric as finite_number
BASE = "https://pool.kryptex.com/prl/api/v1/"


def normalize_finance_state(state,now=None):
    """Order real observations and tolerate malformed external/cache fields."""
    now=time.time() if now is None else now
    result=copy.deepcopy(state) if isinstance(state,dict) else {}
    sources=result.get('sources')
    result['sources']={}
    if isinstance(sources,dict):
        for key,source in sources.items():
            if not isinstance(source,dict):continue
            source=dict(source);data=source.get('data')
            if key=='rewards':
                if not isinstance(data,list):continue
            elif not isinstance(data,dict):continue
            source['fetched_at']=finite_number(source.get('fetched_at')) or 0
            if key=='price':
                for asset in ('pearl-2','bitcoin'):
                    asset_data=data.get(asset)
                    if not isinstance(asset_data,dict):data[asset]={};continue
                    for currency in ('brl','usd'):asset_data[currency]=finite_number(asset_data.get(currency),True)
                    if 'last_updated_at' in asset_data:asset_data['last_updated_at']=finite_number(asset_data['last_updated_at'])
            if key in ('balance','payouts','network'):
                fields={'balance':('total','confirmed','unconfirmed','threshold','maturation_time'),
                        'payouts':('paid',),'network':('net_hashrate','block_time','block_reward','fee','minpay','blocks_for_maturation')}
                for field in fields[key]:
                    if field not in data:continue
                    value=finite_number(data[field])
                    data[field]=value if value is not None and value>=0 else None
            result['sources'][key]=source
    points={}
    snapshots=result.get('snapshots')
    if isinstance(snapshots,list):
        for point in snapshots:
            if not isinstance(point,dict):continue
            stamp,total=finite_number(point.get('timestamp')),finite_number(point.get('total_prl'))
            if stamp is not None and 0<=stamp<=now+5 and total is not None and total>=0:
                points[stamp]={'timestamp':stamp,'total_prl':total}
    result['snapshots']=[points[stamp] for stamp in sorted(points)]
    if not isinstance(result.get('errors'),dict):result['errors']={}
    return result
URLS = {
    "price": "https://api.coingecko.com/api/v3/simple/price?ids=pearl-2,bitcoin&vs_currencies=brl,usd&include_last_updated_at=true",
    "balance": BASE + "miner/balance/" + WALLET,
    "payouts": BASE + "miner/payouts/" + WALLET + "/stats",
    "rewards": BASE + "miner/reward-chart/" + WALLET,
    "payout_history": BASE + "miner/payouts/" + WALLET,
    "network": BASE + "pool/info",
}
if not WALLET:
    URLS = {key: value for key, value in URLS.items() if key in ('price', 'network')}
LOCAL_TZ = dt.timezone(dt.timedelta(hours=-3), "America/Sao_Paulo")


def urls_for_wallet(wallet):
    result={key:value for key,value in URLS.items() if key in ('price','network')}
    if wallet:
        result.update(balance=BASE+'miner/balance/'+wallet,payouts=BASE+'miner/payouts/'+wallet+'/stats',
            rewards=BASE+'miner/reward-chart/'+wallet,payout_history=BASE+'miner/payouts/'+wallet)
    return result


class SharedMarketFetcher:
    """Coalesces only public price/network requests; wallet balances stay separate."""
    def __init__(self,fetcher=None,ttl=45):
        self.fetcher,self.ttl=fetcher or fetch_json,ttl
        self.cache={}
        self.lock=threading.Lock()

    def __call__(self,url):
        if url not in (URLS.get('price'),URLS.get('network')):
            return self.fetcher(url)
        with self.lock:
            cached=self.cache.get(url)
            if cached and time.monotonic()-cached[0]<self.ttl:
                return copy.deepcopy(cached[1])
            data=self.fetcher(url)
            self.cache[url]=(time.monotonic(),copy.deepcopy(data))
            return data


class PortfolioFinanceCollector:
    """One independent cache per public wallet, one balance counted once."""
    def __init__(self,state_dir,history=None,*,wallets=None,fetcher=None):
        self.state_dir=Path(state_dir)
        self.history=history
        self.stop=threading.Event()
        self.samples=queue.Queue(maxsize=2)
        self.lock=threading.Lock()
        self.wallets=set(wallets or [WALLET])
        self.collectors={}
        self.fetcher=SharedMarketFetcher(fetcher or fetch_json)
        self.state={'schema_version':2,'primary_wallet':WALLET,'wallets':{}}

    def configure_wallets(self,wallets):
        from contribution import valid_wallet
        desired={wallet for wallet in wallets if valid_wallet(wallet)}
        with self.lock: self.wallets=desired or {WALLET}

    def _collector(self,wallet):
        if wallet not in self.collectors:
            directory=self.state_dir if wallet==WALLET else self.state_dir/'wallets'/wallet
            collector=FinanceCollector(directory,self.history,wallet=wallet,fetcher=self.fetcher)
            if self.history:
                self.history.ready.wait(2)
                archived=self.history.finance_state(time.time()-35*86400,wallet=wallet)
                snapshots={point['timestamp']:point for point in archived.get('snapshots',[])}
                snapshots.update({point['timestamp']:point for point in collector.state['snapshots']})
                collector.state['snapshots']=sorted(snapshots.values(),key=lambda point:point['timestamp'])
                for key,value in archived.get('sources',{}).items():
                    if value['fetched_at']>collector.state['sources'].get(key,{}).get('fetched_at',0):
                        collector.state['sources'][key]=value
            self.collectors[wallet]=collector
        return self.collectors[wallet]

    def sample(self):
        with self.lock: wallets=sorted(self.wallets)
        collectors={wallet:self._collector(wallet) for wallet in wallets}
        states={}
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures={wallet:executor.submit(collector.sample) for wallet,collector in collectors.items()}
            for wallet,future in futures.items():
                try: states[wallet]=future.result()
                except Exception as error:
                    states[wallet]=copy.deepcopy(collectors[wallet].state)
                    states[wallet].setdefault('errors',{})['collector']=str(error)
        self.state={'schema_version':2,'primary_wallet':WALLET,'wallets':states,'attempted_at':time.time()}
        return copy.deepcopy(self.state)

    def work(self):
        while not self.stop.is_set():
            state=self.sample()
            try: self.samples.put_nowait(state)
            except queue.Full:
                try: self.samples.get_nowait()
                except queue.Empty: pass
                self.samples.put_nowait(state)
            self.stop.wait(60)


def portfolio_model(state,devices,now=None,selected_wallet=None,*,settings=None):
    """Sum unique-wallet credits; GPU-level earnings cannot be inferred from a pool balance."""
    now=time.time() if now is None else now
    settings=SETTINGS if settings is None else settings
    wallet_states=state.get('wallets') if state.get('schema_version')==2 else None
    wallet_states=wallet_states if isinstance(wallet_states,dict) else {state.get('wallet',WALLET):state}
    wallets={device.get('wallet') or settings.get('wallet',WALLET) for device in devices}
    if not wallets: wallets={state.get('primary_wallet',WALLET)}
    hashes={wallet:0.0 for wallet in wallets}
    missing={wallet:[] for wallet in wallets}
    for device in devices:
        miner=device.get('miner') or {}
        wallet=device.get('wallet') or settings.get('wallet',WALLET)
        from rig_panel import fresh_hash
        rate=fresh_hash({**device,'enabled':device.get('enabled',True)},now)
        if rate is not None:
            hashes[wallet]+=rate
        elif device.get('enabled',True) and device.get('miner_running') and not miner.get('paused'):
            missing[wallet].append(device.get('uuid'))
    common={}
    wallet_states={wallet:normalize_finance_state(cached,now) for wallet,cached in wallet_states.items()}
    for cached in wallet_states.values():
        for key in ('price','network'):
            value=cached.get('sources',{}).get(key)
            if value and value.get('fetched_at',0)>common.get(key,{}).get('fetched_at',0):common[key]=value
    models={}
    for wallet in sorted(wallets):
        cached=copy.deepcopy(wallet_states.get(wallet,{'wallet':wallet,'sources':{},'snapshots':[]}))
        for key,value in common.items():
            if value.get('fetched_at',0)>cached.get('sources',{}).get(key,{}).get('fetched_at',0):cached.setdefault('sources',{})[key]=value
        models[wallet]=financial_model(cached,None if missing[wallet] else hashes[wallet] or None,now,wallet=wallet,settings=settings)
        models[wallet]['forecast_missing_gpu_uuids']=missing[wallet]
    if selected_wallet in models or len(models)==1:
        key=selected_wallet if selected_wallet in models else next(iter(models))
        result=copy.deepcopy(models[key])
        result.update(scope='wallet',selected_wallet=key,wallet_count=len(models),wallet_models=models,
            hashrate_by_wallet=hashes,forecast_partial=bool(missing[key]),earnings_attribution='wallet_balance_not_individual_gpu')
        return result
    first=next(iter(models.values()))
    result={key:copy.deepcopy(first.get(key)) for key in ('price_brl','price_usd','price_at','price_btc_usd','price_btc_brl','btc_price_at','btc_price_stale')}
    def total(values):
        values=list(values)
        return sum(values) if values and all(value is not None for value in values) else None
    result.update(scope='portfolio',wallet=None,selected_wallet=None,wallet_count=len(models),wallet_models=models,
        hashrate_by_wallet=hashes,forecast_partial=any(missing.values()),forecast_missing_gpu_uuids=[identifier for items in missing.values() for identifier in items],
        earnings_attribution='unique_wallets',periods={},errors={},forecast=None)
    result['pool_at']=min((model.get('pool_at') or 0 for model in models.values()),default=0) or None
    result['snapshot_count']=sum(model.get('snapshot_count',0) for model in models.values())
    for wallet,model in models.items():
        for key,error in model.get('errors',{}).items(): result['errors'][wallet+':'+key]=error
    for period in ('day','week','month'):
        rows=[model['periods'][period] for model in models.values()]
        amount=total(row.get('prl') for row in rows)
        result['periods'][period]={'prl':amount,'brl':amount*result['price_brl'] if amount is not None and result['price_brl'] else None,
            'partial':any(row.get('partial') for row in rows) or amount is None,
            'since':min((row['since'] for row in rows if row.get('since') is not None),default=None)}
    for key in ('unpaid_prl','unconfirmed_prl','paid_prl','accumulated_prl'):
        result[key]=total(model.get(key) for model in models.values())
    result['accumulated_brl']=result['accumulated_prl']*result['price_brl'] if result['accumulated_prl'] is not None and result['price_brl'] else None
    active=[model.get('forecast') for wallet,model in models.items() if hashes[wallet]>0]
    if active and all(active):
        result['forecast']={key:sum(item.get(key,0) for item in active) for key in ('prl_day','brl_day','brl_7days','brl_30days','hashrate_ths')}
        result['forecast'].update(scope='unique_wallet_groups',support_fraction=None)
    confirmed=total(model['payout'].get('confirmed_prl') for model in models.values())
    result['payout']={'state':'multiple_wallets','state_label':f'{len(models)} carteiras · selecione uma para ver o pagamento',
        'eta_label':'Estimativa individual por carteira','threshold_prl':None,'remaining_prl':None,
        'confirmed_prl':confirmed,'unconfirmed_prl':result['unconfirmed_prl'],'total_prl':result['unpaid_prl'],
        'progress_pct':None,'confirmed_progress_pct':None,'eta_details':'Não existe um único mínimo ou prazo para carteiras diferentes.'}
    amounts={period:result['periods'][period]['prl'] for period in ('day','week','month')}
    amounts.update(accumulated=result['accumulated_prl'],unpaid=result['unpaid_prl'],confirmed=confirmed,
        pending=result['unconfirmed_prl'],paid=result['paid_prl'],forecast7=result['forecast']['prl_day']*7 if result['forecast'] else None,
        forecast30=result['forecast']['prl_day']*30 if result['forecast'] else None,threshold=None,remaining=None)
    result['currencies']={key:value_in_currencies(amount,result['price_brl'],result['price_usd'],result['price_btc_usd'],result['price_btc_brl']) for key,amount in amounts.items()}
    result['support_contribution']={'enabled':any(model.get('support_contribution',{}).get('enabled') for model in models.values()),'scope':'per_wallet'}
    return result


def portfolio_snapshots(state,wallets=None):
    """Observed as-of totals only after every selected wallet has a first sample."""
    if state.get('schema_version')!=2:
        return copy.deepcopy(state.get('snapshots',[]))
    states=state.get('wallets',{})
    identifiers=list(dict.fromkeys(wallets or states.keys()))
    if not identifiers:return []
    if len(identifiers)==1:return copy.deepcopy(states.get(identifiers[0],{}).get('snapshots',[]))
    events=[]
    for wallet in identifiers:
        snapshots=states.get(wallet,{}).get('snapshots',[])
        if not snapshots:return []
        events.extend((point['timestamp'],wallet,point['total_prl']) for point in snapshots)
    grouped={}
    for stamp,wallet,value in events:grouped.setdefault(stamp,{})[wallet]=value
    seen={};result=[]
    for stamp,values in sorted(grouped.items()):
        seen.update(values)
        if len(seen)==len(identifiers):result.append({'timestamp':stamp,'total_prl':sum(seen.values())})
    return result


def fetch_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "HashPilot/1.0 (Tech Fernandes; local read-only dashboard)", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=9) as response:
        return json.load(response)


class FinanceCollector:
    def __init__(self, state_dir: Path, history=None, *, wallet=None, fetcher=None):
        self.wallet=WALLET if wallet is None else wallet
        self.urls=urls_for_wallet(self.wallet)
        self.fetcher=fetcher or fetch_json
        self.path = state_dir / "finance-cache.json"
        self.samples = queue.Queue(maxsize=2)
        self.stop = threading.Event()
        self.state = {"wallet": self.wallet, "sources": {}, "snapshots": []}
        self.history = history
        try:
            cached = json.loads(self.path.read_text(encoding="utf-8-sig"))
            if isinstance(cached,dict) and cached.get("wallet") == self.wallet:
                self.state = normalize_finance_state(cached)
        except (OSError, ValueError):
            pass

    def sample(self):
        errors = {}
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {key: executor.submit(self.fetcher, url) for key, url in self.urls.items()}
            for key, future in futures.items():
                try:
                    payload = future.result()
                    if key=='rewards' and not isinstance(payload,list):raise ValueError('Histórico de recompensas inválido')
                    if key!='rewards' and not isinstance(payload,dict):raise ValueError('Resposta financeira precisa ser um objeto')
                    normalized=normalize_finance_state({'sources':{key:{'fetched_at':time.time(),'data':payload}}})['sources'].get(key,{})
                    payload=normalized.get('data',{})
                    if key == "price" and not (payload.get("pearl-2", {}).get("brl", 0) > 0):
                        raise ValueError("Cotação da Pearl (pearl-2) ausente")
                    if key == "balance" and payload.get('total') is None:
                        raise ValueError("Saldo total ausente na resposta da pool")
                    if key == "payout_history" and (not isinstance(payload, dict) or not isinstance(payload.get("results"), list)):
                        raise ValueError("Histórico de pagamentos ausente na resposta da pool")
                    if key == "payouts" and payload.get('paid') is None:
                        raise ValueError("Total pago ausente na resposta da pool")
                    self.state["sources"][key] = {"fetched_at": time.time(), "data": payload}
                except Exception as error:
                    errors[key] = str(error)
        sources = self.state["sources"]
        # paid + saldo atual (confirmado + pendente). Um saque desloca saldo para paid;
        # não soma novamente o próprio pagamento como uma nova recompensa.
        if "balance" not in errors and "payouts" not in errors and "balance" in sources and "payouts" in sources:
            balance = sources["balance"]["data"]
            paid = sources["payouts"]["data"]["paid"]
            total = float(paid) + float(balance["total"])
            snapshots = self.state["snapshots"]
            now = time.time()
            if not snapshots or now - snapshots[-1]["timestamp"] >= 45:
                snapshots.append({"timestamp": now, "total_prl": total})
        self.state["errors"] = errors
        self.state["attempted_at"] = time.time()
        if self.history:
            self.history.finance(self.state)
        # Apenas cache de trabalho. O arquivo permanente SQLite não é truncado.
        cutoff = time.time()-35*86400
        self.state["snapshots"] = [p for p in self.state["snapshots"] if p["timestamp"] >= cutoff]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            temporary = self.path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.path)
        except OSError as error:
            self.state["errors"]["cache"] = str(error)
        return json.loads(json.dumps(self.state))

    def work(self):
        if self.history:
            self.history.ready.wait(2)
            self.history.finance(self.state)  # migração idempotente do JSON legado
            archived = self.history.finance_state(time.time()-35*86400,wallet=self.wallet)
            snapshots = {p["timestamp"]: p for p in archived["snapshots"]}
            snapshots.update({p["timestamp"]: p for p in self.state["snapshots"]})
            self.state["snapshots"] = sorted(snapshots.values(), key=lambda p: p["timestamp"])
            for key, value in archived["sources"].items():
                if value["fetched_at"] > self.state["sources"].get(key, {}).get("fetched_at", 0):
                    self.state["sources"][key] = value
        if self.state["sources"]:
            self.publish(json.loads(json.dumps(self.state)))
        while not self.stop.is_set():
            self.publish(self.sample())
            self.stop.wait(60)

    def publish(self, state):
        try:
            self.samples.put_nowait(state)
        except queue.Full:
            try:
                self.samples.get_nowait()
            except queue.Empty:
                pass
            self.samples.put_nowait(state)


def financial_model(state: dict, hashrate_ths: float | None, now: float | None = None, *, wallet=None, settings=None):
    wallet=WALLET if wallet is None else wallet
    settings=SETTINGS if settings is None else settings
    now = time.time() if now is None else now
    state=normalize_finance_state(state,now)
    hashrate_ths=finite_number(hashrate_ths,True)
    sources = state.get("sources", {})
    price_source = sources.get("price", {})
    price_data = price_source.get("data", {}).get("pearl-2", {})
    price = price_data.get("brl")
    result = {"price_brl": price, "price_usd": price_data.get("usd"),
              "price_at": price_data.get("last_updated_at", price_source.get("fetched_at")),
              "pool_at": sources.get("balance", {}).get("fetched_at"),
              "errors": state.get("errors", {}), "periods": {}, "forecast": None,
              "reward_history": sources.get("rewards", {}).get("data", []), "snapshot_count": len(state.get("snapshots", []))}
    local = dt.datetime.fromtimestamp(now, LOCAL_TZ)
    midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
    boundaries = {"day": midnight.timestamp(), "week": (midnight-dt.timedelta(days=local.weekday())).timestamp(), "month": now-30*86400}
    snapshots = state.get("snapshots", [])
    for period, boundary in boundaries.items():
        before = [p for p in snapshots if p["timestamp"] <= boundary]
        after = [p for p in snapshots if p["timestamp"] >= boundary]
        # Consulta nominal de 60 s: tolerância de um intervalo adicional. Uma
        # amostra de dias atrás não cobre a virada de dia/semana após app fechado.
        boundary_covered = bool(before and boundary-before[-1]["timestamp"] <= 120)
        baseline = before[-1] if boundary_covered else (after[0] if after else None)
        end = snapshots[-1] if snapshots else None
        enough = bool(baseline and end and end["timestamp"] > baseline["timestamp"])
        amount = end["total_prl"]-baseline["total_prl"] if enough else None
        result["periods"][period] = {"prl": amount, "brl": amount*price if amount is not None and price else None,
                                      "partial": not boundary_covered, "since": baseline["timestamp"] if baseline else None}
    balance = sources.get("balance", {}).get("data", {})
    payouts = sources.get("payouts", {}).get("data", {})
    result["unpaid_prl"] = balance.get("total")
    result["unconfirmed_prl"] = balance.get("unconfirmed")
    result["paid_prl"] = payouts.get("paid")
    result["accumulated_prl"] = finite_number(balance['total']+payouts['paid']) if balance.get('total') is not None and payouts.get('paid') is not None else None
    result["accumulated_brl"] = result["accumulated_prl"]*price if result["accumulated_prl"] is not None and price else None
    network_source = sources.get("network", {})
    network = network_source.get("data", {})
    contribution = support_configuration({**settings,'wallet':wallet},wallet)
    result['support_contribution']=contribution
    current_sources=all(-5<=now-source.get('fetched_at',0)<=180 for source in (price_source,network_source))
    current_sources=current_sources and not any(state.get('errors',{}).get(key) for key in ('price','network'))
    pool_fee=finite_number(network.get('fee',0))
    if current_sources and hashrate_ths and price and network.get("net_hashrate") and network.get("block_time") and network.get("block_reward") and pool_fee is not None and 0<=pool_fee<=1:
        dev_fee = .02  # SRBMiner pearlhash: taxa exibida no próprio log.
        support_fraction = contribution['percent']/100 if contribution['enabled'] else 0
        prl_day = (hashrate_ths*1e12 / float(network["net_hashrate"]) * float(network["block_reward"])
                   * 86400 / float(network["block_time"]) * (1-pool_fee) * (1-dev_fee) * (1-support_fraction))
        result["forecast"] = {"prl_day": prl_day, "brl_day": prl_day*price, "brl_7days": prl_day*price*7,
                              "brl_30days": prl_day*price*30, "hashrate_ths": hashrate_ths,
                              "pool_fee": pool_fee, "dev_fee": dev_fee, "support_fraction":support_fraction,
                              "support_assumption":"configured_mining_time", "network_at": network_source.get("fetched_at")}
        if any(finite_number(result['forecast'][key]) is None for key in ('prl_day','brl_day','brl_7days','brl_30days')):result['forecast']=None
    result['forecast_unavailable_reason']=None if result['forecast'] else 'Hashrate, preço ou rede ausentes, inválidos ou desatualizados'
    result["wallet"] = wallet
    result["payout"] = payout_model(state, result["forecast"], price, now)
    bitcoin = price_source.get("data", {}).get("bitcoin", {})
    result["price_btc_usd"], result["price_btc_brl"] = bitcoin.get("usd"), bitcoin.get("brl")
    result["btc_price_at"] = bitcoin.get("last_updated_at", price_source.get("fetched_at")) if bitcoin else None
    result["btc_price_stale"] = not result["btc_price_at"] or not -5<=now-result["btc_price_at"]<=180
    amounts = {key: result["periods"][key]["prl"] for key in ("day", "week", "month")}
    amounts.update(accumulated=result["accumulated_prl"], unpaid=result["unpaid_prl"],
                   confirmed=balance.get("confirmed"), pending=result["unconfirmed_prl"], paid=result["paid_prl"],
                   forecast7=result["forecast"]["prl_day"]*7 if result["forecast"] else None,
                   forecast30=result["forecast"]["prl_day"]*30 if result["forecast"] else None,
                   threshold=result["payout"]["threshold_prl"], remaining=result["payout"]["remaining_prl"])
    result["currencies"] = {key: value_in_currencies(amount, price, result["price_usd"],
                               result["price_btc_usd"], result["price_btc_brl"]) for key, amount in amounts.items()}
    return result
