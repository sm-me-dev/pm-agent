from __future__ import annotations

from pm_agent.ports.memory import RetrievalQuery


class RetrievalService:
    def __init__(self, store, history_limit: int = 75, character_budget: int = 18_000) -> None:
        self.store = store
        self.history_limit = history_limit
        self.character_budget = character_budget

    def retrieve(self, project_id: str, session_id: str, text: str):
        return self.store.retrieve(
            RetrievalQuery(
                project_id=project_id,
                session_id=session_id,
                text=text,
                history_limit=self.history_limit,
                character_budget=self.character_budget,
            )
        )
