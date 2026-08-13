from __future__ import annotations

import hashlib
import json
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.core.config import Settings
from app.core.errors import AppError
from app.core.time import utc_now
from app.models.universe import InstrumentUniverseItemModel, InstrumentUniverseVersionModel
from app.schemas.universe import (
    InstrumentConfig,
    UniverseDerivedScopes,
    UniverseImpactResponse,
    UniverseResponse,
    UniverseUpdate,
)


MARKET_SYMBOLS = {"SPY", "QQQ", "IWM", "DIA", "RSP", "HYG", "LQD", "TLT", "IEF", "UUP", "GLD", "USO"}
ETF_SYMBOLS = {"SMH", "SOXX", "IGV"}
THEMES = {
    "SPY": "美国大盘", "QQQ": "科技大盘", "IWM": "美国小盘", "DIA": "道指",
    "RSP": "等权大盘", "HYG": "高收益信用", "LQD": "投资级信用", "TLT": "长期国债",
    "IEF": "中期国债", "UUP": "美元", "GLD": "黄金", "USO": "原油",
    "SMH": "半导体", "SOXX": "半导体", "IGV": "软件",
}
CTA_TARGETS = {
    "SPY": "ES", "QQQ": "NQ", "IWM": "RTY", "IEF": "TY", "TLT": "US",
    "UUP": "DXY", "GLD": "GC", "USO": "CL", "HYG": "信用风险", "LQD": "投资级信用",
    "SMH": "半导体风险", "IGV": "软件风险",
}


def derive_scopes(items: list[InstrumentConfig]) -> UniverseDerivedScopes:
    enabled = [item for item in items if item.enabled]
    return UniverseDerivedScopes(
        market_symbols=[item.symbol for item in enabled if item.collection.quote and item.asset_type in {"market", "etf"}],
        instrument_symbols=[
            item.symbol for item in enabled
            if item.collection.daily_history and item.roles.equity_watchlist
        ],
        cta_proxy_symbols=[item.symbol for item in enabled if item.roles.cta_proxy],
        option_symbols=[item.symbol for item in enabled if item.collection.options],
        event_symbols=[item.symbol for item in enabled if item.roles.event_tracking],
        ai_candidate_symbols=[item.symbol for item in enabled if item.roles.ai_candidate],
    )


def default_universe(settings: Settings) -> list[InstrumentConfig]:
    ordered = list(dict.fromkeys(
        [item.strip().upper() for item in settings.moomoo_market_symbols.split(",") if item.strip()]
        + settings.instrument_validation_symbol_list
        + settings.enabled_symbol_list
        + settings.cta_proxy_symbol_list
        + settings.options_collection_symbol_list
        + settings.event_instrument_symbol_list
    ))
    options = set(settings.options_collection_symbol_list)
    events = set(settings.event_instrument_symbol_list)
    cta = set(settings.cta_proxy_symbol_list)
    candidates = set(settings.instrument_validation_symbol_list) | set(settings.enabled_symbol_list)
    items: list[InstrumentConfig] = []
    for symbol in ordered:
        asset_type = "market" if symbol in MARKET_SYMBOLS else "etf" if symbol in ETF_SYMBOLS else "equity"
        has_options = symbol in options
        items.append(InstrumentConfig.model_validate({
            "symbol": symbol,
            "display_name": symbol,
            "asset_type": asset_type,
            "themes": [THEMES.get(symbol, "个股观察" if asset_type == "equity" else "行业 ETF")],
            "enabled": True,
            "roles": {
                "market_benchmark": symbol in {"QQQ", "SPY"},
                "equity_watchlist": symbol in set(settings.instrument_validation_symbol_list),
                "cta_proxy": symbol in cta,
                "options_collection": has_options,
                "event_tracking": symbol in events,
                "ai_candidate": symbol in candidates,
            },
            "benchmarks": {
                "relative_strength": None if symbol == "QQQ" else "QQQ",
                "cta_proxy_for": CTA_TARGETS.get(symbol) if symbol in cta else None,
            },
            "collection": {"quote": True, "daily_history": True, "options": has_options},
            "notes": "由环境配置初始化",
        }))
    return items


