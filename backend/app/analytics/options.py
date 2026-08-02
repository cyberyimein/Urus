from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import isfinite


@dataclass(frozen=True)
class OptionContract:
    code: str
    option_type: str
    expiration: str
    strike: float
    spot: float
    multiplier: float
    bid: float | None
    ask: float | None
    last: float | None
    volume: int
    open_interest: int
    implied_volatility: float | None
    delta: float | None
    gamma: float | None
    quote_time: str | None = None

    @property
    def midpoint(self) -> float | None:
        if self.bid is None or self.ask is None or self.bid < 0 or self.ask < self.bid:
            return None
        return (self.bid + self.ask) / 2


def _rounded(value: float | None, digits: int = 4) -> float | None:
    if value is None or not isfinite(value):
        return None
    return round(value, digits)


def calculate_max_pain(contracts: list[OptionContract]) -> float | None:
    eligible = [item for item in contracts if item.open_interest > 0]
    candidate_strikes = sorted({item.strike for item in eligible})
    if not candidate_strikes:
        return None

    def payout(settlement: float) -> float:
        total = 0.0
        for item in eligible:
            if item.option_type == "CALL":
                intrinsic = max(settlement - item.strike, 0.0)
            else:
                intrinsic = max(item.strike - settlement, 0.0)
            total += intrinsic * item.open_interest * item.multiplier
        return total

    return min(candidate_strikes, key=lambda strike: (payout(strike), strike))


def calculate_expected_move(contracts: list[OptionContract]) -> dict[str, float | None]:
    if not contracts:
        return {"amount": None, "percent": None, "atm_strike": None}
    spot = contracts[0].spot
    strikes = sorted({item.strike for item in contracts})
    if not strikes or spot <= 0:
        return {"amount": None, "percent": None, "atm_strike": None}
    atm_strike = min(strikes, key=lambda strike: abs(strike - spot))
    by_type = {
        item.option_type: item.midpoint
        for item in contracts
        if item.strike == atm_strike and item.midpoint is not None
    }
    call_mid = by_type.get("CALL")
    put_mid = by_type.get("PUT")
    if call_mid is None or put_mid is None:
        return {"amount": None, "percent": None, "atm_strike": atm_strike}
    amount = call_mid + put_mid
    return {
        "amount": _rounded(amount),
        "percent": _rounded(amount / spot * 100),
        "atm_strike": atm_strike,
    }


def calculate_exposure(contracts: list[OptionContract]) -> dict[str, object]:
    strike_rows: dict[float, dict[str, float]] = defaultdict(
        lambda: {
            "call_dex": 0.0,
            "put_dex": 0.0,
            "net_dex": 0.0,
            "absolute_dex": 0.0,
            "call_gex": 0.0,
            "put_gex": 0.0,
            "modeled_net_gex": 0.0,
            "absolute_gex": 0.0,
        }
    )
    usable_delta = 0
    usable_gamma = 0
    for item in contracts:
        if item.open_interest <= 0 or item.spot <= 0 or item.multiplier <= 0:
            continue
        row = strike_rows[item.strike]
        if item.delta is not None and isfinite(item.delta):
            dex = item.delta * item.open_interest * item.multiplier * item.spot
            key = "call_dex" if item.option_type == "CALL" else "put_dex"
            row[key] += dex
            row["net_dex"] += dex
            row["absolute_dex"] += abs(dex)
            usable_delta += 1
        if item.gamma is not None and isfinite(item.gamma):
            gex = item.gamma * item.open_interest * item.multiplier * item.spot**2 * 0.01
            key = "call_gex" if item.option_type == "CALL" else "put_gex"
            row[key] += gex
            row["absolute_gex"] += abs(gex)
            row["modeled_net_gex"] += gex if item.option_type == "CALL" else -gex
            usable_gamma += 1

    rows = [
        {"strike": strike, **{key: _rounded(value, 2) for key, value in values.items()}}
        for strike, values in sorted(strike_rows.items())
    ]

    def wall(metric: str, *, absolute: bool = False) -> dict[str, float] | None:
        if not rows:
            return None
        selected = max(rows, key=lambda row: abs(float(row[metric])) if absolute else float(row[metric]))
        value = float(selected[metric])
        if value == 0:
            return None
        return {"strike": float(selected["strike"]), "exposure": value}

    return {
        "totals": {
            key: _rounded(sum(float(row[key]) for row in rows), 2)
            for key in (
                "call_dex",
                "put_dex",
                "net_dex",
                "absolute_dex",
                "call_gex",
                "put_gex",
                "modeled_net_gex",
                "absolute_gex",
            )
        },
        "walls": {
            "call_dex": wall("call_dex"),
            "put_dex": wall("put_dex", absolute=True),
            "net_dex": wall("net_dex", absolute=True),
            "call_gamma": wall("call_gex"),
            "put_gamma": wall("put_gex"),
            "absolute_gamma": wall("absolute_gex"),
            "modeled_net_gamma": wall("modeled_net_gex", absolute=True),
        },
        "by_strike": rows,
        "usable_delta_contracts": usable_delta,
        "usable_gamma_contracts": usable_gamma,
    }


def summarize_expiration(
    contracts: list[OptionContract], *, expiration: str, days_to_expiry: int
) -> dict[str, object]:
    exposure = calculate_exposure(contracts)
    return {
        "expiration": expiration,
        "days_to_expiry": days_to_expiry,
        "contract_count": len(contracts),
        "max_pain": calculate_max_pain(contracts),
        "expected_move": calculate_expected_move(contracts),
        "exposure": exposure,
    }
