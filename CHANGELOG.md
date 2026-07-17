# Changelog

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
