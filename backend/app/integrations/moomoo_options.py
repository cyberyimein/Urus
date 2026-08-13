from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, date, datetime
from math import isfinite
import socket
from time import monotonic, sleep
from typing import Any, Protocol

import pandas as pd

from app.analytics.options import OptionContract, summarize_expiration, trim_exposure_display


class OptionsCollectorAdapter(Protocol):
    def options_snapshot(self) -> dict[str, object]: ...

    def close(self) -> None: ...


class DisabledOptionsAdapter:
    def options_snapshot(self) -> dict[str, object]:
        return {
            "is_mock": True,
            "status": "not_implemented",
            "available": False,
            "provider": None,
            "symbols": [],
            "note": "Moomoo 期权快照未启用。",
        }

    def close(self) -> None:
        return None


class MoomooOptionsAdapter:
    """Snapshot-only Moomoo options collector; never calls subscribe()."""

    _connect_timeout_seconds = 3.0

    def __init__(
        self,
        *,
        host: str,
        port: int,
        symbols: list[str],
        target_dtes: list[int],
        max_dte: int,
        strike_range_percent: float,
        batch_size: int,
        quote_context_factory: Callable[..., Any] | None = None,
        snapshot_interval_seconds: float = 0.51,
        option_chain_interval_seconds: float = 3.05,
        gamma_profile_range_percent: float = 30.0,
        gamma_profile_points: int = 121,
        risk_free_rate_percent: float = 4.0,
        dividend_yield_percent: float = 0.0,
        monotonic_clock: Callable[[], float] = monotonic,
        sleeper: Callable[[float], None] = sleep,
    ) -> None:
        if not symbols:
            raise ValueError("at least one option target symbol is required")
        if not 1 <= batch_size <= 400:
            raise ValueError("Moomoo option snapshot batch size must be between 1 and 400")
        self.host = host
        self.port = port
        self.symbols = [self._market_code(item) for item in symbols]
        self.target_dtes = target_dtes
        self.max_dte = max_dte
        self.strike_range_percent = strike_range_percent
        self.batch_size = batch_size
        self.snapshot_interval_seconds = max(0.0, snapshot_interval_seconds)
        self.option_chain_interval_seconds = max(0.0, option_chain_interval_seconds)
        self.gamma_profile_range_percent = gamma_profile_range_percent
        self.gamma_profile_points = gamma_profile_points
        self.risk_free_rate_percent = risk_free_rate_percent
        self.dividend_yield_percent = dividend_yield_percent
        self._quote_context_factory = quote_context_factory
        self._monotonic_clock = monotonic_clock
        self._sleeper = sleeper
        self._last_snapshot_request_at: float | None = None
        self._last_option_chain_request_at: float | None = None
        self._quote_ctx: Any | None = None

    @staticmethod
    def _market_code(symbol: str) -> str:
        normalized = symbol.strip().upper()
        return normalized if "." in normalized else f"US.{normalized}"

    def _context(self) -> Any:
        if self._quote_ctx is None:
            factory = self._quote_context_factory
            if factory is None:
                from futu import OpenQuoteContext

                factory = OpenQuoteContext
                self._probe_endpoint()
            self._quote_ctx = factory(host=self.host, port=self.port)
        return self._quote_ctx

    def _probe_endpoint(self) -> None:
        """Fail before the SDK retry loop when OpenD is unreachable."""

        try:
            with socket.create_connection(
                (self.host, self.port),
                timeout=self._connect_timeout_seconds,
            ):
                return
        except OSError as exc:
            raise RuntimeError(
                f"无法连接 Moomoo OpenD {self.host}:{self.port}；"
                "请确认 OpenD 已启动且该端口可访问。"
            ) from exc

    @staticmethod
    def _require_ok(label: str, result: tuple[int, object]) -> object:
        ret, data = result
        if ret != 0:
            raise RuntimeError(f"{label} failed: {data}")
        return data

    @staticmethod
    def _number(value: object, default: float | None = None) -> float | None:
        try:
            result = float(value)
        except (TypeError, ValueError):
            return default
        return result if isfinite(result) else default

    @classmethod
    def _integer(cls, value: object) -> int:
        number = cls._number(value, 0.0)
        return max(0, int(number or 0))

    @staticmethod
    def _chunks(values: list[str], size: int) -> list[list[str]]:
        return [values[index : index + size] for index in range(0, len(values), size)]

    def _market_snapshot(self, codes: list[str], label: str) -> object:
        now = self._monotonic_clock()
        if self._last_snapshot_request_at is not None:
            remaining = self.snapshot_interval_seconds - (now - self._last_snapshot_request_at)
            if remaining > 0:
                self._sleeper(remaining)
        self._last_snapshot_request_at = self._monotonic_clock()
        return self._require_ok(label, self._context().get_market_snapshot(codes))

    def _option_chain(self, underlying: str, start: str, end: str) -> object:
        now = self._monotonic_clock()
        if self._last_option_chain_request_at is not None:
            remaining = self.option_chain_interval_seconds - (
                now - self._last_option_chain_request_at
            )
            if remaining > 0:
                self._sleeper(remaining)
        self._last_option_chain_request_at = self._monotonic_clock()
        label = f"get_option_chain({underlying}, {start}..{end})"
        result = self._context().get_option_chain(underlying, start=start, end=end)
        ret, data = result
        message = str(data).lower()
        if ret != 0 and ("频率" in message or "frequency" in message):
            self._sleeper(30.1)
            self._last_option_chain_request_at = self._monotonic_clock()
            result = self._context().get_option_chain(underlying, start=start, end=end)
        return self._require_ok(label, result)

    def _select_expirations(self, frame: pd.DataFrame) -> list[tuple[str, int]]:
        eligible = frame[
            (frame["option_expiry_date_distance"] >= 0)
            & (frame["option_expiry_date_distance"] <= self.max_dte)
        ].copy()
        if eligible.empty:
            return []
        selected_indexes = {
            (eligible["option_expiry_date_distance"] - target).abs().idxmin()
            for target in self.target_dtes
        }
        selected = eligible.loc[list(selected_indexes)].sort_values("option_expiry_date_distance")
        return [
            (str(row["strike_time"]), int(row["option_expiry_date_distance"]))
            for _, row in selected.iterrows()
        ]

    @staticmethod
    def _expiration_groups(
        expirations: list[tuple[str, int]],
    ) -> list[list[tuple[str, int]]]:
        groups: list[list[tuple[str, int]]] = []
        for expiration in expirations:
            if not groups:
                groups.append([expiration])
                continue
            group_start = date.fromisoformat(groups[-1][0][0])
            current = date.fromisoformat(expiration[0])
            if (current - group_start).days <= 30:
                groups[-1].append(expiration)
            else:
                groups.append([expiration])
        return groups

    def _snapshot_contracts(
        self,
        *,
        underlying: str,
        spot: float,
        expiration: str,
        chain: pd.DataFrame,
    ) -> list[OptionContract]:
        codes = [str(code) for code in chain["code"].tolist()]
        snapshots: list[pd.DataFrame] = []
        for chunk in self._chunks(codes, self.batch_size):
            frame = self._market_snapshot(
                chunk,
                f"get_market_snapshot({underlying} options)",
            )
            if isinstance(frame, pd.DataFrame):
                snapshots.append(frame)
        if not snapshots:
            return []
        snapshot = pd.concat(snapshots, ignore_index=True)
        static_by_code = chain.set_index("code").to_dict("index")
        contracts: list[OptionContract] = []
        for row in snapshot.to_dict("records"):
            code = str(row["code"])
            static = static_by_code.get(code)
            if static is None:
                continue
            multiplier = self._number(row.get("option_contract_multiplier"), 100.0) or 100.0
            contracts.append(
                OptionContract(
                    code=code,
                    option_type=str(static["option_type"]).upper(),
                    expiration=expiration,
                    strike=float(static["strike_price"]),
                    spot=spot,
                    multiplier=multiplier,
                    bid=self._number(row.get("bid_price")),
                    ask=self._number(row.get("ask_price")),
                    last=self._number(row.get("last_price")),
                    volume=self._integer(row.get("volume")),
                    open_interest=self._integer(row.get("option_open_interest")),
                    implied_volatility=self._number(row.get("option_implied_volatility")),
                    delta=self._number(row.get("option_delta")),
                    gamma=self._number(row.get("option_gamma")),
                    quote_time=str(row.get("update_time") or "") or None,
                )
            )
        return contracts

    def options_snapshot(self) -> dict[str, object]:
        context = self._context()
        subscription_before = self._require_ok(
            "query_subscription(before)", context.query_subscription()
        )
        underlying = self._market_snapshot(self.symbols, "get_market_snapshot(underlyings)")
        overview = self._require_ok(
            "get_option_underlying_overview", context.get_option_underlying_overview(self.symbols)
        )
        if not isinstance(underlying, pd.DataFrame) or not isinstance(overview, pd.DataFrame):
            raise RuntimeError("Moomoo returned an invalid options overview response")
        overview_by_code = overview.set_index("code").to_dict("index")
        underlying_by_code = underlying.set_index("code").to_dict("index")

        symbol_results: list[dict[str, object]] = []
        persistence_symbols: list[dict[str, object]] = []
        unavailable_symbols: list[str] = []
        warnings = [
            "Open interest is the latest exchange daily update, not a real-time position change.",
            "Modeled net GEX assumes calls are positive and puts are negative; dealer positions are unknown.",
        ]
        for code in self.symbols:
            quote = underlying_by_code.get(code)
            if quote is None:
                warnings.append(f"{code} underlying snapshot is unavailable")
                unavailable_symbols.append(code.removeprefix("US."))
                continue
            spot = self._number(quote.get("last_price"))
            if spot is None or spot <= 0:
                warnings.append(f"{code} has no valid underlying price")
                unavailable_symbols.append(code.removeprefix("US."))
                continue
            try:
                expirations = self._require_ok(
                    f"get_option_expiration_date({code})", context.get_option_expiration_date(code)
                )
            except Exception as exc:
                warnings.append(f"{code} expiration lookup failed: {exc}")
                unavailable_symbols.append(code.removeprefix("US."))
                continue
            if not isinstance(expirations, pd.DataFrame):
                warnings.append(f"{code} expiration response is invalid")
                unavailable_symbols.append(code.removeprefix("US."))
                continue

            expiration_results: list[dict[str, object]] = []
            persistence_expirations: list[dict[str, object]] = []
            selected_expirations = self._select_expirations(expirations)
            for group in self._expiration_groups(selected_expirations):
                group_start = group[0][0]
                group_end = group[-1][0]
                try:
                    group_chain = self._option_chain(code, group_start, group_end)
                except Exception as exc:
                    warnings.append(
                        f"{code} {group_start}..{group_end} option chain failed: {exc}"
                    )
                    continue
                if not isinstance(group_chain, pd.DataFrame) or group_chain.empty:
                    warnings.append(f"{code} {group_start}..{group_end} option chain is empty")
                    continue
                for expiration, days_to_expiry in group:
                    chain = group_chain[
                        group_chain["strike_time"].astype(str) == expiration
                    ].copy()
                    if chain.empty:
                        warnings.append(f"{code} {expiration} option chain is empty")
                        continue
                    try:
                        contracts = self._snapshot_contracts(
                            underlying=code,
                            spot=spot,
                            expiration=expiration,
                            chain=chain,
                        )
                    except Exception as exc:
                        warnings.append(f"{code} {expiration} option snapshots failed: {exc}")
                        continue
                    if not contracts:
                        warnings.append(f"{code} {expiration} has no contracts in option snapshots")
                        continue
                    analysis = summarize_expiration(
                        contracts,
                        expiration=expiration,
                        days_to_expiry=days_to_expiry,
                        gamma_profile_range_percent=self.gamma_profile_range_percent,
                        gamma_profile_points=self.gamma_profile_points,
                        risk_free_rate_percent=self.risk_free_rate_percent,
                        dividend_yield_percent=self.dividend_yield_percent,
                    )
                    trim_exposure_display(
                        analysis["exposure"],
                        spot=spot,
                        strike_range_percent=self.strike_range_percent,
                    )
                    expiration_results.append(analysis)
                    persistence_expirations.append(
                        {
                            "expiration": expiration,
                            "contracts": [asdict(contract) for contract in contracts],
                        }
                    )

            if not expiration_results:
                warnings.append(f"{code} returned no usable option expirations")
                unavailable_symbols.append(code.removeprefix("US."))
                continue

            overview_row = overview_by_code.get(code, {})
            symbol_results.append(
                {
                    "symbol": code.removeprefix("US."),
                    "spot": spot,
                    "spot_time": str(quote.get("update_time") or "") or None,
                    "overview": {
                        key: self._number(overview_row.get(key))
                        for key in (
                            "call_volume",
                            "put_volume",
                            "call_open_interest",
                            "put_open_interest",
                            "iv",
                            "iv_rank",
                            "iv_percentile",
                            "hv_30d",
                        )
                    },
                    "expirations": expiration_results,
                }
            )
            persistence_symbols.append(
                {
                    "symbol": code.removeprefix("US."),
                    "expirations": persistence_expirations,
                }
            )

        subscription_after = self._require_ok(
            "query_subscription(after)", context.query_subscription()
        )
        quota_keys = (
            "option_used_quota",
            "option_remain_quota",
            "own_option_used_quota",
        )
        before_quota = {key: subscription_before.get(key) for key in quota_keys}
        after_quota = {key: subscription_after.get(key) for key in quota_keys}
        if before_quota != after_quota:
            raise RuntimeError("option subscription quota changed in snapshot-only collector")

        captured_at = datetime.now(UTC).isoformat()
        return {
            "is_mock": False,
            "status": "available" if symbol_results else "unavailable",
            "available": bool(symbol_results),
            "provider": "moomoo_openapi",
            "source_mode": "snapshot",
            "captured_at": captured_at,
            "requested_symbols": [code.removeprefix("US.") for code in self.symbols],
            "unavailable_symbols": unavailable_symbols,
            "symbols": symbol_results,
            "subscription_quota": after_quota,
            "model_assumptions": [
                "DEX = delta × open_interest × contract_multiplier × spot.",
                "GEX = gamma × open_interest × contract_multiplier × spot² × 1%.",
                "Modeled net GEX assigns positive sign to calls and negative sign to puts.",
                "Positive/negative Gamma zones group strikes by modeled net GEX sign; values within 2% of the largest strike exposure are treated as neutral noise.",
                "Strike GEX sign-change markers interpolate between adjacent modeled strike zones; they are not a spot Gamma Profile or Gamma Flip.",
                f"Spot Gamma Profile reprices Black-Scholes gamma across ±{self.gamma_profile_range_percent:g}% of spot using {self.risk_free_rate_percent:g}% risk-free rate and {self.dividend_yield_percent:g}% dividend yield.",
                "Max pain is calculated independently for each expiration.",
            ],
            "warnings": warnings,
            "note": "Moomoo LV1 snapshot-derived DEX/GEX and max-pain analytics; no option subscription used.",
            "_persistence": {
                "captured_at": captured_at,
                "symbols": persistence_symbols,
            },
        }

    def close(self) -> None:
        if self._quote_ctx is not None:
            self._quote_ctx.close()
            self._quote_ctx = None
