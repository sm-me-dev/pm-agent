from __future__ import annotations

from enum import Enum
from pathlib import Path

from prompt_toolkit import PromptSession
from prompt_toolkit.auto_suggest import AutoSuggestFromHistory
from prompt_toolkit.completion import DummyCompleter
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style


class ApprovalChoice(Enum):
    APPROVE = "approve"
    REJECT = "reject"
    ALWAYS_APPROVE = "always-approve"
    SKIP = "skip"
    SKIP_ALL = "skip-all"


class InteractiveInput:
    def __init__(self, history_path: Path | None = None, completer=None) -> None:
        path = history_path or (
            Path.home() / ".local" / "share" / "stateful-pm-agent" / "prompt-history"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        bindings = KeyBindings()

        @bindings.add("enter")
        def submit(event) -> None:
            event.current_buffer.validate_and_handle()

        @bindings.add("escape", "enter")
        def newline(event) -> None:
            event.current_buffer.insert_text("\n")

        self._completer = completer or DummyCompleter()
        self._session: PromptSession[str] = PromptSession(
            history=FileHistory(str(path)),
            auto_suggest=AutoSuggestFromHistory(),
            completer=self._completer,
            multiline=True,
            key_bindings=bindings,
            enable_history_search=True,
            prompt_continuation=lambda width, line_number, wrap_count: " " * (width - 2)
            + "\u00b7 ",
            style=Style.from_dict(
                {
                    "prompt": "bold ansicyan",
                    "toolbar": "bg:#20242b #b8c0cc",
                    "approval": "bold ansiyellow",
                }
            ),
        )

    def prompt(self) -> str:
        return self._session.prompt(
            HTML("<prompt>\U0001f9d1\u200d\U0001f4bb Sm_mE \u276f </prompt>"),
            bottom_toolbar=HTML(
                "<toolbar> Enter submit  \u2022  Alt+Enter newline  \u2022  "
                "@file attach  \u2022  /cmd commands  \u2022  "
                "\u2191/\u2193 history  \u2022  Ctrl+C abort </toolbar>"
            ),
        )

    def approve_action(
        self, action_index: int, action_count: int, action_desc: str
    ) -> ApprovalChoice:
        raw = self._session.prompt(
            HTML(
                f"<approval>Action {action_index}/{action_count}: "
                f"{action_desc}\n  "
                f"[a]pprove  [r]eject  [y] always-approve  [s]kip  [q] quit \u276f </approval>"
            ),
            multiline=False,
            bottom_toolbar=HTML(
                "<toolbar> a: approve once  \u2022  r: reject  \u2022  y: always approve similar  "
                "\u2022  s: skip this  \u2022  q: skip all </toolbar>"
            ),
        ).strip().lower()
        mapping = {
            "a": ApprovalChoice.APPROVE,
            "approve": ApprovalChoice.APPROVE,
            "r": ApprovalChoice.REJECT,
            "reject": ApprovalChoice.REJECT,
            "y": ApprovalChoice.ALWAYS_APPROVE,
            "always": ApprovalChoice.ALWAYS_APPROVE,
            "always-approve": ApprovalChoice.ALWAYS_APPROVE,
            "s": ApprovalChoice.SKIP,
            "skip": ApprovalChoice.SKIP,
            "q": ApprovalChoice.SKIP_ALL,
            "quit": ApprovalChoice.SKIP_ALL,
            "skip-all": ApprovalChoice.SKIP_ALL,
        }
        return mapping.get(raw, ApprovalChoice.SKIP)

    def approval(self, action_id: str) -> ApprovalChoice:
        pass

    def confirm(self, message: str = "Type 'yes' to confirm") -> bool:
        raw = self._session.prompt(
            HTML(f"<approval>{message} \u276f </approval>"),
            multiline=False,
        ).strip().lower()
        return raw == "yes"
