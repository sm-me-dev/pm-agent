from __future__ import annotations

import re
from pathlib import Path
from typing import NamedTuple

MENTION_PATTERN = re.compile(r'@["\']?([^"\'\s&]+)')
_BINARY_CHECK_SIZE = 8192
_MAX_CONTENT_LENGTH = 50_000
_MAX_FILES = 10


class Mention(NamedTuple):
    raw: str
    path: str


def _looks_binary(path: Path) -> bool:
    try:
        with path.open("rb") as f:
            chunk = f.read(_BINARY_CHECK_SIZE)
        return b"\0" in chunk
    except OSError:
        return False


def extract_mentions(text: str) -> list[Mention]:
    mentions = []
    for match in MENTION_PATTERN.finditer(text):
        raw = match.group(0)
        path = match.group(1)
        if (path.startswith('"') and path.endswith('")')) or (
            path.startswith("'") and path.endswith("'")
        ):
            path = path[1:-1]
        elif path.startswith('"') or path.startswith("'"):
            path = path[1:]
        elif path.endswith('"') or path.endswith("'"):
            path = path[:-1]
        mentions.append(Mention(raw=raw, path=path))
    return mentions


def read_mentioned_files(
    mentions: list[Mention],
    project_root: Path | str,
    max_content_length: int = _MAX_CONTENT_LENGTH,
    max_files: int = _MAX_FILES,
) -> tuple[dict[str, str], dict[str, str]]:
    root = Path(project_root) if isinstance(project_root, str) else project_root
    contents: dict[str, str] = {}
    warnings: dict[str, str] = {}

    for mention in mentions[:max_files]:
        file_path = root / mention.path

        try:
            resolved = file_path.resolve()
            if not str(resolved).startswith(str(root.resolve())):
                warnings[mention.path] = "Path escapes project root; skipped."
                continue
        except (OSError, ValueError):
            warnings[mention.path] = "Cannot resolve path; skipped."
            continue

        if not file_path.exists() or not file_path.is_file():
            warnings[mention.path] = "File not found."
            continue

        if _looks_binary(file_path):
            warnings[mention.path] = "Binary or unreadable file; content not attached."
            continue

        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
            if len(content) > max_content_length:
                content = content[:max_content_length] + "\n... [truncated]"
            contents[mention.path] = content
        except (OSError, PermissionError):
            warnings[mention.path] = "Cannot read file (permission or I/O error)."

    return contents, warnings


def format_mentioned_files(contents: dict[str, str], warnings: dict[str, str]) -> str | None:
    if not contents and not warnings:
        return None

    parts = ["Attached File Context:"]
    for path, content in contents.items():
        parts.append(f"--- BEGIN FILE: {path} ---")
        parts.append(content)
        parts.append(f"--- END FILE: {path} ---")
    for path, reason in warnings.items():
        if path in contents:
            continue
        parts.append(f"--- FILE WARNING: {path} ---")
        parts.append(reason)
        parts.append("--- END FILE WARNING ---")

    return "\n".join(parts)


def build_mention_payload(user_text: str, project_root: Path | str) -> str:
    mentions = extract_mentions(user_text)
    if not mentions:
        return user_text

    contents, warnings = read_mentioned_files(mentions, project_root)
    file_block = format_mentioned_files(contents, warnings)

    if file_block:
        return f"User Request:\n{user_text}\n\n{file_block}"
    return user_text
