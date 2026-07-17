from __future__ import annotations

import json
from importlib.resources import files
from pathlib import Path

from pm_agent.domain.models import ContextPacket, Project
from pm_agent.domain.repository_refs import extract_github_references


class PromptBuilder:
    def __init__(
        self,
        context_dir: str | None = None,
        memory: str | None = None,
        project_meta: dict | None = None,
    ) -> None:
        self.system_policy = files("pm_agent.prompts").joinpath("system.md").read_text()
        self.context_markdown = self._load_context(context_dir)
        self.memory = memory or ""
        self.project_meta = project_meta or {}

    @staticmethod
    def response_schema() -> dict:
        return json.loads(files("pm_agent.prompts").joinpath("response_schema.json").read_text())

    def build(
        self,
        project: Project,
        branch: str,
        packet: ContextPacket,
        user_input: str,
    ) -> list[dict[str, str]]:
        identity_lines = [
            f"Name: {project.name}",
            f"Path: {project.canonical_path}",
            f"Branch: {branch}",
        ]
        configured_name = self.project_meta.get("name")
        if configured_name:
            identity_lines.append(f"Configured name: {configured_name}")
        remote = self.project_meta.get("remote")
        if remote:
            identity_lines.append(f"Remote: {remote}")
        system_sections = [
            self.system_policy,
            "## Host Contract\nExternal actions are proposals only. The PM core cannot execute.",
            "## Project\n" + "\n".join(identity_lines),
        ]
        if self.memory.strip():
            system_sections.append(
                "## Project Memory\n" + self.memory.strip()
            )
        if self.context_markdown:
            system_sections.append(f"## Project Specifications\n{self.context_markdown}")
        if packet.items:
            memory = "\n\n".join(
                f"[{item.kind.value}:{item.source_id} @ {item.created_at}]\n"
                f"{item.title}\n{item.content}"
                for item in packet.items
            )
            system_sections.append(f"## Retrieved Long-Term Memory\n{memory}")
        if packet.repository_snapshot:
            system_sections.append(
                "## Confirmed Cached Repository Snapshot\n"
                + json.dumps(packet.repository_snapshot.summary, ensure_ascii=False, indent=2)
            )
        else:
            system_sections.append(
                "## Repository Context\nNo confirmed repository snapshot is stored. "
                "Do not invent repository facts; propose a read-only refresh if needed."
            )
        references = extract_github_references(user_input)
        if references:
            system_sections.append(
                "## Detected GitHub Repository References\n"
                + "\n".join(f"- {reference.slug}" for reference in references)
                + "\nUse the canonical owner/repository slug in every GitHub action payload."
            )

        messages = [{"role": "system", "content": "\n\n".join(system_sections)}]
        history = packet.recent_messages
        if history and history[-1].role == "user" and history[-1].content == user_input:
            history = history[:-1]
        messages.extend(
            {"role": message.role, "content": message.content}
            for message in history
            if message.role in {"user", "assistant"}
        )
        messages.append({"role": "user", "content": user_input})
        return messages

    @staticmethod
    def repair_messages(raw: str, errors: str, schema: dict) -> list[dict[str, str]]:
        return [
            {
                "role": "system",
                "content": (
                    "Repair the invalid response into JSON matching the schema. "
                    "Return JSON only; do not add new decisions or actions."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Validation errors:\n{errors}\n\nSchema:\n{json.dumps(schema)}"
                    f"\n\nInvalid response:\n{raw}"
                ),
            },
        ]

    @staticmethod
    def _load_context(context_dir: str | None) -> str:
        if not context_dir:
            return ""
        root = Path(context_dir)
        if not root.is_dir():
            return ""
        chunks: list[str] = []
        max_bytes = 100_000
        supported = ("*.md", "*.txt", "*.json", "*.yaml", "*.yml", "*.csv")
        for pattern in supported:
            for path in sorted(root.glob(pattern)):
                try:
                    if path.stat().st_size > max_bytes:
                        continue
                except OSError:
                    continue
                try:
                    chunks.append(f"### {path.name}\n{path.read_text(errors='replace')}")
                except OSError:
                    continue
        return "\n\n".join(chunks)
