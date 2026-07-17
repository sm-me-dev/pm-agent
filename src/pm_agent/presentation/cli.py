from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from rich.console import Console

from pm_agent.application.action_service import ActionService
from pm_agent.application.context_service import ContextService
from pm_agent.application.conversation_service import ConversationService
from pm_agent.application.decision_service import DecisionService
from pm_agent.application.error_logger import ErrorLogger
from pm_agent.application.integration_service import IntegrationService
from pm_agent.application.session_service import SessionService
from pm_agent.application.summary_service import SummaryService
from pm_agent.config import AgentConfig, default_db_path, load_env_file
from pm_agent.infrastructure.hosts import IntegrationHostBridge
from pm_agent.infrastructure.models import OpenAICompatibleProvider
from pm_agent.infrastructure.repository import LocalRepositoryAnalyzer
from pm_agent.infrastructure.sqlite import SQLiteStore
from pm_agent.presentation.repl import PMAgentREPL, REPLServices
from pm_agent.project import (
    ProjectLocal,
    discover_or_override,
    global_config_path,
    init_project,
    legacy_global_db_path,
    migrate_project_data,
    resolve_db_path,
)
from pm_agent.prompts import PromptBuilder, ResponseParser

_COMMANDS = frozenset({"init", "repl", "status", "doctor", "spec", "memory", "migrate"})


def _print_err(msg: str) -> None:
    Console(stderr=True).print(f"[bold red]error:[/] {msg}")


def _die(msg: str, code: int = 1) -> None:
    _print_err(msg)
    raise SystemExit(code)


def main(argv: list[str] | None = None) -> int:
    load_env_file()
    argv = argv or sys.argv[1:]

    if argv and argv[0] in _COMMANDS:
        cmd, *rest = argv
        return _dispatch_command(cmd, rest)
    return _legacy_main(argv)


def _dispatch_command(cmd: str, args: list[str]) -> int:
    if cmd == "init":
        return _cmd_init(args)
    elif cmd == "repl":
        return _cmd_repl(args)
    elif cmd == "status":
        return _cmd_status(args)
    elif cmd == "doctor":
        return _cmd_doctor(args)
    elif cmd == "spec":
        return _cmd_spec(args)
    elif cmd == "memory":
        return _cmd_memory(args)
    elif cmd == "migrate":
        return _cmd_migrate(args)
    return 1


def _build_common_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--project-root", help="Exact project root path (bypasses discovery)")
    p.add_argument("--repo", default=None, help="Start path for project discovery (default: cwd)")
    return p


# --- init ---

def _cmd_init(args: list[str]) -> int:
    p = argparse.ArgumentParser(prog="pm-agent init", description="Initialize pm-agent in a project")
    p.add_argument("--project-root", help="Exact project root (default: cwd)")
    p.add_argument("--force", action="store_true", help="Overwrite existing config files")
    parsed = p.parse_args(args)

    target = Path(parsed.project_root).resolve() if parsed.project_root else Path.cwd().resolve()
    try:
        proj = init_project(target, force=parsed.force)
    except FileExistsError as exc:
        _print_err(str(exc))
        return 1

    console = Console()
    console.print(f"[green]Initialized pm-agent in[/] {proj.root}")
    console.print("  [dim].pm-agent/project.toml[/]")
    console.print("  [dim].pm-agent/memory.md[/]")
    console.print("  [dim]State database will be created on first run.[/]")
    return 0


# --- repl (shared with legacy) ---

def _resolve_repl_args(args: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="pm-agent repl", description="Start the PM-agent REPL")
    p.add_argument("--project-root")
    p.add_argument("--repo", default=None)
    p.add_argument("--context")
    p.add_argument("--db")
    p.add_argument("--model")
    p.add_argument("--base-url")
    p.add_argument("--api-key")
    p.add_argument("--session")
    p.add_argument("--branch", default=os.environ.get("PM_AGENT_BRANCH", "unknown"),
                    help="Host-confirmed branch name")
    p.add_argument("--always-approve", "--always-accept", "--accept-all", "--approve-all",
                    action="store_true", dest="always_approve", default=False)
    p.add_argument("--error-log", default=os.environ.get("PM_AGENT_ERROR_LOG"))
    return p.parse_args(args)


