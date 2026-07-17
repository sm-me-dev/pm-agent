from __future__ import annotations

import os

from pm_agent.config import AgentConfig, load_env_file
from pm_agent.presentation.cli import parse_args


def test_load_env_file_populates_config(tmp_path, monkeypatch):
    for key in (
        "PM_AGENT_MODEL",
        "PM_AGENT_BASE_URL",
        "PM_AGENT_API_KEY",
        "PM_AGENT_BRANCH",
    ):
        monkeypatch.delenv(key, raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text(
        "PM_AGENT_MODEL=custom-model\n"
        'PM_AGENT_BASE_URL="http://provider.test/v1"\n'
        "export PM_AGENT_API_KEY='test-key'\n"
        "PM_AGENT_BRANCH=develop\n"
    )

    assert load_env_file(env_file) == env_file
    config = AgentConfig(repo_path=str(tmp_path))
    args = parse_args([])

    assert config.model == "custom-model"
    assert config.base_url == "http://provider.test/v1"
    assert config.api_key == "test-key"
    assert args.branch == "develop"


def test_real_environment_takes_precedence_over_env_file(tmp_path, monkeypatch):
    monkeypatch.setenv("PM_AGENT_MODEL", "process-model")
    env_file = tmp_path / ".env"
    env_file.write_text("PM_AGENT_MODEL=file-model\n")

    load_env_file(env_file)

    assert os.environ["PM_AGENT_MODEL"] == "process-model"
    assert AgentConfig(repo_path=str(tmp_path)).model == "process-model"


def test_missing_env_file_is_not_an_error(tmp_path):
    assert load_env_file(tmp_path / "missing.env") is None
