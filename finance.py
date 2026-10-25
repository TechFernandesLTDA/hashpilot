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

from config import WALLET
from payout import payout_model
from valuation import value_in_currencies
BASE = "https://pool.kryptex.com/prl/api/v1/"
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


def fetch_json(url):
    request = urllib.request.Request(url, headers={"User-Agent": "HashPilot/1.0 (Tech Fernandes; local read-only dashboard)", "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=9) as response:
        return json.load(response)


class FinanceCollector:
    def __init__(self, state_dir: Path, history=None):
        self.path = state_dir / "finance-cache.json"
        self.samples = queue.Queue(maxsize=2)
        self.stop = threading.Event()
        self.state = {"wallet": WALLET, "sources": {}, "snapshots": []}
        self.history = history
        try:
            cached = json.loads(self.path.read_text(encoding="utf-8"))
            if cached.get("wallet") == WALLET:
                self.state = cached
        except (OSError, ValueError):
            pass

    def sample(self):
        errors = {}
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {key: executor.submit(fetch_json, url) for key, url in URLS.items()}
            for key, future in futures.items():
                try:
                    payload = future.result()
                    if key == "price" and not (payload.get("pearl-2", {}).get("brl", 0) > 0):
                        raise ValueError("Cotação da Pearl (pearl-2) ausente")
                    if key == "balance" and "total" not in payload:
                        raise ValueError("Saldo total ausente na resposta da pool")
                    if key == "payout_history" and (not isinstance(payload, dict) or not isinstance(payload.get("results"), list)):
                        raise ValueError("Histórico de pagamentos ausente na resposta da pool")
                    if key == "payouts" and "paid" not in payload:
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
            archived = self.history.finance_state(time.time()-35*86400)
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


def financial_model(state: dict, hashrate_ths: float | None, now: float | None = None):
    now = time.time() if now is None else now
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
    result["accumulated_prl"] = (float(balance["total"])+float(payouts["paid"])) if "total" in balance and "paid" in payouts else None
    result["accumulated_brl"] = result["accumulated_prl"]*price if result["accumulated_prl"] is not None and price else None
    network_source = sources.get("network", {})
    network = network_source.get("data", {})
    if hashrate_ths and price and network.get("net_hashrate") and network.get("block_time") and network.get("block_reward"):
        pool_fee = float(network.get("fee", 0))
        dev_fee = .02  # SRBMiner pearlhash: taxa exibida no próprio log.
        prl_day = (hashrate_ths*1e12 / float(network["net_hashrate"]) * float(network["block_reward"])
                   * 86400 / float(network["block_time"]) * (1-pool_fee) * (1-dev_fee))
        result["forecast"] = {"prl_day": prl_day, "brl_day": prl_day*price, "brl_7days": prl_day*price*7,
                              "brl_30days": prl_day*price*30, "hashrate_ths": hashrate_ths,
                              "pool_fee": pool_fee, "dev_fee": dev_fee, "network_at": network_source.get("fetched_at")}
    result["wallet"] = WALLET
    result["payout"] = payout_model(state, result["forecast"], price, now)
    bitcoin = price_source.get("data", {}).get("bitcoin", {})
    result["price_btc_usd"], result["price_btc_brl"] = bitcoin.get("usd"), bitcoin.get("brl")
    result["btc_price_at"] = bitcoin.get("last_updated_at", price_source.get("fetched_at")) if bitcoin else None
    result["btc_price_stale"] = not result["btc_price_at"] or now-result["btc_price_at"] > 180
    amounts = {key: result["periods"][key]["prl"] for key in ("day", "week", "month")}
    amounts.update(accumulated=result["accumulated_prl"], unpaid=result["unpaid_prl"],
                   confirmed=balance.get("confirmed"), pending=result["unconfirmed_prl"], paid=result["paid_prl"],
                   forecast7=result["forecast"]["prl_day"]*7 if result["forecast"] else None,
                   forecast30=result["forecast"]["prl_day"]*30 if result["forecast"] else None,
                   threshold=result["payout"]["threshold_prl"], remaining=result["payout"]["remaining_prl"])
    result["currencies"] = {key: value_in_currencies(amount, price, result["price_usd"],
                               result["price_btc_usd"], result["price_btc_brl"]) for key, amount in amounts.items()}
    return result
