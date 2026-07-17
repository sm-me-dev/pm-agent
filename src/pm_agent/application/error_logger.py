from __future__ import annotations

import json
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_DEFAULT_LOG_PATH = Path.cwd() / "logs" / "pm-agent-errors.log"


class ActionErrorLogEntry:
    def __init__(
        self,
        *,
        session_id: str = "",
        run_id: str = "",
        action_id: str = "",
        action_type: str = "",
        action_status_before: str = "",
        action_status_after: str = "",
        approval_mode: str = "",
        executor: str = "",
        target_repository: str = "",
        payload: dict[str, Any] | None = None,
        error: str = "",
        exception_type: str = "",
        stack_trace: str = "",
        api_endpoint: str = "",
        exit_code: int | None = None,
        retryable: bool = False,
        user_message: str = "",
        category: str = "",
    ):
        self.timestamp = datetime.now(UTC).isoformat()
        self.session_id = session_id
        self.run_id = run_id
        self.action_id = action_id
        self.action_type = action_type
        self.action_status_before = action_status_before
        self.action_status_after = action_status_after
        self.approval_mode = approval_mode
        self.executor = executor
        self.target_repository = _sanitize_repo(target_repository)
        self.payload = _sanitize_payload(payload or {})
        self.error = _redact_tokens(error)
        self.exception_type = exception_type
        self.stack_trace = _redact_tokens(stack_trace)
        self.api_endpoint = api_endpoint
        self.exit_code = exit_code
        self.retryable = retryable
        self.user_message = user_message
        self.category = category

    def to_dict(self) -> dict[str, Any]:
        result = {}
        for k, v in self.__dict__.items():
            if k == "exit_code" and v is not None:
                result[k] = v
            elif k == "retryable":
                result[k] = v
            elif v:
                result[k] = v
        return result

    def to_json_line(self) -> str:
        return json.dumps(self.to_dict(), default=str, ensure_ascii=False)


class ErrorLogger:
    def __init__(self, path: str | Path | None = None):
        self._path = Path(path) if path else _DEFAULT_LOG_PATH

    @property
    def path(self) -> Path:
        return self._path

    def log(self, entry: ActionErrorLogEntry) -> None:
        self._write(entry)

    def log_failure(
        self,
        *,
        session_id: str = "",
        action_id: str = "",
        action_type: str = "",
        action_status_before: str = "",
        action_status_after: str = "",
        approval_mode: str = "",
        executor: str = "",
        target_repository: str = "",
        payload: dict[str, Any] | None = None,
        error: str = "",
        exception: BaseException | None = None,
        api_endpoint: str = "",
        exit_code: int | None = None,
        retryable: bool = False,
        user_message: str = "",
        category: str = "action_failure",
    ) -> None:
        entry = ActionErrorLogEntry(
            session_id=session_id,
            action_id=action_id,
            action_type=action_type,
            action_status_before=action_status_before,
            action_status_after=action_status_after,
            approval_mode=approval_mode,
            executor=executor,
            target_repository=target_repository,
            payload=payload,
            error=error,
            exception_type=type(exception).__name__ if exception else "",
            stack_trace="".join(
                traceback.format_exception(type(exception), exception, exception.__traceback__)
            )
            if exception
            else "",
            api_endpoint=api_endpoint,
            exit_code=exit_code,
            retryable=retryable,
            user_message=user_message,
            category=category,
        )
        self._write(entry)

    def _write(self, entry: ActionErrorLogEntry) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as f:
                f.write(entry.to_json_line() + "\n")
        except OSError:
            pass


def _sanitize_repo(repo: str) -> str:
    if not repo or "/" not in repo:
        return repo
    return repo


def _sanitize_payload(payload: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(payload)
    for key in ("token", "api_key", "apiKey", "secret", "password", "auth", "authorization"):
        sanitized.pop(key, None)
    return sanitized


_TOKEN_PATTERNS = (
    ("ghp_", "ghp_***"),
    ("gho_", "gho_***"),
    ("github_pat_", "github_pat_***"),
    ("token ", "token ***"),
    ("Bearer ", "Bearer ***"),
    ("Authorization: ", "Authorization: ***"),
)


def _redact_tokens(text: str) -> str:
    for pattern, replacement in _TOKEN_PATTERNS:
        text = text.replace(pattern, replacement)
    return text
