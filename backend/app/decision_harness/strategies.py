from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from inspect import getsource
from math import isfinite
from statistics import median
from typing import Any, Iterable
from uuid import uuid4

from app.analytics.technical import calculate_relative_strength, calculate_technical_indicators
from app.decision_harness.contracts import content_sha256


STRATEGY_DECISION_SCHEMA = "urus.strategy_decision.v1"
CONFIDENCE_TYPE = "heuristic_unvalidated"
HORIZON = {"unit": "trading_day", "value": 5}
STRATEGY_NAMES = (
    "trend_momentum_v1",
    "mean_reversion_v1",
    "breakout_volume_v1",
    "relative_strength_rotation_v1",
    "quality_left_side_reversal_v1",
)


@dataclass(frozen=True)
class StrategyContext:
    dataset: dict[str, Any]
    chart: dict[str, Any]
    symbol: str
    bars: list[dict[str, Any]]
    quality: dict[str, Any]
    indicators: dict[str, Any]
    benchmark: str | None
    benchmark_bars: list[dict[str, Any]]
    relative: dict[str, Any]

    @property
    def latest(self) -> dict[str, Any] | None:
        return self.bars[-1] if self.bars else None

    @property
    def previous(self) -> dict[str, Any] | None:
        return self.bars[-2] if len(self.bars) > 1 else None

    @property
    def as_of(self) -> str | None:
        latest = self.latest
        return str(latest.get("date")) if latest else None


class StrategyAdapter:
    """Base class for a pure, deterministic strategy adapter."""

    name: str
    version: str = "1.0.0"
    minimum_bars: int = 20
    rule_set: str = ""

    @property
    def implementation_sha256(self) -> str:
        try:
            implementation_source = {
                "base": getsource(StrategyAdapter),
                "adapter": getsource(type(self)),
            }
        except (OSError, TypeError):
            # Dynamically supplied adapters may not have inspectable source;
            # their explicit rule_set remains the stable fallback identity.
            implementation_source = self.rule_set
        return content_sha256(
            {
                "name": self.name,
                "version": self.version,
                "rule_set": self.rule_set,
                "implementation_source": implementation_source,
            }
        )

    def evaluate(self, context: StrategyContext) -> dict[str, Any]:
        if context.quality.get("status") in {"missing", "conflicted"}:
            return self.not_applicable(context, "data_quality", "数据质量未通过策略 Gate。")
        if len(context.bars) < self.minimum_bars:
            return self.not_applicable(
                context,
                "insufficient_history",
                f"策略至少需要 {self.minimum_bars} 根完整日 K。",
            )
        return self._evaluate(context)

    def _evaluate(self, context: StrategyContext) -> dict[str, Any]:
        raise NotImplementedError

    def not_applicable(
        self, context: StrategyContext, reason_code: str, reason: str
    ) -> dict[str, Any]:
        return self._decision(
            context,
            status="not_applicable",
            stance="insufficient_data",
            action="no_action",
            score=None,
            setup_progress=_setup_progress(
                stage="insufficient_data",
                as_of=context.as_of,
                confirmation_distance_atr=None,
                invalidation_distance_atr=None,
                bars_in_stage=0,
            ),
            reasons=[_reason(reason_code, reason)],
            risks=["数据不足时不产生正式策略方向。"],
            confirmation_conditions=[],
            invalidation_conditions=[],
            visual_anchors=[],
        )

    def error(self, context: StrategyContext, error: Exception) -> dict[str, Any]:
        return self._decision(
            context,
            status="error",
            stance="insufficient_data",
            action="no_action",
            score=None,
            setup_progress=_setup_progress(
                stage="insufficient_data",
                as_of=context.as_of,
                confirmation_distance_atr=None,
                invalidation_distance_atr=None,
                bars_in_stage=0,
            ),
            reasons=[_reason("strategy_error", f"策略执行失败：{error}")],
            risks=["该策略本次不可用；其他策略仍独立运行。"],
            confirmation_conditions=[],
            invalidation_conditions=[],
            visual_anchors=[],
        )

    def _decision(
        self,
        context: StrategyContext,
        *,
        status: str,
        stance: str,
        action: str,
        score: int | None,
        setup_progress: dict[str, Any],
        reasons: list[dict[str, Any]],
        risks: list[str],
        confirmation_conditions: list[str],
        invalidation_conditions: list[str],
        visual_anchors: list[dict[str, Any]],
    ) -> dict[str, Any]:
        strategy = {
            "name": self.name,
            "version": self.version,
            "implementation_sha256": self.implementation_sha256,
        }
        decision: dict[str, Any] = {
            "schema_version": STRATEGY_DECISION_SCHEMA,
            "decision_id": str(uuid4()),
            "dataset_id": str(context.dataset["dataset_id"]),
            "scope": {
                "scope_type": context.dataset["scope"]["scope_type"],
                "scope_id": context.dataset["scope"]["scope_id"],
                "scope_version": context.dataset["scope"].get("scope_version"),
                "symbol": context.symbol,
            },
            "strategy": strategy,
            "status": status,
            "stance": stance,
            "action": action,
            "horizon": dict(HORIZON),
            "score": score,
            "score_scale": [-100, 100],
            "confidence": None,
            "confidence_type": CONFIDENCE_TYPE,
            "setup_progress": setup_progress,
            "reasons": reasons,
            "risks": risks,
            "confirmation_conditions": confirmation_conditions,
            "invalidation_conditions": invalidation_conditions,
            "visual_anchors": visual_anchors,
            "evidence_refs": _evidence_refs(context),
            "quality": dict(context.quality),
            "generated_at": datetime.now(UTC).isoformat(),
        }
        decision["content_sha256"] = content_sha256(
            {
                key: value
                for key, value in decision.items()
                if key not in {"decision_id", "generated_at", "content_sha256"}
            }
        )
        return decision


