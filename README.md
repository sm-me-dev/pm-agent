# PM-Agent

**Stateful Technical PM Agent** — a CLI tool that brings persistent project-management
context to AI-assisted development. Each project gets its own configuration, memory,
decision log, and action audit trail in a local SQLite database.

## Status

Alpha — under active development. The CLI is stable for daily use; the schema and API
may change before a 1.0 release.

## Key Concepts

```
my-project/
  .pm-agent/        ← created by `pm-agent init`
    project.toml    ← machine-readable project config (commit this)
    memory.md       ← human-editable notes (commit this)
    state.db        ← SQLite state DB (gitignored, auto-created)
    logs/           ← action error logs (gitignored)
```

### Project Discovery

`pm-agent` walks up from your current directory looking for `.pm-agent/`. If
found, that directory becomes the project root. If not found, it looks for a
`.git` directory as a fallback. This means:

- Run `pm-agent status` from any subdirectory of your project.
- Pass `--project-root /path` to pin an exact root (bypasses discovery).
- Pass `--repo /path` to set a different starting point for discovery.

### Commands

| Command | Description |
|---|---|
| `pm-agent init` | Initialize `.pm-agent/` in the current repo |
| `pm-agent` / `pm-agent repl` | Start the interactive REPL |
| `pm-agent status` | Show project state and active config |
| `pm-agent doctor` | Check environment, config, and permissions |
| `pm-agent spec show` | Display the project specification |
| `pm-agent memory show` | Display project memory |
| `pm-agent memory add <text>` | Append a dated memory entry |
| `pm-agent migrate` | Import legacy global DB into `.pm-agent/state.db` |

## Installation

Requires **Python 3.12+** and an OpenAI-compatible chat endpoint
(Ollama, OpenAI, or any compatible API).

### Quick install (recommended)

**Linux / macOS:**

```bash
curl -fsSL https://raw.githubusercontent.com/sm-me-dev/pm-agent/main/scripts/install.sh | bash
```

To pin a version:

```bash
PM_AGENT_VERSION=0.3.0 curl -fsSL https://raw.githubusercontent.com/sm-me-dev/pm-agent/main/scripts/install.sh | bash
```

**Windows (PowerShell):**

```powershell
irm https://raw.githubusercontent.com/sm-me-dev/pm-agent/main/scripts/install.ps1 | iex
```

To pin a version:

```powershell
$env:PM_AGENT_VERSION = "0.3.0"; irm https://raw.githubusercontent.com/sm-me-dev/pm-agent/main/scripts/install.ps1 | iex
```

> **Security note:** piping from the web to your shell is convenient but skips
> normal verification. Review the script before running:
> - [install.sh](scripts/install.sh)
> - [install.ps1](scripts/install.ps1)

The installer detects or installs [pipx](https://github.com/pypa/pipx) and
uses it to install `pm-agent` into an isolated environment.

### pipx (any platform)

```bash
pipx install product-manager-agent
```

### From source

```bash
git clone https://github.com/sm-me-dev/pm-agent.git
cd pm-agent
pip install -e ".[dev]"
```

### Verify

```bash
pm-agent --help
```

## Quickstart

```bash
cd /path/to/your/repo
pm-agent init            # creates .pm-agent/
pm-agent                 # starts the REPL
```

```bash
pm-agent status          # inspect project state
pm-agent doctor          # run diagnostics
pm-agent migrate         # import legacy data if you used pm-agent before v0.3
```

## Configuration Precedence

1. CLI flags (`--model`, `--db`, `--repo`, etc.)
2. Environment variables (`PM_AGENT_*`)
3. `.pm-agent/project.toml` (per-project)
4. `~/.config/pm-agent/config.toml` (user-global)
5. Built-in defaults

### Key Environment Variables

| Variable | Default | Description |
|---|---|---|
| `PM_AGENT_MODEL` | `glm-5.2` | Model name |
| `PM_AGENT_BASE_URL` | `http://localhost:11434/v1` | API endpoint |
| `PM_AGENT_API_KEY` | — | API key |
| `PM_AGENT_HISTORY_LIMIT` | `75` | Messages in context window |
| `PM_AGENT_ALWAYS_APPROVE` | `false` | Auto-approve all actions |

## Project Config (`project.toml`)

```toml
[project]
name = "my-project"
language = "python"
test_command = "pytest"
lint_command = "ruff check"
build_command = ""

[constraints]
allowed_paths = []
approval_default = "prompt"
blocked_actions = []

[paths]
# context_dir = ""
# error_log_path = ""
```

## Migration from Legacy Global DB

If you previously used pm-agent with a global state database at
`~/.local/share/pm-agent/state.db`, migrate to project-local storage:

```bash
cd /path/to/your/repo
pm-agent init       # one-time setup
pm-agent migrate    # copy existing data into .pm-agent/state.db
```

The migration is idempotent — running it multiple times is safe.

## Safety

- Repository access is read-only by default.
- External actions require per-payload approval (configurable via rules).
- All actions are logged with an immutable audit trail.

## Development

```bash
git clone https://github.com/sm-me-dev/pm-agent.git
cd pm-agent
pip install -e ".[dev]"
pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for full development workflow.

## Release

See [RELEASE.md](RELEASE.md) for the release process.

## License

MIT
