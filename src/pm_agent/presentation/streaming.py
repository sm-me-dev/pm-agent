from __future__ import annotations

import re

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.spinner import Spinner
from rich.text import Text

from pm_agent.ports.model import ModelStreamEvent, ModelStreamEventType


class StreamingDisplay:
    _THINK_PATTERN = re.compile(r"<think>(.*?)(?:</think>|$)", re.DOTALL)

    _MAX_REASONING_LINES = 10

    def __init__(self, console: Console) -> None:
        self.console = console
        self.reasoning = ""
        self.content = ""
        self.status = "Preparing context…"
        self._live = Live(
            self._render(),
            console=console,
            refresh_per_second=12,
            transient=True,
        )

    def __enter__(self) -> StreamingDisplay:
        self._live.start()
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self._live.stop()

    def handle(self, event: ModelStreamEvent) -> None:
        if event.event_type is ModelStreamEventType.STATUS:
            self.status = event.text
        elif event.event_type is ModelStreamEventType.REASONING:
            self.reasoning += event.text
            self.status = "Reasoning…"
        elif event.event_type is ModelStreamEventType.CONTENT:
            self.content += event.text
            tagged = self._extract_tagged_reasoning(self.content)
            if tagged:
                self.reasoning = tagged
            self.status = "Structuring response…"
        self._live.update(self._render())

    def _render(self) -> Group:
        items = [Spinner("dots", text=self.status, style="cyan")]
        if self.reasoning.strip():
            items.append(self._reasoning_panel())
        return Group(*items)

    def _reasoning_panel(self) -> Panel:
        lines = self.reasoning.strip().splitlines()
        truncated = lines[-(self._MAX_REASONING_LINES + 1):]
        if len(lines) > self._MAX_REASONING_LINES + 1:
            truncated.insert(0, "\u2026")
        text = "\n".join(truncated)
        width = max(40, self.console.width - 4) if self.console.width else None
        return Panel(
            Text(text, style="dim italic", no_wrap=False),
            title="Status",
            border_style="bright_black",
            width=width,
            subtitle="streaming" if len(lines) > self._MAX_REASONING_LINES else None,
        )

    @classmethod
    def _extract_tagged_reasoning(cls, content: str) -> str:
        matches = cls._THINK_PATTERN.findall(content)
        return "\n".join(part.strip() for part in matches if part.strip())
