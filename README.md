# PM-Agent

**Stateful Technical PM Agent** — a CLI tool that brings persistent project-management
context to AI-assisted development. It maintains project decisions, session history,
action audits, and approved capabilities in a per-project SQLite database.

## Features

- **Per-project persistent state** — context is loaded from `.pm-agent/` in your repo
- **Multi-project isolation** — each project has its own config, memory, approvals, and state DB
- **Interactive REPL** — chat with the agent via a rich terminal interface
- **Action lifecycle** — propose, approve, execute, and audit shell/git/github operations
- **Decision tracking** — record and accept/reject architectural decisions
- **Memory retrieval** — FTS5-powered search across past sessions, decisions, and notes
- **Legacy migration** — import existing state from a global database into per-project storage

## Requirements

- Python 3.12+
- An OpenAI-compatible chat endpoint (e.g., Ollama, OpenAI, or any compatible API)
- SQLite with FTS5 (included in Python's `sqlite3` module)

## Installation

### Quick install (pipx)

```bash
pipx install pm-agent
```

### From source

```bash
git clone https://github.com/sm-me-uwe/pm-agent.git
cd pm-agent
pip install -e .
```

### uv

```bash
uv tool install pm-agent
```

### Verify

```bash
pm-agent --help
```

## Quickstart

### 1. Initialize a project

```bash
cd /path/to/your/repo
pm-agent init
```

This creates a `.pm-agent/` directory with:
- `project.toml` — machine-readable project configuration
- `memory.md` — human-editable project memory/notes

### 2. Start the REPL

```bash
pm-agent
```

Or explicitly:

```bash
pm-agent repl
```

The first run creates the SQLite state database at `.pm-agent/state.db`.

### 3. Inspect project state

```bash
pm-agent status
pm-agent spec show
pm-agent memory show
```

### 4. Add memory notes

```bash
pm-agent memory add "Decided to use SQLite for all persistence"
```

### 5. Run diagnostics

```bash
pm-agent doctor
```

## Commands

| Command | Description |
|---|---|
| `pm-agent init` | Initialize `.pm-agent/` in the current directory |
| `pm-agent repl` | Start the interactive REPL (default) |
| `pm-agent status` | Show project status and active configuration |
| `pm-agent doctor` | Check environment, config, and permissions |
| `pm-agent spec show` | Display the project specification |
| `pm-agent memory show` | Display project memory |
| `pm-agent memory add <text>` | Append a memory entry |
| `pm-agent migrate` | Import legacy global DB into project-local storage |
| `pm-agent --help` | Show all options |

## Configuration

### Precedence (highest to lowest)

1. CLI flags (`--model`, `--db`, `--repo`, etc.)
2. Environment variables (`PM_AGENT_*`)
3. `.pm-agent/project.toml` (project-local)
4. `~/.config/pm-agent/config.toml` (user-global)
5. Built-in defaults

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `PM_AGENT_MODEL` | `glm-5.2` | Model name |
| `PM_AGENT_BASE_URL` | `http://localhost:11434/v1` | API endpoint |
| `PM_AGENT_API_KEY` | `` | API key |
| `PM_AGENT_HISTORY_LIMIT` | `75` | Messages in context window |
| `PM_AGENT_CONTEXT_TOKEN_BUDGET` | `6000` | Token budget × 3 = char budget |
| `PM_AGENT_ALWAYS_APPROVE` | `false` | Auto-approve all actions |

### Project Config (`project.toml`)

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

### Global Config (`~/.config/pm-agent/config.toml`)

```toml
[defaults]
model = ""
base_url = ""
history_limit = 75
```

## Project Layout

```
my-project/
  .pm-agent/
    project.toml      # Project configuration (commit this)
    memory.md         # Human-editable notes (commit this)
    state.db          # SQLite database (gitignored)
    logs/             # Error logs (gitignored)
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

## Safety Model

- Repository access is read-only by default
- Every external action requires explicit approval (unless auto-approved via rules)
- Approval is payload-specific and tied to a content hash
- The agent never writes to the managed repository directly
- All actions are logged with an immutable audit trail

## Development

```bash
git clone https://github.com/sm-me-uwe/pm-agent.git
cd pm-agent
pip install -e ".[dev]"
python -m pytest tests/
```

## License

MIT
