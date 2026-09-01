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
def derive_scopes(items: list[InstrumentConfig]) -> UniverseDerivedScopes:
    enabled = [item for item in items if item.enabled]
    return UniverseDerivedScopes(
        market_symbols=[item.symbol for item in enabled if item.collection.quote and item.asset_type in {"market", "etf"}],
        instrument_symbols=[
            item.symbol for item in enabled
            if item.collection.daily_history and item.roles.equity_watchlist
        ],
        option_symbols=[item.symbol for item in enabled if item.collection.options],
        ai_candidate_symbols=[item.symbol for item in enabled if item.roles.ai_candidate],
        history_symbols=[item.symbol for item in enabled if item.collection.daily_history],
    )


def default_universe(settings: Settings) -> list[InstrumentConfig]:
    ordered = list(dict.fromkeys(
        [item.strip().upper() for item in settings.moomoo_market_symbols.split(",") if item.strip()]
        + settings.instrument_validation_symbol_list
        + settings.enabled_symbol_list
        + settings.options_collection_symbol_list
    ))
    options = set(settings.options_collection_symbol_list)
    candidates = set(settings.instrument_validation_symbol_list) | set(settings.enabled_symbol_list)
    configured_history = set(settings.instrument_validation_symbol_list) | set(settings.enabled_symbol_list) | {"QQQ"}
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
                "options_collection": has_options,
                "ai_candidate": symbol in candidates,
            },
            "benchmarks": {
                "relative_strength": None if symbol == "QQQ" else "QQQ",
            },
            # Market proxy ETFs remain snapshot-only by default.  QQQ and the
            # configured technical universe receive daily history; a symbol
            # added through the UI still defaults to daily_history=true.
            "collection": {"quote": True, "daily_history": symbol in configured_history, "options": has_options},
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

    def save(
        self, update: UniverseUpdate, *, source: str = "runtime"
    ) -> InstrumentUniverseVersionModel:
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
        return self._persist(update.items, source=source)

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
    def content_digest(items: list[InstrumentConfig]) -> str:
        """Return the canonical digest used by Universe API responses."""

        return InstrumentUniverseRepository._digest(items)

    @staticmethod
    def response(version: InstrumentUniverseVersionModel) -> UniverseResponse:
        items = [InstrumentConfig.model_validate({
            "symbol": item.symbol, "display_name": item.display_name, "asset_type": item.asset_type,
            "theme": item.theme, "themes": item.themes or [item.theme], "enabled": item.enabled, "roles": item.roles,
            "benchmarks": item.benchmarks, "collection": item.collection, "notes": item.notes,
        }) for item in version.items]
        return UniverseResponse(
            version_id=version.id, revision=version.revision, content_sha256=InstrumentUniverseRepository.content_digest(items),
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
        if item.roles.ai_candidate: effects.append("进入 AI 排名与现状分析")
        return UniverseImpactResponse(symbol=item.symbol, effects=effects)