class TrendMomentumStrategy(StrategyAdapter):
    name = "trend_momentum_v1"
    minimum_bars = 50
    rule_set = (
        "MA20/50/200 alignment, MACD histogram direction, relative excess return, "
        "and volume effort/result confirmation; no single indicator is decisive."
    )

    def _evaluate(self, context: StrategyContext) -> dict[str, Any]:
        latest = context.latest or {}
        close = _number(latest.get("close"))
        averages = context.indicators.get("moving_average", {})
        ma20 = _number(averages.get("20d"))
        ma50 = _number(averages.get("50d"))
        ma200 = _number(averages.get("200d"))
        macd = _mapping(context.indicators.get("macd_12_26_9"))
        histogram = _number(macd.get("histogram"))
        previous_histogram = _number(macd.get("previous_histogram"))
        volume = _mapping(context.indicators.get("volume_effort_result"))
        relative_20 = _number(_mapping(context.relative.get("excess_returns_percent")).get("20d"))
        score = 0
        reasons: list[dict[str, Any]] = []

        if close is not None and ma20 is not None and ma50 is not None:
            if close > ma20 > ma50:
                score += 25
                reasons.append(_reason("bullish_ma_structure", "价格位于 MA20 与 MA50 上方，均线顺序偏强。"))
            elif close < ma20 < ma50:
                score -= 25
                reasons.append(_reason("bearish_ma_structure", "价格位于 MA20 与 MA50 下方，均线顺序偏弱。"))
            else:
                reasons.append(_reason("mixed_ma_structure", "MA20/MA50 尚未形成同向排列。"))
        if close is not None and ma200 is not None:
            if close > ma200:
                score += 12
                reasons.append(_reason("above_ma200", "价格位于 MA200 上方。"))
            elif close < ma200:
                score -= 12
                reasons.append(_reason("below_ma200", "价格位于 MA200 下方。"))
        if histogram is not None:
            if histogram > 0:
                score += 16
                reasons.append(_reason("macd_positive", "MACD 柱体位于零轴上方。"))
            elif histogram < 0:
                score -= 16
                reasons.append(_reason("macd_negative", "MACD 柱体位于零轴下方。"))
            if previous_histogram is not None and histogram > previous_histogram:
                score += 8
                reasons.append(_reason("macd_improving", "MACD 柱体较前一根改善。"))
            elif previous_histogram is not None and histogram < previous_histogram:
                score -= 8
                reasons.append(_reason("macd_weakening", "MACD 柱体较前一根走弱。"))
        if relative_20 is not None:
            relative_score = max(-15, min(15, round(relative_20 * 1.5)))
            score += relative_score
            reasons.append(_reason("relative_strength_20d", f"相对基准 20 日超额收益 {relative_20:.2f}%。"))
        volume_signal = str(volume.get("signal") or "")
        if volume_signal == "volume_up_demand":
            score += 14
            reasons.append(_reason("volume_demand", "放量上涨且收盘位于当日高位附近。"))
        elif volume_signal == "volume_down_distribution":
            score -= 14
            reasons.append(_reason("volume_distribution", "放量下跌且收盘位于当日低位附近。"))

        score = _bounded_score(score)
        stance, action = _stance_action(score)
        atr = _metric_value(context.indicators, "atr14")
        stage = _stage_for_score(score)
        trigger = ma20 if ma20 is not None else close
        invalidation = ma50 if ma50 is not None else close
        anchors = [
            _series_anchor(
                context,
                series_ids=["ma20", "ma50", "ma200"],
                label="趋势均线结构",
                pane="price",
                tone="bullish" if stance == "bullish" else "bearish" if stance == "bearish" else "neutral",
            ),
            _series_anchor(
                context,
                series_ids=["macd_dif_12_26", "macd_dea_9", "macd_histogram"],
                label="MACD 动量确认",
                pane="momentum",
                tone="bullish" if stance == "bullish" else "bearish" if stance == "bearish" else "neutral",
            ),
        ]
        if stage == "confirmed":
            anchors.extend(
                _price_anchors(
                    context,
                    (("trigger_marker", close, "趋势动量触发"),),
                    tone="bullish" if stance == "bullish" else "bearish",
                )
            )
        return self._decision(
            context,
            status="ok",
            stance=stance,
            action=action,
            score=score,
            setup_progress=_setup_progress(
                stage=stage,
                as_of=context.as_of,
                confirmation_distance_atr=_distance_atr(close, trigger, atr),
                invalidation_distance_atr=_distance_atr(close, invalidation, atr),
                bars_in_stage=1,
            ),
            reasons=reasons or [_reason("no_directional_edge", "趋势与动量证据没有形成明确优势。" )],
            risks=[
                "趋势策略在均值回归或快速反转行情中可能滞后。",
                "score 是策略内部强弱，不是胜率。",
            ],
            confirmation_conditions=["价格维持在 MA20/MA50 方向一致的一侧。", "MACD 柱体不反向恶化。"],
            invalidation_conditions=["收盘跌破 MA50（多头）或重新站上 MA50（空头）。", "相对基准持续落后。"],
            visual_anchors=anchors,
        )


