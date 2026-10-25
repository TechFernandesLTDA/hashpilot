"""Equivalentes monetários de PRL; não representam pagamentos em USD ou BTC."""
from __future__ import annotations
import math


def numeric(value, positive=False):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (ValueError, TypeError):
        return None
    if not math.isfinite(result) or (positive and result <= 0):
        return None
    return result


def value_in_currencies(prl, price_brl=None, price_usd=None, bitcoin_usd=None, bitcoin_brl=None):
    amount = numeric(prl)
    brl, usd = numeric(price_brl, True), numeric(price_usd, True)
    btc_usd, btc_brl = numeric(bitcoin_usd, True), numeric(bitcoin_brl, True)
    result = {"prl": amount, "brl": None, "usd": None, "btc": None}
    if amount is None:
        return result
    result["brl"] = amount*brl if brl is not None else None
    result["usd"] = amount*usd if usd is not None else None
    if usd is not None and btc_usd is not None:
        result["btc"] = amount*usd/btc_usd
    elif brl is not None and btc_brl is not None:
        result["btc"] = amount*brl/btc_brl
    return result
