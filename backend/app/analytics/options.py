from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import exp, isfinite, log, pi, sqrt


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


def classify_strike_gex_structure(
    rows: list[dict[str, object]],
) -> tuple[list[dict[str, object]], list[dict[str, object]], float]:
    """Apply the canonical noise threshold and derive contiguous GEX zones."""
    max_net_gex = max((abs(float(row["modeled_net_gex"])) for row in rows), default=0.0)
    noise_threshold = max_net_gex * 0.02
    for row in rows:
        value = float(row["modeled_net_gex"])
        if max_net_gex == 0 or abs(value) <= noise_threshold:
            row["gamma_regime"] = "neutral"
        else:
            row["gamma_regime"] = "positive" if value > 0 else "negative"

    zones: list[dict[str, object]] = []
    active_zone: dict[str, object] | None = None
    for row in rows:
        regime = str(row["gamma_regime"])
        if regime == "neutral":
            active_zone = None
            continue
        strike = float(row["strike"])
        exposure = float(row["modeled_net_gex"])
        if active_zone is not None and active_zone["sign"] == regime:
            active_zone["end_strike"] = strike
            active_zone["strike_count"] = int(active_zone["strike_count"]) + 1
            active_zone["total_modeled_net_gex"] = (
                float(active_zone["total_modeled_net_gex"]) + exposure
            )
            if abs(exposure) > abs(float(active_zone["peak_exposure"])):
                active_zone["peak_strike"] = strike
                active_zone["peak_exposure"] = exposure
        else:
            active_zone = {
                "sign": regime,
                "start_strike": strike,
                "end_strike": strike,
                "strike_count": 1,
                "total_modeled_net_gex": exposure,
                "peak_strike": strike,
                "peak_exposure": exposure,
            }
            zones.append(active_zone)

    sign_changes = [
        {
            "level": _rounded(
                (float(previous["end_strike"]) + float(current["start_strike"])) / 2,
                4,
            ),
            "from_sign": previous["sign"],
            "to_sign": current["sign"],
            "between_strikes": [previous["end_strike"], current["start_strike"]],
        }
        for previous, current in zip(zones, zones[1:])
        if previous["sign"] != current["sign"]
    ]
    return zones, sign_changes, noise_threshold


def trim_exposure_display(
    exposure: dict[str, object], *, spot: float, strike_range_percent: float
) -> None:
    """Keep full-chain totals/walls while limiting only the rendered strike rows."""
    rows = list(exposure.get("by_strike", []))
    lower = spot * (1 - strike_range_percent / 100)
    upper = spot * (1 + strike_range_percent / 100)
    display_rows = [row for row in rows if lower <= float(row["strike"]) <= upper]
    zones, sign_changes, noise_threshold = classify_strike_gex_structure(display_rows)
    exposure["calculation_strike_count"] = len(rows)
    exposure["display_strike_count"] = len(display_rows)
    exposure["by_strike"] = display_rows
    exposure["gamma_zones"] = zones
    exposure["strike_gex_sign_changes"] = sign_changes
    exposure.pop("gamma_flip_levels", None)
    exposure["gamma_noise_threshold"] = _rounded(noise_threshold, 2)


