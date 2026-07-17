from __future__ import annotations

import logging
from dataclasses import replace

from pm_agent.application.decision_policy import DecisionPolicy
from pm_agent.domain.enums import ActionStatus, DecisionStatus
from pm_agent.domain.models import (
    PMResponse,
    action_fingerprint,
    canonical_json,
    decision_fingerprint,
    payload_hash,
)
from pm_agent.ports.memory import RetrievalQuery
from pm_agent.ports.model import ModelEventHandler, ModelRequest
from pm_agent.prompts.parser import ResponseValidationError

logger = logging.getLogger(__name__)


class ConnectionError(Exception):
    pass


class ConversationService:
    def __init__(
        self,
        store,
        provider,
        prompt_builder,
        parser,
        action_service,
        history_limit: int = 75,
        character_budget: int = 18_000,
    ) -> None:
        self.store = store
        self.provider = provider
        self.prompt_builder = prompt_builder
        self.parser = parser
        self.action_service = action_service
        self.history_limit = history_limit
        self.character_budget = character_budget

    def handle(
        self,
        project,
        session,
        user_input: str,
        on_model_event: ModelEventHandler | None = None,
    ) -> tuple[PMResponse, list]:
        self.store.add_message(session.id, "user", user_input)
        packet = self.store.retrieve(
            RetrievalQuery(
                project_id=project.id,
                session_id=session.id,
                text=user_input,
                history_limit=self.history_limit,
                character_budget=self.character_budget,
            )
        )
        schema = self.prompt_builder.response_schema()
        request = ModelRequest(
            messages=self.prompt_builder.build(project, session.branch, packet, user_input),
            response_schema=schema,
        )
        try:
            result = self._generate(request, on_model_event)
        except ConnectionError as exc:
            raise ConnectionError(f"Model connection failed: {exc}") from exc

        try:
            response = self.parser.parse(result.content)
        except ResponseValidationError as first_error:
            content_preview = result.content[:200] if result.content else "<empty>"
            if not result.content or result.content.strip() in ("{}", ""):
                raise ResponseValidationError(
                    f"Model returned empty or minimal response. "
                    f"Content received: {content_preview}. "
                    f"Original error: {first_error}"
                ) from first_error
            try:
                repair = self._generate(
                    ModelRequest(
                        messages=self.prompt_builder.repair_messages(
                            result.content, str(first_error), schema
                        ),
                        response_schema=schema,
                        temperature=0.0,
                    ),
                    on_model_event,
                )
            except ConnectionError as exc:
                raise ConnectionError(f"Model connection failed during repair: {exc}") from exc
            try:
                response = self.parser.parse(repair.content)
            except ResponseValidationError as repair_error:
                raise ResponseValidationError(
                    f"Model response could not be parsed (repair also failed). "
                    f"First error: {first_error}. "
                    f"Repair error: {repair_error}"
                ) from repair_error

        # Make the agent's ask-vs-act stance explicit and surface regressions
        # (e.g. delegating work it could do itself) as risks rather than asking.
        needs = DecisionPolicy.classify(response)
        warnings = DecisionPolicy.validate(response)
        if warnings:
            response = replace(
                response, execution_needs=needs, risks=[*response.risks, *warnings]
            )
        else:
            response = replace(response, execution_needs=needs)

        stored_actions = []
        approved_candidates = []
        blocked_operations: list[str] = []
        for decision in response.decisions:
            fingerprint = decision_fingerprint(
                decision.topic, decision.title, decision.decision
            )
            existing = self.store.find_decision_in_session(
                project.id, session.id, fingerprint
            )
            if existing is not None:
                logger.debug(
                    "suppressing duplicate decision proposal (fingerprint=%s, existing_status=%s)",
                    fingerprint,
                    existing.status.value,
                )
                continue
            self._supersede_prior_decisions(project, session, decision, fingerprint)
            self.store.add_decision(
                project.id,
                session.id,
                decision.topic,
                decision.title,
                decision.decision,
                decision.reason,
                status=DecisionStatus.PROPOSED,
                fingerprint=fingerprint,
            )
        for candidate in response.actions_requiring_approval:
            fingerprint = action_fingerprint(
                candidate.action_type.value,
                candidate.operation,
                payload_hash(candidate.payload),
            )
            existing = self.store.find_action_in_session(
                project.id, session.id, fingerprint
            )
            if existing is not None:
                logger.debug(
                    "suppressing duplicate action proposal (fingerprint=%s, existing_status=%s)",
                    fingerprint,
                    existing.status.value,
                )
                continue
            proposal = self.action_service.propose(project.id, session.id, candidate)
            if proposal.status is ActionStatus.PROPOSED:
                stored_actions.append(proposal)
                approved_candidates.append(candidate)
            else:
                reason = self.store.get_action_rejection_reason(proposal.id) or "unknown"
                logger.debug(
                    "action_rejected: operation=%s type=%s tool_category=%s reason=%s payload=%s",
                    candidate.operation,
                    candidate.action_type.value,
                    candidate.tool_category,
                    reason,
                    candidate.payload,
                )
                category = DecisionPolicy.categorize_block(proposal.operation)
                if category == "external_access":
                    blocked_operations.append(
                        f"{proposal.operation} (blocked: missing external access/permission "
                        f"- {reason})"
                    )
                else:
                    blocked_operations.append(
                        f"{proposal.operation} (blocked: needs approval - {reason})"
                    )

        if blocked_operations or len(approved_candidates) != len(response.actions_requiring_approval):
            response = replace(
                response,
                actions_requiring_approval=approved_candidates,
                risks=[
                    *response.risks,
                    "Safety policy rejected action proposals: "
                    + ", ".join(blocked_operations),
                ] if blocked_operations else response.risks,
            )

        rendered_json = canonical_json(
            {
                "summary": response.summary,
                "analysis": response.analysis,
                "risks": response.risks,
                "recommendations": response.recommendations,
                "decisions": [
                    {
                        "topic": decision.topic,
                        "title": decision.title,
                        "decision": decision.decision,
                        "reason": decision.reason,
                        "status": decision.status.value,
                    }
                    for decision in response.decisions
                ],
                "actions_requiring_approval": [
                    {
                        "action_type": action.action_type.value,
                        "tool_category": action.tool_category,
                        "operation": action.operation,
                        "reason": action.reason,
                        "impact": action.impact,
                        "payload": action.payload,
                    }
                    for action in response.actions_requiring_approval
                ],
            }
        )
        self.store.add_message(session.id, "assistant", rendered_json)
        return response, stored_actions

    def _generate(self, request, on_model_event: ModelEventHandler | None):
        if on_model_event is None:
            return self.provider.generate(request)
        return self.provider.generate(request, on_model_event)

    def _supersede_prior_decisions(
        self, project, session, decision, fingerprint: str
    ) -> None:
        """If an earlier decision on the same topic/title was proposed but the
        model now proposes a materially different one, mark the old proposal as
        superseded so pending-approval state stays consistent.
        """
        supersedeable = {DecisionStatus.PROPOSED, DecisionStatus.DEFERRED}
        priors = [
            d
            for d in self.store.list_decisions(project.id, limit=10_000)
            if d.session_id == session.id
            and d.topic == decision.topic
            and d.title == decision.title
            and d.fingerprint != fingerprint
            and d.status in supersedeable
        ]
        for prior in priors:
            logger.debug(
                "superseding prior decision %s (status=%s) with revised proposal",
                prior.id,
                prior.status.value,
            )
            self.store.set_decision_status(
                prior.id, project.id, DecisionStatus.SUPERSEDED
            )
