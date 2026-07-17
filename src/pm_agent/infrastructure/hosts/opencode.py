from __future__ import annotations

from collections.abc import Callable

from pm_agent.domain.models import ApprovedAction, DispatchReceipt
from pm_agent.ports.host import HostCapabilities

from .base import verify_approved_action


class OpenCodeHostBridge:
    def __init__(
        self,
        dispatcher: Callable[[ApprovedAction], DispatchReceipt],
        categories: frozenset[str],
    ) -> None:
        self._dispatcher = dispatcher
        self._categories = categories

    def capabilities(self) -> HostCapabilities:
        return HostCapabilities(can_dispatch=True, supported_categories=self._categories)

    def dispatch(self, action: ApprovedAction) -> DispatchReceipt:
        verify_approved_action(action)
        if action.proposal.tool_category not in self._categories:
            raise ValueError(f"Host does not support {action.proposal.tool_category}.")
        return self._dispatcher(action)
