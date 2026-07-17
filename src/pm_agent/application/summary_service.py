from __future__ import annotations

from pm_agent.domain.enums import ActionStatus
from pm_agent.domain.models import SessionSummary, new_id, utc_now


class SummaryService:
    def __init__(self, store) -> None:
        self.store = store

    def create(self, project_id: str, session_id: str) -> SessionSummary:
        messages = self.store.get_recent_messages(session_id, 100)
        decisions = self.store.list_decisions(project_id, limit=20)
        actions = self.store.list_actions(project_id, limit=20)
        user_topics = [message.content for message in messages if message.role == "user"][-5:]
        summary = SessionSummary(
            id=new_id(),
            project_id=project_id,
            session_id=session_id,
            key_topics=user_topics or ["No user topics recorded."],
            decisions=[
                f"{decision.status.value}: {decision.topic} | {decision.title}"
                for decision in decisions
                if decision.session_id == session_id
            ],
            planned_actions=[
                f"{action.status.value}: {action.operation}"
                for action in actions
                if action.session_id == session_id
                and action.status
                in {
                    ActionStatus.PROPOSED,
                    ActionStatus.APPROVED,
                    ActionStatus.DISPATCHED,
                }
            ],
            open_questions=[],
            narrative=(
                f"Session contained {len(messages)} messages, "
                f"{sum(decision.session_id == session_id for decision in decisions)} decisions, "
                f"and {sum(action.session_id == session_id for action in actions)} actions."
            ),
            created_at=utc_now(),
        )
        self.store.save_summary(summary)
        return summary
