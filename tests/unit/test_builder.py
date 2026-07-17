from __future__ import annotations

from pm_agent.prompts.builder import PromptBuilder


def test_load_context_single_md(tmp_path):
    context_dir = tmp_path / "my_context"
    context_dir.mkdir()
    (context_dir / "plan.md").write_text("# Project Plan\n- Step 1\n- Step 2")
    result = PromptBuilder._load_context(str(context_dir))
    assert "plan.md" in result
    assert "Project Plan" in result


def test_load_context_multiple_types(tmp_path):
    context_dir = tmp_path / "ctx"
    context_dir.mkdir()
    (context_dir / "reqs.md").write_text("# Requirements")
    (context_dir / "notes.txt").write_text("Important notes")
    (context_dir / "config.json").write_text('{"key": "value"}')
    (context_dir / "compose.yaml").write_text("version: '3'")
    (context_dir / "data.csv").write_text("col1,col2\na,b")
    result = PromptBuilder._load_context(str(context_dir))
    assert "reqs.md" in result
    assert "notes.txt" in result
    assert "config.json" in result
    assert "compose.yaml" in result
    assert "data.csv" in result


def test_load_context_skips_unsupported(tmp_path):
    context_dir = tmp_path / "ctx"
    context_dir.mkdir()
    (context_dir / "readme.md").write_text("# Ok")
    (context_dir / "script.py").write_text("x = 1")
    (context_dir / "data.bin").write_bytes(b"\x00\x01")
    result = PromptBuilder._load_context(str(context_dir))
    assert "readme.md" in result
    assert "script.py" not in result
    assert "data.bin" not in result


def test_load_context_skips_oversized(tmp_path):
    context_dir = tmp_path / "ctx"
    context_dir.mkdir()
    (context_dir / "small.md").write_text("# Small")
    (context_dir / "large.md").write_text("x" * 200_000)
    result = PromptBuilder._load_context(str(context_dir))
    assert "small.md" in result
    assert "large.md" not in result


def test_load_context_none_or_missing(tmp_path):
    assert PromptBuilder._load_context(None) == ""
    assert PromptBuilder._load_context("") == ""
    assert PromptBuilder._load_context(str(tmp_path / "nonexistent")) == ""


def test_load_context_empty_dir(tmp_path):
    context_dir = tmp_path / "empty"
    context_dir.mkdir()
    assert PromptBuilder._load_context(str(context_dir)) == ""


def test_build_includes_context_section(tmp_path):
    context_dir = tmp_path / "ctx"
    context_dir.mkdir()
    (context_dir / "plan.md").write_text("# UniquePlanningPhrase")
    builder = PromptBuilder(str(context_dir))
    from pm_agent.domain.models import ContextPacket, Project
    project = Project(
        id="p1", name="test", canonical_path=str(tmp_path),
        repo_fingerprint="abc", default_branch="main",
        created_at="now", updated_at="now",
    )
    packet = ContextPacket(items=[], recent_messages=[], repository_snapshot=None)
    messages = builder.build(project, "main", packet, "hello")
    system_content = messages[0]["content"]
    assert "## Project Specifications" in system_content
    assert "plan.md" in system_content
    assert "UniquePlanningPhrase" in system_content


def test_build_omits_context_section_when_empty(monkeypatch, tmp_path):
    builder = PromptBuilder(None)
    from pm_agent.domain.models import ContextPacket, Project
    project = Project(
        id="p1", name="test", canonical_path=str(tmp_path),
        repo_fingerprint="abc", default_branch="main",
        created_at="now", updated_at="now",
    )
    packet = ContextPacket(items=[], recent_messages=[], repository_snapshot=None)
    messages = builder.build(project, "main", packet, "hello")
    system_content = messages[0]["content"]
    assert "## Project Specifications" not in system_content


def test_build_injects_project_memory_section(tmp_path):
    memory = "# Project Memory\nThis is the pm-agent project. Stack: Python."
    builder = PromptBuilder(None, memory=memory)
    from pm_agent.domain.models import ContextPacket, Project
    project = Project(
        id="p1", name="pm-agent", canonical_path=str(tmp_path),
        repo_fingerprint="abc", default_branch="main",
        created_at="now", updated_at="now",
    )
    packet = ContextPacket(items=[], recent_messages=[], repository_snapshot=None)
    messages = builder.build(project, "main", packet, "hello")
    system_content = messages[0]["content"]
    assert "## Project Memory" in system_content
    assert "This is the pm-agent project" in system_content


def test_build_injects_configured_project_name_and_remote(tmp_path):
    builder = PromptBuilder(None, memory="", project_meta={"name": "pm-agent", "remote": "git@x:o/r.git"})
    from pm_agent.domain.models import ContextPacket, Project
    project = Project(
        id="p1", name="folder-name", canonical_path=str(tmp_path),
        repo_fingerprint="abc", default_branch="main",
        created_at="now", updated_at="now",
    )
    packet = ContextPacket(items=[], recent_messages=[], repository_snapshot=None)
    messages = builder.build(project, "main", packet, "hello")
    system_content = messages[0]["content"]
    assert "Configured name: pm-agent" in system_content
    assert "Remote: git@x:o/r.git" in system_content

