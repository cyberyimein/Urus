from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class AnomaloRequest:
    session_id: str
    message: str


@dataclass(frozen=True)
class AnomaloResponse:
    final_text: str | None
    is_mock: bool
    disabled: bool = False


class AnomaloAdapter(Protocol):
    def summarize(self, request: AnomaloRequest) -> AnomaloResponse: ...


class MockAnomaloAdapter:
    """Offline stand-in; it never creates an HTTP client or accesses the network."""

    def summarize(self, request: AnomaloRequest) -> AnomaloResponse:
        return AnomaloResponse(
            final_text=(
                "模拟摘要：假定事件已发布，当前仅用于验证条件步骤、session_id "
                f"({request.session_id}) 和前端展示链路。"
            ),
            is_mock=True,
        )


class DisabledAnomaloAdapter:
    """Disabled behavior for a future production wiring point."""

    def summarize(self, request: AnomaloRequest) -> AnomaloResponse:
        return AnomaloResponse(final_text=None, is_mock=True, disabled=True)

