from __future__ import annotations

import glob as glob_module
import json
import os
import re
import shlex
import shutil
import subprocess
from collections.abc import Callable
from fnmatch import translate as fnmatch_translate
from pathlib import Path
from typing import Any
from uuid import uuid4

from pm_agent.application.error_logger import ErrorLogger
from pm_agent.domain.actions import (
    ALL_GITHUB_OPERATIONS,
    capabilities_from_scopes,
    parse_token_scopes,
    required_capabilities,
    scopes_for_capabilities,
)
from pm_agent.domain.enums import ActionType
from pm_agent.domain.models import ApprovedAction, DispatchReceipt
from pm_agent.ports.host import HostCapabilities

from .base import verify_approved_action
from .standalone import StandaloneHostBridge


class IntegrationHostBridge:
    _GITHUB_READ_OPS: dict[str, Callable[[dict], list[str]]] = {
        "inspect_repository": lambda p: [
            "repo", "view", p["repository"], "--json",
            "name,description,url,defaultBranchRef,createdAt,updatedAt,owner,languages,"
            "repositoryTopics",
        ],
        "read_repository": lambda p: [
            "repo", "view", p["repository"], "--json",
            "name,description,url,defaultBranchRef,createdAt,updatedAt,owner,languages,"
            "repositoryTopics",
        ],
        "list_issues": lambda p: [
            "issue", "list", "--repo", p["repository"], "--json",
            "number,title,state,labels,assignees,milestone,createdAt,updatedAt",
            "--limit", "50",
        ],
        "list_milestones": lambda p: [
            "api", f"repos/{p['repository']}/milestones?state=all",
        ],
        "list_projects": lambda p: [
            "project", "list", "--owner", p["repository"].split("/")[0], "--format", "json",
        ],
        "list_pull_requests": lambda p: [
            "pr", "list", "--repo", p["repository"], "--json",
            "number,title,state,headRefName,baseRefName,author,createdAt,updatedAt,"
            "labels,milestone,additions,deletions",
            "--limit", "50",
        ],
        "list_releases": lambda p: [
            "release", "list", "--repo", p["repository"], "--json",
            "tagName,name,isLatest,createdAt,publishedAt",
            "--limit", "50",
        ],
    }

    _GITHUB_ALL_OPS: frozenset = ALL_GITHUB_OPERATIONS

    def __init__(self, error_logger: ErrorLogger | None = None,
                 repo_root: str | None = None) -> None:
        self._standalone = StandaloneHostBridge()
        self._error_logger = error_logger
        self._repo_root = repo_root

    _ALLOWED_READ_COMMANDS = frozenset({
        "ls", "cat", "find", "head", "tail", "wc", "pwd", "echo", "rg", "grep",
    })

    @staticmethod
    def _extract_command_paths(cmd: str, parts: list[str]) -> list[str]:
        _FLAGS_WITH_ARGS: dict[str, frozenset] = {
            "find": frozenset({"-name", "-iname", "-type", "-path", "-ipath"}),
        }
        if cmd in {"grep", "rg"}:
            found_pattern = False
            paths: list[str] = []
            for p in parts[1:]:
                if p.startswith("-"):
                    continue
                if not found_pattern:
                    found_pattern = True
                else:
                    paths.append(p)
            if not paths:
                paths = ["."]
            return paths
        skip_next = False
        paths: list[str] = []
        flag_args = _FLAGS_WITH_ARGS.get(cmd, frozenset())
        for p in parts[1:]:
            if skip_next:
                skip_next = False
                continue
            if p.startswith("-"):
                if p in flag_args:
                    skip_next = True
                continue
            paths.append(p)
        return paths

    @staticmethod
    def _expand_globs(path_strs: list[str]) -> list[Path]:
        result: list[Path] = []
        for p in path_strs:
            if any(c in p for c in frozenset({"*", "?", "["})):
                matches = glob_module.glob(p, recursive=False)
                if not matches:
                    raise FileNotFoundError(f"No matches for glob: '{p}'")
                for m in matches:
                    result.append(Path(m).resolve())
            else:
                result.append(Path(p).expanduser().resolve())
        return result

    def _execute_local_read(self, action: ApprovedAction) -> DispatchReceipt:
        payload = action.proposal.payload
        command_str: str = payload.get("command", "")
        if not command_str:
            return DispatchReceipt(
                correlation_id=None, dispatched=True, completed=True, exit_code=1,
                message="No command specified in payload.",
                stderr="payload must contain a 'command' field.",
            )
        parts = shlex.split(command_str)
        if not parts:
            return DispatchReceipt(
                correlation_id=None, dispatched=True, completed=True, exit_code=1,
                message="Empty command.",
            )
        cmd = parts[0].lower()
        if cmd not in self._ALLOWED_READ_COMMANDS:
            return DispatchReceipt(
                correlation_id=None, dispatched=True, completed=True, exit_code=1,
                message=f"Command '{cmd}' is not a supported read-only operation.",
                stderr=f"Allowed: {', '.join(sorted(self._ALLOWED_READ_COMMANDS))}",
            )
        if cmd == "pwd":
            return DispatchReceipt(
                correlation_id=uuid4().hex, dispatched=True, completed=True, exit_code=0,
                message=f"Current directory: {os.getcwd()}",
                stdout=os.getcwd(),
            )
        raw_paths = self._extract_command_paths(cmd, parts)
        if not raw_paths and cmd not in {"pwd", "echo", "grep", "rg"}:
            return DispatchReceipt(
                correlation_id=None, dispatched=True, completed=True, exit_code=1,
                message=f"Command '{cmd}' requires a file or directory path.",
            )
        try:
            target_dirs = self._expand_globs(raw_paths)
        except FileNotFoundError as exc:
            return DispatchReceipt(
                correlation_id=None, dispatched=True, completed=True, exit_code=1,
                message=str(exc),
            )
        repo_root = Path(self._repo_root).resolve() if self._repo_root else None
        for resolved in target_dirs:
            if repo_root and not str(resolved).startswith(str(repo_root)):
                return DispatchReceipt(
                    correlation_id=None, dispatched=True, completed=True, exit_code=1,
                    message=f"Path '{resolved}' is outside the allowed repository root.",
                    stderr=f"Allowed root: {repo_root}",
                )
        try:
            result = self._execute_command_safely(cmd, parts, target_dirs)
        except OSError as exc:
            return DispatchReceipt(
                correlation_id=None, dispatched=True, completed=True, exit_code=1,
                message=f"File system error: {exc}", stderr=str(exc),
            )
        return result

    def _execute_command_safely(
        self, cmd: str, parts: list[str], paths: list[Path],
    ) -> DispatchReceipt:
        cid = uuid4().hex
        if cmd == "cat":
            lines: list[str] = []
            total = 0
            max_bytes = 500_000
            for p in paths:
                if not p.is_file():
                    return DispatchReceipt(
                        correlation_id=cid, dispatched=True, completed=True, exit_code=1,
                        message=f"Not a file or not found: {p}", stderr=str(p),
                    )
                if p.stat().st_size > max_bytes:
                    return DispatchReceipt(
                        correlation_id=cid, dispatched=True, completed=True, exit_code=1,
                        message=f"File too large ({p.stat().st_size} bytes, max {max_bytes})",
                        stderr=str(p),
                    )
                text = p.read_text(errors="replace")
                total += len(text)
                if total > max_bytes:
                    text = text[: max_bytes - (total - len(text))]
                    lines.append(text)
                    lines.append(f"... truncated at {max_bytes} bytes")
                    break
                lines.append(text)
            out = "".join(lines)
            return DispatchReceipt(
                correlation_id=cid, dispatched=True, completed=True, exit_code=0,
                message=f"Read {len(out)} characters from {len(paths)} file(s).",
                stdout=out,
            )
        if cmd == "ls":
            entries: list[str] = []
            for p in paths:
                if not p.is_dir():
                    return DispatchReceipt(
                        correlation_id=cid, dispatched=True, completed=True, exit_code=1,
                        message=f"Not a directory: {p}", stderr=str(p),
                    )
                names = sorted(os.listdir(p))
                entries.append(f"{p}/:")
                for name in names:
                    fp = p / name
                    suffix = "/" if fp.is_dir() else ""
                    entries.append(f"  {name}{suffix}")
                entries.append("")
            out = "\n".join(entries).strip()
            return DispatchReceipt(
                correlation_id=cid, dispatched=True, completed=True, exit_code=0,
                message=f"Listed {len(paths)} director(ies).", stdout=out,
            )
        if cmd == "find":
            for p in paths:
                if not p.is_dir():
                    return DispatchReceipt(
                        correlation_id=cid, dispatched=True, completed=True, exit_code=1,
                        message=f"Not a directory: {p}", stderr=str(p),
                    )
            name_filter: str | None = None
            type_filter: str | None = None
            for i, part in enumerate(parts):
                if part == "-name" and i + 1 < len(parts):
                    name_filter = parts[i + 1]
                elif part == "-type" and i + 1 < len(parts):
                    type_filter = parts[i + 1]
                elif part in ("-iname",) and i + 1 < len(parts):
                    name_filter = parts[i + 1]  # simplified: -iname treated as -name
            name_re = re.compile(fnmatch_translate(name_filter)) if name_filter else None
            tree: list[str] = []
            limit = 500
            for p in paths:
                for f in sorted(p.rglob("*")):
                    if len(tree) >= limit:
                        tree.append(f"... truncated at {limit} entries")
                        break
                    if name_re and not name_re.match(f.name):
                        continue
                    if type_filter == "f" and not f.is_file():
                        continue
                    if type_filter == "d" and not f.is_dir():
                        continue
                    suffix = "/" if f.is_dir() else ""
                    tree.append(f"{f.relative_to(p)}{suffix}")
            out = "\n".join(tree)
            return DispatchReceipt(
                correlation_id=cid, dispatched=True, completed=True, exit_code=0,
                message=f"Found {len(tree)} entries across {len(paths)} director(ies).",
                stdout=out,
            )
        if cmd in {"grep", "rg"}:
            recursive = False
            ignore_case = False
            line_number = False
            files_only = False
            pattern: str | None = None
            for part in parts[1:]:
                if part in ("-r", "--recursive"):
                    recursive = True
                elif part in ("-i", "--ignore-case", "-y"):
                    ignore_case = True
                elif part in ("-n", "--line-number"):
                    line_number = True
                elif part in ("-l", "--files-with-matches"):
                    files_only = True
                elif not part.startswith("-"):
                    if pattern is None:
                        pattern = part
            if not pattern:
                return DispatchReceipt(
                    correlation_id=cid, dispatched=True, completed=True, exit_code=1,
                    message="No search pattern provided.",
                )
            flags = re.IGNORECASE if ignore_case else 0
            try:
                regex = re.compile(pattern, flags)
            except re.error as exc:
                return DispatchReceipt(
                    correlation_id=cid, dispatched=True, completed=True, exit_code=1,
                    message=f"Invalid regex: {exc}",
                )
            matches: list[str] = []
            limit = 200
            for search_path in paths:
                if search_path.is_file():
                    files_to_search = [search_path]
                elif search_path.is_dir():
                    if recursive:
                        files_to_search = sorted(
                            f for f in search_path.rglob("*") if f.is_file()
                        )
                    else:
                        files_to_search = sorted(
                            f for f in search_path.iterdir() if f.is_file()
                        )
                else:
                    continue
                for file_path in files_to_search:
                    if len(matches) >= limit:
                        matches.append(f"... truncated at {limit} matches")
                        break
                    try:
                        text = file_path.read_text(errors="replace")
                    except OSError:
                        continue
                    for i, line in enumerate(text.splitlines(), 1):
                        if regex.search(line):
                            if files_only:
                                matches.append(str(file_path))
                                break
                            elif line_number:
                                matches.append(f"{file_path}:{i}:{line}")
                            else:
                                matches.append(f"{file_path}:{line}")
                if len(matches) >= limit:
                    break
            out = "\n".join(matches)
            return DispatchReceipt(
                correlation_id=cid, dispatched=True, completed=True, exit_code=0,
                message=f"Found {len(matches)} match(s)." if not files_only
                        else f"{len(matches)} file(s) match.",
                stdout=out,
            )

        if cmd in {"head", "tail"}:
            n = 10
            for part in parts[1:]:
                if part.startswith("-") and part[1:].isdigit():
                    n = int(part[1:])
                    break
            lines_out: list[str] = []
            for p in paths:
                if not p.is_file():
                    return DispatchReceipt(
                        correlation_id=cid, dispatched=True, completed=True, exit_code=1,
                        message=f"Not a file: {p}", stderr=str(p),
                    )
                text = p.read_text(errors="replace")
                file_lines = text.splitlines()
                selected = file_lines[:n] if cmd == "head" else file_lines[-n:]
                lines_out.append(f"--- {p} ---")
                lines_out.extend(selected)
            out = "\n".join(lines_out)
            return DispatchReceipt(
                correlation_id=cid, dispatched=True, completed=True, exit_code=0,
                message=f"{cmd} of {len(paths)} file(s).", stdout=out,
            )
        if cmd == "wc":
            total_lines = 0
            total_words = 0
            total_chars = 0
            details: list[str] = []
            for p in paths:
                if not p.is_file():
                    return DispatchReceipt(
                        correlation_id=cid, dispatched=True, completed=True, exit_code=1,
                        message=f"Not a file: {p}", stderr=str(p),
                    )
                text = p.read_text(errors="replace")
                n_lines = text.count("\n")
                n_words = len(text.split())
                n_chars = len(text)
                total_lines += n_lines
                total_words += n_words
                total_chars += n_chars
                details.append(f"{n_lines:7} {n_words:7} {n_chars:7} {p}")
            out = "\n".join(details)
            if len(paths) > 1:
                out += f"\n{total_lines:7} {total_words:7} {total_chars:7} total"
            return DispatchReceipt(
                correlation_id=cid, dispatched=True, completed=True, exit_code=0,
                message=f"Counted {len(paths)} file(s).", stdout=out,
            )
        if cmd == "echo":
            out = " ".join(parts[1:])
            return DispatchReceipt(
                correlation_id=cid, dispatched=True, completed=True, exit_code=0,
                message=out[:200], stdout=out,
            )
        return DispatchReceipt(
            correlation_id=cid, dispatched=True, completed=True, exit_code=1,
            message=f"Unsupported command: {cmd}",
        )

    def capabilities(self) -> HostCapabilities:
        gh = shutil.which("gh")
        categories = {"github"} if gh else set()
        return HostCapabilities(
            can_dispatch=bool(categories),
            supported_categories=frozenset(categories),
        )

    def _is_github_type(self, proposal) -> bool:
        return (
            proposal.action_type is ActionType.GITHUB
            or proposal.tool_category == "github"
        )

    def dispatch(self, action: ApprovedAction) -> DispatchReceipt:
        verify_approved_action(action)
        proposal = action.proposal
        is_github_op = proposal.operation in self._GITHUB_ALL_OPS

        if proposal.action_type is ActionType.MCP and proposal.tool_category == "filesystem":
            if proposal.operation in {"inspect_repository", "read_repository"}:
                receipt = self._execute_mcp_inspect(action)
                self._log_if_failed(action, receipt)
                return receipt
            if proposal.operation == "write_document":
                receipt = self._execute_mcp_write_document(action)
                self._log_if_failed(action, receipt)
                return receipt

        if self._is_github_type(proposal):
            if shutil.which("gh") is None:
                self._log_error(
                    action=action,
                    error="gh CLI is not installed or not in PATH.",
                    category="missing_dependency",
                )
                return DispatchReceipt(
                    correlation_id=None,
                    dispatched=True,
                    completed=True,
                    exit_code=1,
                    message="gh CLI is not available.",
                    stderr="gh CLI is not installed or not in PATH.",
                    error_category="missing_dependency",
                )

        if proposal.action_type is ActionType.BASH and (
            proposal.tool_category == "filesystem" or proposal.tool_category in ("", "shell")
        ):
            cmd_parts = shlex.split(action.proposal.payload.get("command", ""))
            cmd = cmd_parts[0].lower() if cmd_parts else ""
            if cmd and cmd in self._ALLOWED_READ_COMMANDS:
                receipt = self._execute_local_read(action)
                self._log_if_failed(action, receipt)
                return receipt

        if not self._is_github_type(proposal):
            if is_github_op:
                self._log_error(
                    error=f"Operation '{proposal.operation}' matches a GitHub operation "
                          f"but action_type={proposal.action_type.value}, "
                          f"tool_category={proposal.tool_category}. Routing to standalone.",
                    category="mismatched_category",
                    action=action,
                )
            return self._standalone.dispatch(action)

        if proposal.operation == "authenticate_browser":
            return self._authenticate_github(action)

        if not is_github_op:
            self._log_error(
                action=action,
                error=f"Unknown GitHub operation '{proposal.operation}'.",
                category="missing_handler",
            )
            return self._standalone.dispatch(action)

        builder = self._GITHUB_READ_OPS.get(proposal.operation)
        if builder is not None:
            receipt = self._execute_github_read(action, builder)
            self._log_if_failed(action, receipt)
            return receipt
        handler = self._resolve_write_handler(proposal.operation)
        if handler is not None:
            scope_error = IntegrationHostBridge._check_github_scopes(proposal.operation)
            if scope_error is not None:
                self._log_if_failed(action, scope_error)
                return scope_error
            receipt = handler(self, action)
            self._log_if_failed(action, receipt)
            return receipt
        self._log_error(
            action=action,
            error=f"No handler registered for known GitHub operation '{proposal.operation}'.",
            category="missing_handler",
        )
        return self._standalone.dispatch(action)

    @staticmethod
    def _resolve_write_handler(
        operation: str,
    ) -> Callable[[IntegrationHostBridge, ApprovedAction], DispatchReceipt] | None:
        handlers: dict[str, Callable[[IntegrationHostBridge, ApprovedAction], DispatchReceipt]] = {
            "create_milestone": IntegrationHostBridge._dispatch_create_milestone,
            "create_issue": IntegrationHostBridge._dispatch_create_issue,
            "create_issues": IntegrationHostBridge._dispatch_create_issues,
            "create_issue_comment": IntegrationHostBridge._dispatch_create_issue_comment,
            "create_sub_issue": IntegrationHostBridge._dispatch_create_sub_issue,
            "setup_sprint": IntegrationHostBridge._dispatch_setup_sprint,
            "add_issue_to_project": IntegrationHostBridge._dispatch_add_issue_to_project,
            "create_project": IntegrationHostBridge._dispatch_create_project,
            "update_issue": IntegrationHostBridge._dispatch_update_issue,
            "update_milestone": IntegrationHostBridge._dispatch_update_milestone,
        }
        return handlers.get(operation)

    def _execute_mcp_inspect(self, action: ApprovedAction) -> DispatchReceipt:
        proposal = action.proposal
        path_str = proposal.payload.get("path") or self._repo_root
        if not path_str:
            return DispatchReceipt(
                correlation_id=action.proposal.id,
                dispatched=True,
                completed=True,
                exit_code=1,
                message="No repository path specified.",
            )
        repo_path = Path(path_str).resolve()
        if self._repo_root:
            allowed_root = Path(self._repo_root).resolve()
            if not str(repo_path).startswith(str(allowed_root)):
                return DispatchReceipt(
                    correlation_id=action.proposal.id,
                    dispatched=True,
                    completed=True,
                    exit_code=1,
                    message=f"Path '{repo_path}' is outside the allowed repository root.",
                    stderr=str(repo_path),
                )

        try:
            from pm_agent.config import default_db_path
            from pm_agent.infrastructure.repository.local_analyzer import LocalRepositoryAnalyzer
            from pm_agent.infrastructure.sqlite.store import SQLiteStore
            from pm_agent.ports.repository_context import SnapshotRequest

            analyzer = LocalRepositoryAnalyzer()
            branch = analyzer._branch_name(repo_path) or "unknown"

            store = SQLiteStore(default_db_path())
            project = store.resolve_project(repo_path, branch)

            snapshot = analyzer.build_snapshot(
                SnapshotRequest(
                    project_id=project.id,
                    repo_path=str(repo_path),
                    branch=branch,
                    action_id=proposal.id,
                )
            )
            store.save_snapshot(snapshot)

            digest = snapshot.tree_digest[:12]
            return DispatchReceipt(
                correlation_id=action.proposal.id,
                dispatched=True,
                completed=True,
                exit_code=0,
                message=f"Repository snapshot stored: {digest}",
                stdout=f"Repository snapshot stored: {digest}",
                result={"snapshot_digest": snapshot.tree_digest},
            )
        except Exception as exc:
            return DispatchReceipt(
                correlation_id=action.proposal.id,
                dispatched=True,
                completed=True,
                exit_code=1,
                message=f"Local repository inspection failed: {exc}",
                stderr=str(exc),
            )

    _DOCUMENT_EXTENSIONS = frozenset({".md", ".txt", ".json", ".yaml", ".yml", ".rst"})

    def _execute_mcp_write_document(self, action: ApprovedAction) -> DispatchReceipt:
        proposal = action.proposal
        path_str = str(proposal.payload.get("path", "")).strip()
        content = str(proposal.payload.get("content", ""))
        if not path_str:
            return DispatchReceipt(
                correlation_id=action.proposal.id,
                dispatched=True,
                completed=True,
                exit_code=1,
                message="write_document requires a 'path' in the payload.",
                error_category="invalid_payload",
            )
        target = Path(path_str)
        if not target.is_absolute() and self._repo_root:
            target = Path(self._repo_root).resolve() / target
        target = target.resolve()
        if self._repo_root:
            allowed_root = Path(self._repo_root).resolve()
            if not str(target).startswith(str(allowed_root)):
                return DispatchReceipt(
                    correlation_id=action.proposal.id,
                    dispatched=True,
                    completed=True,
                    exit_code=1,
                    message=f"Document path '{target}' is outside the allowed repository root.",
                    stderr=str(target),
                    error_category="path_outside_root",
                )
        if target.suffix.lower() not in self._DOCUMENT_EXTENSIONS:
            return DispatchReceipt(
                correlation_id=action.proposal.id,
                dispatched=True,
                completed=True,
                exit_code=1,
                message=(
                    f"write_document only allows {sorted(self._DOCUMENT_EXTENSIONS)} "
                    f"files, got '{target.suffix}'."
                ),
                error_category="invalid_extension",
            )
        try:
            from pm_agent.infrastructure.security.redaction import redact_text

            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(redact_text(content), encoding="utf-8")
            return DispatchReceipt(
                correlation_id=action.proposal.id,
                dispatched=True,
                completed=True,
                exit_code=0,
                message=f"Document written: {target}",
                stdout=f"Document written: {target}",
                result={"path": str(target)},
            )
        except Exception as exc:
            return DispatchReceipt(
                correlation_id=action.proposal.id,
                dispatched=True,
                completed=True,
                exit_code=1,
                message=f"Document write failed: {exc}",
                stderr=str(exc),
                error_category="write_failed",
            )

    def _log_error(
        self,
        action: ApprovedAction | None = None,
        error: str = "",
        category: str = "action_failure",
        exception: BaseException | None = None,
        exit_code: int | None = None,
        retryable: bool = False,
        user_message: str = "",
    ) -> None:
        if self._error_logger is None:
            return
        p = action.proposal if action else None
        self._error_logger.log_failure(
            action_id=p.id if p else "",
            action_type=p.action_type.value if p else "",
            action_status_before=p.status.value if p else "",
            action_status_after="failed",
            executor="IntegrationHostBridge",
            target_repository=(p.payload or {}).get("repository", "") if p else "",
            payload=p.payload if p else None,
            error=error,
            exception=exception,
            exit_code=exit_code,
            retryable=retryable,
            user_message=user_message,
            category=category,
        )

    def _log_if_failed(self, action: ApprovedAction, receipt: DispatchReceipt) -> None:
        if receipt.exit_code and receipt.exit_code != 0:
            error_text = receipt.stderr or receipt.message
            is_scope_error = (
                receipt.error_category == "missing_scope"
                or "missing required scopes" in error_text
            )
            self._log_error(
                action=action,
                error=error_text,
                exit_code=receipt.exit_code,
                category="action_failure",
                retryable=is_scope_error,
                user_message=receipt.message,
            )

    @staticmethod
    def _run_gh(
        args: list[str],
        input_data: str | None = None,
        timeout: int = 30,
    ) -> tuple[int | None, str, str]:
        executable = shutil.which("gh")
        if executable is None:
            return None, "", "gh: command not found"
        try:
            completed = subprocess.run(
                [executable, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                input=input_data,
            )
        except subprocess.TimeoutExpired:
            return 124, "", "Command timed out."
        except OSError as exc:
            return -1, "", str(exc)
        return completed.returncode, completed.stdout.strip(), completed.stderr.strip()

    @staticmethod
    def _authenticate_github(action: ApprovedAction) -> DispatchReceipt:
        executable = shutil.which("gh")
        if executable is None:
            return DispatchReceipt(
                correlation_id=None,
                dispatched=False,
                message="GitHub CLI (`gh`) is not installed or not available on PATH. "
                        "Install it from https://cli.github.com/ then try /connect github again.",
            )
        payload = action.proposal.payload
        hostname = str(payload.get("hostname", "github.com"))
        if hostname != "github.com":
            return DispatchReceipt(
                correlation_id=None,
                dispatched=False,
                message=f"Only github.com is supported for authentication, got '{hostname}'.",
            )
        command = [
            executable,
            "auth",
            "login",
            "--hostname", hostname,
            "--git-protocol",
            str(payload.get("git_protocol", "https")),
            "--web",
            "--skip-ssh-key",
        ]
        correlation_id = uuid4().hex
        try:
            completed = subprocess.run(command, check=False).returncode
        except KeyboardInterrupt:
            return DispatchReceipt(
                correlation_id=correlation_id,
                dispatched=True,
                completed=True,
                exit_code=130,
                message="GitHub browser authentication was cancelled by the user.",
                stderr="Authentication cancelled by user. Use /connect github to retry.",
            )
        except OSError as exc:
            return DispatchReceipt(
                correlation_id=correlation_id,
                dispatched=True,
                completed=True,
                exit_code=-1,
                message=f"GitHub CLI failed to launch: {exc}",
                stderr=str(exc),
            )

        if completed != 0:
            return DispatchReceipt(
                correlation_id=correlation_id,
                dispatched=True,
                completed=True,
                exit_code=completed,
                message="GitHub browser authentication did not complete successfully. "
                        "Try running 'gh auth login' manually or check your browser.",
                stderr="GitHub CLI authentication returned a non-zero exit code. "
                       "Ensure you complete the browser flow within the timeout.",
            )
        account = subprocess.run(
            [executable, "api", "user", "--jq", ".login"],
            check=False,
            capture_output=True,
            text=True,
        )
        username = account.stdout.strip() if account.returncode == 0 else ""
        if not username:
            return DispatchReceipt(
                correlation_id=correlation_id,
                dispatched=True,
                completed=True,
                exit_code=1,
                message="GitHub auth completed but could not verify account. "
                        "Run 'gh auth status' to check.",
                stderr="Failed to retrieve GitHub username after auth.",
            )
        return DispatchReceipt(
            correlation_id=correlation_id,
            dispatched=True,
            completed=True,
            exit_code=0,
            message=(
                f"GitHub connected as {username}. "
                f"Authenticated via browser flow with credential storage in GitHub CLI."
            ),
            result={
                "hostname": hostname,
                "authenticated": True,
                "account": username,
                "credential_storage": "github_cli",
            },
        )

    @staticmethod
    def _check_github_scopes(operation: str) -> DispatchReceipt | None:
        """Preflight permission check based on the integration capability model.

        Returns a clear, user-action-required failure when the current token does
        not grant the capabilities an action needs. Returns None when the action
        needs no capabilities, `gh` is unavailable, or granted scopes cannot be
        determined (in which case failures surface at execution time instead).
        """
        required = required_capabilities(operation)
        if not required:
            return None
        executable = shutil.which("gh")
        if executable is None:
            return None
        try:
            completed = subprocess.run(
                [executable, "auth", "status"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (subprocess.TimeoutExpired, OSError):
            return None
        output = completed.stdout + completed.stderr
        scopes = parse_token_scopes(output)
        if scopes is None:
            # Could not determine granted scopes; rely on runtime error classification.
            return None
        granted = capabilities_from_scopes(scopes)
        missing = required - granted
        if not missing:
            return None
        needed_scopes = scopes_for_capabilities(missing)
        scopes_str = ",".join(sorted(needed_scopes))
        missing_caps = ", ".join(sorted(cap.value for cap in missing))
        return DispatchReceipt(
            correlation_id=None,
            dispatched=True,
            completed=True,
            exit_code=1,
            error_category="missing_scope",
            message=(
                f"GitHub action '{operation}' requires capabilities [{missing_caps}] "
                f"which are not granted by the current token.\n"
                f"Fix: Run:  gh auth refresh -s {scopes_str}"
            ),
            stderr=f"missing required scopes [{scopes_str}]",
        )

    @staticmethod
    def _execute_github_read(
        action: ApprovedAction,
        args_builder: Callable[[dict], list[str]],
    ) -> DispatchReceipt:
        scope_error = IntegrationHostBridge._check_github_scopes(action.proposal.operation)
        if scope_error is not None:
            return scope_error
        executable = shutil.which("gh")
        if executable is None:
            return DispatchReceipt(
                correlation_id=None,
                dispatched=True,
                completed=True,
                exit_code=1,
                message="GitHub read operation requires `gh` CLI which is not available.",
                stderr="gh: command not found",
            )
        payload = action.proposal.payload
        try:
            gh_args = args_builder(payload)
        except (KeyError, IndexError) as exc:
            return DispatchReceipt(
                correlation_id=None,
                dispatched=True,
                completed=True,
                exit_code=1,
                message=f"GitHub read action missing required payload field: {exc}",
                stderr=str(exc),
            )
        correlation_id = uuid4().hex
        try:
            completed = subprocess.run(
                [executable, *gh_args],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            return DispatchReceipt(
                correlation_id=correlation_id,
                dispatched=True,
                completed=True,
                exit_code=124,
                message=f"GitHub read action '{action.proposal.operation}' timed out after 30s.",
                stderr="Command timed out.",
            )
        except OSError as exc:
            return DispatchReceipt(
                correlation_id=correlation_id,
                dispatched=True,
                completed=True,
                exit_code=-1,
                message=f"GitHub CLI failed to launch: {exc}",
                stderr=str(exc),
            )

        stdout = completed.stdout.strip()
        stderr = completed.stderr.strip()
        if completed.returncode != 0:
            msg = f"GitHub read action '{action.proposal.operation}' failed (exit {completed.returncode})."
            text = f"{msg}\n{stderr}".lower()
            permission_failure = (
                "missing required scopes" in stderr
                or "http 401" in text
                or "http 403" in text
                or "permission denied" in text
                or "unauthorized" in text
                or "forbidden" in text
            )
            if "missing required scopes" in stderr:
                m = re.search(r"missing required scopes \[([^\]]+)\]", stderr)
                scopes = m.group(1) if m else "unknown"
                scopes = scopes.replace(" ", ",")
                msg += (
                    f"\nThe GitHub token is missing required scopes: [{scopes}].\n"
                    f"Fix: Run:  gh auth refresh -s {scopes}"
                )
            return DispatchReceipt(
                correlation_id=correlation_id,
                dispatched=True,
                completed=True,
                exit_code=completed.returncode,
                error_category="missing_scope" if permission_failure else None,
                message=msg,
                stdout=stdout,
                stderr=stderr,
            )

        result: dict = {}
        if stdout:
            try:
                result = json.loads(stdout)
            except json.JSONDecodeError:
                result = {"raw": stdout}

        return DispatchReceipt(
            correlation_id=correlation_id,
            dispatched=True,
            completed=True,
            exit_code=0,
            message=f"GitHub read action '{action.proposal.operation}' completed successfully.",
            stdout=stdout,
            stderr=stderr,
            result=result,
        )

    @staticmethod
    def _normalize_due_on(value: str) -> str:
        if value and len(value) == 10 and value.count("-") == 2:
            return f"{value}T00:00:00Z"
        return value

    @staticmethod
    def _make_receipt(
        action: ApprovedAction,
        rc: int | None,
        stdout: str,
        stderr: str,
        correlation_id: str,
        success_msg: str,
        failure_msg: str | None = None,
        result: dict[str, Any] | None = None,
    ) -> DispatchReceipt:
        operation = action.proposal.operation
        if rc is None:
            return DispatchReceipt(
                correlation_id=correlation_id,
                dispatched=True,
                completed=True,
                exit_code=1,
                message="gh CLI is not available.",
                stderr=stderr,
            )
        if rc != 0:
            msg = failure_msg or f"GitHub action '{operation}' failed (exit {rc})."
            text = f"{msg}\n{stderr}".lower()
            permission_failure = (
                "missing required scopes" in stderr
                or "http 401" in text
                or "http 403" in text
                or "permission denied" in text
                or "unauthorized" in text
                or "forbidden" in text
            )
            if "missing required scopes" in stderr:
                m = re.search(r"missing required scopes \[([^\]]+)\]", stderr)
                scopes = m.group(1) if m else "unknown"
                scopes = scopes.replace(" ", ",")
                msg += (
                    f"\nThe GitHub token is missing required scopes: [{scopes}].\n"
                    f"Fix: Run:  gh auth refresh -s {scopes}"
                )
            return DispatchReceipt(
                correlation_id=correlation_id,
                dispatched=True,
                completed=True,
                exit_code=rc,
                error_category="missing_scope" if permission_failure else None,
                message=msg,
                stdout=stdout,
                stderr=stderr,
            )
        parsed: dict[str, Any] = result or {}
        if not parsed and stdout:
            try:
                parsed = json.loads(stdout)
            except json.JSONDecodeError:
                parsed = {"raw": stdout}
        return DispatchReceipt(
            correlation_id=correlation_id,
            dispatched=True,
            completed=True,
            exit_code=0,
            message=success_msg,
            stdout=stdout,
            stderr=stderr,
            result=parsed,
        )

    @staticmethod
    def _gh_api_post(
        endpoint: str,
        body: dict[str, Any],
        timeout: int = 30,
    ) -> tuple[int | None, str, str]:
        return IntegrationHostBridge._run_gh(
            ["api", endpoint, "--method", "POST", "--input", "-"],
            input_data=json.dumps(body),
            timeout=timeout,
        )

    @staticmethod
    def _gh_api_patch(
        endpoint: str,
        body: dict[str, Any],
        timeout: int = 30,
    ) -> tuple[int | None, str, str]:
        return IntegrationHostBridge._run_gh(
            ["api", endpoint, "--method", "PATCH", "--input", "-"],
            input_data=json.dumps(body),
            timeout=timeout,
        )

    @staticmethod
    def _gh_api_graphql(
        query: str,
        variables: dict[str, Any] | None = None,
        timeout: int = 30,
    ) -> tuple[int | None, str, str]:
        args = ["api", "graphql", "-f", f"query={query}"]
        if variables:
            for key, value in variables.items():
                if isinstance(value, bool):
                    args.extend(["-F", f"{key}={str(value).lower()}"])
                elif isinstance(value, int):
                    args.extend(["-F", f"{key}={value}"])
                else:
                    args.extend(["-f", f"{key}={value}"])
        return IntegrationHostBridge._run_gh(args, timeout=timeout)

    @staticmethod
    def _dispatch_create_milestone(
        self: IntegrationHostBridge, action: ApprovedAction
    ) -> DispatchReceipt:
        payload = action.proposal.payload
        repo = payload["repository"]
        m = payload.get("milestone", payload.get("sprint", {}))
        title = m.get("title", "")
        body: dict[str, Any] = {"title": title}
        description = m.get("description", m.get("goal"))
        if description:
            body["description"] = description
        due_on = m.get("due_on", m.get("end_date"))
        if due_on:
            body["due_on"] = self._normalize_due_on(due_on)
        cid = uuid4().hex
        rc, stdout, stderr = self._gh_api_post(f"repos/{repo}/milestones", body)
        return self._make_receipt(
            action, rc, stdout, stderr, cid,
            success_msg=f"Milestone '{title}' created.",
            failure_msg=f"Failed to create milestone '{title}'.",
        )

    @staticmethod
    def _resolve_milestone_titles(
        repo: str, titles: set[str],
    ) -> dict[str, int]:
        if not titles:
            return {}
        rc, stdout, stderr = IntegrationHostBridge._run_gh([
            "api", f"repos/{repo}/milestones?state=all&per_page=100",
        ])
        if rc != 0 or not stdout:
            return {}
        try:
            milestones = json.loads(stdout)
        except json.JSONDecodeError:
            return {}
        search_map = {t.strip().lower(): t for t in titles}
        resolved: dict[str, int] = {}
        for m in milestones:
            title = m.get("title")
            if not title:
                continue
            norm_title = title.strip().lower()
            if norm_title in search_map:
                resolved[search_map[norm_title]] = m["number"]
        return resolved

    @staticmethod
    def _dispatch_create_issue(
        self: IntegrationHostBridge, action: ApprovedAction
    ) -> DispatchReceipt:
        payload = action.proposal.payload
        repo = payload["repository"]
        issue = payload.get("issue", payload)
        title = issue.get("title", "")
        body: dict[str, Any] = {"title": title}
        if issue.get("body"):
            body["body"] = issue["body"]
        if issue.get("labels"):
            body["labels"] = issue["labels"]
        ms = issue.get("milestone")
        if ms is not None:
            if isinstance(ms, str):
                resolved = self._resolve_milestone_titles(repo, {ms})
                resolved_num = resolved.get(ms)
                if resolved_num is None:
                    return DispatchReceipt(
                        correlation_id=uuid4().hex, dispatched=True, completed=True,
                        exit_code=1,
                        message=f"Milestone '{ms}' not found. Create it first or use its number.",
                        stderr=f"Milestone title '{ms}' could not be resolved to a number.",
                    )
                body["milestone"] = resolved_num
            else:
                body["milestone"] = ms
        cid = uuid4().hex
        rc, stdout, stderr = self._gh_api_post(f"repos/{repo}/issues", body)
        return self._make_receipt(
            action, rc, stdout, stderr, cid,
            success_msg=f"Issue '{title}' created.",
            failure_msg=f"Failed to create issue '{title}'.",
        )

    @staticmethod
    def _dispatch_create_issue_comment(
        self: IntegrationHostBridge, action: ApprovedAction
    ) -> DispatchReceipt:
        payload = action.proposal.payload
        repo = payload["repository"]
        issue_number = payload.get("issue_number")
        body = payload.get("body", "")
        if not issue_number:
            return DispatchReceipt(
                correlation_id=uuid4().hex, dispatched=True, completed=True,
                exit_code=1,
                message="create_issue_comment requires 'issue_number' and 'body'.",
                error_category="invalid_payload",
            )
        cid = uuid4().hex
        rc, stdout, stderr = self._gh_api_post(
            f"repos/{repo}/issues/{issue_number}/comments", {"body": body}
        )
        return self._make_receipt(
            action, rc, stdout, stderr, cid,
            success_msg=f"Comment posted on issue #{issue_number}.",
            failure_msg=f"Failed to post comment on issue #{issue_number}.",
        )

    @staticmethod
    def _dispatch_create_sub_issue(
        self: IntegrationHostBridge, action: ApprovedAction
    ) -> DispatchReceipt:
        payload = action.proposal.payload
        repo = payload["repository"]
        parent = payload.get("parent")
        title = payload.get("title", "")
        body = payload.get("body", "")
        if not parent:
            return DispatchReceipt(
                correlation_id=uuid4().hex, dispatched=True, completed=True,
                exit_code=1,
                message="create_sub_issue requires 'parent' (issue number) and 'title'.",
                error_category="invalid_payload",
            )
        cid = uuid4().hex
        rc, stdout, stderr = self._gh_api_post(
            f"repos/{repo}/issues", {"title": title, "body": body}
        )
        if rc != 0:
            return self._make_receipt(
                action, rc, stdout, stderr, cid,
                success_msg="", failure_msg=f"Failed to create sub-issue '{title}'.",
            )
        try:
            created = json.loads(stdout) if stdout else {}
            number = created.get("number")
        except json.JSONDecodeError:
            number = None
        if number is not None:
            link_rc, link_stdout, link_stderr = self._gh_api_post(
                f"repos/{repo}/issues/{number}/sub_issues",
                {"parent_issue_number": parent},
            )
            if link_rc != 0:
                return self._make_receipt(
                    action, link_rc, link_stdout, link_stderr, cid,
                    success_msg=f"Sub-issue #{number} created (link to parent #{parent} failed).",
                    failure_msg=f"Sub-issue #{number} created but linking to #{parent} failed.",
                )
        return self._make_receipt(
            action, rc, stdout, stderr, cid,
            success_msg=f"Sub-issue '{title}' created under #{parent}.",
            failure_msg=f"Failed to create sub-issue '{title}'.",
        )

    @staticmethod
    def _dispatch_create_issues(
        self: IntegrationHostBridge, action: ApprovedAction
    ) -> DispatchReceipt:
        payload = action.proposal.payload
        repo = payload["repository"]
        issues = payload.get("issues", [])
        milestone_titles = {
            iss["milestone"] for iss in issues
            if isinstance(iss.get("milestone"), str)
        }
        milestone_map = self._resolve_milestone_titles(repo, milestone_titles)
        cid = uuid4().hex
        created: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for issue in issues:
            title = issue.get("title", "")
            body: dict[str, Any] = {"title": title}
            if issue.get("body"):
                body["body"] = issue["body"]
            if issue.get("labels"):
                body["labels"] = issue["labels"]
            ms = issue.get("milestone")
            if ms is not None:
                if isinstance(ms, str):
                    resolved_num = milestone_map.get(ms)
                    if resolved_num is None:
                        errors.append({
                            "title": title, "error": f"Milestone '{ms}' not found. Create it first or use its number.",
                        })
                        continue
                    body["milestone"] = resolved_num
                else:
                    body["milestone"] = ms
            rc, stdout, stderr = self._gh_api_post(f"repos/{repo}/issues", body)
            if rc == 0 and stdout:
                result = json.loads(stdout)
                created.append({
                    "number": result.get("number"),
                    "title": title,
                    "url": result.get("html_url"),
                })
            else:
                errors.append({"title": title, "error": stderr or "Unknown error"})
        total = len(issues)
        success_count = len(created)
        fail_count = len(errors)
        result = {"created": created, "errors": errors, "total": total, "failed": fail_count}
        if success_count == total:
            return DispatchReceipt(
                correlation_id=cid, dispatched=True, completed=True, exit_code=0,
                message=f"All {total} issues created.",
                stdout=json.dumps(created), result=result,
            )
        if success_count > 0:
            return DispatchReceipt(
                correlation_id=cid, dispatched=True, completed=True, exit_code=1,
                message=f"{success_count}/{total} issues created ({fail_count} failed).",
                stdout=json.dumps(created), stderr=json.dumps(errors), result=result,
            )
        return DispatchReceipt(
            correlation_id=cid, dispatched=True, completed=True, exit_code=1,
            message=f"Failed to create {total} issues.",
            stderr=json.dumps(errors), result=result,
        )

    @staticmethod
    def _dispatch_setup_sprint(
        self: IntegrationHostBridge, action: ApprovedAction
    ) -> DispatchReceipt:
        payload = action.proposal.payload
        repo = payload["repository"]
        sprint = payload.get("sprint", payload.get("milestone", {}))
        title = sprint.get("title", "")
        body: dict[str, Any] = {"title": title}
        description_parts: list[str] = []
        goal = sprint.get("goal")
        if goal:
            description_parts.append(f"Goal: {goal}")
        if sprint.get("start_date"):
            description_parts.append(f"Start: {sprint['start_date']}")
        if sprint.get("end_date"):
            description_parts.append(f"End: {sprint['end_date']}")
            body["due_on"] = self._normalize_due_on(sprint["end_date"])
        if description_parts:
            body["description"] = "\n".join(description_parts)
        cid = uuid4().hex
        rc, stdout, stderr = self._gh_api_post(f"repos/{repo}/milestones", body)
        return self._make_receipt(
            action, rc, stdout, stderr, cid,
            success_msg=f"Sprint milestone '{title}' created.",
            failure_msg=f"Failed to create sprint milestone '{title}'.",
        )

    @staticmethod
    def _dispatch_add_issue_to_project(
        self: IntegrationHostBridge, action: ApprovedAction
    ) -> DispatchReceipt:
        payload = action.proposal.payload
        repo = payload["repository"]
        owner, repo_name = repo.split("/")
        project_number = payload.get("project_number")
        project_title = payload.get("project_title")
        issue_numbers = payload.get("issue_numbers", [])
        cid = uuid4().hex
        project_id, get_err = self._resolve_project_node_id(owner, project_number, project_title)
        if project_id is None:
            return DispatchReceipt(
                correlation_id=cid, dispatched=True, completed=True, exit_code=1,
                message=get_err or "Project not found.",
                stderr=get_err or "",
            )
        items: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        for num in issue_numbers:
            issue_id, err = self._resolve_issue_node_id(owner, repo_name, num)
            if issue_id is None:
                errors.append({"issue_number": num, "error": err or "Issue not found"})
                continue
            add_query = (
                "mutation($project:ID!$issue:ID!){"
                "addProjectV2ItemById(input:{projectId:$project contentId:$issue}){"
                "item{id}}}"
            )
            rc, stdout, stderr = self._gh_api_graphql(
                add_query, {"project": project_id, "issue": issue_id},
            )
            if rc == 0 and stdout:
                data = json.loads(stdout)
                item_id = (data.get("data", {}).get("addProjectV2ItemById", {})
                           .get("item", {}).get("id", ""))
                items.append({"issue_number": num, "item_id": item_id, "status": "added"})
            else:
                errors.append({"issue_number": num, "error": stderr or "Add failed"})
        total = len(issue_numbers)
        success_count = len(items)
        result = {"added": items, "errors": errors, "total": total, "failed": len(errors)}
        if success_count == total:
            return DispatchReceipt(
                correlation_id=cid, dispatched=True, completed=True, exit_code=0,
                message=f"All {total} issues added to project.",
                result=result,
            )
        if success_count > 0:
            return DispatchReceipt(
                correlation_id=cid, dispatched=True, completed=True, exit_code=1,
                message=f"{success_count}/{total} issues added to project ({len(errors)} failed).",
                stderr=json.dumps(errors), result=result,
            )
        return DispatchReceipt(
            correlation_id=cid, dispatched=True, completed=True, exit_code=1,
            message=f"Failed to add {total} issues to project.",
            stderr=json.dumps(errors), result=result,
        )

    @staticmethod
    def _resolve_project_node_id(
        owner: str, number: int | None, title: str | None,
    ) -> tuple[str | None, str | None]:
        if number is not None:
            return IntegrationHostBridge._resolve_project_node_id_by_number(owner, number)
        if title:
            return None, f"Project lookup by title ('{title}') is not supported; use project_number."
        return None, "No project_number or project_title provided."

    @staticmethod
    def _resolve_project_node_id_by_number(
        owner: str, number: int,
    ) -> tuple[str | None, str | None]:
        query = (
            "query($login:String!$number:Int!){"
            "organization(login:$login){projectV2(number:$number){id}}"
            "}"
        )
        rc, stdout, stderr = IntegrationHostBridge._gh_api_graphql(
            query, {"login": owner, "number": number},
        )
        if rc == 0 and stdout:
            data = json.loads(stdout)
            org_result = data.get("data", {}).get("organization", {}).get("projectV2")
            if org_result and org_result.get("id"):
                return org_result["id"], None
        query_user = (
            "query($login:String!$number:Int!){"
            "user(login:$login){projectV2(number:$number){id}}"
            "}"
        )
        rc, stdout, stderr = IntegrationHostBridge._gh_api_graphql(
            query_user, {"login": owner, "number": number},
        )
        if rc == 0 and stdout:
            data = json.loads(stdout)
            user_result = data.get("data", {}).get("user", {}).get("projectV2")
            if user_result and user_result.get("id"):
                return user_result["id"], None
        return None, stderr or "Project not found."

    @staticmethod
    def _resolve_issue_node_id(
        owner: str, repo: str, number: int,
    ) -> tuple[str | None, str | None]:
        query = (
            "query($owner:String!$repo:String!$number:Int!){"
            "repository(owner:$owner name:$repo){"
            "issue(number:$number){id}}}"
        )
        rc, stdout, stderr = IntegrationHostBridge._gh_api_graphql(
            query, {"owner": owner, "repo": repo, "number": number},
        )
        if rc == 0 and stdout:
            data = json.loads(stdout)
            issue = (data.get("data", {}).get("repository", {}).get("issue"))
            if issue and issue.get("id"):
                return issue["id"], None
        return None, stderr or "Issue not found."

    @staticmethod
    def _dispatch_create_project(
        self: IntegrationHostBridge, action: ApprovedAction
    ) -> DispatchReceipt:
        payload = action.proposal.payload
        repo = payload["repository"]
        owner = repo.split("/")[0]
        project_name = payload.get("name", "")
        if not project_name:
            project_name = payload.get("project", {}).get("name", "")
        if not project_name:
            project_name = payload.get("title", "Untitled Project")
        cid = uuid4().hex
        rc, stdout, stderr = IntegrationHostBridge._run_gh([
            "project", "create", "--owner", owner,
            "--title", project_name,
        ])
        return IntegrationHostBridge._make_receipt(
            action, rc, stdout, stderr, cid,
            success_msg=f"Project '{project_name}' created.",
            failure_msg=f"Failed to create project '{project_name}'.",
        )

    @staticmethod
    def _dispatch_update_issue(
        self: IntegrationHostBridge, action: ApprovedAction
    ) -> DispatchReceipt:
        payload = action.proposal.payload
        repo = payload["repository"]
        issue = payload.get("issue", {})
        number = issue.get("number", payload.get("issue_number"))
        if not number:
            return DispatchReceipt(
                correlation_id=None, dispatched=True, completed=True, exit_code=1,
                message="update_issue requires an issue.number or issue_number in the payload.",
            )
        body: dict[str, Any] = {}
        if issue.get("title"):
            body["title"] = issue["title"]
        if issue.get("body"):
            body["body"] = issue["body"]
        if issue.get("state"):
            body["state"] = issue["state"]
        if issue.get("labels"):
            body["labels"] = issue["labels"]
        if issue.get("milestone"):
            body["milestone"] = issue["milestone"]
        cid = uuid4().hex
        rc, stdout, stderr = self._gh_api_patch(f"repos/{repo}/issues/{number}", body)
        title = issue.get("title", f"#{number}")
        return self._make_receipt(
            action, rc, stdout, stderr, cid,
            success_msg=f"Issue {title} updated.",
            failure_msg=f"Failed to update issue {title}.",
        )

    @staticmethod
    def _dispatch_update_milestone(
        self: IntegrationHostBridge, action: ApprovedAction
    ) -> DispatchReceipt:
        payload = action.proposal.payload
        repo = payload["repository"]
        m = payload.get("milestone", {})
        number = m.get("number", payload.get("milestone_number"))
        if not number:
            return DispatchReceipt(
                correlation_id=None, dispatched=True, completed=True, exit_code=1,
                message="update_milestone requires a milestone.number or milestone_number in payload.",
            )
        body: dict[str, Any] = {}
        if m.get("title"):
            body["title"] = m["title"]
        if m.get("description"):
            body["description"] = m["description"]
        if m.get("state"):
            body["state"] = m["state"]
        if m.get("due_on"):
            body["due_on"] = m["due_on"]
        cid = uuid4().hex
        rc, stdout, stderr = self._gh_api_patch(f"repos/{repo}/milestones/{number}", body)
        title = m.get("title", f"#{number}")
        return self._make_receipt(
            action, rc, stdout, stderr, cid,
            success_msg=f"Milestone {title} updated.",
            failure_msg=f"Failed to update milestone {title}.",
        )