class MeanReversionStrategy(StrategyAdapter):
    name = "mean_reversion_v1"
    minimum_bars = 20
    rule_set = (
        "Bollinger 20/2 location, RSI14 extreme and slope, distance from MA20, "
        "ATR context, and reversal confirmation; do not buy oversold alone."
    )

    def _evaluate(self, context: StrategyContext) -> dict[str, Any]:
        latest = context.latest or {}
        close = _number(latest.get("close"))
        rsi = _mapping(context.indicators.get("rsi14"))
        rsi_value = _number(rsi.get("value"))
        rsi_previous = _number(rsi.get("previous_value"))
        bollinger = _mapping(context.indicators.get("bollinger_20_2"))
        position = _number(bollinger.get("position_ratio"))
        lower = _number(bollinger.get("lower"))
        upper = _number(bollinger.get("upper"))
        middle = _number(bollinger.get("middle"))
        averages = context.indicators.get("moving_average", {})
        ma50 = _number(averages.get("50d"))
        volume = _mapping(context.indicators.get("volume_effort_result"))
        score = 0
        reasons: list[dict[str, Any]] = []

        if position is not None:
            if position <= 0.08:
                score += 28
                reasons.append(_reason("near_lower_band", "价格接近或跌破布林带下轨。"))
            elif position >= 0.92:
                score -= 28
                reasons.append(_reason("near_upper_band", "价格接近或突破布林带上轨。"))
            elif position < 0.35:
                score += 10
            elif position > 0.65:
                score -= 10
        if rsi_value is not None:
            if rsi_value <= 30:
                score += 24
                reasons.append(_reason("rsi_oversold", f"RSI14 为 {rsi_value:.1f}，处于超卖区。"))
            elif rsi_value >= 70:
                score -= 24
                reasons.append(_reason("rsi_overbought", f"RSI14 为 {rsi_value:.1f}，处于超买区。"))
        if rsi_value is not None and rsi_previous is not None:
            if rsi_value > rsi_previous and rsi_value <= 45:
                score += 14
                reasons.append(_reason("rsi_recovery", "RSI14 正从弱势区修复。"))
            elif rsi_value < rsi_previous and rsi_value >= 55:
                score -= 14
                reasons.append(_reason("rsi_fading", "RSI14 正从强势区回落。"))
        if close is not None and ma50 is not None:
            if close < ma50:
                score -= 12
                reasons.append(_reason("downtrend_reversion_risk", "价格位于 MA50 下方，反弹可能只是下跌趋势中的回归。"))
            elif close > ma50:
                score += 8
        volume_signal = str(volume.get("signal") or "")
        if volume_signal == "volume_up_demand":
            score += 10
            reasons.append(_reason("reversal_volume", "放量上涨为反转提供量价确认。"))
        elif volume_signal == "volume_down_distribution":
            score -= 10
            reasons.append(_reason("distribution_risk", "放量下跌削弱均值回归假设。"))

        score = _bounded_score(score)
        stance, action = _stance_action(score)
        atr = _metric_value(context.indicators, "atr14")
        stage = _stage_for_score(score)
        anchors = [
            _series_anchor(
                context,
                series_ids=[
                    "bollinger_upper_20_2",
                    "bollinger_middle_20",
                    "bollinger_lower_20_2",
                ],
                label="Bollinger 20/2",
                pane="price",
                tone="bullish" if stance == "bullish" else "bearish" if stance == "bearish" else "neutral",
            ),
            _series_anchor(
                context,
                series_ids=["rsi14"],
                label="RSI14 反转确认",
                pane="momentum",
                tone="bullish" if stance == "bullish" else "bearish" if stance == "bearish" else "neutral",
            ),
        ]
        if stage == "confirmed":
            anchors.extend(
                _price_anchors(
                    context,
                    (("trigger_marker", close, "均值回归触发"),),
                    tone="bullish" if stance == "bullish" else "bearish",
                )
            )
        return self._decision(
            context,
            status="ok",
            stance=stance,
            action=action,
            score=score,
            setup_progress=_setup_progress(
                stage=stage,
                as_of=context.as_of,
                confirmation_distance_atr=_distance_atr(close, middle, atr),
                invalidation_distance_atr=_distance_atr(close, lower if score >= 0 else upper, atr),
                bars_in_stage=1,
            ),
            reasons=reasons or [_reason("no_extreme", "价格和动量没有形成足够的偏离。" )],
            risks=[
                "超买/超卖可以在强趋势中持续，极值不是反转确认。",
                "跌破下轨且放量时，均值回归假设失效风险上升。",
            ],
            confirmation_conditions=["价格重新收回布林带内侧。", "RSI14 方向与价格修复同步。"],
            invalidation_conditions=["继续沿趋势方向扩张并伴随放量。", "价格远离中轨后未出现反转确认。"],
            visual_anchors=anchors,
        )


