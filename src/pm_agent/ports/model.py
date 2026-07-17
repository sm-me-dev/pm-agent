from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


@dataclass(frozen=True)
class ModelRequest:
    messages: list[dict[str, str]]
    response_schema: dict[str, Any]
    temperature: float = 0.2


@dataclass(frozen=True)
class ModelResult:
    content: str
    used_native_schema: bool


class ModelStreamEventType(StrEnum):
    STATUS = "status"
    REASONING = "reasoning"
    CONTENT = "content"


@dataclass(frozen=True)
class ModelStreamEvent:
    event_type: ModelStreamEventType
    text: str


ModelEventHandler = Callable[[ModelStreamEvent], None]


class ModelProvider(Protocol):
    def generate(
        self,
        request: ModelRequest,
        on_event: ModelEventHandler | None = None,
    ) -> ModelResult: ...
