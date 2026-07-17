"""Tests for bash-block interception."""
from __future__ import annotations

import re

BASH_BLOCK_RE = re.compile(r"```bash\s*\n(.*?)```", re.DOTALL)


def test_extracts_single_block():
    text = "Plan:\n\n```bash\npytest -q\n```\n"
    cmds = BASH_BLOCK_RE.findall(text)
    assert len(cmds) == 1
    assert "pytest -q" in cmds[0]


def test_extracts_multiple_blocks_in_order():
    text = (
        "First run tests:\n\n```bash\npytest -q\n```\n\n"
        "Then check git:\n\n```bash\ngit status\n```\n"
    )
    cmds = BASH_BLOCK_RE.findall(text)
    assert len(cmds) == 2
    assert "pytest -q" in cmds[0]
    assert "git status" in cmds[1]


def test_no_blocks_returns_empty():
    text = "Just a regular response, no code blocks."
    assert BASH_BLOCK_RE.findall(text) == []


def test_ignores_non_bash_fenced_blocks():
    text = "```python\nprint('hi')\n```\n\n```bash\nls\n```\n"
    cmds = BASH_BLOCK_RE.findall(text)
    assert len(cmds) == 1
    assert "ls" in cmds[0]


def test_substitute_removes_all_bash_blocks():
    text = "```bash\nls\n```\nbody\n```bash\npwd\n```\n"
    cleaned = BASH_BLOCK_RE.sub("[pending]", text)
    assert "```bash" not in cleaned
    assert "[pending]" in cleaned
    assert "body" in cleaned


def test_multiline_command_preserved():
    text = "```bash\npytest -q \\\n  --tb=short\n```\n"
    cmds = BASH_BLOCK_RE.findall(text)
    assert len(cmds) == 1
    assert "--tb=short" in cmds[0]
