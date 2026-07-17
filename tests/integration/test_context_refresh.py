from __future__ import annotations

from pm_agent.application.action_service import ActionService
from pm_agent.application.context_service import ContextService
from pm_agent.domain.enums import ActionStatus
from pm_agent.infrastructure.hosts import StandaloneHostBridge
from pm_agent.infrastructure.repository import LocalRepositoryAnalyzer
from pm_agent.infrastructure.sqlite import SQLiteStore


def test_approved_standalone_refresh_builds_and_stores_snapshot(tmp_path):
    (tmp_path / "README.md").write_text("# Example")
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    actions = ActionService(store, StandaloneHostBridge())
    context = ContextService(store, LocalRepositoryAnalyzer(), actions)
    proposal = context.propose_refresh(project.id, session.id, str(tmp_path))

    receipt = actions.approve(proposal.id)
    assert not receipt.dispatched
    snapshot = context.complete_local_refresh(proposal.id, "unknown", str(tmp_path))

    assert snapshot.created_by_action_id == proposal.id
    assert store.latest_snapshot(project.id).id == snapshot.id
    assert store.get_action(proposal.id).status.value == "succeeded"


def test_local_refresh_cannot_escape_active_project(tmp_path):
    managed = tmp_path / "managed"
    outside = tmp_path / "outside"
    managed.mkdir()
    outside.mkdir()
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(managed)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    actions = ActionService(store, StandaloneHostBridge())
    context = ContextService(store, LocalRepositoryAnalyzer(), actions)
    proposal = context.propose_refresh(project.id, session.id, str(outside))
    actions.approve(proposal.id)

    try:
        context.complete_local_refresh(proposal.id, "unknown", str(managed))
    except ValueError as exc:
        assert "active project root" in str(exc)
    else:
        raise AssertionError("Expected refresh outside the project root to be rejected.")


def test_approved_inspect_repository_does_not_emit_pending_dispatch(tmp_path):
    (tmp_path / "README.md").write_text("# Example")
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    actions = ActionService(store, StandaloneHostBridge())
    context = ContextService(store, LocalRepositoryAnalyzer(), actions)
    proposal = context.propose_refresh(project.id, session.id, str(tmp_path))

    receipt = actions.approve(proposal.id)
    assert not receipt.dispatched
    assert "Standalone mode" in receipt.message

    snapshot = context.complete_local_refresh(proposal.id, "unknown", str(tmp_path))
    updated = store.get_action(proposal.id)

    assert updated.status is ActionStatus.SUCCEEDED
    assert updated.status.value == "succeeded"
    assert store.latest_snapshot(project.id) is not None
    assert store.latest_snapshot(project.id).id == snapshot.id


def test_context_loading_reads_text_files(tmp_path):
    context_dir = tmp_path / "context"
    context_dir.mkdir()
    (context_dir / "requirements.md").write_text("# Requirements\n- Feature X\n- Feature Y")
    (context_dir / "arch.json").write_text('{"layers": ["api", "domain"]}')
    (context_dir / "compose.yaml").write_text("version: '3'\nservices:\n  app: .")
    (context_dir / "notes.txt").write_text("Some important context notes.")
    (context_dir / "data.csv").write_text("col1,col2\nv1,v2")
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    actions = ActionService(store, StandaloneHostBridge())
    context = ContextService(store, LocalRepositoryAnalyzer(), actions)

    loaded = context.load_context_files(project.id, session.id, base_path=str(tmp_path))

    assert len(loaded) == 5
    assert "requirements.md" in loaded
    assert "arch.json" in loaded
    assert "compose.yaml" in loaded
    assert "notes.txt" in loaded
    assert "data.csv" in loaded


def test_context_loading_skips_unsupported_extensions(tmp_path):
    context_dir = tmp_path / "context"
    context_dir.mkdir()
    (context_dir / "readme.md").write_text("# Ok")
    (context_dir / "script.py").write_text("print('hello')")
    (context_dir / "binary.bin").write_bytes(b"\x00\x01\x02")
    (context_dir / "image.png").write_bytes(b"fake png")
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    actions = ActionService(store, StandaloneHostBridge())
    context = ContextService(store, LocalRepositoryAnalyzer(), actions)

    loaded = context.load_context_files(project.id, session.id, base_path=str(tmp_path))

    assert loaded == ["readme.md"]


def test_context_loading_skips_oversized_files(tmp_path):
    context_dir = tmp_path / "context"
    context_dir.mkdir()
    (context_dir / "small.md").write_text("# Small")
    (context_dir / "large.md").write_text("x" * 200_000)
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    actions = ActionService(store, StandaloneHostBridge())
    context = ContextService(store, LocalRepositoryAnalyzer(), actions)

    loaded = context.load_context_files(project.id, session.id, base_path=str(tmp_path))

    assert loaded == ["small.md"]


def test_context_loading_missing_directory_returns_empty(tmp_path):
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    actions = ActionService(store, StandaloneHostBridge())
    context = ContextService(store, LocalRepositoryAnalyzer(), actions)

    loaded = context.load_context_files(project.id, session.id, base_path=str(tmp_path))

    assert loaded == []


def test_context_loading_empty_directory_returns_empty(tmp_path):
    context_dir = tmp_path / "context"
    context_dir.mkdir()
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    actions = ActionService(store, StandaloneHostBridge())
    context = ContextService(store, LocalRepositoryAnalyzer(), actions)

    loaded = context.load_context_files(project.id, session.id, base_path=str(tmp_path))

    assert loaded == []


def test_context_loading_stores_retrievable_notes(tmp_path):
    context_dir = tmp_path / "context"
    context_dir.mkdir()
    (context_dir / "plan.md").write_text("# Plan\n- Step 1\n- Step 2")
    store = SQLiteStore(tmp_path / "state.db")
    project = store.resolve_project(tmp_path)
    session = store.start_session(project.id, "s", "m", "p", "unknown")
    actions = ActionService(store, StandaloneHostBridge())
    context = ContextService(store, LocalRepositoryAnalyzer(), actions)

    context.load_context_files(project.id, session.id, base_path=str(tmp_path))

    from pm_agent.ports.memory import RetrievalQuery
    packet = store.retrieve(
        RetrievalQuery(
            session_id=session.id,
            project_id=project.id,
            text="plan",
            item_limit=10,
            character_budget=5000,
            history_limit=0,
        )
    )
    titles = [item.title for item in packet.items if item.kind.value == "repo_note"]
    assert any("plan.md" in t for t in titles), f"No context notes found: {titles}"
