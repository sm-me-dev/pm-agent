from __future__ import annotations

import json

from rich.console import Console, Group
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from pm_agent.domain.enums import ActionType
from pm_agent.domain.models import (
    ActionProposal,
    Decision,
    IntegrationInfo,
    MemoryCounts,
    PMResponse,
    Project,
    Session,
    SessionSummary,
)


def _panel_width(console: Console | None = None) -> int | None:
    if console is None:
        return None
    w = console.width
    return max(40, w - 4) if w else None


def startup_text(
    project: Project, session: Session, counts: MemoryCounts, model: str
) -> str:
    return (
        "Stateful Technical PM Agent\n"
        f"Model: {model}\n"
        f"Session: {session.name}\n"
        f"Project: {project.name}\n"
        f"Branch: {session.branch}"
    )


def render_startup(
    console: Console,
    project: Project,
    session: Session,
    counts: MemoryCounts,
    model: str,
) -> None:
    console.print(Panel(
        startup_text(project, session, counts, model),
        border_style="cyan",
        width=_panel_width(console),
    ))
    console.print(
        "\nMemory Loaded:\n"
        f"- {counts.messages} messages\n"
        f"- {counts.decisions} decisions\n"
        f"- {counts.repo_notes} repo notes\n\n"
        "Ready.\n"
    )


def integration_panel(integration: IntegrationInfo) -> Panel:
    colors = {
        "connected": "green",
        "available": "cyan",
        "host-managed": "magenta",
        "unavailable": "red",
    }
    body = Group(
        Text.assemble(("Status: ", "bold"), integration.status),
        Text.assemble(("Authentication: ", "bold"), integration.authentication),
        Text("\nCapabilities", style="bold"),
        Text("\n".join(f"\u2022 {capability}" for capability in integration.capabilities)),
        *(
            [Text(f"\nSetup: {integration.setup_hint}", style="yellow")]
            if integration.setup_hint
            else []
        ),
    )
    return Panel(
        body,
        title=Text(f"{integration.name} [{integration.key}]"),
        border_style=colors.get(integration.status, "white"),
    )


def response_renderable(response: PMResponse, actions: list[ActionProposal]) -> Group:
    decisions = "\n\n".join(
        decision_candidate_markdown(decision) for decision in response.decisions
    ) or "None."
    return Group(
        Panel(Markdown(response.summary or "None."), title="Summary", border_style="green"),
        Panel(Markdown(response.analysis or "None."), title="Analysis", border_style="cyan"),
        Panel(Markdown(_bullets(response.risks)), title="Risks", border_style="red"),
        Panel(
            Markdown(_bullets(response.recommendations)),
            title="Recommendations",
            border_style="magenta",
        ),
        Panel(Markdown(decisions), title="Decisions", border_style="blue"),
        *(
            [Panel(Text("None.", style="dim"), title="Actions Requiring Approval")]
            if not actions
            else [action_panel(action) for action in actions]
        ),
    )


def response_markdown(response: PMResponse, actions: list[ActionProposal]) -> str:
    risks = _bullets(response.risks)
    recommendations = _bullets(response.recommendations)
    decisions = "\n\n".join(
        decision_candidate_markdown(decision) for decision in response.decisions
    ) or "None."
    action_text = "\n\n".join(action_markdown(action) for action in actions) or "None."
    return (
        f"# Summary\n\n{response.summary or 'None.'}\n\n"
        f"# Analysis\n\n{response.analysis or 'None.'}\n\n"
        f"# Risks\n\n{risks}\n\n"
        f"# Recommendations\n\n{recommendations}\n\n"
        f"# Decisions\n\n{decisions}\n\n"
        f"# Actions Requiring Approval\n\n{action_text}"
    )


def decision_candidate_markdown(decision) -> str:
    return (
        f"### Decision: {decision.topic} | {decision.title}\n\n"
        f"Decision:\n{decision.decision}\n\n"
        f"Reason:\n{decision.reason}\n\n"
        f"Status:\n{decision.status.value}"
    )


def decision_markdown(decision: Decision) -> str:
    return (
        f"### Decision: {decision.topic} | {decision.title}\n\n"
        f"Decision:\n{decision.decision}\n\n"
        f"Reason:\n{decision.reason}\n\n"
        f"Status:\n{decision.status.value}"
    )


def decision_table(decisions: list[Decision], title: str = "Pending Decisions") -> Table:
    table = Table(title=title, border_style="cyan", header_style="bold cyan")
    table.add_column("#", style="cyan", no_wrap=True)
    table.add_column("Topic", style="yellow")
    table.add_column("Decision", style="white", ratio=2)
    table.add_column("Created", style="dim")
    for i, d in enumerate(decisions, 1):
        table.add_row(str(i), d.topic, d.decision[:80], d.created_at[:10])
    return table


def action_markdown(action: ActionProposal) -> str:
    if action.action_type in {ActionType.BASH, ActionType.GIT}:
        language = "bash"
        payload = str(action.payload.get("command", ""))
        label = "Command"
    else:
        language = "json"
        payload = json.dumps(action.payload, ensure_ascii=False, indent=2, sort_keys=True)
        label = "Payload"
    return (
        "### Action Proposal\n\n"
        f"ID:\n{action.id}\n\n"
        f"Type:\n{action.action_type.value}\n\n"
        f"Reason:\n{action.reason}\n\n"
        f"Impact:\n{action.impact}\n\n"
        f"{label}:\n\n```{language}\n{payload}\n```\n\n"
        "Approval Required:\nYES"
    )


