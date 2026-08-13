from __future__ import annotations

from math import isfinite
from statistics import fmean, median, pstdev
from typing import Any


def _number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(float(value)):
        raise ValueError("value must be a finite number")
    return float(value)


def level_distances(spot: float, levels: dict[str, float]) -> dict[str, Any]:
    spot_value = _number(spot)
    result = {}
    for name, raw_level in levels.items():
        level = _number(raw_level)
        distance = level - spot_value
        result[str(name)] = {
            "level": level,
            "signed_distance": distance,
            "absolute_distance": abs(distance),
            "percent_distance": distance / abs(spot_value) * 100 if spot_value else None,
            "relation": "above" if distance > 0 else "below" if distance < 0 else "at",
        }
    return {"spot": spot_value, "levels": result}


def option_payoff(
    prices: list[float],
    legs: list[dict[str, Any]],
    multiplier: float | None = None,
) -> dict[str, Any]:
    if not legs or not prices:
        raise ValueError("prices and legs are required")
    multiplier_value = _number(multiplier) if multiplier is not None else None
    if multiplier_value is not None and multiplier_value <= 0:
        raise ValueError("multiplier must be positive")
    normalized: list[dict[str, Any]] = []
    missing: list[str] = []
    expirations: set[str] = set()
    for index, leg in enumerate(legs):
        option_type = str(leg.get("option_type", "")).lower()
        side = str(leg.get("side", "")).lower()
        if option_type not in {"call", "put"} or side not in {"buy", "sell"}:
            raise ValueError(f"invalid leg {index}")
        quantity = int(leg.get("quantity", 0))
        if quantity <= 0:
            raise ValueError(f"leg {index} quantity must be positive")
        premium = leg.get("premium")
        if premium is None:
            missing.append(f"legs[{index}].premium")
        else:
            premium = _number(premium)
            if premium < 0:
                raise ValueError(f"leg {index} premium must not be negative")
        expiration = str(leg.get("expiration") or "").strip()
        if expiration:
            expirations.add(expiration)
        normalized.append({"option_type": option_type, "side": side, "strike": _number(leg["strike"]), "quantity": quantity, "premium": premium, "expiration": expiration})
    if multiplier_value is None:
        missing.append("multiplier")
    # A terminal payoff for a calendar requires an implied-volatility/time
    # model.  Do not present a same-expiry intrinsic payoff as exact calendar
    # economics just because the legs contain premiums.
    calendar = len(expirations) > 1
    if calendar:
        missing.append("calendar_valuation")
    complete = not missing
    def profit_at(price: float) -> float:
        if multiplier_value is None:
            return 0.0
        total = 0.0
        for leg in normalized:
            intrinsic = max(price - leg["strike"], 0.0) if leg["option_type"] == "call" else max(leg["strike"] - price, 0.0)
            direction = 1 if leg["side"] == "buy" else -1
            payoff = intrinsic * leg["quantity"] * multiplier_value
            premium_value = (leg["premium"] or 0.0) * leg["quantity"] * multiplier_value
            total += direction * (payoff - premium_value)
        return total

    scenarios = []
    for raw_price in prices:
        price = _number(raw_price)
        leg_payoffs = []
        for leg in normalized:
            intrinsic = max(price - leg["strike"], 0.0) if leg["option_type"] == "call" else max(leg["strike"] - price, 0.0)
            direction = 1 if leg["side"] == "buy" else -1
            payoff = intrinsic * leg["quantity"] * (multiplier_value or 0.0)
            premium_value = (leg["premium"] or 0.0) * leg["quantity"] * (multiplier_value or 0.0)
            profit = direction * (payoff - premium_value)
            leg_payoffs.append({"payoff": payoff, "profit": profit})
        scenarios.append({"underlying_price": price, "legs": leg_payoffs, "total_profit": profit_at(price) if complete else None})
    exact_values: dict[str, Any] = {"net_debit_or_credit": None, "max_profit": None, "max_loss": None, "breakevens": []}
    if complete:
        call_slope = sum((1 if leg["side"] == "buy" else -1) * leg["quantity"] * multiplier_value for leg in normalized if leg["option_type"] == "call")
        put_slope = sum((1 if leg["side"] == "buy" else -1) * leg["quantity"] * multiplier_value for leg in normalized if leg["option_type"] == "put")
        bounded = call_slope == 0 and put_slope == 0
        exact_values["bounded"] = bounded
        if bounded:
            critical_prices = sorted({*[_number(price) for price in prices], *[leg["strike"] for leg in normalized]})
            totals = [profit_at(price) for price in critical_prices]
            exact_values.update({"max_profit": max(totals), "max_loss": min(totals)})
        else:
            exact_values["unbounded_sides"] = {"upper": "unbounded" if call_slope else "bounded", "lower": "unbounded" if put_slope else "bounded"}
        crossing = []
        for left, right in zip(scenarios, scenarios[1:]):
            left_profit, right_profit = left["total_profit"], right["total_profit"]
            if left_profit == 0:
                crossing.append(left["underlying_price"])
            elif left_profit * right_profit < 0:
                ratio = abs(left_profit) / (abs(left_profit) + abs(right_profit))
                crossing.append(left["underlying_price"] + (right["underlying_price"] - left["underlying_price"]) * ratio)
        exact_values["breakevens"] = crossing
        exact_values["net_debit_or_credit"] = sum(
            (1 if leg["side"] == "buy" else -1) * (leg["premium"] or 0.0) * leg["quantity"] * multiplier_value
            for leg in normalized
        )
    return {"complete": complete, "missing_fields": list(dict.fromkeys(missing)), "multiplier": multiplier_value, "calendar": calendar, "scenarios": scenarios, **exact_values}


