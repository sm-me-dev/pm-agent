from __future__ import annotations

from pm_agent.presentation.cli import parse_args


def test_default_always_approve_is_false():
    args = parse_args([])
    assert args.always_approve is False


def test_always_approve_flag():
    args = parse_args(["--always-approve"])
    assert args.always_approve is True


def test_always_accept_alias():
    args = parse_args(["--always-accept"])
    assert args.always_approve is True


def test_accept_all_alias():
    args = parse_args(["--accept-all"])
    assert args.always_approve is True


def test_approve_all_alias():
    args = parse_args(["--approve-all"])
    assert args.always_approve is True


def test_error_log_flag():
    args = parse_args(["--error-log", "/tmp/test-log.jsonl"])
    assert args.error_log == "/tmp/test-log.jsonl"


def test_error_log_default_none():
    args = parse_args([])
    assert args.error_log is None
