from __future__ import annotations

import re
from typing import Any

from openai import OpenAI

from pm_agent.ports.model import (
    ModelEventHandler,
    ModelRequest,
    ModelResult,
    ModelStreamEvent,
    ModelStreamEventType,
)


class OpenAICompatibleProvider:
    _THINK_PATTERN = re.compile(r"<think>(.*?)</think>", re.DOTALL | re.IGNORECASE)

    def __init__(
        self,
        model: str,
        base_url: str,
        api_key: str,
        structured_output_mode: str = "auto",
    ) -> None:
        self.model = model
        self.structured_output_mode = structured_output_mode
        self.client = OpenAI(base_url=base_url or None, api_key=api_key or "not-set")

    def generate(
        self,
        request: ModelRequest,
        on_event: ModelEventHandler | None = None,
    ) -> ModelResult:
        if self.structured_output_mode != "json":
            try:
                return self._create(
                    request,
                    {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "pm_response",
                            "strict": True,
                            "schema": request.response_schema,
                        },
                    },
                    used_native_schema=True,
                    on_event=on_event,
                )
            except Exception:
                if self.structured_output_mode == "native":
                    raise
        return self._create(
            request,
            {"type": "json_object"},
            used_native_schema=False,
            on_event=on_event,
        )

    def _create(
        self,
        request: ModelRequest,
        response_format: dict[str, Any],
        *,
        used_native_schema: bool,
        on_event: ModelEventHandler | None,
    ) -> ModelResult:
        if on_event is None:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=request.messages,
                temperature=request.temperature,
                response_format=response_format,
            )
            content = response.choices[0].message.content or "{}"
            clean_content, _ = self._separate_thinking(content)
            return ModelResult(content=clean_content, used_native_schema=used_native_schema)

        on_event(ModelStreamEvent(ModelStreamEventType.STATUS, "Connecting to model…"))
        stream = self.client.chat.completions.create(
            model=self.model,
            messages=request.messages,
            temperature=request.temperature,
            response_format=response_format,
            stream=True,
        )
        content_parts: list[str] = []
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            reasoning = self._delta_text(delta, "reasoning_content", "reasoning")
            if reasoning:
                on_event(ModelStreamEvent(ModelStreamEventType.REASONING, reasoning))
            content = self._delta_text(delta, "content")
            if content:
                content_parts.append(content)
                on_event(ModelStreamEvent(ModelStreamEventType.CONTENT, content))
        content, _ = self._separate_thinking("".join(content_parts) or "{}")
        return ModelResult(content=content, used_native_schema=used_native_schema)

    @staticmethod
    def _delta_text(delta: Any, *names: str) -> str:
        for name in names:
            value = getattr(delta, name, None)
            if isinstance(value, str):
                return value
        return ""

    @classmethod
    def _separate_thinking(cls, content: str) -> tuple[str, str]:
        reasoning = "\n".join(
            match.strip() for match in cls._THINK_PATTERN.findall(content) if match.strip()
        )
        clean_content = cls._THINK_PATTERN.sub("", content).strip()
        return clean_content or "{}", reasoning
