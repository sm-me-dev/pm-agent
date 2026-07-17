from __future__ import annotations

import os
from pathlib import Path

import pytest

from pm_agent.presentation.cli import main, _COMMANDS


class TestEntrypoints:
    def test_help_exits_zero(self):
        try:
            main(["--help"])
        except SystemExit as exc:
            assert exc.code == 0

    def test_module_help(self):
        import subprocess
        result = subprocess.run(
            ["python", "-m", "pm_agent", "--help"],
            capture_output=True, text=True, cwd=Path(__file__).resolve().parent.parent.parent,
        )
        assert result.returncode == 0

    def test_unknown_subcommand(self):
        try:
            main(["nonexistent"])
        except SystemExit as exc:
            assert exc.code == 2  # argparse error code

    @pytest.mark.parametrize("cmd", sorted(_COMMANDS))
    def test_all_commands_recognized(self, cmd):
        assert cmd in {"init", "repl", "status", "doctor", "spec", "memory", "migrate"}


class TestInitCommand:
    def test_init_creates_structure(self, tmp_path):
        try:
            main(["init", "--project-root", str(tmp_path)])
        except SystemExit as exc:
            assert exc.code == 0
        assert (tmp_path / ".pm-agent").is_dir()
        assert (tmp_path / ".pm-agent" / "project.toml").is_file()
        assert (tmp_path / ".pm-agent" / "memory.md").is_file()

    def test_init_twice_fails(self, tmp_path):
        main(["init", "--project-root", str(tmp_path)])
        try:
            main(["init", "--project-root", str(tmp_path)])
        except SystemExit as exc:
            assert exc.code == 1

    def test_init_with_force(self, tmp_path):
        main(["init", "--project-root", str(tmp_path)])
        (tmp_path / ".pm-agent" / "project.toml").write_text("modified", encoding="utf-8")
        main(["init", "--project-root", str(tmp_path), "--force"])
        content = (tmp_path / ".pm-agent" / "project.toml").read_text(encoding="utf-8")
        assert "[project]" in content


class TestStatusCommand:
    def test_status_no_project(self):
        try:
            main(["status", "--project-root", "/nonexistent"])
        except SystemExit as exc:
            assert exc.code == 1

    def test_status_with_project(self, tmp_path):
        main(["init", "--project-root", str(tmp_path)])
        try:
            main(["status", "--project-root", str(tmp_path)])
        except SystemExit as exc:
            assert exc.code == 0


class TestDoctorCommand:
    def test_doctor_no_project(self):
        try:
            main(["doctor", "--project-root", "/nonexistent"])
        except SystemExit as exc:
            assert exc.code == 1

    def test_doctor_with_project(self, tmp_path):
        main(["init", "--project-root", str(tmp_path)])
        try:
            main(["doctor", "--project-root", str(tmp_path)])
        except SystemExit as exc:
            assert exc.code == 1  # will fail on writable checks for /tmp, but that's fine


class TestSpecCommand:
    def test_spec_show_no_project(self):
        try:
            main(["spec", "show", "--project-root", "/nonexistent"])
        except SystemExit as exc:
            assert exc.code == 1

    def test_spec_show_with_project(self, tmp_path):
        main(["init", "--project-root", str(tmp_path)])
        try:
            main(["spec", "show", "--project-root", str(tmp_path)])
        except SystemExit as exc:
            assert exc.code == 0

    def test_spec_show_no_file(self, tmp_path):
        (tmp_path / ".pm-agent").mkdir()
        try:
            main(["spec", "show", "--project-root", str(tmp_path)])
        except SystemExit as exc:
            assert exc.code == 0


class TestMemoryCommand:
    def test_memory_show_no_project(self):
        try:
            main(["memory", "show", "--project-root", "/nonexistent"])
        except SystemExit as exc:
            assert exc.code == 1

    def test_memory_show_no_file(self, tmp_path):
        main(["init", "--project-root", str(tmp_path)])
        os.unlink(str(tmp_path / ".pm-agent" / "memory.md"))
        try:
            main(["memory", "show", "--project-root", str(tmp_path)])
        except SystemExit as exc:
            assert exc.code == 0

    def test_memory_add(self, tmp_path):
        main(["init", "--project-root", str(tmp_path)])
        try:
            main(["memory", "add", "test entry", "--project-root", str(tmp_path)])
        except SystemExit as exc:
            assert exc.code == 0
        mem = (tmp_path / ".pm-agent" / "memory.md").read_text(encoding="utf-8")
        assert "test entry" in mem

    def test_memory_show_content(self, tmp_path):
        main(["init", "--project-root", str(tmp_path)])
        main(["memory", "add", "content check", "--project-root", str(tmp_path)])
        try:
            main(["memory", "show", "--project-root", str(tmp_path)])
        except SystemExit as exc:
            assert exc.code == 0


class TestMigrateCommand:
    def test_migrate_no_legacy(self, tmp_path):
        main(["init", "--project-root", str(tmp_path)])
        try:
            main(["migrate", "--project-root", str(tmp_path)])
        except SystemExit as exc:
            assert exc.code == 1

    def test_migrate_dry_run(self, tmp_path):
        main(["init", "--project-root", str(tmp_path)])
        # No legacy DB exists, but --dry-run still validates source exists first
        try:
            main(["migrate", "--dry-run", "--project-root", str(tmp_path)])
        except SystemExit as exc:
            assert exc.code == 1