def _cmd_repl(args: list[str]) -> int:
    parsed = _resolve_repl_args(args)
    return _start_repl(parsed)


def _make_legacy_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="pm-agent",
        description="Stateful Technical PM Agent \u2014 project-aware CLI",
    )
    p.add_argument("--project-root", help="Exact project root path (bypasses discovery)")
    p.add_argument("--repo", default=os.getcwd())
    p.add_argument("--context")
    p.add_argument("--db")
    p.add_argument("--model")
    p.add_argument("--base-url")
    p.add_argument("--api-key")
    p.add_argument("--session")
    p.add_argument("--branch", default=os.environ.get("PM_AGENT_BRANCH", "unknown"),
                    help="Host-confirmed branch name")
    p.add_argument("--always-approve", "--always-accept", "--accept-all", "--approve-all",
                    action="store_true", dest="always_approve", default=False)
    p.add_argument("--error-log", default=os.environ.get("PM_AGENT_ERROR_LOG"))
    return p


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return _make_legacy_parser().parse_args(argv)


def _legacy_main(argv: list[str] | None) -> int:
    parsed = _make_legacy_parser().parse_args(argv)
    return _start_repl(parsed)


def build_repl(parsed: argparse.Namespace) -> PMAgentREPL:
    repo = Path(parsed.repo).resolve()
    if not repo.is_dir():
        _die(f"Repository path does not exist: {repo}", 1)

    db_path = Path(parsed.db).resolve() if getattr(parsed, "db", None) else default_db_path()
    if not db_path.parent.exists():
        db_path.parent.mkdir(parents=True, exist_ok=True)

    context_dir = parsed.context or None

    def _env(*names: str, default: str = "") -> str:
        for name in names:
            value = os.environ.get(name)
            if value:
                return value
        return default

    model = parsed.model or _env("PM_AGENT_MODEL", "OPENAI_MODEL", default="glm-5.2")
    base_url = parsed.base_url or _env("PM_AGENT_BASE_URL", "OPENAI_BASE_URL", default="http://localhost:11434/v1")
    api_key = parsed.api_key or _env("PM_AGENT_API_KEY", "OPENAI_API_KEY", default="")
    branch = getattr(parsed, "branch", "unknown")

    def _provider(base: str) -> str:
        return "ollama" if "11434" in base else "openai" if "openai.com" in base else "openai-compatible"

    def _structured() -> str:
        return _env("PM_AGENT_STRUCTURED_OUTPUT_MODE", default="auto")

    console = Console()
    try:
        store = SQLiteStore(db_path)
    except Exception as exc:
        _die(f"Failed to open database: {exc}", 2)

    project = store.resolve_project(repo, branch)
    session_service = SessionService(store)
    session = session_service.start(
        project, model, _provider(base_url), branch,
        name=getattr(parsed, "session", None),
    )

    error_log_path = getattr(parsed, "error_log", None)
    error_logger = ErrorLogger(path=error_log_path) if error_log_path else ErrorLogger()
    host = IntegrationHostBridge(error_logger=error_logger, repo_root=str(repo))
    action_service = ActionService(store, host)
    prompt_builder = PromptBuilder(context_dir)
    services = REPLServices(
        store=store,
        conversation=ConversationService(
            store,
            OpenAICompatibleProvider(
                model, base_url, api_key, _structured(),
            ),
            prompt_builder,
            ResponseParser(),
            action_service,
            history_limit=int(_env("PM_AGENT_HISTORY_LIMIT", default="75")),
            character_budget=int(_env("PM_AGENT_CONTEXT_TOKEN_BUDGET", default="6000")) * 3,
        ),
        actions=action_service,
        decisions=DecisionService(store),
        integrations=IntegrationService(store, action_service),
        context=ContextService(store, LocalRepositoryAnalyzer(), action_service),
        sessions=session_service,
        summaries=SummaryService(store),
    )
    return PMAgentREPL(
        console, project, session, services, model,
        always_approve=getattr(parsed, "always_approve", False),
        error_logger=error_logger,
        context_dir=context_dir,
    )