class QualityLeftSideReversalStrategy(StrategyAdapter):
    """Composite cash-equity setup for a controlled left-side reversal.

    The user's Decision Scope is the research-universe qualification.  This
    adapter adds an investability gate but deliberately does not pretend that
    OHLCV data proves a fundamental moat or an intact investment thesis.
    """

    name = "quality_left_side_reversal_v1"
    version = "1.0.0"
    minimum_bars = 200
    rule_set = (
        "Research-scope and liquidity gate, RSI12 oversold/recovery, gap or volume-profile "
        "support zone, beta-adjusted 3-day alpha, and volume confirmation. Cash equity only; "
        "oversold alone never confirms an entry candidate."
    )

    def evaluate(self, context: StrategyContext) -> dict[str, Any]:
        if not context.benchmark or not context.benchmark_bars:
            return self.not_applicable(
                context,
                "benchmark_missing",
                "左侧反转策略需要 benchmark 才能计算 Beta 调整相对强弱。",
            )
        return super().evaluate(context)

    def _evaluate(self, context: StrategyContext) -> dict[str, Any]:
        latest = context.latest or {}
        close = _number(latest.get("close"))
        atr = _metric_value(context.indicators, "atr14")
        rsi = _mapping(context.indicators.get("rsi12"))
        rsi_value = _number(rsi.get("value"))
        rsi_previous = _number(rsi.get("previous_value"))
        volume = _mapping(context.indicators.get("volume_effort_result"))
        volume_signal = str(volume.get("signal") or "")
        dollar_volumes = [
            float(item["close"]) * float(item.get("volume") or 0)
            for item in context.bars[-20:]
        ]
        median_dollar_volume = median(dollar_volumes) if dollar_volumes else 0.0
        investable = close is not None and close >= 5 and median_dollar_volume >= 20_000_000
        support = _left_side_support_zone(context.bars, atr)
        support_distance = _support_distance_atr(close, support, atr)
        near_support = support_distance is not None and support_distance <= 0.75
        support_invalidated = bool(
            support
            and close is not None
            and atr is not None
            and close < float(support["lower"]) - 0.5 * atr
        )
        beta = _number(_mapping(context.relative.get("beta")).get("20d"))
        alpha_1d = _beta_adjusted_alpha(context.bars, context.benchmark_bars, beta, periods=1)
        alpha_3d = _beta_adjusted_alpha(context.bars, context.benchmark_bars, beta, periods=3)
        oversold = rsi_value is not None and rsi_value < 30
        recovering = bool(
            rsi_value is not None
            and rsi_previous is not None
            and rsi_previous <= 30 < rsi_value
        )
        rsi_improving = bool(
            rsi_value is not None and rsi_previous is not None and rsi_value > rsi_previous
        )
        relative_confirmed = bool(
            alpha_3d is not None
            and alpha_3d > 0
            and (alpha_1d is None or alpha_1d >= 0)
        )
        distribution = volume_signal == "volume_down_distribution"

        reasons = [
            _reason(
                "research_scope_gate",
                "标的已由用户纳入当前 Decision Scope；这代表研究资格，不等同于基本面自动认证。",
            )
        ]
        score = 0
        if investable:
            score += 10
            reasons.append(
                _reason(
                    "investability_passed",
                    f"20 日中位成交额约 {median_dollar_volume / 1_000_000:.1f}M，满足现货流动性 Gate。",
                )
            )
        else:
            reasons.append(
                _reason(
                    "investability_failed",
                    "价格或 20 日中位成交额未达到现货左侧策略的最低可交易门槛。",
                )
            )
        if oversold:
            score += 25
            reasons.append(_reason("rsi12_oversold", f"RSI12 为 {rsi_value:.1f}，进入短期超卖区。"))
        elif recovering:
            score += 30
            reasons.append(_reason("rsi12_recovery", f"RSI12 回到 30 上方（{rsi_value:.1f}），动能开始修复。"))
        elif rsi_value is not None and rsi_value < 35:
            score += 10
            reasons.append(_reason("rsi12_approaching", f"RSI12 为 {rsi_value:.1f}，接近超卖区。"))
        if near_support and support:
            score += 25
            reasons.append(
                _reason(
                    "structural_support_near",
                    f"价格距离{support['label']} {support_distance:.2f} ATR。",
                )
            )
        elif support:
            reasons.append(
                _reason(
                    "structural_support_waiting",
                    f"最近的{support['label']}距离约 {support_distance:.2f} ATR。"
                    if support_distance is not None
                    else f"已识别{support['label']}，但暂时无法计算 ATR 距离。",
                )
            )
        if relative_confirmed:
            score += 25
            reasons.append(
                _reason(
                    "beta_adjusted_alpha_confirmed",
                    f"相对 {context.benchmark} 的 Beta 调整 3 日 Alpha 为 {alpha_3d:.2f}%。",
                )
            )
        elif alpha_3d is not None:
            score -= 10
            reasons.append(
                _reason(
                    "beta_adjusted_alpha_weak",
                    f"相对 {context.benchmark} 的 Beta 调整 3 日 Alpha 仍为 {alpha_3d:.2f}%。",
                )
            )
        if rsi_improving:
            score += 10
        if volume_signal in {"volume_up_demand", "volume_down_absorption"}:
            score += 10
            reasons.append(_reason("volume_absorption", "量价结构显示承接或需求改善。"))
        if distribution:
            score -= 35
            reasons.append(_reason("volume_distribution", "放量下跌且收盘靠近日内低位，接飞刀风险仍高。"))

        score = _bounded_score(score)
        if not investable:
            stage, stance, action = "ineligible", "neutral", "no_action"
        elif support_invalidated or (distribution and support_distance is not None and support_distance <= 0.5):
            stage, stance, action = "invalidated", "bearish", "avoid"
        elif (oversold or recovering) and near_support and relative_confirmed and rsi_improving:
            stage, stance, action = "confirmed", "bullish", "prioritize"
        elif (oversold or recovering) and near_support:
            stage, stance, action = "armed", "bullish", "watch"
        elif oversold or recovering or near_support:
            stage, stance, action = "watching", "neutral", "wait"
        else:
            stage, stance, action = "no_setup", "neutral", "wait"

        tone = "bullish" if stance == "bullish" else "bearish" if stance == "bearish" else "neutral"
        anchors = [
            _series_anchor(
                context,
                series_ids=["rsi12"],
                label="RSI12 左侧动能",
                pane="momentum",
                tone=tone,
            ),
            _series_anchor(
                context,
                series_ids=[f"relative_performance_vs_{context.benchmark}"],
                label=f"相对强弱 vs {context.benchmark}",
                pane="relative_strength",
                tone=tone,
            ),
        ]
        if support:
            anchors.append(
                _zone_anchor(
                    context,
                    lower=float(support["lower"]),
                    upper=float(support["upper"]),
                    label=str(support["label"]),
                    start_time=str(support["start_time"]),
                    tone="warning" if stage in {"armed", "watching"} else tone,
                )
            )
        if stage == "confirmed":
            anchors.extend(
                _price_anchors(
                    context,
                    (("trigger_marker", close, "左侧反转确认"),),
                    tone="bullish",
                )
            )

        invalidation = (
            float(support["lower"]) - 0.5 * atr
            if support and atr is not None
            else None
        )
        return self._decision(
            context,
            status="ok",
            stance=stance,
            action=action,
            score=score,
            setup_progress=_setup_progress(
                stage=stage,
                as_of=context.as_of,
                confirmation_distance_atr=support_distance,
                invalidation_distance_atr=_distance_atr(close, invalidation, atr),
                bars_in_stage=1,
            ),
            reasons=reasons,
            risks=[
                "当前资格 Gate 只确认用户研究范围与现货流动性；基本面护城河、反转逻辑和高确信度仍需人工或后续基本面快照确认。",
                "RSI 超卖可以在下跌趋势中持续；只有支撑位置与 Beta 调整相对强弱共同确认时才进入 confirmed。",
                "该策略只输出现金股票研究建议，不包含期权或自动下单。",
            ],
            confirmation_conditions=[
                "RSI12 从超卖区改善，且价格仍位于有效支撑区附近。",
                f"相对 {context.benchmark} 的 Beta 调整 3 日 Alpha 转正，最新一日不再恶化。",
                "没有出现放量破位或明显派发。",
            ],
            invalidation_conditions=[
                "收盘跌破支撑区下沿 0.5 ATR。",
                "相对强弱继续恶化并伴随放量下跌。",
                "人工或基本面资格审查判定投资逻辑已破坏。",
            ],
            visual_anchors=anchors,
        )


