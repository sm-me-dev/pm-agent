from __future__ import annotations

from datetime import datetime


class SessionService:
    def __init__(self, store) -> None:
        self.store = store

    def start(self, project, model: str, provider: str, branch: str, name: str | None = None):
        session_name = name or f"session-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        return self.store.start_session(project.id, session_name, model, provider, branch)

    def close(self, session_id: str) -> None:
        self.store.close_session(session_id)
        self.store.checkpoint()
