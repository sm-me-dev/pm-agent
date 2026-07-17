from __future__ import annotations

from pm_agent.infrastructure.repository import LocalRepositoryAnalyzer
from pm_agent.ports.repository_context import SnapshotRequest


def test_analyzer_is_read_only_and_excludes_secrets(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\nname="demo"\n')
    (tmp_path / "README.md").write_text("# Demo")
    (tmp_path / ".env").write_text("API_KEY=secret")
    (tmp_path / "main.py").write_text("print('hi')")
    before = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    snapshot = LocalRepositoryAnalyzer().build_snapshot(
        SnapshotRequest("p1", str(tmp_path), "main")
    )
    after = sorted(path.relative_to(tmp_path) for path in tmp_path.rglob("*"))
    assert before == after
    assert "Python" in snapshot.summary["languages"]
    assert ".env" not in snapshot.summary["tree"]
    assert "secret" not in str(snapshot.summary)


def test_analyzer_detects_branch_from_git_head(tmp_path):
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text("ref: refs/heads/feature/planning\n")
    snapshot = LocalRepositoryAnalyzer().build_snapshot(
        SnapshotRequest("p1", str(tmp_path), "unknown")
    )
    assert snapshot.branch == "feature/planning"
    assert snapshot.summary["branch"] == "feature/planning"
