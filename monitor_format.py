"""Formatação de valores do painel: ausente aparece como N/D, nunca como zero."""
from __future__ import annotations

import math


def fmt(value, suffix="", precision=0):
    value=safe_number(value)
    return "—" if value is None else f"{value:,.{precision}f}".replace(",", " ") + suffix


def safe_number(value):
    if isinstance(value,bool):return None
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError, OverflowError):
        return None


def format_prl(value):
    value = safe_number(value)
    return "N/D" if value is None else f"{value:.8f}".replace(".", ",") + " PRL"


def format_brl(value):
    value = safe_number(value)
    return "N/D" if value is None else "R$ " + f"{value:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def payout_for_local_state(model,data):
    """Do not turn a historical earning rate into a live ETA for a stopped rig."""
    payout=dict(model.get('payout') or {})
    devices=((data or {}).get('rig') or {}).get('devices') or []
    wallet=model.get('selected_wallet') or model.get('wallet')
    if wallet:devices=[d for d in devices if d.get('wallet')==wallet]
    remaining=safe_number(payout.get('remaining_prl'))
    if devices and all(d.get('miner_running') is False for d in devices) and remaining is not None and remaining>0:
        payout.update(eta_label='Sem previsão local: rig parado e mínimo ainda não atingido',
            eta_details='O saldo permanece na pool. É necessário mais trabalho creditado para atingir o mínimo; outros workers fora deste rig não são acompanhados.',
            rate_label='O ritmo histórico não comprova geração atual.')
    return payout


def thermal_description(control, compact=False):
    if control is None:
        return "Supervisor não iniciado"
    if control.get("error"):
        return "Erro: " + str(control["error"])[:(45 if compact else 140)]
    if not control.get("enabled"):
        return "Controle desativado"
    modes = {"hold": "Mantendo", "cooling": "Resfriando", "anticipating": "Antecipando calor",
             "recovering": "Recuperando", "emergency": "Emergência",'baseline':'Calibrando referência',
             'manual_baseline':'Benchmark solicitado','validating':'Validando ajuste','accepted':'Ajuste validado',
             'rollback':'Revertendo ajuste','insufficient_data':'Aguardando medições válidas'}
    mode = modes.get(control.get("mode"), str(control.get("mode", "Aguardando")))
    target = control.get("target_control") if control.get("mode") == "target" else None
    if isinstance(target, dict):
        fan = target.get("fan_pct")
        mode = f"Alvo {target.get('target_c', 0):.0f} °C" + (f" · ventoinha {fan:.0f}%" if isinstance(fan, (int, float)) else "")
        if target.get("ceiling_mhz"):
            mode += f" · teto {target['ceiling_mhz']:.0f} MHz"
        if not compact and target.get("reason") and not control.get("reason"):
            control = {**control, "reason": target["reason"]}
    clock = control.get("current_clock_mhz",(control.get('sample') or {}).get('clock_mhz',"—"))
    desired = control.get("desired_clock_mhz",(control.get('proposal') or {}).get('clock_mhz'))
    clock_text = f"{clock} → {desired} MHz" if desired is not None and desired != clock else f"{clock} MHz"
    age = control.get("age_seconds")
    age_text = f"{age:.0f} s" if age is not None else "sem horário"
    text = f"{mode} · {clock_text} · {age_text}"
    if control.get("stale"):
        text += " · desatualizado"
    if not compact and control.get("reason"):
        text += " — " + str(control["reason"])
    return text