class BreakoutVolumeStrategy(StrategyAdapter):
    name = "breakout_volume_v1"
    minimum_bars = 60
    rule_set = (
        "Prior 20/60 session high-low breakouts, close location, ATR expansion, "
        "and volume effort/result; failed breaks remain explicit risk."
    )

    def _evaluate(self, context: StrategyContext) -> dict[str, Any]:
        bars = context.bars
        latest = context.latest or {}
        previous = context.previous or {}
        close = _number(latest.get("close"))
        previous_close = _number(previous.get("close"))
        prior20_high = max(_number(item.get("high")) or 0 for item in bars[-21:-1])
        prior20_low = min(_number(item.get("low")) or 0 for item in bars[-21:-1])
        prior60_high = max(_number(item.get("high")) or 0 for item in bars[-61:-1])
        prior60_low = min(_number(item.get("low")) or 0 for item in bars[-61:-1])
        volume = _mapping(context.indicators.get("volume_effort_result"))
        volume_ratio = _number(volume.get("volume_ratio_20d"))
        close_location = _number(volume.get("close_location_ratio"))
        range_atr_ratio = _number(volume.get("range_atr_ratio"))
        score = 0
        reasons: list[dict[str, Any]] = []
        bullish_breakout = close is not None and close > prior20_high
        bearish_breakdown = close is not None and close < prior20_low
        if close is not None and close > prior60_high:
            score += 34
            reasons.append(_reason("breakout_60d", "收盘突破前 60 个交易日高点。"))
        elif bullish_breakout:
            score += 22
            reasons.append(_reason("breakout_20d", "收盘突破前 20 个交易日高点。"))
        elif close is not None and close < prior60_low:
            score -= 34
            reasons.append(_reason("breakdown_60d", "收盘跌破前 60 个交易日低点。"))
        elif bearish_breakdown:
            score -= 22
            reasons.append(_reason("breakdown_20d", "收盘跌破前 20 个交易日低点。"))
        if volume_ratio is not None:
            if volume_ratio >= 1.5:
                score += 18 if score >= 0 else -18
                reasons.append(_reason("volume_expansion", f"成交量达到 20 日均量 {volume_ratio:.2f} 倍。"))
            elif volume_ratio < 0.8:
                score = round(score * 0.75)
                reasons.append(_reason("low_volume_break", "价格突破/跌破但成交量没有充分参与。"))
        if close_location is not None:
            if score > 0 and close_location >= 0.7:
                score += 10
                reasons.append(_reason("close_near_high", "突破日收盘位于日内高位附近。"))
            elif score < 0 and close_location <= 0.3:
                score -= 10
                reasons.append(_reason("close_near_low", "跌破日收盘位于日内低位附近。"))
        if range_atr_ratio is not None and range_atr_ratio >= 1.5:
            score += 8 if score > 0 else -8 if score < 0 else 0
        failed_bullish = previous_close is not None and previous_close > prior20_high and close is not None and close <= prior20_high
        failed_bearish = previous_close is not None and previous_close < prior20_low and close is not None and close >= prior20_low
        if failed_bullish:
            score -= 25
            reasons.append(_reason("failed_bullish_breakout", "价格重新跌回 20 日突破位下方。"))
        if failed_bearish:
            score += 25
            reasons.append(_reason("failed_bearish_breakdown", "价格重新收回 20 日跌破位上方。"))

        score = _bounded_score(score)
        stance, action = _stance_action(score)
        atr = _metric_value(context.indicators, "atr14")
        trigger = prior20_high if score >= 0 else prior20_low
        stage = (
            "invalidated"
            if failed_bullish or failed_bearish
            else _stage_for_score(score)
        )
        invalidation = prior20_low if score > 0 else prior20_high
        level_start = str(bars[-21]["date"])
        anchors = _price_anchors(
            context,
            (
                ("confirmation_line", trigger, "20 日突破/失效位"),
                ("invalidation_line", invalidation, "反向失效参考"),
            ),
            tone="bullish" if stance == "bullish" else "bearish" if stance == "bearish" else "neutral",
            start_time=level_start,
            end_time=context.as_of,
        )
        if stage == "confirmed":
            anchors.extend(
                _price_anchors(
                    context,
                    (("trigger_marker", close, "突破触发"),),
                    tone="bullish" if stance == "bullish" else "bearish",
                )
            )
        elif stage == "invalidated":
            anchors.extend(
                _price_anchors(
                    context,
                    (("evidence_marker", close, "失败突破"),),
                    tone="warning",
                )
            )
        return self._decision(
            context,
            status="ok",
            stance=stance,
            action=action,
            score=score,
            setup_progress=_setup_progress(
                stage=stage,
                as_of=context.as_of,
                confirmation_distance_atr=_distance_atr(close, trigger, atr),
                invalidation_distance_atr=_distance_atr(close, invalidation, atr),
                bars_in_stage=1,
            ),
            reasons=reasons or [_reason("inside_range", "价格仍在 20 日突破区间内。" )],
            risks=[
                "低成交量突破容易形成失败突破。",
                "突破策略对隔夜跳空和快速回撤敏感。",
            ],
            confirmation_conditions=["收盘维持在突破位外侧。", "成交量参与不快速衰减。"],
            invalidation_conditions=["收盘重新回到突破位内侧。", "突破后出现放量反向 K 线。"],
            visual_anchors=anchors,
        )


class RelativeStrengthRotationStrategy(StrategyAdapter):
    name = "relative_strength_rotation_v1"
    minimum_bars = 20
    rule_set = (
        "Date-aligned excess return versus the declared benchmark, 5/20/60 day "
        "persistence and relative index direction; benchmark absence is not applicable."
    )

    def evaluate(self, context: StrategyContext) -> dict[str, Any]:
        if not context.benchmark or not context.benchmark_bars:
            return self.not_applicable(context, "benchmark_missing", "未提供可对齐的 benchmark 日 K。")
        return super().evaluate(context)

    def _evaluate(self, context: StrategyContext) -> dict[str, Any]:
        excess = _mapping(context.relative.get("excess_returns_percent"))
        five = _number(excess.get("5d"))
        twenty = _number(excess.get("20d"))
        sixty = _number(excess.get("60d"))
        score = 0
        reasons: list[dict[str, Any]] = []
        for value, weight, label in ((five, 1, "5"), (twenty, 2, "20"), (sixty, 1, "60")):
            if value is None:
                continue
            score += max(-20, min(20, round(value * weight)))
            reasons.append(_reason(f"excess_return_{label}d", f"相对 {context.benchmark} 的 {label} 日超额收益 {value:.2f}%。"))
        if five is not None and twenty is not None:
            if five > 0 and twenty > 0:
                score += 15
                reasons.append(_reason("relative_persistence", "短中期相对强弱保持正向。"))
            elif five < 0 and twenty < 0:
                score -= 15
                reasons.append(_reason("relative_weakness_persistence", "短中期相对强弱持续落后。"))
        score = _bounded_score(score)
        stance, action = _stance_action(score)
        relative_series_id = f"relative_performance_vs_{context.benchmark}"
        return self._decision(
            context,
            status="ok" if excess else "not_applicable",
            stance=stance if excess else "insufficient_data",
            action=action if excess else "no_action",
            score=score if excess else None,
            setup_progress=_setup_progress(
                stage=_stage_for_score(score) if excess else "insufficient_data",
                as_of=context.as_of,
                confirmation_distance_atr=None,
                invalidation_distance_atr=None,
                bars_in_stage=1 if excess else 0,
            ),
            reasons=reasons or [_reason("relative_neutral", "没有足够的相对收益方向。" )],
            risks=[
                f"相对强弱只表达相对 {context.benchmark} 的表现，不等于绝对上涨。",
                "短样本相对强弱可能受单日异常收益影响。",
            ],
            confirmation_conditions=[f"相对 {context.benchmark} 的 5 日和 20 日超额收益保持同向。"],
            invalidation_conditions=[f"相对 {context.benchmark} 的 5 日超额收益转负并持续。"],
            visual_anchors=(
                [
                    _series_anchor(
                        context,
                        series_ids=[relative_series_id],
                        label=f"相对强弱 vs {context.benchmark}",
                        pane="relative_strength",
                        tone="bullish" if stance == "bullish" else "bearish" if stance == "bearish" else "neutral",
                    )
                ]
                if excess
                else []
            ),
        )


