from __future__ import annotations

import json

from pm_agent.domain.approval_rules import ApprovalRule, proposal_matches_rule
from pm_agent.domain.enums import ActionType
from pm_agent.domain.models import ActionProposal


def _make_proposal(
    action_type=ActionType.GITHUB,
    tool_category="github",
    operation="create_issue",
    payload=None,
) -> ActionProposal:
    return ActionProposal(
        id="p1",
        project_id="proj1",
        session_id="s1",
        action_type=action_type,
        tool_category=tool_category,
        operation=operation,
        reason="test",
        impact="test",
        payload=payload or {"repository": "owner/repo", "title": "test"},
        payload_sha256="hash",
        risk_level="medium",
        status="proposed",
        created_at="now",
    )


def _make_rule(
    action_type="github",
    tool_category="github",
    operation="create_issue",
    payload_pattern=None,
) -> ApprovalRule:
    return ApprovalRule(
        id="r1",
        project_id="proj1",
        action_type=action_type,
        tool_category=tool_category,
        operation=operation,
        payload_pattern=payload_pattern,
        reason="test rule",
        created_at="now",
        created_by="test",
    )


class TestBroadRules:
    def test_no_payload_pattern_matches_any_payload(self):
        proposal = _make_proposal(payload={"repository": "a/b", "title": "different"})
        rule = _make_rule(payload_pattern=None)
        assert proposal_matches_rule(proposal, rule)

    def test_operation_none_matches_any_operation(self):
        proposal = _make_proposal(operation="create_milestone")
        rule = _make_rule(operation=None)
        assert proposal_matches_rule(proposal, rule)

    def test_action_type_none_matches_any_type(self):
        proposal = _make_proposal(action_type=ActionType.BASH, tool_category="shell")
        rule = _make_rule(action_type=None, tool_category=None)
        assert proposal_matches_rule(proposal, rule)

    def test_all_fields_none_matches_anything(self):
        proposal = _make_proposal(
            action_type=ActionType.MCP, tool_category="filesystem",
            operation="inspect_repository",
        )
        rule = ApprovalRule(
            id="r1", project_id="proj1",
            action_type=None, tool_category=None, operation=None,
            payload_pattern=None, reason="catch-all",
            created_at="now", created_by="test",
        )
        assert proposal_matches_rule(proposal, rule)

    def test_mismatched_action_type_blocks(self):
        proposal = _make_proposal(action_type=ActionType.BASH)
        rule = _make_rule(action_type="github")
        assert not proposal_matches_rule(proposal, rule)

    def test_mismatched_operation_blocks(self):
        proposal = _make_proposal(operation="create_milestone")
        rule = _make_rule(operation="create_issue")
        assert not proposal_matches_rule(proposal, rule)


class TestPayloadPattern:
    def test_exact_payload_pattern_matches(self):
        payload = {"repository": "a/b", "title": "match"}
        proposal = _make_proposal(payload=payload)
        pattern = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        rule = _make_rule(payload_pattern=pattern)
        assert proposal_matches_rule(proposal, rule)

    def test_different_payload_does_not_match(self):
        proposal = _make_proposal(payload={"repository": "a/b", "title": "different"})
        rule = _make_rule(payload_pattern='{"title":"original","repository":"a/b"}')
        assert not proposal_matches_rule(proposal, rule)

    def test_broad_rule_with_no_operation_and_no_payload(self):
        proposal = _make_proposal(operation="setup_sprint")
        rule = _make_rule(operation=None, payload_pattern=None)
        assert proposal_matches_rule(proposal, rule)


class TestToolCategoryNone:
    def test_tool_category_none_matches_any_tool_category(self):
        proposal = _make_proposal(action_type=ActionType.BASH, tool_category="filesystem")
        rule = _make_rule(action_type="bash", tool_category=None)
        assert proposal_matches_rule(proposal, rule)

    def test_tool_category_none_matches_empty_tool_category(self):
        proposal = _make_proposal(action_type=ActionType.BASH, tool_category="")
        rule = _make_rule(action_type="bash", tool_category=None)
        assert proposal_matches_rule(proposal, rule)

    def test_tool_category_none_matches_all_bash_ops(self):
        proposal = _make_proposal(
            action_type=ActionType.BASH, tool_category="filesystem",
            operation="ls", payload={"command": "ls /tmp"},
        )
        rule = _make_rule(action_type="bash", tool_category=None, operation=None,
                          payload_pattern=None)
        assert proposal_matches_rule(proposal, rule)

    def test_action_type_still_filters_with_tool_category_none(self):
        proposal = _make_proposal(action_type=ActionType.GITHUB, tool_category="github")
        rule = _make_rule(action_type="bash", tool_category=None)
        assert not proposal_matches_rule(proposal, rule)


class TestCapabilityFailures:
    def test_unsupported_operation_not_matched_by_wrong_rule(self):
        proposal = _make_proposal(operation="delete_repository")
        rule = _make_rule(operation="create_issue")
        assert not proposal_matches_rule(proposal, rule)

    def test_unsupported_action_type_not_matched(self):
        proposal = _make_proposal(action_type=ActionType.BASH, tool_category="shell")
        rule = _make_rule(action_type="github", tool_category="github")
        assert not proposal_matches_rule(proposal, rule)
