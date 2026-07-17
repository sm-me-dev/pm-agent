from __future__ import annotations

from rich.console import Console

from pm_agent.infrastructure.models.openai_compatible import OpenAICompatibleProvider
from pm_agent.ports.model import ModelStreamEvent, ModelStreamEventType
from pm_agent.presentation.streaming import StreamingDisplay


def test_streaming_display_collects_dedicated_reasoning():
    display = StreamingDisplay(Console(file=None))
    display.handle(ModelStreamEvent(ModelStreamEventType.REASONING, "Inspect context."))
    display.handle(ModelStreamEvent(ModelStreamEventType.REASONING, " Build plan."))
    assert display.reasoning == "Inspect context. Build plan."


def test_streaming_display_extracts_think_tags_from_content():
    display = StreamingDisplay(Console(file=None))
    display.handle(
        ModelStreamEvent(
            ModelStreamEventType.CONTENT,
            '<think>Compare options.</think>{"summary":"Done"}',
        )
    )
    assert display.reasoning == "Compare options."


def test_provider_removes_think_tags_before_json_parsing():
    content, reasoning = OpenAICompatibleProvider._separate_thinking(
        '<think>Compare options.</think>{"summary":"Done"}'
    )
    assert content == '{"summary":"Done"}'
    assert reasoning == "Compare options."
