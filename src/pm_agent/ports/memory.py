from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pm_agent.domain.models import ContextPacket


@dataclass(frozen=True)
class RetrievalQuery:
    project_id: str
    session_id: str
    text: str
    history_limit: int = 75
    item_limit: int = 16
    character_budget: int = 18_000


class MemoryStore(Protocol):
    def retrieve(self, query: RetrievalQuery) -> ContextPacket: ...