def risk_reward(entry: float, stop: float, target: float, direction: str) -> dict[str, Any]:
    entry_value, stop_value, target_value = _number(entry), _number(stop), _number(target)
    direction = direction.lower()
    if direction not in {"long", "short"}:
        raise ValueError("direction must be long or short")
    risk = entry_value - stop_value if direction == "long" else stop_value - entry_value
    reward = target_value - entry_value if direction == "long" else entry_value - target_value
    if risk <= 0 or reward < 0:
        raise ValueError("stop and target do not match direction")
    return {"risk": risk, "reward": reward, "risk_reward_ratio": reward / risk}


def position_size(account_value: float, max_risk_percent: float, entry: float, stop: float, multiplier: float = 1, max_position_percent: float | None = None) -> dict[str, Any]:
    account, risk_percent, entry_value, stop_value, multiplier_value = map(_number, (account_value, max_risk_percent, entry, stop, multiplier))
    if account <= 0 or risk_percent <= 0 or multiplier_value <= 0 or entry_value == stop_value:
        raise ValueError("account, risk percent and multiplier must be positive; entry cannot equal stop")
    risk_budget = account * risk_percent / 100
    risk_per_unit = abs(entry_value - stop_value) * multiplier_value
    quantity = int(risk_budget // risk_per_unit)
    if max_position_percent is not None:
        max_notional = account * _number(max_position_percent) / 100
        quantity = min(quantity, int(max_notional // (entry_value * multiplier_value)))
    return {"risk_budget": risk_budget, "risk_per_unit": risk_per_unit, "quantity": max(0, quantity), "actual_risk": max(0, quantity) * risk_per_unit}


def statistics(operation: str, values: list[float], value: float | None = None, other: list[float] | None = None) -> dict[str, Any]:
    clean = [_number(item) for item in values]
    if not clean or len(clean) > 5000:
        raise ValueError("values must contain between 1 and 5000 numbers")
    operation = operation.lower()
    if operation == "mean":
        return {"operation": operation, "value": fmean(clean)}
    if operation == "median":
        return {"operation": operation, "value": median(clean)}
    if operation == "standard_deviation":
        return {"operation": operation, "value": pstdev(clean)}
    if operation == "z_score":
        if value is None or pstdev(clean) == 0:
            raise ValueError("z_score requires value and non-zero standard deviation")
        return {"operation": operation, "value": (_number(value) - fmean(clean)) / pstdev(clean)}
    if operation == "correlation":
        if other is None or len(other) != len(clean) or len(clean) < 2:
            raise ValueError("correlation requires same-sized other values")
        other_values = [_number(item) for item in other]
        mean_x, mean_y = fmean(clean), fmean(other_values)
        covariance = sum((x - mean_x) * (y - mean_y) for x, y in zip(clean, other_values))
        denominator = (sum((x - mean_x) ** 2 for x in clean) * sum((y - mean_y) ** 2 for y in other_values)) ** 0.5
        return {"operation": operation, "value": covariance / denominator if denominator else None}
    if operation == "linear_regression":
        if other is None or len(other) != len(clean) or len(clean) < 2:
            raise ValueError("linear_regression requires same-sized y values")
        y_values = [_number(item) for item in other]
        mean_x, mean_y = fmean(clean), fmean(y_values)
        denominator = sum((x - mean_x) ** 2 for x in clean)
        if denominator == 0:
            raise ValueError("linear_regression requires varying x values")
        slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(clean, y_values)) / denominator
        return {"operation": operation, "slope": slope, "intercept": mean_y - slope * mean_x}
    raise ValueError(f"unsupported statistics operation: {operation}")
