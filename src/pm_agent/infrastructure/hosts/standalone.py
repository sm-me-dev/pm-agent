from __future__ import annotations

from pm_agent.domain.models import ApprovedAction, DispatchReceipt
from pm_agent.ports.host import HostCapabilities

from .base import verify_approved_action


class StandaloneHostBridge:
    def capabilities(self) -> HostCapabilities:
        return HostCapabilities(can_dispatch=False, supported_categories=frozenset())

    def dispatch(self, action: ApprovedAction) -> DispatchReceipt:
        verify_approved_action(action)
        return DispatchReceipt(
            correlation_id=None,
            dispatched=False,
            message="Standalone mode recorded approval; execute through OpenCode and report the result.",
            deferred_external=True,
        )
