from __future__ import annotations

from pm_agent.infrastructure.sqlite import SQLiteStore
from pm_agent.ports.memory import RetrievalQuery


def _store(tmp_path):
    return SQLiteStore(tmp_path / "state.db")


def test_retrieve_returns_project_identity_without_fts_match(tmp_path):
    store = _store(tmp_path)
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    store.save_summary(_summary(project.id, session.id, "This is the pm-agent project."))
    store.add_decision(
        project.id, session.id, "product", "Scope", "MVP is auth + dashboard.",
        "Stakeholder input.", status=__import__(
            "pm_agent.domain.enums", fromlist=["DecisionStatus"]
        ).DecisionStatus.PROPOSED,
    )

    # A completely unrelated user input must still surface the project identity.
    packet = store.retrieve(RetrievalQuery(
        project_id=project.id, session_id=session.id,
        text="what is the weather today", history_limit=10, character_budget=18_000,
    ))
    contents = " ".join(f"{i.title} {i.content}" for i in packet.items)
    assert "pm-agent" in contents
    assert "MVP is auth" in contents


def test_retrieve_includes_proposed_decisions(tmp_path):
    store = _store(tmp_path)
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    store.add_decision(
        project.id, session.id, "product", "Open question", "We should pick a market.",
        "Unknown.", status=__import__(
            "pm_agent.domain.enums", fromlist=["DecisionStatus"]
        ).DecisionStatus.PROPOSED,
    )
    packet = store.retrieve(RetrievalQuery(
        project_id=project.id, session_id=session.id,
        text="unrelated", history_limit=10, character_budget=18_000,
    ))
    assert any(i.kind.value == "decision" for i in packet.items)


def _summary(project_id, session_id, narrative):
    from pm_agent.domain.models import SessionSummary

    return SessionSummary(
        id="s1", project_id=project_id, session_id=session_id,
        key_topics=["identity"], decisions=["scope"], planned_actions=["audit"],
        open_questions=[], narrative=narrative, created_at="now",
    )
