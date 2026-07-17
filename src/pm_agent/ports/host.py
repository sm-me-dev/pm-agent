from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from pm_agent.domain.models import ApprovedAction, DispatchReceipt


@dataclass(frozen=True)
class HostCapabilities:
    can_dispatch: bool
    supported_categories: frozenset[str]


class HostBridge(Protocol):
    def capabilities(self) -> HostCapabilities: ...

    def dispatch(self, action: ApprovedAction) -> DispatchReceipt: ...