def _start_repl(parsed: argparse.Namespace) -> int:
    proj = discover_or_override(
        project_root=getattr(parsed, "project_root", None),
        repo=getattr(parsed, "repo", None),
        cwd=Path.cwd(),
    )

    if proj is None:
        if getattr(parsed, "repo", None) is not None:
            db_path = _final_db_path(None, parsed)
        else:
            _die("No project found. Run 'pm-agent init' or pass --repo to use legacy mode.", 1)
            return 1
    elif proj.is_initialized:
        pass
    else:
        if getattr(parsed, "repo", None) is not None:
            db_path = _final_db_path(proj, parsed)
        else:
            _die(
                f"No .pm-agent/ found in this repository ({proj.root.name}). "
                f"Run 'pm-agent init' to initialize.",
                1,
            )
            return 1

    db_path = _final_db_path(proj if proj and proj.is_initialized else None, parsed)
    context_dir = _resolve_context(proj, parsed)
    error_log_path = getattr(parsed, "error_log", None) or (
        str(proj.default_error_log_path) if proj and proj.is_initialized else None
    )
    branch = parsed.branch

    config = AgentConfig(
        repo_path=str(proj.root if proj else parsed.repo),
        context_dir=context_dir,
        db_path=db_path,
        model=parsed.model or AgentConfig.__dataclass_fields__["model"].default_factory(),
        base_url=parsed.base_url or AgentConfig.__dataclass_fields__["base_url"].default_factory(),
        api_key=parsed.api_key if parsed.api_key is not None else AgentConfig.__dataclass_fields__["api_key"].default_factory(),
        history_limit=AgentConfig.__dataclass_fields__["history_limit"].default_factory(),
        always_approve=parsed.always_approve,
        error_log_path=error_log_path,
    )

    inner_parsed = argparse.Namespace(
        repo=config.repo_path,
        context=config.context_dir,
        db=str(config.db_path),
        model=config.model,
        base_url=config.base_url,
        api_key=config.api_key,
        session=getattr(parsed, "session", None),
        branch=branch,
        always_approve=config.always_approve,
        error_log=config.error_log_path,
    )
    repl = build_repl(inner_parsed)
    repl.run()
    return 0


def _final_db_path(proj: ProjectLocal | None, parsed: argparse.Namespace) -> Path:
    env_db = os.environ.get("PM_AGENT_DB_PATH")
    if proj and proj.is_initialized:
        return resolve_db_path(proj, getattr(parsed, "db", None), env_db)
    cli_db = getattr(parsed, "db", None)
    if cli_db:
        return Path(cli_db).expanduser().resolve()
    if env_db:
        return Path(env_db).expanduser().resolve()
    if proj:
        return proj.default_db_path
    return legacy_global_db_path()


def _resolve_context(proj: ProjectLocal | None, parsed: argparse.Namespace) -> str | None:
    if getattr(parsed, "context", None):
        return parsed.context
    if proj and proj.is_initialized:
        return str(proj.context_dir)
    if proj:
        return str(proj.root / "context")
    return None


# --- status ---

def _cmd_status(args: list[str]) -> int:
    p = argparse.ArgumentParser(prog="pm-agent status", description="Show project status")
    p.add_argument("--project-root")
    p.add_argument("--repo", default=None)
    parsed = p.parse_args(args)

    proj = discover_or_override(parsed.project_root, parsed.repo, Path.cwd())
    if proj is None:
        _die("No project found. Run 'pm-agent init' or pass --repo.", 1)

    console = Console()
    if proj.is_initialized:
        console.print(f"[bold]Project:[/] {proj.root.name}")
        console.print(f"  [bold]Root:[/] {proj.root}")
        console.print("  [bold]State:[/] project-local (.pm-agent/)")
        console.print(f"  [bold]DB:[/] {resolve_db_path(proj, None, os.environ.get('PM_AGENT_DB_PATH'))}")
        console.print(f"  [bold]Config:[/] {proj.pm_agent_dir / 'project.toml'}")
        gcfg = global_config_path()
        if gcfg.is_file():
            console.print(f"  [bold]Global Config:[/] {gcfg}")
        console.print(f"  [bold]Memory:[/] {proj.pm_agent_dir / 'memory.md'} ({len(proj.memory)} chars)")
        console.print(f"  [bold]Logs:[/] {proj.log_dir}")
    else:
        mode = "legacy" if parsed.repo is not None else "uninitialized git repo"
        console.print(f"[bold]Project:[/] {proj.root.name}")
        console.print(f"  [bold]Root:[/] {proj.root}")
        console.print(f"  [bold]State:[/] {mode}")
        console.print(f"  [bold]DB:[/] {resolve_db_path(proj, None, os.environ.get('PM_AGENT_DB_PATH'))}")

    try:
        from pm_agent.infrastructure.sqlite import SQLiteStore
        store = SQLiteStore(resolve_db_path(proj, None, os.environ.get("PM_AGENT_DB_PATH")))
        project_obj = store.resolve_project(str(proj.root))
        counts = store.memory_counts(project_obj.id)
        console.print(f"  [bold]Sessions:[/] {counts.messages} messages, {counts.decisions} decisions, {counts.repo_notes} notes")
    except Exception:
        console.print("  [dim](no state database yet)[/]")

    return 0


