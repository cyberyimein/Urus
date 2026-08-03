from __future__ import annotations

import argparse
from collections.abc import Iterable

import pandas as pd
from futu import OpenQuoteContext, RET_OK


DEFAULT_UNDERLYINGS = ("US.SPY", "US.QQQ", "US.SMH", "US.IGV", "US.INTC")


def _require_ok(label: str, result: tuple[int, object]) -> object:
    ret, data = result
    if ret != RET_OK:
        raise RuntimeError(f"{label} failed: {data}")
    return data


def _quota_summary(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        return {"raw_type": type(value).__name__}
    keys = (
        "total_used",
        "remain",
        "option_used_quota",
        "option_remain_quota",
        "own_option_used_quota",
    )
    return {key: value.get(key) for key in keys}


def _select_atm_contracts(chain: pd.DataFrame, spot: float) -> list[str]:
    selected: list[str] = []
    for option_type in ("CALL", "PUT"):
        candidates = chain[chain["option_type"] == option_type].copy()
        if candidates.empty:
            continue
        candidates["distance"] = (candidates["strike_price"] - spot).abs()
        selected.append(str(candidates.sort_values("distance").iloc[0]["code"]))
    return selected


def _existing_columns(frame: pd.DataFrame, names: Iterable[str]) -> list[str]:
    return [name for name in names if name in frame.columns]


def run_probe(host: str, port: int, underlyings: tuple[str, ...]) -> None:
    quote_ctx = OpenQuoteContext(host=host, port=port)
    try:
        before = _require_ok("query_subscription(before)", quote_ctx.query_subscription())
        print("subscription_before", _quota_summary(before))

        underlying_snapshot = _require_ok(
            "get_market_snapshot(underlyings)",
            quote_ctx.get_market_snapshot(list(underlyings)),
        )
        assert isinstance(underlying_snapshot, pd.DataFrame)
        print(
            underlying_snapshot[
                _existing_columns(
                    underlying_snapshot,
                    ("code", "update_time", "last_price", "bid_price", "ask_price"),
                )
            ].to_string(index=False)
        )

        overview = _require_ok(
            "get_option_underlying_overview",
            quote_ctx.get_option_underlying_overview(list(underlyings)),
        )
        assert isinstance(overview, pd.DataFrame)
        print(
            overview[
                _existing_columns(
                    overview,
                    (
                        "code",
                        "call_volume",
                        "put_volume",
                        "call_open_interest",
                        "put_open_interest",
                        "iv",
                        "iv_rank",
                        "iv_percentile",
                        "hv_30d",
                    ),
                )
            ].to_string(index=False)
        )

        option_codes: list[str] = []
        for underlying in underlyings:
            expirations = _require_ok(
                f"get_option_expiration_date({underlying})",
                quote_ctx.get_option_expiration_date(underlying),
            )
            assert isinstance(expirations, pd.DataFrame)
            future_expirations = expirations[
                expirations["option_expiry_date_distance"] >= 0
            ].sort_values("option_expiry_date_distance")
            if future_expirations.empty:
                raise RuntimeError(f"no active option expiration for {underlying}")
            nearest_expiry = str(future_expirations.iloc[0]["strike_time"])

            chain = _require_ok(
                f"get_option_chain({underlying}, {nearest_expiry})",
                quote_ctx.get_option_chain(
                    underlying,
                    start=nearest_expiry,
                    end=nearest_expiry,
                ),
            )
            assert isinstance(chain, pd.DataFrame)
            spot = float(
                underlying_snapshot.loc[
                    underlying_snapshot["code"] == underlying, "last_price"
                ].iloc[0]
            )
            selected = _select_atm_contracts(chain, spot)
            option_codes.extend(selected)
            print(
                f"chain {underlying}: expiry={nearest_expiry} "
                f"contracts={len(chain)} selected={selected}"
            )

        option_snapshot = _require_ok(
            "get_market_snapshot(options)",
            quote_ctx.get_market_snapshot(option_codes),
        )
        assert isinstance(option_snapshot, pd.DataFrame)
        print(
            option_snapshot[
                _existing_columns(
                    option_snapshot,
                    (
                        "code",
                        "update_time",
                        "last_price",
                        "bid_price",
                        "ask_price",
                        "volume",
                        "option_open_interest",
                        "option_implied_volatility",
                        "option_delta",
                        "option_gamma",
                        "option_vega",
                        "option_theta",
                    ),
                )
            ].to_string(index=False)
        )

        after = _require_ok("query_subscription(after)", quote_ctx.query_subscription())
        print("subscription_after", _quota_summary(after))
        if _quota_summary(before) != _quota_summary(after):
            raise RuntimeError("subscription quota changed during snapshot-only probe")
    finally:
        quote_ctx.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Probe Moomoo option snapshots for core ETFs and watchlist symbols."
    )
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=11111)
    parser.add_argument(
        "--symbols",
        default=",".join(DEFAULT_UNDERLYINGS),
        help="Comma-separated symbols; US. prefix is optional.",
    )
    args = parser.parse_args()
    underlyings = tuple(
        symbol if "." in symbol else f"US.{symbol.upper()}"
        for item in args.symbols.split(",")
        if (symbol := item.strip())
    )
    run_probe(args.host, args.port, underlyings)


if __name__ == "__main__":
    main()
