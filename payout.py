"""Previsão de pagamento da pool: estimativa explícita, nunca um cronômetro do servidor."""
from __future__ import annotations
import math
import time

PAYOUT_CYCLE_SECONDS = 3600  # Regra pública da Kryptex para Pearl.


def number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def duration(seconds):
    seconds=number(seconds)
    if seconds is None:
        return "não disponível"
    minutes = max(0, math.ceil(seconds / 60))
    days, remainder = divmod(minutes, 1440)
    hours, minutes = divmod(remainder, 60)
    if days > 365:
        return "mais de 1 ano"
    parts = ([f"{days} d"] if days else []) + ([f"{hours} h"] if hours else [])
    if minutes or not parts:
        parts.append(f"{minutes} min")
    return " ".join(parts)


def observed_rate(state, now):
    points = {}
    for point in state.get("snapshots", []):
        if not isinstance(point,dict):continue
        stamp, total = number(point.get("timestamp")), number(point.get("total_prl"))
        if stamp is not None and total is not None and now-3600 <= stamp <= now+5:
            points[stamp] = total
    ordered = sorted(points.items())
    if len(ordered) < 2:
        return None, None
    span = ordered[-1][0] - ordered[0][0]
    change = ordered[-1][1] - ordered[0][1]
    if span < 1800 or change <= 0:
        return None, None
    return change / span * 86400, span


