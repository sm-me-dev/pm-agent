from __future__ import annotations


class PromptService:
    def __init__(self, prompt_builder) -> None:
        self.prompt_builder = prompt_builder

    def build(self, project, session, packet, user_input: str):
        return self.prompt_builder.build(project, session.branch, packet, user_input)

    def schema(self) -> dict:
        return self.prompt_builder.response_schema()
