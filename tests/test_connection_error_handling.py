from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from pm_agent.application.conversation_service import ConnectionError, ConversationService
from pm_agent.prompts.parser import ResponseValidationError


class MockProvider:
    def __init__(self, should_fail: bool = False, error_type: str = "connection"):
        self.should_fail = should_fail
        self.error_type = error_type

    def generate(self, request, on_event=None):
        if self.should_fail:
            if self.error_type == "connection":
                raise ConnectionError("Model connection failed: APIConnectionError: Connection error")
            elif self.error_type == "timeout":
                raise ConnectionError("Model connection failed: APITimeoutError: Timeout")
            else:
                raise Exception("Some other error")
        return MagicMock(content='{"summary": "test", "analysis": "test", "risks": [], "recommendations": [], "decisions": [], "actions_requiring_approval": []}')


class MockPromptBuilder:
    def response_schema(self):
        return {}

    def build(self, project, branch, packet, user_input):
        return []

    def repair_messages(self, content, error, schema):
        return []


class MockParser:
    def __init__(self, fail_first: bool = False):
        self.fail_first = fail_first
        self.call_count = 0

    def parse(self, content):
        self.call_count += 1
        if self.fail_first and self.call_count == 1:
            raise ResponseValidationError("Invalid response")
        from pm_agent.domain.models import PMResponse
        return PMResponse(
            summary="test",
            analysis="test",
            risks=[],
            recommendations=[],
            decisions=[],
            actions_requiring_approval=[],
        )


class MockStore:
    def add_message(self, *args, **kwargs):
        pass

    def retrieve(self, *args, **kwargs):
        return MagicMock()


class MockActionService:
    def propose(self, *args, **kwargs):
        from pm_agent.domain.models import DispatchReceipt
        return DispatchReceipt(
            id="test",
            action_id="test",
            dispatched=False,
            message="test",
        )


def test_connection_error_is_caught_and_reraised():
    """Test that connection errors from the provider are caught and re-raised."""
    provider = MockProvider(should_fail=True, error_type="connection")
    conversation_service = ConversationService(
        store=MockStore(),
        provider=provider,
        prompt_builder=MockPromptBuilder(),
        parser=MockParser(),
        action_service=MockActionService(),
    )

    project = MagicMock()
    session = MagicMock()

    with pytest.raises(ConnectionError) as exc_info:
        conversation_service.handle(project, session, "test input")

    assert "Model connection failed" in str(exc_info.value)


def test_connection_error_during_repair_is_caught():
    """Test that connection errors during repair are caught and re-raised."""
    class RepairProvider:
        def __init__(self):
            self.call_count = 0

        def generate(self, request, on_event=None):
            self.call_count += 1
            if self.call_count == 1:
                # First call returns valid JSON but parser will fail
                return MagicMock(content='{"summary": "test", "analysis": "test", "risks": [], "recommendations": [], "decisions": [], "actions_requiring_approval": []}')
            elif self.call_count == 2:
                # Repair call fails with connection error
                raise ConnectionError("Model connection failed during repair")
            return MagicMock(content='{"summary": "test", "analysis": "test", "risks": [], "recommendations": [], "decisions": [], "actions_requiring_approval": []}')

    provider = RepairProvider()
    conversation_service = ConversationService(
        store=MockStore(),
        provider=provider,
        prompt_builder=MockPromptBuilder(),
        parser=MockParser(fail_first=True),
        action_service=MockActionService(),
    )

    project = MagicMock()
    session = MagicMock()

    with pytest.raises(ConnectionError) as exc_info:
        conversation_service.handle(project, session, "test input")

    assert "Model connection failed during repair" in str(exc_info.value)


def test_connection_error_preserves_stack_trace():
    """Test that connection errors preserve the original stack trace."""
    def failing_provider(*args, **kwargs):
        raise ConnectionError("Original error")

    provider = MagicMock()
    provider.generate.side_effect = failing_provider

    conversation_service = ConversationService(
        store=MockStore(),
        provider=provider,
        prompt_builder=MockPromptBuilder(),
        parser=MockParser(),
        action_service=MockActionService(),
    )

    project = MagicMock()
    session = MagicMock()

    with pytest.raises(ConnectionError) as exc_info:
        conversation_service.handle(project, session, "test input")

    assert exc_info.value.__cause__ is not None