def payout_model(state, forecast=None, price_brl=None, now=None):
    now = time.time() if now is None else now
    sources = state.get("sources", {})
    balance_source = sources.get("balance", {})
    balance = balance_source.get("data", {})
    network = sources.get("network", {}).get("data", {})
    threshold = number(balance.get("threshold"))
    threshold_source = "carteira"
    if not threshold:
        threshold = number(network.get("minpay")) or None
        threshold_source = "mínimo geral da pool"
    total, confirmed, pending = (number(balance.get(key)) for key in ("total", "confirmed", "unconfirmed"))
    price = number(price_brl)
    rate, span = observed_rate(state, now)
    rate_label = f"Ritmo observado na pool nos últimos {span/60:.0f} min" if rate else "Ritmo ainda não estimável"
    if rate is None and forecast:
        rate = number(forecast.get("prl_day")) or None
        if rate:
            rate_label = "Estimativa pela rede e pelo hashrate atual"
    maturation = number(balance.get("maturation_time"))
    if maturation is None:
        blocks, block_time = number(network.get("blocks_for_maturation")), number(network.get("block_time"))
        if blocks is not None and block_time is not None:
            maturation = blocks * block_time
    source_at = number(balance_source.get("fetched_at"))
    stale = source_at is None or not -5<=now-source_at<=180 or bool(state.get("errors", {}).get("balance"))
    missing = max(0.0, threshold-total) if threshold is not None and total is not None else None
    missing_confirmed = max(0.0, threshold-confirmed) if threshold is not None and confirmed is not None else None
    results = sources.get("payout_history", {}).get("data", {}).get("results", [])
    payments = [p for p in results if isinstance(p, dict) and number(p.get("date")) is not None and number(p['date'])<=now+5]
    last_payment = None
    if payments:
        payment = max(payments, key=lambda p: float(p["date"]))
        last_payment = {"date": float(payment["date"]), "timestamp": float(payment["date"]),
                        "amount_prl": number(payment.get("received")), "amount": number(payment.get("received")),
                        "txid": str(payment.get("txid", "")), "status": str(payment.get("status", ""))}
    output = {"threshold_prl": threshold, "threshold_brl": threshold*price if threshold is not None and price is not None else None,
              "threshold_source": threshold_source, "total_prl": total, "confirmed_prl": confirmed, "unconfirmed_prl": pending,
              "remaining_prl": missing, "remaining_confirmed_prl": missing_confirmed,
              "remaining_brl": missing*price if missing is not None and price is not None else None,
              "progress_pct": min(100.0, total/threshold*100) if total is not None and threshold else None,
              "confirmed_progress_pct": min(100.0, confirmed/threshold*100) if confirmed is not None and threshold else None,
              "rate_prl_day": rate, "rate_label": rate_label, "maturation_seconds": maturation,
              "maturation_label": duration(maturation), "maturation_blocks": number(network.get("blocks_for_maturation")),
              "payout_cycle_seconds": PAYOUT_CYCLE_SECONDS, "last_payment": last_payment,
              "source_at": source_at, "stale": stale, "eta_min_seconds": None, "eta_max_seconds": None,
              "generation_seconds": None, "state_label": "Consultando saldo da pool", "eta_label": "Aguardando dados da pool",
              "eta_details": "A pool paga automaticamente após atingir o mínimo confirmado. Depois do envio, a carteira depende da confirmação e sincronização da rede."}
    if stale:
        output.update(state_label="Saldo ainda não atualizado", eta_label="Estimativa indisponível: aguarde a atualização da pool")
        return output
    if not threshold or total is None or confirmed is None:
        return output
    if confirmed >= threshold-1e-9:
        output.update(state_label="Mínimo confirmado atingido; aguardando envio", eta_min_seconds=0,
                      eta_max_seconds=PAYOUT_CYCLE_SECONDS,
                      eta_label="Próximo ciclo automático: aproximadamente até 1 h",
                      eta_details="O mínimo já está confirmado. A pool processa pagamentos uma vez por hora, sem publicar o instante exato da próxima rodada. Após o envio, aguarde confirmação/sincronização da carteira.")
    elif total >= threshold-1e-9:
        output.update(state_label="Mínimo gerado; aguardando confirmação", eta_min_seconds=0,
                      eta_max_seconds=maturation+PAYOUT_CYCLE_SECONDS if maturation is not None else None,
                      eta_label=(f"Janela estimada: agora até cerca de {duration(maturation+PAYOUT_CYCLE_SECONDS)}" if maturation is not None else "Aguardando confirmação; prazo exato não disponível"),
                      eta_details="O total inclui créditos pendentes e ainda não habilita o envio. A maturação indicada pela pool é uma duração típica, não o tempo restante individual de cada crédito. A janela pode variar; após confirmar, há o próximo ciclo de pagamento e a confirmação da transação.")
    elif rate:
        generation = missing / rate * 86400
        maturation_delay = maturation if maturation is not None else 0
        earliest = generation + maturation_delay
        output.update(state_label="Acumulando até o mínimo de pagamento", generation_seconds=generation,
                      eta_min_seconds=earliest, eta_max_seconds=earliest+PAYOUT_CYCLE_SECONDS,
                      eta_label=f"Estimativa de envio: {duration(earliest)} a {duration(earliest+PAYOUT_CYCLE_SECONDS)}",
                      eta_details=f"Faltam cerca de {duration(generation)} para gerar o mínimo, se o ritmo continuar. " +
                      (f"Foi incluída uma maturação típica de {duration(maturation_delay)}, " if maturation_delay else "") +
                      "mais até aproximadamente 1 h de espera pelo ciclo. Não é um cronômetro da pool; dificuldade, créditos pendentes e confirmação/sincronização da carteira podem mudar o prazo.")
        if maturation is None:
            output.update(eta_min_seconds=None, eta_max_seconds=None,
                          eta_label=f"Para gerar o mínimo: cerca de {duration(generation)}; confirmação sem prazo disponível",
                          eta_details="O prazo de geração usa o ritmo atual. Sem uma duração de maturação informada pela pool/rede, ainda não é possível estimar o envio completo. Após confirmar, há o ciclo horário e a confirmação/sincronização da carteira.")
    else:
        output.update(state_label="Acumulando até o mínimo de pagamento", eta_label="Sem ritmo suficiente para estimar o prazo",
                      eta_details="Ainda faltam créditos para o mínimo. A estimativa aparece quando houver ritmo observado suficiente ou hashrate/rede atuais. A confirmação dos créditos e o ciclo de pagamento são etapas adicionais.")
    return output
