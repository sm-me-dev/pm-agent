from __future__ import annotations

from pm_agent.domain.repository_refs import extract_github_references


def test_extracts_at_reference_and_github_urls():
    references = extract_github_references(
        "Plan @sm-me-dev/unified-workspace-engine and "
        "https://github.com/openai/openai-python.git."
    )
    assert [reference.slug for reference in references] == [
        "sm-me-dev/unified-workspace-engine",
        "openai/openai-python",
    ]


def test_does_not_treat_source_paths_as_github_repositories():
    assert extract_github_references("Inspect src/pm_agent and tests/unit.") == []