class StrategyRegistry:
    """Discover and execute every registered deterministic strategy."""

    def __init__(self, adapters: Iterable[StrategyAdapter] | None = None) -> None:
        self.adapters = tuple(
            adapters
            or (
                TrendMomentumStrategy(),
                MeanReversionStrategy(),
                QualityLeftSideReversalStrategy(),
                BreakoutVolumeStrategy(),
                RelativeStrengthRotationStrategy(),
            )
        )
        names = tuple(adapter.name for adapter in self.adapters)
        if len(names) != len(set(names)):
            raise ValueError(f"策略名称必须唯一：{names}")
        self._strategy_order = {name: index for index, name in enumerate(names)}

    @property
    def strategy_set_sha256(self) -> str:
        return content_sha256(
            [
                {
                    "name": adapter.name,
                    "version": adapter.version,
                    "implementation_sha256": adapter.implementation_sha256,
                }
                for adapter in self.adapters
            ]
        )

    def evaluate(
        self, dataset: dict[str, Any], chart: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        decisions: list[dict[str, Any]] = []
        for symbol in dataset.get("scope", {}).get("symbols", []):
            context = _build_context(dataset, chart, str(symbol))
            for adapter in self.adapters:
                try:
                    decision = adapter.evaluate(context)
                except Exception as error:  # isolate one strategy from the rest
                    decision = adapter.error(context, error)
                decision["strategy_set_sha256"] = self.strategy_set_sha256
                decision["content_sha256"] = content_sha256(
                    {
                        key: value
                        for key, value in decision.items()
                        if key not in {"decision_id", "generated_at", "content_sha256"}
                    }
                )
                decisions.append(decision)
        decisions.sort(
            key=lambda item: (
                str(item["scope"]["symbol"]),
                self._strategy_order.get(item["strategy"]["name"], len(self._strategy_order)),
                item["strategy"]["name"],
            )
        )
        return decisions, deterministic_synthesis(dataset, decisions, self.strategy_set_sha256)

    @staticmethod
    def with_overlays(chart: dict[str, Any], decisions: Iterable[dict[str, Any]]) -> dict[str, Any]:
        output = dict(chart)
        # Rebuilding a projection must not accumulate duplicate overlays when
        # a dataset is frozen again or a newer strategy set is requested.
        overlays = [
            item for item in (output.get("overlays") or []) if not item.get("strategy_decision_id")
        ]
        state_segments = [
            item
            for item in (output.get("state_segments") or [])
            if not item.get("strategy_decision_id")
        ]
        for decision in decisions:
            symbol = str(decision["scope"]["symbol"])
            strategy = decision["strategy"]
            for index, anchor in enumerate(decision.get("visual_anchors") or []):
                overlays.append(
                    {
                        "overlay_id": f"{decision['decision_id']}:anchor:{index}",
                        "strategy_decision_id": decision["decision_id"],
                        "symbol": symbol,
                        "strategy_name": strategy["name"],
                        "strategy_version": strategy.get("version"),
                        "stance": decision.get("stance"),
                        "action": decision.get("action"),
                        "kind": anchor.get("kind", "marker"),
                        "price": anchor.get("price"),
                        "lower_price": anchor.get("lower_price"),
                        "upper_price": anchor.get("upper_price"),
                        "series_ids": anchor.get("series_ids", []),
                        "pane": anchor.get("pane"),
                        "start_time": anchor.get("start_time"),
                        "end_time": anchor.get("end_time"),
                        "label": anchor.get("label"),
                        "tone": anchor.get("tone", "neutral"),
                        "evidence_refs": anchor.get("evidence_refs", []),
                        "reason": (decision.get("reasons") or [{}])[0].get("detail"),
                        "confirmation_conditions": decision.get("confirmation_conditions", []),
                        "invalidation_conditions": decision.get("invalidation_conditions", []),
                    }
                )
            progress = decision.get("setup_progress") or {}
            state_segments.append(
                {
                    "segment_id": f"{decision['decision_id']}:state",
                    "strategy_decision_id": decision["decision_id"],
                    "symbol": symbol,
                    "strategy_name": strategy["name"],
                    "state": progress.get("stage", "insufficient_data"),
                    "start_time": progress.get("stage_since"),
                    "end_time": None,
                    "label": f"{strategy['name']} · {progress.get('stage', 'insufficient_data')}",
                }
            )
        output["overlays"] = overlays
        output["state_segments"] = state_segments
        return output


def deterministic_synthesis(
    dataset: dict[str, Any], decisions: list[dict[str, Any]], strategy_set_sha256: str
) -> dict[str, Any]:
    usable = [
        item
        for item in decisions
        if item.get("status") == "ok" and item.get("stance") in {"bullish", "bearish", "neutral"}
    ]
    quality_values = [
        str(item.get("quality", {}).get("status"))
        for item in decisions
        if item.get("quality")
    ]
    has_strategy_error = any(item.get("status") == "error" for item in decisions)
    bullish = [item for item in usable if item.get("stance") == "bullish"]
    bearish = [item for item in usable if item.get("stance") == "bearish"]
    neutral = [item for item in usable if item.get("stance") == "neutral"]
    has_failed_quality = any(value in {"missing", "conflicted"} for value in quality_values)
    if has_failed_quality or (has_strategy_error and not usable):
        consensus_state = "insufficient_data"
    elif not usable:
        consensus_state = "no_signal"
    elif bullish and bearish:
        consensus_state = "conflicted"
    elif len(bullish) >= 2 or len(bearish) >= 2:
        consensus_state = "aligned"
    elif bullish or bearish or neutral:
        consensus_state = "mixed"
    else:
        consensus_state = "no_signal"

    if consensus_state == "aligned":
        suggested_action = "prioritize" if bullish else "avoid"
    elif consensus_state in {"conflicted", "mixed"}:
        suggested_action = "watch"
    else:
        suggested_action = "no_action"
    supporting = sorted(
        bullish if bullish else bearish,
        key=lambda item: abs(_number(item.get("score")) or 0),
        reverse=True,
    )
    conflicting = sorted(
        bearish if bullish else bullish,
        key=lambda item: abs(_number(item.get("score")) or 0),
        reverse=True,
    )
    strategy_set: list[dict[str, Any]] = []
    seen_strategies: set[tuple[str, str, str]] = set()
    for item in decisions:
        strategy = item["strategy"]
        identity = (
            str(strategy["name"]),
            str(strategy["version"]),
            str(strategy["implementation_sha256"]),
        )
        if identity in seen_strategies:
            continue
        seen_strategies.add(identity)
        strategy_set.append(
            {
                "name": identity[0],
                "version": identity[1],
                "implementation_sha256": identity[2],
            }
        )

    payload: dict[str, Any] = {
        "schema_version": "urus.deterministic_synthesis.v1",
        "dataset_id": dataset["dataset_id"],
        "scope": dict(dataset["scope"]),
        "strategy_set_sha256": strategy_set_sha256,
        "consensus_state": consensus_state,
        "bullish_count": len(bullish),
        "bearish_count": len(bearish),
        "neutral_count": len(neutral),
        "not_applicable_count": sum(item.get("status") == "not_applicable" for item in decisions),
        "error_count": sum(item.get("status") == "error" for item in decisions),
        "strongest_supporting_strategy_ids": [item["decision_id"] for item in supporting],
        "strongest_conflicting_strategy_ids": [item["decision_id"] for item in conflicting],
        "suggested_action": suggested_action,
        "conflict_summary": _conflict_summary(consensus_state, bullish, bearish, neutral),
        "strategy_set": strategy_set,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    payload["content_sha256"] = content_sha256(
        {key: value for key, value in payload.items() if key not in {"generated_at", "content_sha256"}}
    )
    return payload


def _build_context(dataset: dict[str, Any], chart: dict[str, Any], symbol: str) -> StrategyContext:
    instrument = (chart.get("instruments") or {}).get(symbol) or {}
    bars = [dict(item) for item in ((instrument.get("price") or {}).get("bars") or [])]
    quality = dict((dataset.get("quality", {}).get("symbols", {}) or {}).get(symbol) or {})
    indicators = calculate_technical_indicators(bars, source="daily_bars")
    benchmark = None
    benchmark_bars: list[dict[str, Any]] = []
    for candidate in dataset.get("scope", {}).get("benchmark_symbols", []):
        candidate_instrument = (chart.get("instruments") or {}).get(str(candidate)) or {}
        candidate_bars = [
            dict(item) for item in ((candidate_instrument.get("price") or {}).get("bars") or [])
        ]
        candidate_quality = (dataset.get("quality", {}).get("symbols", {}) or {}).get(str(candidate), {})
        if candidate_bars and candidate_quality.get("status") not in {"missing", "conflicted"}:
            benchmark = str(candidate)
            benchmark_bars = candidate_bars
            break
    relative = (
        calculate_relative_strength(
            bars,
            benchmark_bars,
            benchmark=benchmark or "",
            source="daily_bars",
        )
        if benchmark_bars
        else {"available": False, "excess_returns_percent": {}, "warnings": ["benchmark missing"]}
    )
    return StrategyContext(
        dataset=dataset,
        chart=chart,
        symbol=symbol,
        bars=bars,
        quality=quality,
        indicators=indicators,
        benchmark=benchmark,
        benchmark_bars=benchmark_bars,
        relative=relative,
    )


def _number(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(float(value)):
        return float(value)
    return None


def _mapping(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _metric_value(indicators: dict[str, Any], key: str) -> float | None:
    return _number(_mapping(indicators.get(key)).get("value"))


def _bounded_score(value: int | float) -> int:
    return max(-100, min(100, int(round(value))))


def _stance_action(score: int) -> tuple[str, str]:
    if score >= 60:
        return "bullish", "prioritize"
    if score >= 25:
        return "bullish", "watch"
    if score <= -60:
        return "bearish", "avoid"
    if score <= -25:
        return "bearish", "watch"
    return "neutral", "wait"


def _stage_for_score(score: int, *, bullish_stage: str = "confirmed") -> str:
    if abs(score) >= 60:
        return bullish_stage if score >= 0 else "confirmed"
    if abs(score) >= 25:
        return "near_confirmation"
    return "forming"


def _setup_progress(
    *,
    stage: str,
    as_of: str | None,
    confirmation_distance_atr: float | None,
    invalidation_distance_atr: float | None,
    bars_in_stage: int,
) -> dict[str, Any]:
    return {
        "stage": stage,
        "stage_since": as_of,
        "confirmation_distance_atr": _rounded(confirmation_distance_atr),
        "invalidation_distance_atr": _rounded(invalidation_distance_atr),
        "bars_in_stage": bars_in_stage,
        "changed_from_previous_stage": None,
    }


def _distance_atr(value: float | None, reference: float | None, atr: float | None) -> float | None:
    if value is None or reference is None or atr is None or atr <= 0:
        return None
    return abs(value - reference) / atr


def _rounded(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


def _reason(code: str, detail: str) -> dict[str, Any]:
    return {"code": code, "detail": detail}


def _evidence_refs(context: StrategyContext) -> list[dict[str, Any]]:
    symbol = context.symbol
    return [
        {
            "path": f"chart.instruments[{symbol}].price.bars",
            "as_of": context.as_of,
            "label": "完整日 K",
        },
        {
            "path": f"indicator_snapshots[{symbol}]",
            "input_bar_hash": context.quality.get("input_bar_hash"),
            "label": "技术指标快照",
        },
    ]


def _price_anchors(
    context: StrategyContext,
    anchors: Iterable[tuple[str, float | None, str]],
    *,
    tone: str,
    start_time: str | None = None,
    end_time: str | None = None,
) -> list[dict[str, Any]]:
    return [
        {
            "kind": kind,
            "price": _rounded(price),
            "label": label,
            "tone": tone,
            "start_time": start_time or context.as_of,
            "end_time": end_time,
            "evidence_refs": _evidence_refs(context),
        }
        for kind, price, label in anchors
        if price is not None
    ]


def _series_anchor(
    context: StrategyContext,
    *,
    series_ids: list[str],
    label: str,
    pane: str,
    tone: str,
) -> dict[str, Any]:
    return {
        "kind": "series_highlight",
        "series_ids": list(series_ids),
        "pane": pane,
        "label": label,
        "tone": tone,
        "start_time": context.bars[0]["date"] if context.bars else context.as_of,
        "end_time": context.as_of,
        "evidence_refs": _evidence_refs(context),
    }


def _zone_anchor(
    context: StrategyContext,
    *,
    lower: float,
    upper: float,
    label: str,
    start_time: str,
    tone: str,
) -> dict[str, Any]:
    return {
        "kind": "price_zone",
        "lower_price": _rounded(min(lower, upper)),
        "upper_price": _rounded(max(lower, upper)),
        "label": label,
        "tone": tone,
        "start_time": start_time,
        "end_time": context.as_of,
        "evidence_refs": _evidence_refs(context),
    }


def _left_side_support_zone(
    bars: list[dict[str, Any]], atr: float | None
) -> dict[str, Any] | None:
    """Find the nearest valid gap or high-volume price-density support."""
    if len(bars) < 40 or atr is None or atr <= 0:
        return None
    close = _number(bars[-1].get("close"))
    if close is None:
        return None
    candidates: list[dict[str, Any]] = []
    start = max(1, len(bars) - 180)
    for index in range(start, len(bars) - 2):
        previous_high = _number(bars[index - 1].get("high"))
        current_low = _number(bars[index].get("low"))
        if previous_high is None or current_low is None or previous_high <= 0:
            continue
        if current_low / previous_high - 1 < 0.0075:
            continue
        lower, upper = previous_high, current_low
        later_closes = [
            value
            for item in bars[index + 1 : -1]
            if (value := _number(item.get("close"))) is not None
        ]
        if any(value < lower - 0.5 * atr for value in later_closes):
            continue
        distance = _distance_to_zone(close, lower, upper)
        if distance <= 3 * atr:
            candidates.append(
                {
                    "kind": "gap_support",
                    "label": "向上缺口支撑区",
                    "lower": lower,
                    "upper": upper,
                    "start_time": bars[index - 1]["date"],
                    "distance": distance,
                    "priority": 0,
                }
            )
    if candidates:
        return min(candidates, key=lambda item: (item["distance"], -float(item["lower"])))

    profile_bars = bars[-120:-1]
    lows = [_number(item.get("low")) for item in profile_bars]
    highs = [_number(item.get("high")) for item in profile_bars]
    valid_lows = [value for value in lows if value is not None]
    valid_highs = [value for value in highs if value is not None]
    if not valid_lows or not valid_highs:
        return None
    price_min, price_max = min(valid_lows), max(valid_highs)
    span = price_max - price_min
    if span <= 0:
        return None
    bin_width = max(span / 24, atr * 0.6)
    bin_count = max(1, int(span / bin_width) + 1)
    volumes = [0.0] * bin_count
    first_dates: list[str | None] = [None] * bin_count
    for item in profile_bars:
        typical = sum(float(item[key]) for key in ("high", "low", "close")) / 3
        index = min(bin_count - 1, max(0, int((typical - price_min) / bin_width)))
        volumes[index] += max(0.0, float(item.get("volume") or 0))
        if first_dates[index] is None:
            first_dates[index] = str(item["date"])
    eligible = [
        index
        for index in range(bin_count)
        if price_min + index * bin_width <= close + atr
        and _distance_to_zone(
            close,
            price_min + index * bin_width,
            min(price_max, price_min + (index + 1) * bin_width),
        )
        <= 3 * atr
    ]
    if not eligible:
        return None
    index = max(eligible, key=lambda item: (volumes[item], item))
    lower = price_min + index * bin_width
    upper = min(price_max, lower + bin_width)
    return {
        "kind": "volume_profile_support",
        "label": "成交密集支撑区",
        "lower": lower,
        "upper": upper,
        "start_time": first_dates[index] or profile_bars[0]["date"],
        "distance": _distance_to_zone(close, lower, upper),
        "priority": 1,
    }


def _distance_to_zone(value: float, lower: float, upper: float) -> float:
    if lower <= value <= upper:
        return 0.0
    return lower - value if value < lower else value - upper


def _support_distance_atr(
    close: float | None, support: dict[str, Any] | None, atr: float | None
) -> float | None:
    if close is None or support is None or atr is None or atr <= 0:
        return None
    return _distance_to_zone(close, float(support["lower"]), float(support["upper"])) / atr


def _beta_adjusted_alpha(
    bars: list[dict[str, Any]],
    benchmark_bars: list[dict[str, Any]],
    beta: float | None,
    *,
    periods: int,
) -> float | None:
    if beta is None or periods < 1:
        return None
    instrument = {str(item["date"]): _number(item.get("close")) for item in bars}
    benchmark = {str(item["date"]): _number(item.get("close")) for item in benchmark_bars}
    dates = [
        day
        for day in sorted(set(instrument) & set(benchmark))
        if instrument[day] is not None and benchmark[day] is not None
    ]
    if len(dates) <= periods:
        return None
    start, end = dates[-periods - 1], dates[-1]
    start_instrument, end_instrument = instrument[start], instrument[end]
    start_benchmark, end_benchmark = benchmark[start], benchmark[end]
    if not start_instrument or not start_benchmark or end_instrument is None or end_benchmark is None:
        return None
    instrument_return = end_instrument / start_instrument - 1
    benchmark_return = end_benchmark / start_benchmark - 1
    return round((instrument_return - beta * benchmark_return) * 100, 4)


def _conflict_summary(
    state: str,
    bullish: list[dict[str, Any]],
    bearish: list[dict[str, Any]],
    neutral: list[dict[str, Any]],
) -> str:
    if state == "conflicted":
        return "趋势、均值回归或相对强弱策略出现方向冲突，保留全部原始输出，不自动平均。"
    if state == "aligned":
        return "至少两个可用策略方向一致，未发现相反方向的强策略。"
    if state == "mixed":
        return f"当前有 {len(bullish)} 个看多、{len(bearish)} 个看空、{len(neutral)} 个中性策略，方向未形成一致。"
    if state == "no_signal":
        return "策略没有形成可执行方向，保持观察。"
    return "关键数据质量或策略执行不可用，不能形成可靠的确定性综合建议。"
