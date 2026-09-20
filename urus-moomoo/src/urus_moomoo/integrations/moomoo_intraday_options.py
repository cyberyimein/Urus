from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
import hashlib
import json
import math
from typing import Any

from urus_moomoo.integrations.moomoo_options import MoomooOptionsAdapter, _pandas
from urus_moomoo.services.quota import detail_symbols


# ``futu-api`` uses the case-sensitive string ``"None"`` as the key for
# ``AuType.NONE``.  Keep the provider literal here rather than using the
# human-readable enum name ``"NONE"``; the SDK validates this before it sends
# a request to OpenD.
HISTORY_KLINE_AUTYPE_NONE = "None"


class MoomooIntradayOptionsAdapter(MoomooOptionsAdapter):
    """Narrow index-ETF point-in-time collector using the shared OpenD limiter."""

    def __init__(
        self,
        *args: Any,
        history_request_interval_seconds: float = 0.55,
        min_quote_coverage: float = 0.80,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self.history_request_interval_seconds = max(0.51, history_request_interval_seconds)
        self.min_quote_coverage = min(1.0, max(0.0, min_quote_coverage))

    @classmethod
    def _json_safe(cls, value: Any) -> Any:
        if value is None or isinstance(value, (str, bool, int)):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else None
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, dict):
            return {str(key): cls._json_safe(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._json_safe(item) for item in value]
        if hasattr(value, "item"):
            try:
                return cls._json_safe(value.item())
            except (TypeError, ValueError):
                pass
        return str(value)

    def _history_slot(self) -> None:
        if self._rate_limiter is not None:
            self._rate_limiter.acquire_rate_slot(
                "moomoo_quote_history",
                self.history_request_interval_seconds,
                now=datetime.now(UTC),
                sleeper=self._sleeper,
            )

    def _history_admission(self, codes: list[str]) -> dict[str, object]:
        """Give intraday research first claim on history-symbol capacity."""
        if self._rate_limiter is not None:
            self._rate_limiter.acquire_rate_slot(
                "moomoo_history_quota",
                self.history_request_interval_seconds,
                now=datetime.now(UTC),
                sleeper=self._sleeper,
            )
        context = self._context()
        if not hasattr(context, "get_history_kl_quota"):
            return {
                "available": False,
                "admitted_codes": [],
                "deferred_codes": codes,
                "warning": "OpenD does not expose historical K-line quota; minute bars paused.",
            }
        try:
            raw_result = context.get_history_kl_quota(get_detail=True)
            if not isinstance(raw_result, tuple) or len(raw_result) < 2:
                raise RuntimeError(f"unexpected quota response: {raw_result}")
            if raw_result[0] != 0:
                raise RuntimeError(f"get_history_kl_quota failed: {raw_result[1]}")
            raw = raw_result[1] if len(raw_result) == 2 else raw_result[1:]
            if isinstance(raw, tuple):
                used_value = self._number(raw[0]) if len(raw) >= 1 else None
                remain_value = self._number(raw[1]) if len(raw) >= 2 else None
                used = int(used_value) if used_value is not None else None
                remain = int(remain_value) if remain_value is not None else None
                details = raw[2] if len(raw) >= 3 else []
            elif isinstance(raw, dict):
                used_value = self._number(raw.get("used_quota", raw.get("usedQuota")))
                remain_value = self._number(
                    raw.get("remain_quota", raw.get("remainQuota"))
                )
                used = int(used_value) if used_value is not None else None
                remain = int(remain_value) if remain_value is not None else None
                details = raw.get("detail_list", raw.get("detailList", []))
            else:
                used, remain, details = None, None, []
            if remain is None:
                raise RuntimeError("quota response has no remaining-history count")
            total = used + remain if used is not None else None
            # Existing quota-window symbols cost no new slot. New symbols are
            # admitted only while OpenD reports remaining capacity.
            slots_left = remain
            reported_window = detail_symbols(details)
            requested_symbols = {code.removeprefix("US.") for code in codes}
            window = reported_window & requested_symbols
            admitted: list[str] = []
            deferred: list[str] = []
            for code in codes:
                symbol = code.removeprefix("US.")
                if symbol in window:
                    admitted.append(code)
                elif slots_left > 0:
                    admitted.append(code)
                    slots_left -= 1
                else:
                    deferred.append(code)
            return {
                "available": True,
                "used": used,
                "remain": remain,
                "total": total,
                "priority": "intraday_options_first",
                "window_symbols": sorted(window),
                "admitted_codes": admitted,
                "deferred_codes": deferred,
            }
        except Exception as exc:
            return {
                "available": False,
                "admitted_codes": [],
                "deferred_codes": codes,
                "warning": f"Historical K-line quota unavailable; minute bars paused: {exc}",
            }

    def _minute_bars(
        self, code: str, trading_date: str, raw_batches: list[dict[str, Any]]
    ) -> list[dict[str, object]]:
        pd = _pandas()
        frames: list[Any] = []
        page_req_key = None
        while True:
            self._history_slot()
            request_started_at = datetime.now(UTC)
            result = self._context().request_history_kline(
                code=code,
                start=trading_date,
                end=trading_date,
                ktype="K_1M",
                autype=HISTORY_KLINE_AUTYPE_NONE,
                max_count=1000,
                page_req_key=page_req_key,
                extended_time=False,
            )
            if len(result) == 3:
                ret, frame, page_req_key = result
            else:
                ret, frame = result
                page_req_key = None
            if ret != 0:
                raise RuntimeError(f"request_history_kline({code}, K_1M) failed: {frame}")
            response_received_at = datetime.now(UTC)
            raw_frame = frame if isinstance(frame, pd.DataFrame) else pd.DataFrame(frame)
            raw_batches.append({
                "code": code,
                "ktype": "K_1M",
                "request_started_at": request_started_at.isoformat(),
                "response_received_at": response_received_at.isoformat(),
                "records": self._json_safe(raw_frame.to_dict("records")),
            })
            frames.append(frame if isinstance(frame, pd.DataFrame) else pd.DataFrame(frame))
            if page_req_key is None:
                break
        frame = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
        rows: list[dict[str, object]] = []
        for item in frame.to_dict("records"):
            try:
                volume = self._number(item.get("volume"))
                turnover = self._number(item.get("turnover"))
                vwap = turnover / volume if turnover is not None and volume not in {None, 0} else None
                bar_time = datetime.fromisoformat(str(item["time_key"]))
                if bar_time.tzinfo is None:
                    bar_time = bar_time.replace(tzinfo=self._market_timezone())
                # Moomoo's timestamp is the start of the minute. Mark a still-
                # forming bar provisional; a later response becomes canonical
                # while the revision table preserves both observations.
                now = datetime.now(UTC)
                is_forming = (
                    bar_time.astimezone(self._market_timezone()).date()
                    == now.astimezone(self._market_timezone()).date()
                    and bar_time.astimezone(UTC) + timedelta(minutes=1) > now
                )
                rows.append(
                    {
                        "symbol": code.removeprefix("US."),
                        "bar_time": str(item["time_key"]),
                        "open": float(item["open"]),
                        "high": float(item["high"]),
                        "low": float(item["low"]),
                        "close": float(item["close"]),
                        "volume": volume,
                        "turnover": turnover,
                        "vwap": vwap,
                        "provider": "moomoo_openapi",
                        "quality_state": "provisional" if is_forming else "ready",
                    }
                )
            except (KeyError, TypeError, ValueError):
                continue
        return rows

    @staticmethod
    def _select_intraday_expirations(
        frame: Any, capture_tier: str, *, max_dte: int = 45
    ) -> list[tuple[str, int]]:
        eligible = frame[frame["option_expiry_date_distance"] >= 0].copy()
        if capture_tier == "fast":
            eligible = eligible.sort_values("option_expiry_date_distance").head(2)
        elif capture_tier == "structure":
            eligible = eligible[eligible["option_expiry_date_distance"] <= 7].sort_values(
                "option_expiry_date_distance"
            )
        elif capture_tier == "eod":
            eligible = eligible[
                (eligible["option_expiry_date_distance"] > 0)
                & (eligible["option_expiry_date_distance"] <= max_dte)
            ].sort_values("option_expiry_date_distance")
        else:
            raise ValueError(f"unsupported intraday capture tier: {capture_tier}")
        return [
            (str(row["strike_time"]), int(row["option_expiry_date_distance"]))
            for _, row in eligible.iterrows()
        ]

    def _detailed_contracts(
        self,
        *,
        underlying: str,
        spot: float,
        expiration: str,
        days_to_expiry: int,
        chain: Any,
        band_percent: float,
        raw_batches: list[dict[str, Any]],
    ) -> tuple[list[dict[str, object]], list[str]]:
        lower = spot * (1 - band_percent / 100)
        upper = spot * (1 + band_percent / 100)
        chain = chain[
            (chain["strike_price"].astype(float) >= lower)
            & (chain["strike_price"].astype(float) <= upper)
        ].copy()
        if chain.empty:
            return [], []
        codes = [str(item) for item in chain["code"].tolist()]
        static_by_code = chain.set_index("code").to_dict("index")
        pd = _pandas()
        results: list[dict[str, object]] = []
        for chunk in self._chunks(codes, self.batch_size):
            request_started_at = datetime.now(UTC)
            frame = self._market_snapshot(chunk, f"get_market_snapshot({underlying} intraday)")
            received_at = datetime.now(UTC)
            if not isinstance(frame, pd.DataFrame):
                frame = pd.DataFrame(frame)
            quote_records = frame.to_dict("records")
            raw_batches.append({
                "underlying": underlying,
                "expiration": expiration,
                "requested_codes": chunk,
                "returned_codes": [str(item.get("code") or "") for item in quote_records],
                "request_started_at": request_started_at.isoformat(),
                "response_received_at": received_at.isoformat(),
                "records": self._json_safe(quote_records),
            })
            for quote in quote_records:
                code = str(quote.get("code") or "")
                static = static_by_code.get(code)
                if not code or static is None:
                    continue
                volume_value = self._number(quote.get("volume"))
                open_interest_value = self._number(quote.get("option_open_interest"))
                results.append(
                    {
                        "contract_code": code,
                        "option_type": str(static.get("option_type") or "").upper(),
                        "expiration": expiration,
                        "days_to_expiry": days_to_expiry,
                        "strike": self._number(static.get("strike_price")),
                        "contract_multiplier": self._number(
                            quote.get("option_contract_multiplier"),
                            self._number(static.get("lot_size"), 100.0),
                        ),
                        "exercise_type": quote.get("option_area_type"),
                        "bid": self._number(quote.get("bid_price")),
                        "ask": self._number(quote.get("ask_price")),
                        "bid_size": self._number(quote.get("bid_vol")),
                        "ask_size": self._number(quote.get("ask_vol")),
                        "last": self._number(quote.get("last_price")),
                        "volume": int(volume_value) if volume_value is not None else None,
                        "open_interest": (
                            int(open_interest_value) if open_interest_value is not None else None
                        ),
                        "implied_volatility": self._number(
                            quote.get("option_implied_volatility")
                        ),
                        "delta": self._number(quote.get("option_delta")),
                        "gamma": self._number(quote.get("option_gamma")),
                        "vega": self._number(quote.get("option_vega")),
                        "theta": self._number(quote.get("option_theta")),
                        "rho": self._number(quote.get("option_rho")),
                        "quote_time": str(quote.get("update_time") or "") or None,
                        "request_started_at": request_started_at.isoformat(),
                        "received_at": received_at.isoformat(),
                        "warnings": [
                            "open_interest_is_latest_exchange_daily_update",
                            "provider_greeks_are_not_urus_calculated_greeks",
                        ],
                        "provider_metadata": {
                            "strike_time": str(static.get("strike_time") or expiration),
                            "lot_size": self._number(static.get("lot_size")),
                        },
                    }
                )
        return results, codes

    def capture_intraday(
        self,
        *,
        symbols: list[str],
        capture_tier: str,
        band_percent: float,
        trading_date: date | None = None,
        max_dte: int = 45,
    ) -> dict[str, Any]:
        if capture_tier not in {"fast", "structure", "eod"}:
            raise ValueError("capture_tier must be fast, structure, or eod")
        pd = _pandas()
        codes = [self._market_code(item) for item in symbols]
        context = self._context()
        underlying_request_started_at = datetime.now(UTC)
        underlying = self._market_snapshot(codes, "get_market_snapshot(intraday underlyings)")
        underlying_received_at = datetime.now(UTC)
        if not isinstance(underlying, pd.DataFrame):
            underlying = pd.DataFrame(underlying)
        quotes = underlying.set_index("code").to_dict("index") if not underlying.empty else {}
        started_at = underlying_request_started_at
        session_date = trading_date or started_at.astimezone(self._market_timezone()).date()
        market_date = session_date.isoformat()
        bars: list[dict[str, object]] = []
        results: list[dict[str, object]] = []
        warnings = [
            "Open interest is a daily exchange update, not intraday dealer positioning.",
            "Raw facts are stored without assigning dealer long/short direction.",
        ]
        raw_history_batches: list[dict[str, Any]] = []
        raw_expiration_records: dict[str, Any] = {}
        raw_chain_batches: list[dict[str, Any]] = []
        raw_option_snapshot_batches: list[dict[str, Any]] = []
        quality_symbols: dict[str, Any] = {}
        history_quota = self._history_admission(codes)
        history_codes = set(history_quota["admitted_codes"])
        deferred_history_codes = [str(item) for item in history_quota["deferred_codes"]]
        if history_quota.get("warning"):
            warnings.append(str(history_quota["warning"]))
        if deferred_history_codes:
            warnings.append(
                "Minute bars deferred by history quota reserve: "
                + ", ".join(code.removeprefix("US.") for code in deferred_history_codes)
            )

        for code in codes:
            quote = quotes.get(code)
            spot = self._number(quote.get("last_price")) if quote else None
            if spot is None or spot <= 0:
                warnings.append(f"{code} has no valid underlying spot")
                continue
            if code in history_codes:
                try:
                    bars.extend(self._minute_bars(code, market_date, raw_history_batches))
                except Exception as exc:
                    deferred_history_codes.append(code)
                    warnings.append(f"{code} minute bars failed: {exc}")
            expirations_frame = self._option_metadata(
                f"get_option_expiration_date({code})",
                lambda code=code: context.get_option_expiration_date(code),
            )
            if not isinstance(expirations_frame, pd.DataFrame):
                expirations_frame = pd.DataFrame(expirations_frame)
            raw_expiration_records[code] = self._json_safe(
                expirations_frame.to_dict("records")
            )
            selected = self._select_intraday_expirations(
                expirations_frame, capture_tier, max_dte=max_dte
            )
            expiration_results: list[dict[str, object]] = []
            expiry_quality: list[dict[str, Any]] = []
            for group in self._expiration_groups(selected):
                group_chain = self._option_chain(code, group[0][0], group[-1][0])
                if not isinstance(group_chain, pd.DataFrame):
                    group_chain = pd.DataFrame(group_chain)
                raw_chain_batches.append({
                    "underlying": code,
                    "start": group[0][0],
                    "end": group[-1][0],
                    "records": self._json_safe(group_chain.to_dict("records")),
                })
                for expiration, days_to_expiry in group:
                    chain = group_chain[
                        group_chain["strike_time"].astype(str) == expiration
                    ].copy()
                    contracts, requested_codes = self._detailed_contracts(
                        underlying=code,
                        spot=spot,
                        expiration=expiration,
                        days_to_expiry=days_to_expiry,
                        chain=chain,
                        band_percent=band_percent,
                        raw_batches=raw_option_snapshot_batches,
                    )
                    valid_quotes = sum(
                        1 for item in contracts
                        if item.get("bid") is not None and item.get("ask") is not None
                        and float(item["bid"]) >= 0 and float(item["ask"]) >= float(item["bid"])
                    )
                    returned_codes = {str(item["contract_code"]) for item in contracts}
                    coverage = valid_quotes / len(requested_codes) if requested_codes else 0.0
                    expiry_quality.append({
                        "expiration": expiration,
                        "expected_contract_count": len(requested_codes),
                        "returned_contract_count": len(returned_codes),
                        "valid_quote_count": valid_quotes,
                        "quote_coverage": coverage,
                        "missing_contract_codes": sorted(set(requested_codes) - returned_codes),
                        "complete": bool(requested_codes)
                        and len(returned_codes) == len(requested_codes)
                        and coverage >= self.min_quote_coverage,
                    })
                    if contracts:
                        expiration_results.append(
                            {
                                "expiration": expiration,
                                "days_to_expiry": days_to_expiry,
                                "requested_band_percent": band_percent,
                                "actual_min_strike": min(float(x["strike"]) for x in contracts),
                                "actual_max_strike": max(float(x["strike"]) for x in contracts),
                                "contracts": contracts,
                            }
                        )
            if expiration_results:
                results.append(
                    {
                        "symbol": code.removeprefix("US."),
                        "spot": spot,
                        "spot_time": str(quote.get("update_time") or "") or None,
                        "spot_request_started_at": underlying_request_started_at.isoformat(),
                        "spot_received_at": underlying_received_at.isoformat(),
                        "expirations": expiration_results,
                    }
                )
            quality_symbols[code.removeprefix("US.")] = {
                "expected_expirations": [item[0] for item in selected],
                "returned_expirations": [str(item["expiration"]) for item in expiration_results],
                "expirations": expiry_quality,
                "complete": bool(selected) and len(expiration_results) == len(selected)
                and all(item["complete"] for item in expiry_quality),
            }

        bar_symbols = {str(item["symbol"]).upper() for item in bars if item.get("symbol")}
        deferred_history_codes.extend(
            code for code in history_codes if code.removeprefix("US.") not in bar_symbols
        )

        requested_symbols = [item.removeprefix("US.") for item in codes]
        quality_complete = (
            not deferred_history_codes
            and set(quality_symbols) == set(requested_symbols)
            and all(item["complete"] for item in quality_symbols.values())
        )
        return {
            "schema_version": "moomoo-intraday-options-v2",
            "provider": "moomoo_openapi",
            "captured_at": datetime.now(UTC).isoformat(),
            "trading_date": market_date,
            "capture_tier": capture_tier,
            "request_band_percent": band_percent,
            "requested_symbols": requested_symbols,
            "minute_bar_symbols": sorted(bar_symbols),
            "minute_bar_unavailable_symbols": sorted(
                {item.removeprefix("US.") for item in deferred_history_codes}
            ),
            "history_quota": {
                key: value
                for key, value in history_quota.items()
                if key not in {"admitted_codes", "deferred_codes"}
            },
            "underlying_bars": bars,
            "symbols": results,
            "quality_summary": {
                "complete": quality_complete,
                "minimum_quote_coverage": self.min_quote_coverage,
                "symbols": quality_symbols,
            },
            "provider_raw": {
                "underlying_snapshot": {
                    "requested_codes": codes,
                    "request_started_at": underlying_request_started_at.isoformat(),
                    "response_received_at": underlying_received_at.isoformat(),
                    "records": self._json_safe(underlying.to_dict("records")),
                },
                "expiration_metadata": raw_expiration_records,
                "option_chain_batches": raw_chain_batches,
                "option_snapshot_batches": raw_option_snapshot_batches,
                "history_kline_batches": raw_history_batches,
            },
            "warnings": warnings,
        }

    def capture_eod_options(
        self,
        *,
        symbols: list[str],
        trading_date: date,
        band_percent: float = 10.0,
        max_dte: int = 45,
    ) -> dict[str, Any]:
        """Collect the frozen Eoflora v1 EOD input without same-day 0DTE."""

        if max_dte != 45 or not math.isclose(band_percent, 10.0):
            raise ValueError("Eoflora EOD v1 requires max_dte=45 and band_percent=10")
        payload = self.capture_intraday(
            symbols=symbols,
            capture_tier="eod",
            band_percent=band_percent,
            trading_date=trading_date,
            max_dte=max_dte,
        )
        rows: list[dict[str, Any]] = []
        filtered_contracts: list[str] = []
        expiry_types: dict[tuple[str, str], set[str]] = {}
        total_contracts = 0
        iv_backfill_attempted = 0
        iv_backfill_succeeded = 0
        for symbol_payload in payload.get("symbols", []):
            if not isinstance(symbol_payload, dict):
                continue
            symbol = str(symbol_payload.get("symbol") or "").upper()
            for expiration_payload in symbol_payload.get("expirations", []):
                if not isinstance(expiration_payload, dict):
                    continue
                expiration = str(expiration_payload.get("expiration") or "")
                types = expiry_types.setdefault((symbol, expiration), set())
                for contract in expiration_payload.get("contracts", []):
                    if not isinstance(contract, dict):
                        continue
                    total_contracts += 1
                    option_type = str(contract.get("option_type") or "").lower()
                    raw_iv = self._number(contract.get("implied_volatility"))
                    row = {
                        "date": trading_date.isoformat(),
                        "symbol": symbol,
                        "expiration": expiration,
                        "strike": contract.get("strike"),
                        "type": option_type,
                        "bid": contract.get("bid"),
                        "ask": contract.get("ask"),
                        "open_interest": contract.get("open_interest"),
                        "implied_volatility": raw_iv / 100 if raw_iv is not None else None,
                        "delta": contract.get("delta"),
                        "gamma": contract.get("gamma"),
                        "volume": contract.get("volume"),
                        "contract_code": contract.get("contract_code"),
                        "quote_time": contract.get("quote_time"),
                        "request_started_at": contract.get("request_started_at"),
                        "received_at": contract.get("received_at"),
                        "iv_source": "moomoo" if raw_iv is not None and raw_iv > 0 else None,
                        "iv_model": None,
                    }
                    if raw_iv is None or raw_iv <= 0:
                        iv_backfill_attempted += 1
                        calculated = self._calculate_eod_iv_and_greeks(
                            row=row,
                            spot=symbol_payload.get("spot"),
                            trading_date=trading_date,
                            expiration=expiration,
                        )
                        if calculated is not None:
                            row.update(calculated)
                            iv_backfill_succeeded += 1
                    if not self._valid_eoflora_row(row):
                        filtered_contracts.append(str(contract.get("contract_code") or ""))
                        continue
                    rows.append(row)
                    types.add(option_type)

        underlying_closes: dict[str, float] = {}
        for symbol in {item.upper().removeprefix("US.") for item in symbols}:
            symbol_bars = [
                item
                for item in payload.get("underlying_bars", [])
                if isinstance(item, dict) and str(item.get("symbol") or "").upper() == symbol
            ]
            if symbol_bars:
                underlying_closes[symbol] = float(
                    max(symbol_bars, key=lambda item: str(item.get("bar_time") or ""))["close"]
                )

        missing_two_sided = [
            f"{symbol}:{expiration}"
            for (symbol, expiration), types in expiry_types.items()
            if types != {"call", "put"}
        ]
        requested_symbols = {item.upper().removeprefix("US.") for item in symbols}
        required_symbols = {"SPY", "QQQ", "IWM"}
        missing_required_symbols = sorted(required_symbols - requested_symbols)
        quality = payload.get("quality_summary")
        provider_complete = bool(quality.get("complete")) if isinstance(quality, dict) else False
        retained_quote_ratio = len(rows) / total_contracts if total_contracts else 0.0
        complete = (
            provider_complete
            and bool(rows)
            and retained_quote_ratio >= getattr(self, "min_quote_coverage", 0.80)
            and not missing_two_sided
            and not missing_required_symbols
            and set(underlying_closes) == requested_symbols
        )
        content = {
            "trading_date": trading_date.isoformat(),
            "requested_symbols": sorted(requested_symbols),
            "dte_range": [1, max_dte],
            # The frozen consumer canonicalises this contract value as 10.0.
            # Normalise before hashing so TOML integer syntax cannot change
            # the otherwise identical content digest.
            "strike_band_percent": float(band_percent),
            "underlying_closes": underlying_closes,
            "option_rows": rows,
        }
        content_sha256 = hashlib.sha256(
            json.dumps(content, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        base_payload = dict(payload)
        base_payload.pop("symbols", None)
        return {
            **base_payload,
            "schema_version": "urus.moomoo.eoflora-eod.v1",
            "collection_mode": "eoflora_eod",
            "dte_min": 1,
            "dte_max": max_dte,
            "request_band_percent": band_percent,
            "underlying_closes": underlying_closes,
            "eoflora_option_rows": rows,
            "content_sha256": content_sha256,
            "quality_summary": {
                **(quality if isinstance(quality, dict) else {}),
                "complete": complete,
                # This field describes emitted rows and therefore remains empty:
                # invalid source rows are never allowed across the Eoflora boundary.
                "invalid_contract_codes": [],
                "source_contract_count": total_contracts,
                "retained_contract_count": len(rows),
                "retained_quote_ratio": retained_quote_ratio,
                "filtered_contract_count": len(filtered_contracts),
                "filtered_contract_codes": filtered_contracts,
                "iv_backfill_attempted": iv_backfill_attempted,
                "iv_backfill_succeeded": iv_backfill_succeeded,
                "iv_backfill_failed": iv_backfill_attempted - iv_backfill_succeeded,
                "iv_backfill_model": "black_scholes_european_mid_bisection_v1",
                "iv_backfill_price": "valid_bid_ask_midpoint",
                "iv_backfill_rate_percent": getattr(self, "risk_free_rate_percent", 4.0),
                "iv_backfill_dividend_yield_percent": getattr(
                    self, "dividend_yield_percent", 0.0
                ),
                "missing_two_sided_expirations": missing_two_sided,
                "missing_required_symbols": missing_required_symbols,
            },
            "ready": complete,
            "complete": complete,
        }

    def _calculate_eod_iv_and_greeks(
        self,
        *,
        row: dict[str, Any],
        spot: Any,
        trading_date: date,
        expiration: str,
    ) -> dict[str, Any] | None:
        """Invert a valid midpoint and return auditable BS IV/Greeks.

        This is deliberately fail-closed.  A stale or asynchronous quote that
        violates the model's no-arbitrage range is filtered instead of being
        coerced into a plausible-looking volatility.
        """

        try:
            underlying = float(spot)
            strike = float(row["strike"])
            bid = float(row["bid"])
            ask = float(row["ask"])
            expiry_date = date.fromisoformat(expiration)
        except (TypeError, ValueError, KeyError):
            return None
        option_type = str(row.get("type") or "").lower()
        if (
            option_type not in {"call", "put"}
            or not all(math.isfinite(value) for value in (underlying, strike, bid, ask))
            or underlying <= 0
            or strike <= 0
            or bid < 0
            or ask < bid
            or ask <= 0
        ):
            return None
        days = (expiry_date - trading_date).days
        if days < 1:
            return None
        time_years = days / 365.0
        rate = getattr(self, "risk_free_rate_percent", 4.0) / 100.0
        dividend_yield = getattr(self, "dividend_yield_percent", 0.0) / 100.0
        midpoint = (bid + ask) / 2.0

        low_sigma = 1.0e-6
        high_sigma = 5.0
        low_price, _, _ = self._black_scholes_price_delta_gamma(
            option_type, underlying, strike, time_years, rate, dividend_yield, low_sigma
        )
        high_price, _, _ = self._black_scholes_price_delta_gamma(
            option_type, underlying, strike, time_years, rate, dividend_yield, high_sigma
        )
        tolerance = max(1.0e-8, midpoint * 1.0e-10)
        if midpoint < low_price - tolerance or midpoint > high_price + tolerance:
            return None

        for _ in range(80):
            sigma = (low_sigma + high_sigma) / 2.0
            price, _, _ = self._black_scholes_price_delta_gamma(
                option_type, underlying, strike, time_years, rate, dividend_yield, sigma
            )
            if price < midpoint:
                low_sigma = sigma
            else:
                high_sigma = sigma
        implied_volatility = (low_sigma + high_sigma) / 2.0
        _, delta, gamma = self._black_scholes_price_delta_gamma(
            option_type,
            underlying,
            strike,
            time_years,
            rate,
            dividend_yield,
            implied_volatility,
        )
        return {
            "implied_volatility": implied_volatility,
            "delta": delta,
            "gamma": gamma,
            "iv_source": "calculated_mid",
            "iv_model": "black_scholes_european_mid_bisection_v1",
            "iv_quote_price": midpoint,
            "iv_time_years": time_years,
            "iv_risk_free_rate": rate,
            "iv_dividend_yield": dividend_yield,
        }

    @staticmethod
    def _black_scholes_price_delta_gamma(
        option_type: str,
        spot: float,
        strike: float,
        time_years: float,
        rate: float,
        dividend_yield: float,
        volatility: float,
    ) -> tuple[float, float, float]:
        sqrt_time = math.sqrt(time_years)
        d1 = (
            math.log(spot / strike)
            + (rate - dividend_yield + 0.5 * volatility * volatility) * time_years
        ) / (volatility * sqrt_time)
        d2 = d1 - volatility * sqrt_time
        def normal_cdf(value: float) -> float:
            return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))
        discounted_spot = spot * math.exp(-dividend_yield * time_years)
        discounted_strike = strike * math.exp(-rate * time_years)
        if option_type == "call":
            price = discounted_spot * normal_cdf(d1) - discounted_strike * normal_cdf(d2)
            delta = math.exp(-dividend_yield * time_years) * normal_cdf(d1)
        else:
            price = discounted_strike * normal_cdf(-d2) - discounted_spot * normal_cdf(-d1)
            delta = math.exp(-dividend_yield * time_years) * (normal_cdf(d1) - 1.0)
        density = math.exp(-0.5 * d1 * d1) / math.sqrt(2.0 * math.pi)
        gamma = (
            math.exp(-dividend_yield * time_years)
            * density
            / (spot * volatility * sqrt_time)
        )
        return price, delta, gamma

    @staticmethod
    def _valid_eoflora_row(row: dict[str, Any]) -> bool:
        try:
            strike = float(row["strike"])
            bid = float(row["bid"])
            ask = float(row["ask"])
            open_interest = float(row["open_interest"])
            implied_volatility = float(row["implied_volatility"])
            delta = float(row["delta"])
            gamma = float(row["gamma"])
        except (KeyError, TypeError, ValueError):
            return False
        option_type = str(row.get("type") or "").lower()
        return (
            all(
                math.isfinite(value)
                for value in (strike, bid, ask, open_interest, implied_volatility, delta, gamma)
            )
            and strike > 0
            and bid >= 0
            and ask >= bid
            and ask > 0
            and open_interest >= 0
            and implied_volatility > 0
            and gamma >= 0
            and abs(delta) <= 1
            and ((option_type == "call" and delta >= 0) or (option_type == "put" and delta <= 0))
        )

    def capture_session_close(self, *, symbols: list[str], trading_date: date) -> dict[str, Any]:
        """Refetch complete regular-session bars after close for labels and final revisions."""
        codes = [self._market_code(item) for item in symbols]
        admission = self._history_admission(codes)
        raw_batches: list[dict[str, Any]] = []
        bars: list[dict[str, object]] = []
        unavailable_symbols: list[str] = []
        warnings: list[str] = []
        for code in admission.get("admitted_codes", []):
            try:
                bars.extend(self._minute_bars(str(code), trading_date.isoformat(), raw_batches))
            except Exception as exc:
                symbol = str(code).removeprefix("US.").upper()
                unavailable_symbols.append(symbol)
                warnings.append(f"{code} minute bars failed: {exc}")
        official_closes: dict[str, float] = {}
        for symbol in symbols:
            symbol_bars = [item for item in bars if item["symbol"] == symbol.upper()]
            if symbol_bars:
                official_closes[symbol.upper()] = float(
                    max(symbol_bars, key=lambda item: str(item["bar_time"]))["close"]
                )
        return {
            "schema_version": "moomoo-intraday-session-close-v1",
            "provider": "moomoo_openapi",
            "captured_at": datetime.now(UTC).isoformat(),
            "trading_date": trading_date.isoformat(),
            "requested_symbols": [item.upper() for item in symbols],
            "underlying_bars": bars,
            "minute_bar_unavailable_symbols": sorted(set(unavailable_symbols)),
            "official_closes": official_closes,
            "provider_raw": {"history_kline_batches": raw_batches},
            "warnings": warnings,
            "complete": set(official_closes) == {item.upper() for item in symbols},
        }

    @staticmethod
    def _market_timezone():
        from zoneinfo import ZoneInfo

        return ZoneInfo("America/New_York")
