from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from pm_agent.project import (
    ProjectLocal,
    discover,
    discover_or_override,
    init_project,
    legacy_global_db_path,
    load_global_config,
    load_project_config,
    load_memory,
    migrate_project_data,
    resolve_db_path,
    global_config_dir,
    global_config_path,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def initialized_project(tmp_path):
    pm = tmp_path / ".pm-agent"
    pm.mkdir()
    (pm / "project.toml").write_text("[project]\nname = 'test-project'\n", encoding="utf-8")
    (pm / "memory.md").write_text("# Project Memory\n\n- 2024-01-01: started\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def git_repo(tmp_path):
    (tmp_path / ".git").mkdir()
    return tmp_path


@pytest.fixture
def bare_dir(tmp_path):
    return tmp_path


@pytest.fixture
def legacy_db(tmp_path):
    """Create a legacy DB with one project, one session, a couple messages."""
    from pm_agent.infrastructure.sqlite import SQLiteStore
    from pm_agent.domain.enums import DecisionStatus
    path = tmp_path / "state.db"
    store = SQLiteStore(path)
    project = store.resolve_project("/tmp/test-project", "main")
    session = store.start_session(project.id, "test-session", "gpt4", "openai", "main")
    store.add_message(session.id, "user", "hello")
    store.add_message(session.id, "assistant", "hi")
    decision = store.add_decision(
        project.id, session.id, "arch", "use-sqlite",
        "Use SQLite for persistence", "Best for single-user"
    )
    store.set_decision_status(decision.id, project.id, DecisionStatus.ACCEPTED)
    return path, project, session


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------

class TestDiscover:
    def test_finds_dot_pm_agent(self, initialized_project):
        proj = discover(initialized_project)
        assert proj is not None
        assert proj.root == initialized_project.resolve()
        assert proj.is_initialized
        assert proj.project_config["project"]["name"] == "test-project"
        assert proj.memory == "# Project Memory\n\n- 2024-01-01: started\n"

    def test_finds_dot_pm_agent_in_parent(self, initialized_project):
        sub = initialized_project / "sub" / "dir"
        sub.mkdir(parents=True)
        proj = discover(sub)
        assert proj is not None
        assert proj.root == initialized_project.resolve()
        assert proj.is_initialized

    def test_finds_git_only(self, git_repo):
        proj = discover(git_repo)
        assert proj is not None
        assert proj.root == git_repo.resolve()
        assert not proj.is_initialized
        assert proj.pm_agent_dir is None

    def test_dot_pm_agent_beats_git(self, initialized_project):
        (initialized_project / ".git").mkdir()
        proj = discover(initialized_project)
        assert proj is not None
        assert proj.is_initialized

    def test_nested_repo(self, tmp_path):
        parent = tmp_path / "parent"
        parent.mkdir()
        (parent / ".git").mkdir()
        child = parent / "sub" / "nested"
        child.mkdir(parents=True)
        (child / ".git").mkdir()
        proj = discover(child)
        assert proj is not None
        assert proj.root == child.resolve()
        assert not proj.is_initialized

    def test_nested_dot_pm_agent(self, tmp_path):
        parent = tmp_path / "parent"
        parent.mkdir()
        (parent / ".pm-agent").mkdir()
        child = parent / "sub"
        child.mkdir()
        (child / ".pm-agent").mkdir()
        proj = discover(child)
        assert proj is not None
        assert proj.root == child.resolve()
        assert proj.is_initialized

    def test_worktree(self, tmp_path):
        (tmp_path / ".git").write_text("gitdir: /some/where/else\n", encoding="utf-8")
        proj = discover(tmp_path)
        assert proj is not None
        assert proj.root == tmp_path.resolve()

    def test_symlink(self, initialized_project, tmp_path):
        link = tmp_path / "linked"
        link.symlink_to(initialized_project, target_is_directory=True)
        proj = discover(link)
        assert proj is not None
        assert proj.is_initialized
        assert proj.root == initialized_project.resolve()

    def test_nothing_found(self, bare_dir):
        proj = discover(bare_dir)
        assert proj is None

    def test_at_root(self):
        proj = discover(Path("/"))
        assert proj is None

    def test_subdir_of_parent(self, initialized_project):
        sub = initialized_project / "src" / "components"
        sub.mkdir(parents=True)
        proj = discover(sub)
        assert proj is not None
        assert proj.root == initialized_project.resolve()


class TestDiscoverOrOverride:
    def test_project_root_exact(self, initialized_project):
        proj = discover_or_override(str(initialized_project), None, Path.cwd())
        assert proj is not None
        assert proj.root == initialized_project.resolve()

    def test_project_root_does_not_exist(self):
        proj = discover_or_override("/nonexistent/path", None, Path.cwd())
        assert proj is None

    def test_project_root_skips_discovery(self, tmp_path):
        parent = tmp_path / "parent"
        parent.mkdir()
        (parent / ".pm-agent").mkdir()
        child = parent / "child"
        child.mkdir()
        proj = discover_or_override(str(child), None, Path.cwd())
        assert proj is not None
        assert proj.root == child.resolve()
        assert not proj.is_initialized

    def test_repo_flag(self, initialized_project):
        proj = discover_or_override(None, str(initialized_project), Path("/"))
        assert proj is not None
        assert proj.root == initialized_project.resolve()


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

class TestInit:
    def test_creates_structure(self, bare_dir):
        proj = init_project(bare_dir)
        assert proj.is_initialized
        assert (bare_dir / ".pm-agent").is_dir()
        assert (bare_dir / ".pm-agent" / "project.toml").is_file()
        assert (bare_dir / ".pm-agent" / "memory.md").is_file()
        assert not (bare_dir / ".pm-agent" / "state.db").exists()

    def test_project_toml_template(self, bare_dir):
        init_project(bare_dir)
        content = (bare_dir / ".pm-agent" / "project.toml").read_text(encoding="utf-8")
        assert "[project]" in content
        assert "[constraints]" in content

    def test_memory_md_header(self, bare_dir):
        init_project(bare_dir)
        content = (bare_dir / ".pm-agent" / "memory.md").read_text(encoding="utf-8")
        assert "# Project Memory" in content

    def test_state_db_not_created(self, bare_dir):
        init_project(bare_dir)
        assert not (bare_dir / ".pm-agent" / "state.db").exists()

    def test_idempotent_no_force(self, bare_dir):
        init_project(bare_dir)
        with pytest.raises(FileExistsError):
            init_project(bare_dir, force=False)

    def test_force_overwrites_config(self, bare_dir):
        proj = init_project(bare_dir)
        (proj.pm_agent_dir / "project.toml").write_text("original", encoding="utf-8")
        init_project(bare_dir, force=True)
        content = (bare_dir / ".pm-agent" / "project.toml").read_text(encoding="utf-8")
        assert "[project]" in content

    def test_force_does_not_touch_state_db(self, bare_dir):
        proj = init_project(bare_dir)
        state_db = proj.pm_agent_dir / "state.db"
        state_db.write_text("existing data", encoding="utf-8")
        init_project(bare_dir, force=True)
        assert state_db.read_text(encoding="utf-8") == "existing data"

    def test_into_non_git_dir(self, bare_dir):
        proj = init_project(bare_dir)
        assert proj.is_initialized

    def test_project_config_loaded(self, bare_dir):
        proj = init_project(bare_dir)
        assert isinstance(proj.project_config, dict)


# ---------------------------------------------------------------------------
# ProjectLocal properties
# ---------------------------------------------------------------------------

class TestProjectLocal:
    def test_default_db_path_initialized(self, initialized_project):
        proj = discover(initialized_project)
        assert proj.default_db_path == proj.pm_agent_dir / "state.db"

    def test_default_db_path_git_only(self, git_repo):
        proj = discover(git_repo)
        assert proj.default_db_path == legacy_global_db_path()

    def test_log_dir_initialized(self, initialized_project):
        proj = discover(initialized_project)
        assert proj.log_dir == proj.pm_agent_dir / "logs"

    def test_log_dir_git_only(self, git_repo):
        proj = discover(git_repo)
        assert proj.log_dir == proj.root / "logs"

    def test_history_path_initialized(self, initialized_project):
        proj = discover(initialized_project)
        assert proj.history_path == proj.pm_agent_dir / "prompt-history"

    def test_context_dir_initialized(self, initialized_project):
        proj = discover(initialized_project)
        assert proj.context_dir == proj.root / "context"

    def test_context_dir_with_subdir(self, initialized_project):
        (initialized_project / ".pm-agent" / "context").mkdir()
        proj = discover(initialized_project)
        assert proj.context_dir == proj.pm_agent_dir / "context"


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

class TestConfig:
    def test_load_project_config(self, initialized_project):
        cfg = load_project_config(initialized_project / ".pm-agent")
        assert cfg["project"]["name"] == "test-project"

    def test_load_project_config_missing(self, bare_dir):
        cfg = load_project_config(bare_dir / ".pm-agent")
        assert cfg == {}

    def test_load_global_config_missing(self):
        cfg = load_global_config()
        assert cfg == {}

    def test_load_memory(self, initialized_project):
        mem = load_memory(initialized_project / ".pm-agent")
        assert "started" in mem

    def test_load_memory_missing(self, bare_dir):
        mem = load_memory(bare_dir / ".pm-agent")
        assert mem == ""

    def test_global_config_dir(self):
        path = global_config_dir()
        assert "pm-agent" in str(path)
        assert path.name == "pm-agent"

    def test_global_config_path(self):
        path = global_config_path()
        assert path.name == "config.toml"


# ---------------------------------------------------------------------------
# DB path resolution
# ---------------------------------------------------------------------------

class TestResolveDbPath:
    def test_cli_flag(self, initialized_project):
        proj = discover(initialized_project)
        path = resolve_db_path(proj, "/custom/path.db", None)
        assert str(path) == "/custom/path.db"

    def test_env_var(self, initialized_project):
        proj = discover(initialized_project)
        path = resolve_db_path(proj, None, "/env/path.db")
        assert str(path) == "/env/path.db"

    def test_project_toml_path(self, tmp_path):
        pm = tmp_path / ".pm-agent"
        pm.mkdir()
        (pm / "project.toml").write_text('[paths]\ndb_path = "custom.db"\n', encoding="utf-8")
        proj = discover(tmp_path)
        assert proj.is_initialized
        path = resolve_db_path(proj, None, None)
        assert str(path) == str((tmp_path / "custom.db").resolve())

    def test_default_project_local(self, initialized_project):
        proj = discover(initialized_project)
        path = resolve_db_path(proj, None, None)
        assert str(path) == str((initialized_project / ".pm-agent" / "state.db").resolve())

    def test_default_legacy(self, git_repo):
        proj = discover(git_repo)
        path = resolve_db_path(proj, None, None)
        assert "pm-agent" in str(path)
        assert "state.db" in str(path)


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

class TestMigrate:
    def test_clean_migrate(self, legacy_db, tmp_path):
        source_path, src_project, src_session = legacy_db
        dest_path = tmp_path / "dest" / "state.db"
        project_root = Path("/tmp/test-project")

        result = migrate_project_data(source_path, dest_path, project_root, dry_run=False)

        assert result.get("projects", 0) >= 1
        assert result.get("sessions", 0) >= 1
        assert result.get("messages", 0) >= 2
        assert result.get("decisions", 0) >= 1

        import sqlite3
        conn = sqlite3.connect(str(dest_path))
        conn.row_factory = sqlite3.Row
        try:
            projects = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
            assert projects >= 1
            messages = conn.execute("SELECT COUNT(*) FROM messages_v2").fetchone()[0]
            assert messages >= 2
        finally:
            conn.close()

    def test_migrate_idempotent(self, legacy_db, tmp_path):
        source_path, _, _ = legacy_db
        dest_path = tmp_path / "dest" / "state.db"
        project_root = Path("/tmp/test-project")

        result1 = migrate_project_data(source_path, dest_path, project_root, dry_run=False)
        result2 = migrate_project_data(source_path, dest_path, project_root, dry_run=False)

        assert result2.get("already_existed", 0) >= (
            result1.get("projects", 0)
            + result1.get("sessions", 0)
            + result1.get("messages", 0)
            + result1.get("decisions", 0)
        )

    def test_migrate_dry_run(self, legacy_db, tmp_path):
        source_path, _, _ = legacy_db
        dest_path = tmp_path / "dest" / "state.db"
        project_root = Path("/tmp/test-project")

        result = migrate_project_data(source_path, dest_path, project_root, dry_run=True)

        assert not dest_path.exists()
        assert result.get("messages", 0) >= 2

    def test_migrate_no_matching_project(self, legacy_db, tmp_path):
        source_path, _, _ = legacy_db
        dest_path = tmp_path / "dest" / "state.db"
        project_root = Path("/tmp/other-project")

        with pytest.raises(ValueError, match="No legacy data"):
            migrate_project_data(source_path, dest_path, project_root, dry_run=False)

    def test_migrate_missing_source(self, tmp_path):
        source_path = tmp_path / "nonexistent.db"
        dest_path = tmp_path / "dest" / "state.db"
        project_root = Path("/tmp/test-project")

        with pytest.raises(FileNotFoundError, match="No legacy database"):
            migrate_project_data(source_path, dest_path, project_root, dry_run=False)

    def test_migrate_only_current_project(self, tmp_path):
        from pm_agent.infrastructure.sqlite import SQLiteStore
        source_path = tmp_path / "multi.db"
        store = SQLiteStore(source_path)
        p1 = store.resolve_project("/tmp/project-a")
        p2 = store.resolve_project("/tmp/project-b")
        s1 = store.start_session(p1.id, "s1", "gpt4", "openai", "main")
        s2 = store.start_session(p2.id, "s2", "gpt4", "openai", "main")
        store.add_message(s1.id, "user", "msg from A")
        store.add_message(s2.id, "user", "msg from B")

        dest_path = tmp_path / "dest" / "state.db"
        project_root = Path("/tmp/project-a")

        result = migrate_project_data(source_path, dest_path, project_root, dry_run=False)

        import sqlite3
        conn = sqlite3.connect(str(dest_path))
        conn.row_factory = sqlite3.Row
        try:
            messages = conn.execute("SELECT * FROM messages_v2").fetchall()
            assert len(messages) == 1
            assert "A" in messages[0]["content"]
        finally:
            conn.close()
