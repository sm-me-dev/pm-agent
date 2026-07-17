from __future__ import annotations

from pm_agent.domain.enums import ActionStatus
from pm_agent.domain.models import (
    ActionCandidate,
    ActionOutcome,
    ActionProposal,
    ApprovedAction,
    DispatchReceipt,
    action_fingerprint,
    new_id,
    payload_hash,
    utc_now,
)
from pm_agent.domain.policies import ActionPolicy
from pm_agent.ports.host import HostBridge


class ActionService:
    def __init__(self, store, host: HostBridge, policy: ActionPolicy | None = None) -> None:
        self.store = store
        self.host = host
        self.policy = policy or ActionPolicy()

    def propose(self, project_id: str, session_id: str, candidate: ActionCandidate) -> ActionProposal:
        policy_decision = self.policy.evaluate(candidate)
        proposal = ActionProposal(
            id=new_id(),
            project_id=project_id,
            session_id=session_id,
            action_type=candidate.action_type,
            tool_category=candidate.tool_category,
            operation=candidate.operation,
            reason=candidate.reason,
            impact=candidate.impact,
            payload=candidate.payload,
            payload_sha256=payload_hash(candidate.payload),
            risk_level=policy_decision.risk_level,
            status=ActionStatus.PROPOSED,
            created_at=utc_now(),
            fingerprint=action_fingerprint(
                candidate.action_type.value,
                candidate.operation,
                payload_hash(candidate.payload),
            ),
        )
        self.store.create_action(proposal)
        if not policy_decision.allowed:
            return self.store.transition_action(
                proposal.id,
                ActionStatus.REJECTED,
                "policy",
                {"reason": policy_decision.reason},
            )
        return proposal

    def approve(self, action_id: str, actor: str = "user") -> DispatchReceipt:
        proposal = self.store.get_action(action_id)
        if proposal is None:
            raise KeyError(f"Unknown action: {action_id}")
        if proposal.status is not ActionStatus.PROPOSED:
            raise ValueError(
                f"Action {action_id} is {proposal.status.value} and cannot be approved. "
                "Retry it to create a fresh policy-evaluated proposal."
            )
        approved = self.store.transition_action(
            action_id,
            ActionStatus.APPROVED,
            actor,
            {"payload_sha256": proposal.payload_sha256},
        )
        if not self.store.action_has_approval(action_id, approved.payload_sha256):
            raise ValueError("Approval audit record could not be verified.")
        approved_action = ApprovedAction(
            proposal=approved,
            approved_payload_sha256=approved.payload_sha256,
            approved_by=actor,
            approved_at=utc_now(),
        )
        receipt = self.host.dispatch(approved_action)
        if receipt.dispatched:
            self.store.transition_action(
                action_id,
                ActionStatus.DISPATCHED,
                "host",
                {"correlation_id": receipt.correlation_id},
            )
            if receipt.completed:
                self.store.record_outcome(
                    ActionOutcome(
                        action_id=action_id,
                        host_correlation_id=receipt.correlation_id,
                        exit_code=receipt.exit_code,
                        stdout=receipt.stdout,
                        stderr=receipt.stderr,
                        result=receipt.result,
                        started_at=None,
                        completed_at=utc_now(),
                        recorded_at=utc_now(),
                    )
                )
        return receipt

    def retry(
        self,
        action_id: str,
        session_id: str,
    ) -> ActionProposal:
        proposal = self.store.get_action(action_id)
        if proposal is None:
            raise KeyError(f"Unknown action: {action_id}")
        if proposal.status not in {
            ActionStatus.REJECTED,
            ActionStatus.FAILED,
            ActionStatus.EXPIRED,
        }:
            raise ValueError(
                f"Only rejected, failed, or expired actions can be retried; "
                f"action {action_id} is {proposal.status.value}."
            )
        return self.propose(
            proposal.project_id,
            session_id,
            ActionCandidate(
                action_type=proposal.action_type,
                tool_category=proposal.tool_category,
                operation=proposal.operation,
                reason=f"Retry of {proposal.id}: {proposal.reason}",
                impact=proposal.impact,
                payload=proposal.payload,
            ),
        )

    def reject(self, action_id: str, actor: str = "user") -> ActionProposal:
        return self.store.transition_action(action_id, ActionStatus.REJECTED, actor)

    def record_outcome(
        self,
        action_id: str,
        exit_code: int | None,
        stdout: str = "",
        stderr: str = "",
        result: dict | None = None,
        correlation_id: str | None = None,
    ) -> ActionProposal:
        proposal = self.store.get_action(action_id)
        if proposal is None:
            raise KeyError(f"Unknown action: {action_id}")
        if proposal.status is ActionStatus.APPROVED:
            proposal = self.store.transition_action(
                action_id,
                ActionStatus.DISPATCHED,
                "external-host",
                {"correlation_id": correlation_id},
            )
        if proposal.status is not ActionStatus.DISPATCHED:
            raise ValueError("Only approved or dispatched actions can receive outcomes.")
        return self.store.record_outcome(
            ActionOutcome(
                action_id=action_id,
                host_correlation_id=correlation_id,
                exit_code=exit_code,
                stdout=stdout,
                stderr=stderr,
                result=result or {},
                started_at=None,
                completed_at=utc_now(),
                recorded_at=utc_now(),
            )
        )
