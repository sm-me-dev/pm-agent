from __future__ import annotations

from pm_agent.domain.enums import DecisionStatus


class DecisionService:
    def __init__(self, store) -> None:
        self.store = store

    def accept(self, project_id: str, decision_id: str) -> None:
        self.store.set_decision_status(decision_id, project_id, DecisionStatus.ACCEPTED)

    def reject(self, project_id: str, decision_id: str) -> None:
        self.store.set_decision_status(decision_id, project_id, DecisionStatus.REJECTED)

    def defer(self, project_id: str, decision_id: str) -> None:
        self.store.set_decision_status(decision_id, project_id, DecisionStatus.DEFERRED)

    def list(self, project_id: str):
        return self.store.list_decisions(project_id)