def help_panel() -> Panel:
    categories = [
        ("General", ["/help", "/status", "/history", "/clear", "/summary", "/quit"]),
        ("Actions", ["/actions", "/approve <uuid>", "/reject <uuid>", "/retry <id>", "/action-result <id>"]),
        ("Decisions", ["/decisions", "/decision topic | title | decision | reason", "/accept <id>", "/reject-decision <id>"]),
        ("Integrations", ["/integrations", "/integration <key>", "/connect github"]),
        ("Permissions", ["/permissions", "/permission <action-id>", "/perm-revoke <rule-id>"]),
        ("Context", ["/refresh"]),
    ]
    lines: list[Text] = []
    for title, cmds in categories:
        lines.append(Text(f"\n{title}", style="bold cyan"))
        for cmd in cmds:
            lines.append(Text(f"  {cmd}", style="white"))
    body = Group(*lines)
    return Panel(body, title="Commands", border_style="cyan")


def action_panel(action: ActionProposal) -> Panel:
    if action.action_type in {ActionType.BASH, ActionType.GIT}:
        payload = Syntax(
            str(action.payload.get("command", "")),
            "bash",
            theme="ansi_dark",
            word_wrap=True,
        )
    else:
        payload = Syntax(
            json.dumps(action.payload, ensure_ascii=False, indent=2, sort_keys=True),
            "json",
            theme="ansi_dark",
            word_wrap=True,
        )
    body = Group(
        Text.assemble(("ID: ", "bold"), action.id),
        Text.assemble(("Type: ", "bold"), action.action_type.value),
        Text.assemble(("Status: ", "bold"), action.status.value),
        Text.assemble(("Risk: ", "bold"), action.risk_level),
        Text.assemble(("Reason: ", "bold"), action.reason),
        Text.assemble(("Impact: ", "bold"), action.impact),
        Text("\nPayload", style="bold"),
        payload,
        Text("\nApproval Required: YES", style="bold yellow"),
    )
    return Panel(body, title="Action Proposal", border_style="yellow")


def actions_pending_list(actions: list[ActionProposal]) -> Panel:
    if not actions:
        return Panel(Text("None.", style="dim"), title="Pending Actions")
    lines: list[Text] = []
    for action in actions:
        lines.append(
            Text.assemble(
                ("  ", ""),
                (f"{action.id[:8]}", "cyan"),
                ("  |  ", "dim"),
                (f"{action.action_type.value}", "yellow"),
                ("  |  ", "dim"),
                (f"{action.operation}", "white"),
                ("  |  ", "dim"),
                (f"{action.status.value}", "green" if action.status.value == "proposed" else "dim"),
            )
        )
        if action.reason:
            lines.append(Text(f"       {action.reason}", style="dim"))
    body = Group(*lines)
    return Panel(
        body,
        title=f"Pending Actions ({len(actions)})",
        border_style="yellow",
    )


def actions_table(actions: list[ActionProposal], title: str = "Actions") -> Panel:
    if not actions:
        return Panel(Text("None.", style="dim"), title=title)
    lines: list[Text] = []
    for action in actions:
        status_colors = {
            "proposed": "yellow",
            "approved": "green",
            "rejected": "red",
            "dispatched": "cyan",
            "succeeded": "green",
            "failed": "red",
            "expired": "dim",
        }
        color = status_colors.get(action.status.value, "white")
        lines.append(
            Text.assemble(
                ("  ", ""),
                (f"{action.id[:8]}", "cyan"),
                ("  |  ", "dim"),
                (f"{action.action_type.value}", "yellow"),
                ("  |  ", "dim"),
                (f"{action.operation}", "white"),
                ("  |  ", "dim"),
                (f"{action.status.value}", color),
            )
        )
    body = Group(*lines)
    return Panel(body, title=f"{title} ({len(actions)})", border_style="blue")


def render_approval_prompt(action: ActionProposal) -> Group:
    return Group(
        Text(
            f"\u26a0 Approve {action.id}?",
            style="bold yellow",
        ),
    )


def render_authorization_guard(console: Console, action: ActionProposal) -> None:
    render_approval_prompt(action)


def render_action_outcome(
    console: Console,
    *,
    exit_code: int | None,
    stdout: str,
    stderr: str,
    result: dict,
) -> None:
    width = _panel_width(console)
    console.print(
        Panel(
            Text(f"Exit code: {exit_code if exit_code is not None else 'not provided'}"),
            title="Command Result",
            border_style="green" if exit_code == 0 else "red",
            width=width,
        )
    )
    if stdout:
        console.print(Panel(Text(stdout.rstrip()), title="stdout", border_style="blue", width=width))
    if stderr:
        console.print(Panel(Text(stderr.rstrip()), title="stderr", border_style="red", width=width))
    if result:
        console.print(
            Panel(
                Syntax(
                    json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True),
                    "json",
                    theme="ansi_dark",
                    word_wrap=True,
                ),
                title="Structured Result",
                border_style="cyan",
                width=width,
            )
        )


def summary_markdown(summary: SessionSummary) -> str:
    return (
        "# Session Summary\n\n"
        f"## Key Topics\n{_bullets(summary.key_topics)}\n\n"
        f"## Decisions Made\n{_bullets(summary.decisions)}\n\n"
        f"## Planned Actions\n{_bullets(summary.planned_actions)}\n\n"
        f"## Open Questions\n{_bullets(summary.open_questions)}"
    )


def print_markdown(console: Console, content: str) -> None:
    console.print(Markdown(content))


def _bullets(values: list[str]) -> str:
    return "\n".join(f"- {value}" for value in values) if values else "None."
