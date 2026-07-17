from __future__ import annotations

from pm_agent.domain.models import ApprovedAction


def verify_approved_action(action: ApprovedAction) -> None:
    if action.proposal.payload_sha256 != action.approved_payload_sha256:
        raise ValueError("Approved action payload hash does not match the proposal.")