# --- doctor ---

def _cmd_doctor(args: list[str]) -> int:
    p = argparse.ArgumentParser(prog="pm-agent doctor", description="Check pm-agent setup")
    p.add_argument("--project-root")
    p.add_argument("--repo", default=None)
    parsed = p.parse_args(args)

    proj = discover_or_override(parsed.project_root, parsed.repo, Path.cwd())
    console = Console()
    all_pass = True

    if proj is None:
        # Can still check global config and legacy DB
        console.print("[yellow]\u2717 No project found[/] (run `pm-agent init` or pass --repo)")
        all_pass = False
    elif proj.is_initialized:
        console.print(f"[green]\u2713 .pm-agent/[/] {proj.pm_agent_dir}")
        _check_toml(console, proj.pm_agent_dir / "project.toml", "project config")
    else:
        console.print(f"[yellow]\u2717 No .pm-agent/[/] in {proj.root} (run `pm-agent init`)")
        all_pass = False

    db_path = resolve_db_path(proj, None, os.environ.get("PM_AGENT_DB_PATH")) if proj else legacy_global_db_path()
    _check_writable(console, db_path, "DB path")

    if proj and proj.is_initialized:
        _check_writable(console, proj.log_dir / "test-write.tmp", "log path")
        _check_writable(console, proj.history_path, "prompt history")

    gcfg = global_config_path()
    if gcfg.is_file():
        _check_toml(console, gcfg, "global config")
    else:
        console.print("[dim]\u2713 global config not present (optional)[/]")

    legacy_path = legacy_global_db_path()
    if legacy_path.exists():
        console.print(f"[dim]\u2192 Legacy DB found: {legacy_path}. Run `pm-agent migrate` to import.[/]")

    return 0 if all_pass else 1


def _check_toml(console: Console, path: Path, label: str) -> None:
    try:
        import tomllib
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        console.print(f"[green]\u2713 {label}[/] ({len(data)} sections)")
    except (tomllib.TOMLDecodeError, OSError) as exc:
        console.print(f"[red]\u2717 {label}[/] invalid: {exc}")


def _check_writable(console: Console, path: Path, label: str) -> None:
    try:
        p = Path(path)
        if p.suffix:
            p.parent.mkdir(parents=True, exist_ok=True)
        else:
            p.mkdir(parents=True, exist_ok=True)
            p.rmdir()
            console.print(f"[green]\u2713 {label}[/] {p} (writable)")
            return
        test = p.parent / ".pm-agent-write-test"
        test.touch()
        test.unlink()
        console.print(f"[green]\u2713 {label}[/] {p} (writable)")
    except OSError as exc:
        console.print(f"[red]\u2717 {label}[/] not writable: {exc}")


# --- spec ---

