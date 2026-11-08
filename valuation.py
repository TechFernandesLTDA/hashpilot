"""Equivalentes monetários de PRL; não representam pagamentos em USD ou BTC."""
from __future__ import annotations
import math

from data_contracts import CurrencyValues


def numeric(value: object, positive: bool = False) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        result = float(value)  # type: ignore[arg-type]
    except (ValueError, TypeError, OverflowError):
        return None
    if not math.isfinite(result) or (positive and result <= 0):
        return None
    return result


def value_in_currencies(prl: object, price_brl: object = None, price_usd: object = None, bitcoin_usd: object = None, bitcoin_brl: object = None) -> CurrencyValues:
    amount = numeric(prl)
    brl, usd = numeric(price_brl, True), numeric(price_usd, True)
    btc_usd, btc_brl = numeric(bitcoin_usd, True), numeric(bitcoin_brl, True)
    result: CurrencyValues = {"prl": amount, "brl": None, "usd": None, "btc": None}
    if amount is None:
        return result
    result["brl"] = amount*brl if brl is not None else None
    result["usd"] = amount*usd if usd is not None else None
    if usd is not None and btc_usd is not None:
        result["btc"] = amount*usd/btc_usd
    elif brl is not None and btc_brl is not None:
        result["btc"] = amount*brl/btc_brl
    return {"prl": numeric(result["prl"]), "brl": numeric(result["brl"]), "usd": numeric(result["usd"]), "btc": numeric(result["btc"])}