class InstrumentUniverseRepository:
    def __init__(self, session: Session):
        self.session = session

    def active(self) -> InstrumentUniverseVersionModel | None:
        statement = (
            select(InstrumentUniverseVersionModel)
            .options(selectinload(InstrumentUniverseVersionModel.items))
            .order_by(InstrumentUniverseVersionModel.revision.desc())
            .limit(1)
        )
        return self.session.scalar(statement)

    def get(self, version_id: str) -> InstrumentUniverseVersionModel | None:
        statement = (
            select(InstrumentUniverseVersionModel)
            .options(selectinload(InstrumentUniverseVersionModel.items))
            .where(InstrumentUniverseVersionModel.id == version_id)
        )
        return self.session.scalar(statement)

    def ensure_default(self, settings: Settings) -> InstrumentUniverseVersionModel:
        current = self.active()
        if current is not None:
            return current
        return self._persist(default_universe(settings), source="environment")

    def save(self, update: UniverseUpdate) -> InstrumentUniverseVersionModel:
        current = self.active()
        if current is not None and update.base_version_id != current.id:
            raise AppError(
                "标的设置已被其他页面更新，请刷新后再保存。",
                code="universe_version_conflict",
                status_code=409,
                details={"current_version_id": current.id},
            )
        digest = self._digest(update.items)
        if current is not None and current.content_sha256 == digest:
            return current
        return self._persist(update.items, source="runtime")

    def list_versions(self, limit: int = 30) -> list[InstrumentUniverseVersionModel]:
        statement = (
            select(InstrumentUniverseVersionModel)
            .options(selectinload(InstrumentUniverseVersionModel.items))
            .order_by(InstrumentUniverseVersionModel.revision.desc())
            .limit(limit)
        )
        return list(self.session.scalars(statement))

    def _persist(self, items: list[InstrumentConfig], *, source: str) -> InstrumentUniverseVersionModel:
        revision = int(self.session.scalar(select(func.max(InstrumentUniverseVersionModel.revision))) or 0) + 1
        version = InstrumentUniverseVersionModel(
            id=str(uuid4()), revision=revision, content_sha256=self._digest(items), source=source, created_at=utc_now()
        )
        version.items = [
            InstrumentUniverseItemModel(
                id=str(uuid4()), position=position, symbol=item.symbol, display_name=item.display_name,
                asset_type=item.asset_type, theme=item.theme, themes=item.themes, enabled=item.enabled,
                roles=item.roles.model_dump(mode="json"), benchmarks=item.benchmarks.model_dump(mode="json"),
                collection=item.collection.model_dump(mode="json"), notes=item.notes,
            )
            for position, item in enumerate(items)
        ]
        self.session.add(version)
        self.session.commit()
        self.session.refresh(version)
        return self.get(version.id) or version

    @staticmethod
    def _digest(items: list[InstrumentConfig]) -> str:
        canonical = json.dumps(
            [item.model_dump(mode="json") for item in items], ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def response(version: InstrumentUniverseVersionModel) -> UniverseResponse:
        items = [InstrumentConfig.model_validate({
            "symbol": item.symbol, "display_name": item.display_name, "asset_type": item.asset_type,
            "theme": item.theme, "themes": item.themes or [item.theme], "enabled": item.enabled, "roles": item.roles,
            "benchmarks": item.benchmarks, "collection": item.collection, "notes": item.notes,
        }) for item in version.items]
        return UniverseResponse(
            version_id=version.id, revision=version.revision, content_sha256=version.content_sha256,
            source=version.source, created_at=version.created_at, items=items, derived=derive_scopes(items),
        )

    @staticmethod
    def impact(item: InstrumentConfig) -> UniverseImpactResponse:
        effects: list[str] = []
        if not item.enabled:
            effects.append("已停用：后续采集和 AI 分析均不使用")
        if item.collection.quote: effects.append("采集实时报价")
        if item.collection.daily_history: effects.append("采集日线并计算技术指标")
        if item.collection.options: effects.append("采集期权链、IV/HV、Gamma 结构")
        if item.roles.cta_proxy: effects.append(f"参与 CTA 压力：{item.benchmarks.cta_proxy_for}")
        if item.roles.event_tracking: effects.append("参与事件检索")
        if item.roles.ai_candidate: effects.append("进入 AI 排名与现状分析")
        return UniverseImpactResponse(symbol=item.symbol, effects=effects)