def _cmd_spec(args: list[str]) -> int:
    p = argparse.ArgumentParser(prog="pm-agent spec", description="Show project specification")
    sub = p.add_subparsers(dest="subcmd")
    show = sub.add_parser("show", help="Display project.toml content")
    show.add_argument("--project-root")
    show.add_argument("--repo", default=None)
    parsed = p.parse_args(args)

    if parsed.subcmd != "show":
        p.print_help()
        return 1

    proj = discover_or_override(parsed.project_root, parsed.repo, Path.cwd())
    if proj is None:
        _die("No project found.", 1)

    spec_path = (proj.pm_agent_dir / "project.toml") if proj.is_initialized else None
    if spec_path is None or not spec_path.is_file():
        Console().print("No spec file.")
        return 0
    Console().print(spec_path.read_text(encoding="utf-8").rstrip())
    return 0


# --- memory ---

def _cmd_memory(args: list[str]) -> int:
    p = argparse.ArgumentParser(prog="pm-agent memory", description="Manage project memory")
    sub = p.add_subparsers(dest="subcmd")
    show = sub.add_parser("show", help="Display memory.md content")
    show.add_argument("--project-root")
    show.add_argument("--repo", default=None)
    add = sub.add_parser("add", help="Append a memory entry")
    add.add_argument("text", nargs="+", help="Memory text")
    add.add_argument("--project-root")
    add.add_argument("--repo", default=None)
    parsed = p.parse_args(args)

    if parsed.subcmd == "show":
        return _memory_show(parsed)
    elif parsed.subcmd == "add":
        return _memory_add(parsed)
    p.print_help()
    return 1


def _memory_show(parsed) -> int:
    proj = discover_or_override(getattr(parsed, "project_root", None),
                                 getattr(parsed, "repo", None), Path.cwd())
    if proj is None:
        _die("No project found.", 1)
    if not proj.is_initialized:
        _die("Project not initialized. Run 'pm-agent init' first.", 1)
    mem_path = proj.pm_agent_dir / "memory.md"
    if not mem_path.is_file():
        Console().print("No memory file.")
        return 0
    Console().print(mem_path.read_text(encoding="utf-8").rstrip())
    return 0


def _memory_add(parsed) -> int:
    proj = discover_or_override(getattr(parsed, "project_root", None),
                                 getattr(parsed, "repo", None), Path.cwd())
    if proj is None:
        _die("No project found.", 1)
    if not proj.is_initialized:
        _die("Project not initialized. Run 'pm-agent init' first.", 1)
    from datetime import date
    text = " ".join(parsed.text)
    mem_path = proj.pm_agent_dir / "memory.md"
    if not mem_path.is_file():
        mem_path.write_text("# Project Memory\n\n", encoding="utf-8")
    with mem_path.open("a", encoding="utf-8") as f:
        f.write(f"- {date.today()}: {text}\n")
    Console().print(f"[green]Added:[/] {text}")
    return 0


# --- migrate ---

def _cmd_migrate(args: list[str]) -> int:
    p = argparse.ArgumentParser(prog="pm-agent migrate",
                                description="Migrate legacy global DB to project-local state")
    p.add_argument("--project-root")
    p.add_argument("--repo", default=None)
    p.add_argument("--dry-run", action="store_true",
                   help="Show what would be migrated without writing")
    parsed = p.parse_args(args)

    proj = discover_or_override(parsed.project_root, parsed.repo, Path.cwd())
    if proj is None:
        _die("No project found. Run 'pm-agent init' first.", 1)
    if not proj.is_initialized:
        _die("Project not initialized. Run 'pm-agent init' first.", 1)

    source = legacy_global_db_path()
    dest = proj.default_db_path

    console = Console()
    if not source.exists():
        _die(f"No legacy database found at {source}.", 1)

    try:
        result = migrate_project_data(source, dest, proj.root, dry_run=parsed.dry_run)
    except (FileNotFoundError, ValueError) as exc:
        _die(str(exc), 1)

    if parsed.dry_run:
        console.print("[yellow]Dry run[/] \u2014 no data was written.")
    else:
        console.print(f"[green]Migrated[/] for project {proj.root}")

    for key, count in sorted(result.items()):
        if key == "already_existed":
            console.print(f"  Already existed: {count}")
        else:
            label = key.replace("_", " ").title()
            console.print(f"  {label}: {count}")

    if not parsed.dry_run:
        console.print("[dim]Note: prompt history was not migrated.[/]")
    return 0