def calculate_spot_gamma_profile(
    contracts: list[OptionContract],
    *,
    days_to_expiry: int,
    range_percent: float = 30.0,
    point_count: int = 121,
    risk_free_rate_percent: float = 4.0,
    dividend_yield_percent: float = 0.0,
) -> dict[str, object]:
    """Reprice contract gamma across hypothetical spots and find zero-GEX crossings."""
    if not contracts or contracts[0].spot <= 0:
        return {"available": False, "points": [], "gamma_flip_levels": []}
    if range_percent <= 0 or point_count < 3:
        raise ValueError("spot gamma profile requires a positive range and at least 3 points")

    usable: list[tuple[OptionContract, float]] = []
    for item in contracts:
        iv = item.implied_volatility
        if item.open_interest <= 0 or iv is None or not isfinite(iv) or iv <= 0:
            continue
        sigma = iv / 100.0
        if 0 < sigma <= 5:
            usable.append((item, sigma))
    if not usable:
        return {"available": False, "points": [], "gamma_flip_levels": []}

    current_spot = contracts[0].spot
    point_count = point_count if point_count % 2 == 1 else point_count + 1
    lower = current_spot * (1 - range_percent / 100)
    upper = current_spot * (1 + range_percent / 100)
    step = (upper - lower) / (point_count - 1)
    time_years = max(days_to_expiry / 365.0, 1.0 / (365.0 * 24.0))
    risk_free_rate = risk_free_rate_percent / 100.0
    dividend_yield = dividend_yield_percent / 100.0
    sqrt_time = sqrt(time_years)
    normalizer = sqrt(2 * pi)

    points: list[dict[str, float]] = []
    for index in range(point_count):
        hypothetical_spot = current_spot if index == point_count // 2 else lower + step * index
        call_gex = 0.0
        put_gex = 0.0
        for item, sigma in usable:
            d1 = (
                log(hypothetical_spot / item.strike)
                + (risk_free_rate - dividend_yield + sigma**2 / 2) * time_years
            ) / (sigma * sqrt_time)
            density = exp(-(d1**2) / 2) / normalizer
            gamma = exp(-dividend_yield * time_years) * density / (
                hypothetical_spot * sigma * sqrt_time
            )
            gex = (
                gamma
                * item.open_interest
                * item.multiplier
                * hypothetical_spot**2
                * 0.01
            )
            if item.option_type == "CALL":
                call_gex += gex
            else:
                put_gex -= gex
        points.append(
            {
                "spot": float(_rounded(hypothetical_spot, 4) or hypothetical_spot),
                "call_gex": float(_rounded(call_gex, 2) or 0.0),
                "put_gex": float(_rounded(put_gex, 2) or 0.0),
                "net_gex": float(_rounded(call_gex + put_gex, 2) or 0.0),
            }
        )

    flip_levels: list[float] = []
    for left, right in zip(points, points[1:]):
        left_value = left["net_gex"]
        right_value = right["net_gex"]
        if left_value == 0:
            level = left["spot"]
        elif left_value * right_value < 0:
            level = left["spot"] + (right["spot"] - left["spot"]) * (
                -left_value / (right_value - left_value)
            )
        else:
            continue
        rounded_level = float(_rounded(level, 4) or level)
        if not flip_levels or abs(flip_levels[-1] - rounded_level) > 0.0001:
            flip_levels.append(rounded_level)

    primary_flip = (
        min(flip_levels, key=lambda level: abs(level - current_spot)) if flip_levels else None
    )
    current_point = points[point_count // 2]
    return {
        "available": True,
        "points": points,
        "gamma_flip_levels": flip_levels,
        "primary_gamma_flip": primary_flip,
        "current_spot": current_spot,
        "current_spot_net_gex": current_point["net_gex"],
        "usable_iv_contracts": len(usable),
        "range_percent": range_percent,
        "point_count": point_count,
        "time_years": _rounded(time_years, 8),
        "risk_free_rate_percent": risk_free_rate_percent,
        "dividend_yield_percent": dividend_yield_percent,
        "model": "Black-Scholes gamma; calls positive and puts negative by assumption.",
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
            signed_gex = gex if item.option_type == "CALL" else -gex
            row[key] += signed_gex
            row["absolute_gex"] += abs(gex)
            row["modeled_net_gex"] += signed_gex
            usable_gamma += 1

    rows = [
        {"strike": strike, **{key: _rounded(value, 2) for key, value in values.items()}}
        for strike, values in sorted(strike_rows.items())
    ]

    gamma_zones, sign_changes, gamma_noise_threshold = classify_strike_gex_structure(rows)

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
            "put_gamma": wall("put_gex", absolute=True),
            "absolute_gamma": wall("absolute_gex"),
            "modeled_net_gamma": wall("modeled_net_gex", absolute=True),
        },
        "by_strike": rows,
        "gamma_zones": gamma_zones,
        "strike_gex_sign_changes": sign_changes,
        "gamma_noise_threshold": _rounded(gamma_noise_threshold, 2),
        "usable_delta_contracts": usable_delta,
        "usable_gamma_contracts": usable_gamma,
    }


def summarize_expiration(
    contracts: list[OptionContract],
    *,
    expiration: str,
    days_to_expiry: int,
    gamma_profile_range_percent: float = 30.0,
    gamma_profile_points: int = 121,
    risk_free_rate_percent: float = 4.0,
    dividend_yield_percent: float = 0.0,
) -> dict[str, object]:
    exposure = calculate_exposure(contracts)
    return {
        "expiration": expiration,
        "days_to_expiry": days_to_expiry,
        "contract_count": len(contracts),
        "max_pain": calculate_max_pain(contracts),
        "expected_move": calculate_expected_move(contracts),
        "exposure": exposure,
        "spot_gamma_profile": calculate_spot_gamma_profile(
            contracts,
            days_to_expiry=days_to_expiry,
            range_percent=gamma_profile_range_percent,
            point_count=gamma_profile_points,
            risk_free_rate_percent=risk_free_rate_percent,
            dividend_yield_percent=dividend_yield_percent,
        ),
    }
