from __future__ import annotations

from rich.console import Console
from rich.table import Table

from pm_agent.domain.models import ActionProposal
from pm_agent.presentation.input import ApprovalChoice, InteractiveInput
from pm_agent.presentation.renderers import action_panel


def render_action_selector(
    console: Console,
    actions: list[ActionProposal],
) -> None:
    if not actions:
        console.print("[dim]No pending actions.[/]")
        return
    table = Table(
        title="Pending Actions",
        border_style="yellow",
        header_style="bold yellow",
        width=min(console.width - 2, 100) if console.width else None,
    )
    table.add_column("#", style="cyan", no_wrap=True)
    table.add_column("Type", style="yellow")
    table.add_column("Operation", style="white")
    table.add_column("Risk", style="magenta")
    table.add_column("Summary", style="dim", ratio=2)
    for i, action in enumerate(actions, 1):
        reason = (action.reason or action.impact or "")[:60]
        table.add_row(
            str(i),
            action.action_type.value,
            action.operation,
            action.risk_level,
            reason,
        )
    console.print(table)


def confirm_approval(
    console: Console,
    input_session: InteractiveInput,
    action: ActionProposal,
) -> ApprovalChoice:
    console.print(action_panel(action))
    try:
        return input_session.approve_action(1, 1, action.operation)
    except (EOFError, KeyboardInterrupt):
        return ApprovalChoice.SKIP
