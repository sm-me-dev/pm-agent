from __future__ import annotations

import os
import sys
import warnings
from dataclasses import dataclass, field
from pathlib import Path


def _deprecated_project_root() -> Path:
    warnings.warn(
        "project_root() is deprecated. Use ProjectLocal.root or an explicit path instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return Path(__file__).resolve().parents[2]


def project_root() -> Path:
    return _deprecated_project_root()


def load_env_file(path: str | Path | None = None, project_root: Path | None = None) -> Path | None:
    if path:
        env_path = Path(path).expanduser()
    elif project_root:
        env_path = project_root / ".env"
    else:
        env_path = Path.cwd() / ".env"
    if not env_path.is_file():
        return None
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        key, separator, raw_value = line.partition("=")
        key = key.strip()
        if not separator or not key or not key.replace("_", "").isalnum() or key[0].isdigit():
            continue
        value = raw_value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ.setdefault(key, value)
    return env_path


def _env(*names: str, default: str = "") -> str:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return default


def default_db_path() -> Path:
    configured = _env("PM_AGENT_DB_PATH")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".local" / "share" / "stateful-pm-agent" / "state.db"


def global_config_dir() -> Path:
    plat = sys.platform
    if plat == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif plat == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "pm-agent"


def global_config_path() -> Path:
    return global_config_dir() / "config.toml"


@dataclass(frozen=True)
class AgentConfig:
    repo_path: str
    context_dir: str | None = None
    db_path: Path = field(default_factory=default_db_path)
    model: str = field(
        default_factory=lambda: _env("PM_AGENT_MODEL", "OPENAI_MODEL", default="glm-5.2")
    )
    base_url: str = field(
        default_factory=lambda: _env(
            "PM_AGENT_BASE_URL", "OPENAI_BASE_URL", default="http://localhost:11434/v1"
        )
    )
    api_key: str = field(
        default_factory=lambda: _env("PM_AGENT_API_KEY", "OPENAI_API_KEY", default="")
    )
    history_limit: int = field(
        default_factory=lambda: int(_env("PM_AGENT_HISTORY_LIMIT", default="75"))
    )
    context_character_budget: int = field(
        default_factory=lambda: int(_env("PM_AGENT_CONTEXT_TOKEN_BUDGET", default="6000")) * 3
    )
    structured_output_mode: str = field(
        default_factory=lambda: _env("PM_AGENT_STRUCTURED_OUTPUT_MODE", default="auto")
    )
    always_approve: bool = field(
        default_factory=lambda: any(
            _env(var).lower() in {"true", "1", "yes"}
            for var in (
                "PM_AGENT_ALWAYS_APPROVE",
                "PM_AGENT_ALWAYS_ACCEPT",
                "PM_AGENT_ACCEPT_ALL",
                "PM_AGENT_APPROVE_ALL",
                "PM_AGENT_AUTO_APPROVE",
                "PM_AGENT_ASSUME_YES",
            )
        )
    )
    error_log_path: str | None = None

    @property
    def provider_name(self) -> str:
        if "11434" in self.base_url:
            return "ollama"
        if "openai.com" in self.base_url:
            return "openai"
        return "openai-compatible"
