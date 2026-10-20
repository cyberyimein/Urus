from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date


_GROUP_PRIORITY = ("A", "B", "C", "D")
_WINDOW_SHARD_IDS = ("G1", "G2", "G3")
_NON_US_VENUE_PREFIXES = {
    "AS", "AU", "BE", "BSE", "CA", "CH", "CN", "DE", "F", "FR", "HK", "ID",
    "IT", "JP", "KR", "LSE", "MC", "MI", "NA", "NZ", "SG", "SH", "SZ", "TO",
    "TSE", "TW", "VN",
}


def normalize_equity_symbol(value: object) -> str:
    """Normalize a US ticker without splitting share-class tickers such as BRK.B."""

    symbol = str(value or "").strip().upper()
    if symbol.startswith("US."):
        symbol = symbol[3:]
    if "." in symbol:
        prefix, suffix = symbol.split(".", 1)
        if prefix in _NON_US_VENUE_PREFIXES or suffix[:1].isdigit() or len(suffix) != 1:
            raise ValueError(f"intraday equity capture only supports US symbols: {value!r}")
    if (
        not symbol
        or symbol.startswith(".")
        or symbol.endswith(".")
        or any(character.isspace() for character in symbol)
        or ":" in symbol
    ):
        raise ValueError(f"invalid US equity symbol: {value!r}")
    return symbol


def build_intraday_equity_plan(
    groups: Mapping[str, Sequence[str]],
    *,
    slot_index: int,
    allowed_symbols: Sequence[str] | None = None,
) -> dict[str, object]:
    """Plan full-pool quotes and a deterministic rotating subset of D minutes."""

    if isinstance(slot_index, bool) or not isinstance(slot_index, int) or slot_index < 0:
        raise ValueError("slot_index must be a non-negative integer")
    if not isinstance(groups, Mapping):
        raise TypeError("groups must map A/B/C/D to symbol sequences")

    normalized_groups = {str(group).strip().upper(): symbols for group, symbols in groups.items()}
    unknown_groups = set(normalized_groups) - set(_GROUP_PRIORITY)
    if unknown_groups:
        raise ValueError(f"unsupported equity groups: {', '.join(sorted(unknown_groups))}")

    quote_symbols: list[str] = []
    group_by_symbol: dict[str, str] = {}
    for group in _GROUP_PRIORITY:
        members = normalized_groups.get(group, ())
        if isinstance(members, (str, bytes)) or not isinstance(members, Sequence):
            raise TypeError(f"group {group} must be a sequence of symbols")
        for raw_symbol in members:
            symbol = normalize_equity_symbol(raw_symbol)
            if symbol not in group_by_symbol:
                group_by_symbol[symbol] = group
                quote_symbols.append(symbol)

    if allowed_symbols is not None:
        allowed = {normalize_equity_symbol(symbol) for symbol in allowed_symbols}
        outside_scope = sorted(set(quote_symbols) - allowed)
        if outside_scope:
            raise ValueError(
                "equity groups contain symbols outside the task allowed scope: "
                + ", ".join(outside_scope)
            )

    core_symbols = [
        symbol for symbol in quote_symbols if group_by_symbol[symbol] in {"A", "B", "C"}
    ]
    d_symbols = sorted(
        symbol for symbol in quote_symbols if group_by_symbol[symbol] == "D"
    )
    rotation = slot_index % 3
    rotated_d_symbols = [
        symbol for index, symbol in enumerate(d_symbols) if index % 3 == rotation
    ]

    return {
        "quote_symbols": quote_symbols,
        "minute_symbols": core_symbols + rotated_d_symbols,
        "group_by_symbol": group_by_symbol,
        "slot_index": slot_index,
        "rotation": rotation,
    }


def normalize_equity_shard_symbols(
    value: Mapping[str, Sequence[str]],
    *,
    allowed_symbols: Sequence[str],
) -> dict[str, list[str]]:
    """Validate the opt-in, fixed G1/G2/G3 minute-symbol partition."""

    if not isinstance(value, Mapping):
        raise TypeError("equity_shard_symbols must map G1, G2, and G3 to symbol lists")
    supplied = {str(key).strip().upper(): members for key, members in value.items()}
    if set(supplied) != set(_WINDOW_SHARD_IDS):
        raise ValueError("equity_shard_symbols must define exactly G1, G2, and G3")

    allowed = {normalize_equity_symbol(symbol) for symbol in allowed_symbols}
    result: dict[str, list[str]] = {}
    assigned: set[str] = set()
    for shard_id in _WINDOW_SHARD_IDS:
        members = supplied[shard_id]
        if isinstance(members, (str, bytes)) or not isinstance(members, Sequence):
            raise TypeError(f"equity shard {shard_id} must be a sequence of symbols")
        normalized: list[str] = []
        for raw_symbol in members:
            symbol = normalize_equity_symbol(raw_symbol)
            if symbol not in allowed:
                raise ValueError(
                    f"equity shard {shard_id} contains a symbol outside task scope: {symbol}"
                )
            if symbol in assigned:
                raise ValueError(f"equity symbol {symbol} appears in more than one shard")
            assigned.add(symbol)
            normalized.append(symbol)
        if not normalized:
            raise ValueError(f"equity shard {shard_id} must not be empty")
        result[shard_id] = normalized

    missing = sorted(allowed - assigned)
    if missing:
        raise ValueError(
            "equity_shard_symbols must assign every task symbol; missing: "
            + ", ".join(missing)
        )
    return result


def build_minute_timestamp_policy(
    role: str,
    verification: object,
    *,
    market_timezone: str,
) -> dict[str, object]:
    """Bind the timestamp-boundary claim to explicit provider validation evidence."""

    if role not in {"start", "end"}:
        raise ValueError("minute timestamp role must be 'start' or 'end'")
    evidence = dict(verification) if isinstance(verification, Mapping) else {}
    evidence_date = str(evidence.get("evidence_trading_date") or "").strip()
    capture_check = str(evidence.get("capture_check_sha256") or "").strip().lower()
    valid_evidence = (
        evidence.get("provider") == "moomoo_openapi"
        and evidence.get("market") == "US"
        and evidence.get("interval") == "K_1M"
        and evidence.get("role") == role
        and _valid_iso_date(evidence_date)
        and len(capture_check) == 64
        and all(character in "0123456789abcdef" for character in capture_check)
    )
    return {
        "source_fields": ["time_key", "bar_time"],
        "role": role,
        "interval_seconds": 60,
        "naive_timestamp_timezone": market_timezone,
        "provider_boundary_verified": valid_evidence,
        "provider_boundary_evidence": evidence if valid_evidence else None,
        "enablement_note": (
            None
            if valid_evidence
            else "Provider boundary evidence is missing or does not match this capture policy."
        ),
    }


def _valid_iso_date(value: str) -> bool:
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False
