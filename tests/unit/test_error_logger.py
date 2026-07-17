from __future__ import annotations

import json
import re
from pathlib import Path

from pm_agent.application.error_logger import (
    ActionErrorLogEntry,
    ErrorLogger,
    _redact_tokens,
    _sanitize_payload,
)


def test_log_entry_to_json_line_contains_timestamp(tmp_path):
    entry = ActionErrorLogEntry(action_id="abc123", error="test error")
    line = entry.to_json_line()
    parsed = json.loads(line)
    assert "timestamp" in parsed
    assert parsed["action_id"] == "abc123"
    assert parsed["error"] == "test error"


def test_log_entry_omits_empty_fields(tmp_path):
    entry = ActionErrorLogEntry(action_id="x")
    parsed = entry.to_dict()
    assert "action_id" in parsed
    assert "timestamp" in parsed
    assert "session_id" not in parsed
    assert "error" not in parsed


def test_error_logger_writes_file(tmp_path):
    log_path = tmp_path / "errors.jsonl"
    logger = ErrorLogger(log_path)
    entry = ActionErrorLogEntry(action_id="abc", error="something broke")
    logger.log(entry)
    lines = log_path.read_text().strip().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["action_id"] == "abc"
    assert parsed["error"] == "something broke"


def test_error_logger_appends(tmp_path):
    log_path = tmp_path / "errors.jsonl"
    logger = ErrorLogger(log_path)
    logger.log(ActionErrorLogEntry(action_id="a1"))
    logger.log(ActionErrorLogEntry(action_id="a2"))
    lines = log_path.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["action_id"] == "a1"
    assert json.loads(lines[1])["action_id"] == "a2"


def test_log_failure_populates_all_fields(tmp_path):
    log_path = tmp_path / "errors.jsonl"
    logger = ErrorLogger(log_path)
    logger.log_failure(
        session_id="s1",
        action_id="a1",
        action_type="github",
        action_status_before="approved",
        action_status_after="failed",
        approval_mode="always_approve",
        executor="IntegrationHostBridge",
        target_repository="owner/repo",
        payload={"repository": "owner/repo", "title": "test"},
        error="API returned 404",
        api_endpoint="repos/owner/repo/milestones",
        exit_code=1,
        retryable=False,
        user_message="Failed to create milestone.",
        category="action_failure",
    )
    lines = log_path.read_text().strip().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["session_id"] == "s1"
    assert parsed["action_id"] == "a1"
    assert parsed["action_type"] == "github"
    assert parsed["action_status_before"] == "approved"
    assert parsed["action_status_after"] == "failed"
    assert parsed["approval_mode"] == "always_approve"
    assert parsed["executor"] == "IntegrationHostBridge"
    assert parsed["target_repository"] == "owner/repo"
    assert parsed["error"] == "API returned 404"
    assert parsed["exit_code"] == 1
    assert parsed["retryable"] is False
    assert parsed["category"] == "action_failure"


def test_log_failure_with_exception_stores_traceback(tmp_path):
    log_path = tmp_path / "errors.jsonl"
    logger = ErrorLogger(log_path)
    try:
        raise ValueError("test exception")
    except ValueError as exc:
        logger.log_failure(action_id="a1", error="wrapped", exception=exc)

    lines = log_path.read_text().strip().splitlines()
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert parsed["exception_type"] == "ValueError"
    assert "test exception" in parsed["stack_trace"]
    assert "test_error_logger" in parsed["stack_trace"]


def test_sanitize_payload_removes_secrets():
    payload = {
        "repository": "owner/repo",
        "title": "test",
        "token": "ghp_abc123",
        "api_key": "sk-123",
        "secret": "my-secret",
        "password": "hunter2",
    }
    sanitized = _sanitize_payload(payload)
    assert sanitized["repository"] == "owner/repo"
    assert sanitized["title"] == "test"
    assert "token" not in sanitized
    assert "api_key" not in sanitized
    assert "secret" not in sanitized
    assert "password" not in sanitized


def test_redact_tokens():
    text = "Authorization: Bearer ghp_abc123def456 and gho_xyz789"
    redacted = _redact_tokens(text)
    assert "ghp_abc123def456" not in redacted
    assert "gho_xyz789" not in redacted
    assert "Authorization: ***" in redacted


def test_logger_creates_directory(tmp_path):
    log_path = tmp_path / "subdir" / "errors.jsonl"
    logger = ErrorLogger(log_path)
    logger.log(ActionErrorLogEntry(action_id="a1"))
    assert log_path.is_file()


def test_logger_path_property(tmp_path):
    log_path = tmp_path / "my-errors.jsonl"
    logger = ErrorLogger(log_path)
    assert logger.path == log_path


def test_logger_default_path_is_under_logs():
    logger = ErrorLogger()
    assert "logs" in str(logger.path)
    assert logger.path.name == "pm-agent-errors.log"


def test_missing_log_file_does_not_crash(tmp_path):
    log_path = tmp_path / "nonexistent" / "errors.jsonl"
    logger = ErrorLogger(log_path)
    entry = ActionErrorLogEntry(action_id="a1")
    logger.log(entry)
    assert log_path.is_file()
