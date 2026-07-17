# Changelog

## 0.3.8 (2026-07-17)

### Fixed
- Safety policy now accepts `cmd` and `shell_command` as alternative payload keys for bash commands, so GLM-5.2 models that don't use the `command` key are no longer silently rejected.
- Safety policy now fuzzy-matches tool_category values containing "filesystem" or "shell" (e.g. "file_system"), reducing false rejections from models that deviate from the exact schema.
- GitHub action scope errors (e.g. missing `read:project`) are now marked as `retryable` in the error log so downstream consumers know the user can fix and retry.
- Added pre-flight GitHub scope check: before dispatching `list_projects`, `add_issue_to_project`, `setup_sprint`, etc., pm-agent now runs `gh auth status` and fails fast with a clear fix command if required scopes are missing.
- Rejection reasons from the safety policy are now shown inline in the "Safety policy rejected" message (e.g. `cat /path (reason: A bash proposal requires a command)`).

### Added
- Debug logging in `ActionPolicy.evaluate()` and `_evaluate_bash()` to help diagnose model payload issues.
- Debug logging in `ConversationService` when actions are rejected.

## 0.3.7 (2026-07-17)

### Fixed
- `load_env_file()` now also loads `.env` from the project's `.pm-agent/` directory after project discovery, so per-project env vars are picked up automatically.

## 0.3.6 (2026-07-17)

### Fixed
- Fix mypy type error in `load_env_file` caused by 0.3.5 release.

## 0.3.5 (2026-07-17)

### Fixed
- `load_env_file()` now searches `~/.env` and `~/.config/pm-agent/.env` in addition to `cwd/.env`, so PM_* env vars are picked up from more locations.
- `ResponseValidationError` now shows a clear "Model Response Error" panel instead of "Unexpected error".
- Empty model responses produce a specific error message ("Model returned empty or minimal response") instead of a generic parse error.

## 0.3.4 (2026-07-17)

### Fixed
- Connection errors now raise the correct custom `ConnectionError` type so the REPL handler catches them properly (was raising built-in `ConnectionError` which bypassed the specific handler).

## 0.3.3 (2026-07-17)

### Fixed
- Connection errors from model provider (e.g. "Connection reset by peer") are now caught and displayed as a clear "Model Connection Error" instead of a generic "Request failed" message.
- `pm-agent migrate` is now idempotent — rerunning it no longer fails with `IntegrityError: UNIQUE constraint failed` on `action_outcomes`.

## 0.3.2 (2026-07-17)

### Fixed
- `pm-agent --help` now shows available subcommands instead of legacy parser.
- Updated tests to match new CLI dispatch.

## 0.3.1 (2026-07-17) — Published

## 0.3.0 (2026-07-17) — First Public Release

### Overview

pm-agent is a stateful technical PM CLI that brings persistent project-management
context to AI-assisted development. Each project gets its own SQLite-backed
state, configuration, memory, and action audit trail.

### Added
- **Project isolation** — per-project `.pm-agent/` with `project.toml`, `memory.md`, `state.db`.
- **Project discovery** — walks up from CWD for `.pm-agent/`, falls back to `.git`.
- **Multi-project CLI commands** — `pm-agent init`, `status`, `doctor`, `spec`.
- **Project memory** — `pm-agent memory show` / `memory add`.
- **Legacy migration** — `pm-agent migrate` imports global DB into per-project storage.
- **TOML config system** — project-local (`project.toml`) and user-global (`~/.config/pm-agent/config.toml`).
- **Config precedence** — CLI flags > env vars > project.toml > global config > built-in defaults.
- **Action lifecycle** — propose, approve, execute, audit shell/git/github operations.
- **Decision tracking** — record and accept/reject architectural decisions.
- **Interactive REPL** — rich terminal interface with FTS5-powered context retrieval.
- **Safety model** — read-only repo access, per-payload approval required, immutable audit trail.
- **Stdlib-first** — SQLite with FTS5, TOML parsing via `tomllib` (Python 3.12+).

### Changed
- REPL and error logger no longer depend on the deprecated `project_root()` function.
- Exit codes: 0=success, 1=operational error, 2=config error.
- Installer scripts for Linux/macOS (`scripts/install.sh`) and Windows (`scripts/install.ps1`).
