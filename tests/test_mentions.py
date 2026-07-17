import os
import tempfile
from pathlib import Path

import pytest

from pm_agent.presentation.file_scanner import LazyFileIndex, scan_files
from pm_agent.presentation.completer import PMCompleter, SLASH_COMMANDS
from pm_agent.presentation.mentions import (
    Mention,
    build_mention_payload,
    extract_mentions,
    format_mentioned_files,
    read_mentioned_files,
)


class TestScanFiles:
    def test_scan_empty_directory(self, tmp_path):
        result = scan_files(tmp_path)
        assert result == []

    def test_scan_with_files(self, tmp_path):
        (tmp_path / "main.py").touch()
        (tmp_path / "utils.py").touch()
        result = scan_files(tmp_path)
        assert sorted(result) == ["main.py", "utils.py"]

    def test_scan_excludes_git(self, tmp_path):
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").touch()
        (tmp_path / "main.py").touch()
        result = scan_files(tmp_path)
        assert result == ["main.py"]

    def test_scan_excludes_pycache(self, tmp_path):
        (tmp_path / "__pycache__").mkdir()
        (tmp_path / "__pycache__" / "module.pyc").touch()
        (tmp_path / "main.py").touch()
        result = scan_files(tmp_path)
        assert result == ["main.py"]

    def test_scan_nested_directories(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").touch()
        (tmp_path / "src" / "utils").mkdir()
        (tmp_path / "src" / "utils" / "helper.py").touch()
        result = scan_files(tmp_path)
        assert "src/main.py" in result
        assert "src/utils/helper.py" in result

    def test_scan_respects_max_depth(self, tmp_path):
        (tmp_path / "a").mkdir()
        (tmp_path / "a" / "b").mkdir()
        (tmp_path / "a" / "b" / "c").mkdir()
        (tmp_path / "a" / "b" / "c" / "deep.py").touch()
        (tmp_path / "top.py").touch()
        result = scan_files(tmp_path, max_depth=2)
        assert "top.py" in result
        assert "a/b/c/deep.py" not in result


class TestLazyFileIndex:
    def test_search_returns_matches(self, tmp_path):
        (tmp_path / "main.py").touch()
        (tmp_path / "utils.py").touch()
        index = LazyFileIndex(tmp_path)
        matches = index.search("main")
        assert matches == ["main.py"]

    def test_search_case_insensitive(self, tmp_path):
        (tmp_path / "Main.py").touch()
        index = LazyFileIndex(tmp_path)
        matches = index.search("main")
        assert matches == ["Main.py"]

    def test_search_partial_match(self, tmp_path):
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").touch()
        index = LazyFileIndex(tmp_path)
        matches = index.search("src/main")
        assert "src/main.py" in matches


class TestPMCompleter:
    def test_slash_commands_complete(self, tmp_path):
        index = LazyFileIndex(tmp_path)
        completer = PMCompleter(index)
        assert "/help" in SLASH_COMMANDS
        assert "/status" in SLASH_COMMANDS

    def test_completer_initialization(self, tmp_path):
        index = LazyFileIndex(tmp_path)
        completer = PMCompleter(index)
        assert completer._file_index is index
        assert len(completer._commands) == len(SLASH_COMMANDS)


class TestExtractMentions:
    def test_no_mentions(self):
        result = extract_mentions("Hello world")
        assert result == []

    def test_single_mention(self):
        result = extract_mentions("Look at @src/main.py")
        assert len(result) == 1
        assert result[0].path == "src/main.py"

    def test_multiple_mentions(self):
        result = extract_mentions("Check @file1.py and @file2.py")
        assert len(result) == 2
        assert result[0].path == "file1.py"
        assert result[1].path == "file2.py"

    def test_quoted_mention(self):
        result = extract_mentions('Look at @"src/main.py"')
        assert len(result) == 1
        assert result[0].path == "src/main.py"


class TestReadMentionedFiles:
    def test_read_existing_file(self, tmp_path):
        (tmp_path / "test.py").write_text("print('hello')")
        mentions = [Mention(raw="@test.py", path="test.py")]
        contents, warnings = read_mentioned_files(mentions, tmp_path)
        assert contents["test.py"] == "print('hello')"
        assert warnings == {}

    def test_skip_nonexistent_file(self, tmp_path):
        mentions = [Mention(raw="@missing.py", path="missing.py")]
        contents, warnings = read_mentioned_files(mentions, tmp_path)
        assert contents == {}
        assert "missing.py" in warnings

    def test_path_traversal_blocked(self, tmp_path):
        (tmp_path / "secret.txt").write_text("secret")
        mentions = [Mention(raw="@../../../secret.txt", path="../../../secret.txt")]
        contents, warnings = read_mentioned_files(mentions, tmp_path)
        assert contents == {}
        assert "../../../secret.txt" in warnings


class TestFormatMentionedFiles:
    def test_format_empty(self):
        result = format_mentioned_files({}, {})
        assert result is None

    def test_format_single_file(self):
        result = format_mentioned_files({"main.py": "print('hello')"}, {})
        assert "--- BEGIN FILE: main.py ---" in result
        assert "print('hello')" in result
        assert "--- END FILE: main.py ---" in result

    def test_format_warning(self):
        result = format_mentioned_files({}, {"missing.py": "File not found."})
        assert "--- FILE WARNING: missing.py ---" in result
        assert "File not found." in result
        assert "--- END FILE WARNING ---" in result


class TestBuildMentionPayload:
    def test_no_mentions_returns_original(self, tmp_path):
        result = build_mention_payload("Hello world", tmp_path)
        assert result == "Hello world"

    def test_mentions_injects_content(self, tmp_path):
        (tmp_path / "test.py").write_text("x = 1")
        result = build_mention_payload("Look at @test.py", tmp_path)
        assert "User Request:" in result
        assert "Look at @test.py" in result
        assert "Attached File Context:" in result
        assert "--- BEGIN FILE: test.py ---" in result
        assert "x = 1" in result
        assert "--- END FILE: test.py ---" in result
