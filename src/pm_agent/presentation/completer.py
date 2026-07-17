"""Prompt-toolkit completer for @ mentions and / commands.

Provides autocomplete for file references (@path) and slash commands (/cmd).
"""
from __future__ import annotations

from collections.abc import Sequence

from prompt_toolkit.completion import Completer, Completion, WordCompleter

from pm_agent.presentation.file_scanner import LazyFileIndex

# Available slash commands
SLASH_COMMANDS: list[str] = [
    "/help",
    "/status",
    "/history",
    "/clear",
    "/summary",
    "/quit",
    "/q",
    "/actions",
    "/approve",
    "/reject",
    "/retry",
    "/action-result",
    "/decisions",
    "/decision",
    "/accept",
    "/reject-decision",
    "/integrations",
    "/integration",
    "/connect",
    "/refresh",
    "/permissions",
    "/permission",
    "/perm",
    "/perms",
    "/perm-revoke",
]


class PMCompleter(Completer):
    """Custom completer that handles @ and / prefixes.

    - @ triggers file path autocomplete from project directory
    - / triggers slash command autocomplete
    """

    def __init__(
        self,
        file_index: LazyFileIndex,
        commands: Sequence[str] = SLASH_COMMANDS,
    ) -> None:
        self._file_index = file_index
        self._commands = list(commands)
        # Word completer for slash commands (fallback)
        self._word_completer = WordCompleter(
            self._commands,
            ignore_case=True,
            match_middle=True,
        )

    def get_completions(self, document, complete_event):
        """Get completions based on cursor position and prefix."""
        text = document.text_before_cursor
        line_start = text.rfind("\n") + 1
        line_text = text[line_start:]

        # Find the last @ or / trigger
        at_pos = line_text.rfind("@")
        slash_pos = line_text.rfind("/")

        # Determine which trigger is active
        if at_pos >= 0 and (slash_pos < 0 or at_pos > slash_pos):
            # After @: file autocomplete
            prefix = line_text[at_pos + 1:]
            yield from self._file_completions(prefix, at_pos)
        elif slash_pos >= 0:
            # After /: command autocomplete
            prefix = line_text[slash_pos:]
            yield from self._command_completions(prefix, slash_pos)
        else:
            # No trigger: suggest @ and / as triggers
            if line_text.endswith("@"):
                yield Completion("@", start_position=0, display="@")
            elif line_text.endswith("/"):
                yield Completion("/", start_position=0, display="/")

    def _file_completions(self, prefix: str, start_pos: int) -> Sequence[Completion]:
        """Get file path completions."""
        completions = []
        matches = self._file_index.search(prefix)

        for file_path in matches[:50]:  # Limit to 50 results
            completions.append(
                Completion(
                    file_path,
                    start_position=-len(prefix),
                    display=file_path,
                )
            )

        return completions

    def _command_completions(self, prefix: str, start_pos: int) -> Sequence[Completion]:
        """Get slash command completions."""
        completions = []
        prefix_lower = prefix.lower()

        for cmd in self._commands:
            if cmd.lower().startswith(prefix_lower):
                completions.append(
                    Completion(
                        cmd,
                        start_position=-len(prefix),
                        display=cmd,
                    )
                )

        return completions
