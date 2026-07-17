from __future__ import annotations

from pm_agent.application.session_service import SessionService
from pm_agent.application.summary_service import SummaryService
from pm_agent.infrastructure.sqlite import SQLiteStore
from pm_agent.presentation.renderers import summary_markdown


def test_shutdown_persists_and_renders_summary(tmp_path):
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    sessions = SessionService(store)
    session = sessions.start(project, "glm-5.2", "test", "unknown", "sprint-1")
    store.add_message(session.id, "user", "Plan milestone one")
    summary = SummaryService(store).create(project.id, session.id)
    sessions.close(session.id)
    rendered = summary_markdown(summary)
    assert "# Session Summary" in rendered
    assert "## Key Topics" in rendered
    assert "Plan milestone one" in rendered
