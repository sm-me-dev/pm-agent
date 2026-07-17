from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import ActionProposal, new_id, utc_now


@dataclass(frozen=True)
class ApprovalRule:
    id: str
    project_id: str
    action_type: str | None
    tool_category: str | None
    operation: str | None
    payload_pattern: str | None
    reason: str
    created_at: str
    created_by: str


def _extract_safety_payload(proposal: ActionProposal) -> dict[str, Any]:
    safety = {}
    payload = proposal.payload or {}

    if proposal.action_type.value == "github":
        if "repository" in payload:
            safety["repository"] = payload["repository"]

    elif proposal.action_type.value == "bash":
        cmd = str(payload.get("command", "")).strip()
        if cmd:
            import shlex
            try:
                parts = shlex.split(cmd)
                if parts:
                    safety["command_executable"] = parts[0].rsplit("/", 1)[-1].lower()
            except ValueError:
                safety["command_executable"] = cmd

    elif proposal.action_type.value == "git":
        cmd = str(payload.get("command", "")).strip()
        if cmd:
            import shlex
            try:
                parts = shlex.split(cmd)
                if len(parts) >= 2 and parts[0] == "git":
                    safety["git_subcommand"] = parts[1]
            except ValueError:
                pass

    elif proposal.action_type.value == "mcp":
        if "path" in payload:
            safety["path"] = str(Path(payload["path"]).resolve())

    return safety


def proposal_matches_rule(proposal: ActionProposal, rule: ApprovalRule) -> bool:
    if rule.action_type and proposal.action_type.value != rule.action_type:
        return False
    if rule.tool_category and proposal.tool_category != rule.tool_category:
        return False
    if rule.operation and proposal.operation != rule.operation:
        return False
    if rule.payload_pattern:
        try:
            pattern_dict = json.loads(rule.payload_pattern)
            if isinstance(pattern_dict, dict):
                proposal_safety = _extract_safety_payload(proposal)
                for k, v in pattern_dict.items():
                    if k == "command_executable":
                        if proposal_safety.get("command_executable") != v:
                            return False
                    elif k == "git_subcommand":
                        if proposal_safety.get("git_subcommand") != v:
                            return False
                    elif k == "path":
                        if proposal_safety.get("path") != v:
                            return False
                    else:
                        if proposal.payload.get(k) != v:
                            return False
            else:
                canonical = json.dumps(proposal.payload, sort_keys=True, separators=(",", ":"))
                if rule.payload_pattern not in canonical:
                    return False
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
    return True


def make_approval_rule(
    project_id: str,
    proposal: ActionProposal,
    reason: str = "Always approve similar actions",
    created_by: str = "user",
    include_payload: bool = False,
) -> ApprovalRule:
    if include_payload:
        pattern_dict = proposal.payload
    else:
        pattern_dict = _extract_safety_payload(proposal)

    payload_pattern = json.dumps(pattern_dict, sort_keys=True, separators=(",", ":")) if pattern_dict else None

    return ApprovalRule(
        id=new_id(),
        project_id=project_id,
        action_type=proposal.action_type.value,
        tool_category=proposal.tool_category,
        operation=proposal.operation,
        payload_pattern=payload_pattern,
        reason=reason,
        created_at=utc_now(),
        created_by=created_by,
    )
