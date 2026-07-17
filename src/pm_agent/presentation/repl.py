from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from pm_agent.application.action_service import ActionService
from pm_agent.application.context_service import ContextService
from pm_agent.application.conversation_service import ConnectionError, ConversationService
from pm_agent.application.decision_service import DecisionService
from pm_agent.application.error_logger import ErrorLogger
from pm_agent.application.integration_service import IntegrationService
from pm_agent.application.session_service import SessionService
from pm_agent.application.summary_service import SummaryService
from pm_agent.domain.approval_rules import ApprovalRule, make_approval_rule
from pm_agent.domain.enums import ActionStatus, DecisionStatus
from pm_agent.domain.errors import classify_action_error
from pm_agent.domain.models import (
    DispatchReceipt,
    Project,
    Session,
    new_id,
    utc_now,
)
from pm_agent.presentation.approval import confirm_approval, render_action_selector
from pm_agent.presentation.completer import PMCompleter
from pm_agent.presentation.file_scanner import LazyFileIndex
from pm_agent.presentation.input import ApprovalChoice, InteractiveInput
from pm_agent.presentation.mentions import build_mention_payload
from pm_agent.presentation.renderers import (
    action_panel,
    actions_pending_list,
    actions_table,
    decision_markdown,
    decision_table,
    help_panel,
    integration_panel,
    print_markdown,
    render_action_outcome,
    render_startup,
    response_renderable,
    summary_markdown,
)
from pm_agent.presentation.streaming import StreamingDisplay
from pm_agent.prompts.parser import ResponseValidationError


MAX_RECOVERY_ATTEMPTS = 3


@dataclass
class REPLServices:
    store: object
    conversation: ConversationService
    actions: ActionService
    decisions: DecisionService
    integrations: IntegrationService
    context: ContextService
    sessions: SessionService
    summaries: SummaryService


class PMAgentREPL:
    def __init__(
        self,
        console: Console,
        project: Project,
        session: Session,
        services: REPLServices,
        model: str,
        always_approve: bool = False,
        error_logger: ErrorLogger | None = None,
        context_dir: str | None = None,
    ) -> None:
        self.console = console
        self.project = project
        self.session = session
        self.services = services
        self.model = model
        self.always_approve = always_approve
        self._closed = False
        self._context_loaded = False
        self._error_logger = error_logger
        self._context_dir = context_dir

        self._file_index = LazyFileIndex(project.canonical_path)
        self._completer = PMCompleter(self._file_index)
        self.input = InteractiveInput(completer=self._completer)

        self._presented_decision_ids: set[str] = set()
        self._recovery_attempts = 0
        self._halt_requested = False

        if always_approve:
            self._install_always_approve_rules()

    def _install_always_approve_rules(self) -> None:
        existing = self.services.store.list_approval_rules(self.project.id)
        existing_keys = {r.action_type for r in existing if r.operation is None}
        created = 0
        for atype in ("bash", "git", "github", "mcp"):
            if atype in existing_keys:
                continue
            rule = ApprovalRule(
                id=new_id(),
                project_id=self.project.id,
                action_type=atype,
                tool_category=None,
                operation=None,
                payload_pattern=None,
                reason=f"CLI --always-approve: auto-approve all {atype} actions",
                created_at=utc_now(),
                created_by="cli",
            )
            self.services.store.add_approval_rule(rule)
            created += 1
        if created:
            self.console.print(
                f"[green]Auto-approval enabled:[/] {created} broad rule(s) installed "
                f"(use /permissions to view)."
            )

    def run(self) -> None:
        render_startup(
            self.console,
            self.project,
            self.session,
            self.services.store.memory_counts(self.project.id),
            self.model,
        )
        self.console.print("[dim]Type /help for commands.[/]")
        try:
            while True:
                try:
                    user_input = self.input.prompt().strip()
                except EOFError:
                    break
                except KeyboardInterrupt:
                    self.console.print("[dim]Input cleared. Press Ctrl+D or use /quit to exit.[/]")
                    continue
                if not user_input:
                    continue
                if user_input.startswith("/"):
                    try:
                        if self._command(user_input):
                            break
                    except KeyboardInterrupt:
                        self.console.print("[yellow]Command cancelled.[/]")
                    except Exception as exc:
                        self.console.print(
                            Panel(
                                str(exc),
                                title="Command Failed",
                                border_style="red",
                            )
                        )
                        self._log_orchestration_error(exc)
                    continue
                self._chat(user_input)
        finally:
            self.close()

    def close(self) -> None:
        if self._closed:
            return
        summary = self.services.summaries.create(self.project.id, self.session.id)
        self.services.sessions.close(self.session.id)
        print_markdown(self.console, summary_markdown(summary))
        self._closed = True

    def _chat(self, user_input: str) -> None:
        self._recovery_attempts = 0
        self._halt_requested = False
        if not self._context_loaded:
            self._context_loaded = True
            c_dir_str = self._context_dir or str(Path.cwd() / "context")
            context_dir = Path(c_dir_str).resolve()
            if context_dir.is_dir():
                loaded = self.services.context.load_context_files(
                    self.project.id, self.session.id, base_path=str(context_dir),
                )
                if loaded:
                    self.console.print(
                        f"[dim]Loaded {len(loaded)} context file(s) from {context_dir}: "
                        f"{', '.join(loaded)}[/]"
                    )
            else:
                self.console.print(
                    f"[yellow]Project context directory not found: {context_dir}. "
                    f"Place planning context files (*.md, *.txt, *.json, *.yaml, *.csv) "
                    f"there to inform the PM.[/]"
                )

        while True:
            processed_input = build_mention_payload(
                user_input, self.project.canonical_path
            )

            try:
                with StreamingDisplay(self.console) as display:
                    response, actions = self.services.conversation.handle(
                        self.project,
                        self.session,
                        processed_input,
                        on_model_event=display.handle,
                    )
            except KeyboardInterrupt:
                self.console.print(
                    Panel(
                        "The active model stream was cancelled safely. "
                        "No action was approved.",
                        title="Stream Aborted",
                        border_style="yellow",
                    )
                )
                return
            except ConnectionError as exc:
                self.console.print(
                    Panel(
                        f"Connection to model failed: {exc}\n\n"
                        "This is typically a temporary network or server issue. "
                        "Please check your connection and try again.",
                        title="Model Connection Error",
                        border_style="red",
                    )
                )
                self._log_orchestration_error(exc, retryable=True, category="model_connection_error")
                return
            except ResponseValidationError as exc:
                self.console.print(
                    Panel(
                        f"Model returned an invalid response: {exc}\n\n"
                        "The model may not support the required response format, "
                        "or may be overloaded. Try again or check your model configuration.",
                        title="Model Response Error",
                        border_style="red",
                    )
                )
                self._log_orchestration_error(exc, retryable=True, category="model_response_error")
                return
            except Exception as exc:
                self.console.print(f"[bold red]Request failed:[/] {exc}")
                self._log_orchestration_error(exc)
                return

            self.console.print(response_renderable(response, actions))

            event_parts: list[str] = []

            decision_events = self._resolve_pending_decisions()
            if decision_events:
                event_parts.append(decision_events)

            if actions:
                action_events = self._interactive_approval_flow(actions)
                if action_events:
                    event_parts.append(action_events)

            if self._halt_requested:
                self.console.print(
                    "[dim]Stopping to let you resolve the issue. "
                    "Fix it, then continue with a new request or /retry.[/]"
                )
                break

            if not event_parts:
                break

            user_input = "\n---\n".join(event_parts)

    def _interactive_approval_flow(self, actions: list) -> str:
        proposed = [a for a in actions if a.status is ActionStatus.PROPOSED]
        if not proposed:
            return ""

        self.console.print()
        render_action_selector(self.console, proposed)
        self.console.print()

        events: list[str] = []
        for action in proposed:
            action = self.services.store.get_action(action.id)
            if action is None or action.status is not ActionStatus.PROPOSED:
                continue

            rule = self.services.store.find_approval_rule(action)
            if rule is not None:
                self.console.print(
                    f"[green]Auto-approved[/] {action.operation} "
                    f"(rule: {rule.reason})"
                )
                ctx = self._approve_and_continue(action)
                if ctx:
                    events.append(ctx)
                continue

            choice = confirm_approval(self.console, self.input, action)

            if choice is ApprovalChoice.APPROVE:
                ctx = self._approve_and_continue(action)
                if ctx:
                    events.append(ctx)
            elif choice is ApprovalChoice.REJECT:
                self.services.actions.reject(action.id)
                self.services.store.add_message(
                    self.session.id, "system",
                    f"[action_outcome] Action rejected by user: {action.operation}"
                )
                self.console.print(f"[red]Action {action.id[:8]} rejected.[/]")
                events.append(self._format_action_rejected(action))
            elif choice is ApprovalChoice.ALWAYS_APPROVE:
                rule = make_approval_rule(
                    self.project.id,
                    action,
                    reason=f"Always approve {action.operation} ({action.action_type.value})",
                )
                self.services.store.add_approval_rule(rule)
                self.console.print(
                    f"[green]Rule saved:[/] always approve {action.operation} "
                    f"({action.action_type.value})"
                )
                ctx = self._approve_and_continue(action)
                if ctx:
                    events.append(ctx)
            elif choice is ApprovalChoice.SKIP_ALL:
                self.console.print("[dim]Remaining actions skipped.[/]")
                break

        return "\n---\n".join(events)

    def _resolve_pending_decisions(self) -> str:
        decisions = [
            d
            for d in self.services.store.list_decisions(
                self.project.id,
                statuses=(DecisionStatus.PROPOSED,),
            )
            if d.id not in self._presented_decision_ids
        ]
        if not decisions:
            return ""
        self._presented_decision_ids.update(d.id for d in decisions)
        self.console.print()
        self.console.print(decision_table(decisions))
        events: list[str] = []
        accept_all = self.always_approve
        for decision in decisions:
            self.console.print()
            self.console.print(
                f"  [bold]{decision.topic}[/] — {decision.title}"
            )
            self.console.print(f"  [dim]{decision.decision[:200]}[/]")
            if accept_all:
                self.services.decisions.accept(self.project.id, decision.id)
                self.console.print("  [green]Decision accepted (all).[/]")
                events.append(self._format_decision_accepted(decision))
                continue
            raw = self.console.input(
                "  Accept (a), Reject (r), Skip (s), All (aa) [a]: "
            ).strip().lower()
            if raw in ("r", "reject"):
                self.services.decisions.reject(self.project.id, decision.id)
                self.console.print("  [red]Decision rejected.[/]")
                events.append(self._format_decision_rejected(decision))
            elif raw in ("s", "skip"):
                self.services.decisions.defer(self.project.id, decision.id)
                self.console.print("  [dim]Deferred.[/]")
                continue
            elif raw in ("q", "quit", "skip-all"):
                for remaining in decisions[decisions.index(decision):]:
                    self.services.decisions.defer(self.project.id, remaining.id)
                self.console.print("  [dim]Remaining decisions deferred.[/]")
                break
            elif raw in ("aa", "accept-all", "all"):
                accept_all = True
                self.services.decisions.accept(self.project.id, decision.id)
                self.console.print("  [green]Decision accepted (all).[/]")
                events.append(self._format_decision_accepted(decision))
            else:
                self.services.decisions.accept(self.project.id, decision.id)
                self.console.print("  [green]Decision accepted.[/]")
                events.append(self._format_decision_accepted(decision))
        return "\n---\n".join(events)

    def _approve_and_continue(self, action) -> str:
        try:
            receipt = self.services.actions.approve(action.id)
        except (KeyError, ValueError) as exc:
            self.console.print(f"[red]Approval failed:[/] {exc}")
            return ""

        if receipt.deferred_external:
            result_msg = (
                f"Action {action.id[:8]} ({action.operation}) approved for external/standalone execution."
            )
            self.console.print(
                Panel(
                    f"Action approved but not executed by host.\n"
                    f"Please run this action manually outside PM-agent:\n\n"
                    f"  Action ID: {action.id}\n"
                    f"  Operation: {action.operation}\n"
                    f"  Payload: {json.dumps(action.payload)}\n\n"
                    f"Once execution is complete, report the outcome to PM-agent using:\n"
                    f"  /action-result {action.id} {{\"exit_code\": 0, \"stdout\": \"...\"}}",
                    title=f"Action Approved (Awaiting External Execution): {action.operation}",
                    border_style="yellow"
                )
            )
            self.services.store.add_message(
                self.session.id, "system", f"[action_outcome] {result_msg}"
            )
            return self._format_action_result(action, receipt)

        if receipt.dispatched and receipt.exit_code in (None, 0):
            outcome = ""
            if receipt.stdout:
                outcome += receipt.stdout[:200]
            if receipt.stderr:
                outcome += f"\n[stderr] {receipt.stderr[:200]}"
            result_msg = (
                f"Action {action.id[:8]} ({action.operation}) completed (exit=0)."
                f"\n{outcome}".strip()
            )
            self.console.print(
                Panel(receipt.message, title=f"Action Executed: {action.operation}",
                      border_style="green")
            )
            if outcome:
                self.console.print(outcome)
            self.services.store.add_message(
                self.session.id, "system", f"[action_outcome] {result_msg}"
            )
            return self._format_action_result(action, receipt)

        # Dispatched but failed, or dispatch not available: route through the
        # structured error path so the model can recover (if appropriate) or
        # the run halts and asks the user.
        self._log_action_error(
            action, receipt,
            error=receipt.message or receipt.stderr,
            category=receipt.error_category or "action_failure",
        )
        return self._handle_failed_dispatch(action, receipt)

    def _handle_failed_dispatch(self, action, receipt) -> str:
        error = classify_action_error(receipt, action)
        self._recovery_attempts += 1
        if not error.agent_fixable or self._recovery_attempts > MAX_RECOVERY_ATTEMPTS:
            self._halt_for_user(error, action)
            return ""
        self.console.print(
            Panel(
                f"{error.message}\n\n"
                f"Category: {error.category.value}. The model will be given this "
                f"error and may attempt to revise the action "
                f"(attempt {self._recovery_attempts}/{MAX_RECOVERY_ATTEMPTS}).",
                title=f"Action Failed (recoverable): {action.operation}",
                border_style="yellow",
            )
        )
        return error.to_event()

    def _halt_for_user(self, error, action) -> None:
        self._halt_requested = True
        self.console.print(
            Panel(
                f"{error.user_guidance or error.message}\n\n"
                f"Action: {action.operation} ({action.action_type.value})\n"
                f"This action cannot be completed automatically. Resolve it and "
                f"continue, or adjust your request.",
                title="Action Requires Your Intervention",
                border_style="red",
            )
        )

    def _log_action_error(
        self,
        action,
        receipt: DispatchReceipt | None = None,
        error: str = "",
        category: str = "action_failure",
        exception: BaseException | None = None,
    ) -> None:
        if self._error_logger is None:
            return
        stderr = (receipt.stderr or "") if receipt else ""
        msg = error or stderr or (receipt.message if receipt else "")
        user_msg = (receipt.message if receipt else "") or msg
        current = self.services.store.get_action(action.id)
        before_status = current.status.value if current else getattr(action, "status", "unknown")
        self._error_logger.log_failure(
            session_id=self.session.id,
            action_id=action.id,
            action_type=action.action_type.value,
            action_status_before=before_status,
            action_status_after="failed",
            executor="REPL",
            target_repository=action.payload.get("repository", "") if hasattr(action, "payload") else "",
            payload=getattr(action, "payload", {}),
            error=msg,
            exception=exception,
            exit_code=receipt.exit_code if receipt else None,
            retryable=False,
            user_message=user_msg,
            category=category,
        )

    def _log_orchestration_error(self, exception: BaseException, retryable: bool = True, category: str = "orchestration_error") -> None:
        if self._error_logger is None:
            return
        self._error_logger.log_failure(
            session_id=self.session.id,
            error=str(exception),
            exception=exception,
            category=category,
            retryable=retryable,
            user_message=f"Unexpected error: {exception}",
        )

    def _format_action_result(self, action, receipt) -> str:
        stdout = (receipt.stdout or "")[:500]
        stderr = (receipt.stderr or "")[:500]
        if receipt.dispatched or receipt.exit_code is not None:
            return (
                f"[action_result]\n"
                f"Action: {action.operation}\n"
                f"Type: {action.action_type.value}\n"
                f"Status: completed (exit {receipt.exit_code})\n"
                f"Stdout:\n{stdout}\n"
                f"Stderr:\n{stderr}"
            )
        return (
            f"[action_result]\n"
            f"Action: {action.operation}\n"
            f"Type: {action.action_type.value}\n"
            f"Status: failed (dispatch not available)\n"
            f"Message: {receipt.message}"
        )

    @staticmethod
    def _format_decision_accepted(decision) -> str:
        return (
            f"[decision_accepted]\n"
            f"Topic: {decision.topic}\n"
            f"Title: {decision.title}\n"
            f"Decision: {decision.decision}"
        )

    @staticmethod
    def _format_decision_rejected(decision) -> str:
        return (
            f"[decision_rejected]\n"
            f"Topic: {decision.topic}\n"
            f"Title: {decision.title}\n"
            f"Reason: rejected by user"
        )

    @staticmethod
    def _format_action_rejected(action) -> str:
        return (
            f"[action_rejected]\n"
            f"Action: {action.operation}\n"
            f"Type: {action.action_type.value}\n"
            f"Reason: rejected by user"
        )

    def _approve_single(self, action_id: str) -> str:
        action = self.services.store.get_action(action_id)
        if action is None:
            self.console.print(f"[red]Unknown action: {action_id}[/]")
            self.console.print("[dim]Use /actions to see all actions with their IDs.[/]")
            return ""
        if action.status is not ActionStatus.PROPOSED:
            self.console.print(
                Panel(
                    f"Action {action.id[:8]} is {action.status.value} and cannot be approved.\n"
                    f"Use /retry {action.id[:8]} to create a fresh proposal.",
                    title="Action Not Approvable",
                    border_style="yellow",
                )
            )
            return ""
        return self._approve_and_continue(action)

    def _approve_all(self) -> str:
        actions = self.services.store.list_actions(self.project.id)
        proposed = [a for a in actions if a.status is ActionStatus.PROPOSED]
        if not proposed:
            self.console.print("[dim]No pending actions to approve.[/]")
            return ""
        self.console.print(
            f"[yellow]Approve {len(proposed)} action(s)? Type 'yes' to confirm.[/]"
        )
        if not self.input.confirm():
            self.console.print("[dim]Batch approval cancelled.[/]")
            return ""
        events: list[str] = []
        for action in proposed:
            ctx = self._approve_and_continue(action)
            if ctx:
                events.append(ctx)
        return "\n---\n".join(events)

    def _command(self, value: str) -> bool:
        command, _, argument = value.partition(" ")
        if command in {"/quit", "/q"}:
            return True

        if command == "/help":
            self.console.print(help_panel())

        elif command == "/status":
            counts = self.services.store.memory_counts(self.project.id)
            self.console.print(
                f"[bold]Project:[/] {self.project.name}\n"
                f"[bold]Session:[/] {self.session.name}\n"
                f"[bold]Messages:[/] {counts.messages}\n"
                f"[bold]Decisions:[/] {counts.decisions}\n"
                f"[bold]Repo Notes:[/] {counts.repo_notes}"
            )

        elif command == "/history":
            messages = self.services.store.get_recent_messages(self.session.id, limit=25)
            if not messages:
                self.console.print("[dim]No messages in this session.[/]")
            else:
                for msg in messages:
                    prefix = "[bold cyan]You:[/]" if msg.role == "user" else "[bold green]PM:[/]"
                    display = msg.content[:200].replace("\n", " ")
                    self.console.print(f"{prefix} {display}")

        elif command == "/clear":
            self.console.clear()

        elif command == "/summary":
            summary = self.services.summaries.create(self.project.id, self.session.id)
            print_markdown(self.console, summary_markdown(summary))

        elif command == "/decisions":
            decisions = self.services.decisions.list(self.project.id)
            if not decisions:
                self.console.print("[dim]No decisions.[/]")
            for decision in decisions:
                print_markdown(self.console, decision_markdown(decision))

        elif command == "/decision":
            self._manual_decision(argument)

        elif command == "/accept":
            decision_id = argument.strip()
            if not decision_id:
                event_text = self._resolve_pending_decisions()
                if event_text:
                    self._chat(event_text)
            else:
                self.services.decisions.accept(self.project.id, decision_id)
                self.console.print("[green]Decision accepted.[/]")
                decisions = [
                    d for d in self.services.store.list_decisions(self.project.id)
                    if d.id == decision_id
                ]
                if decisions:
                    self._chat(self._format_decision_accepted(decisions[0]))

        elif command == "/reject-decision":
            decision_id = argument.strip()
            if not decision_id:
                event_text = self._resolve_pending_decisions()
                if event_text:
                    self._chat(event_text)
            else:
                self.services.decisions.reject(self.project.id, decision_id)
                self.console.print("[red]Decision rejected.[/]")
                decisions = [
                    d for d in self.services.store.list_decisions(self.project.id)
                    if d.id == decision_id
                ]
                if decisions:
                    self._chat(self._format_decision_rejected(decisions[0]))

        elif command == "/actions":
            action_filter = argument.strip().lower()
            actions = self.services.store.list_actions(self.project.id)
            if not actions:
                self.console.print("[dim]No actions recorded.[/]")
            elif action_filter == "pending":
                pending = [a for a in actions if a.status is ActionStatus.PROPOSED]
                self.console.print(actions_pending_list(pending))
            elif action_filter == "recent":
                self.console.print(actions_table(actions[:10], title="Recent Actions"))
            else:
                proposed = [a for a in actions if a.status is ActionStatus.PROPOSED]
                recent = [a for a in actions if a.status is not ActionStatus.PROPOSED][:10]
                if proposed:
                    self.console.print(actions_pending_list(proposed))
                if recent:
                    self.console.print(actions_table(recent, title="Recent Actions"))

        elif command in {"/approve", "/approve-all", "/accept-all", "/approve-all-pending"}:
            argument = argument.strip()
            if command in {"/approve-all", "/accept-all", "/approve-all-pending"}:
                event_text = self._approve_all()
                if event_text:
                    self._chat(event_text)
            elif argument == "all":
                event_text = self._approve_all()
                if event_text:
                    self._chat(event_text)
            elif not argument:
                self.console.print("[yellow]Usage: /approve <uuid> or /approve all[/]")
                self.console.print(
                    "[dim]Use /actions pending to see actions awaiting approval.[/]"
                )
            else:
                event_text = self._approve_single(argument)
                if event_text:
                    self._chat(event_text)

        elif command == "/reject":
            action_id = argument.strip()
            if not action_id:
                self.console.print("[yellow]Usage: /reject <uuid>[/]")
                return False
            action = self.services.store.get_action(action_id)
            if action is None:
                self.console.print(f"[red]Unknown action: {action_id}[/]")
                return False
            if action.status is not ActionStatus.PROPOSED:
                self.console.print(
                    f"[yellow]Action {action.id[:8]} is {action.status.value} "
                    "and cannot be rejected.[/]"
                )
                return False
            self.services.actions.reject(action_id)
            self.services.store.add_message(
                self.session.id, "system",
                f"[action_outcome] Action rejected by user: {action.operation}"
            )
            self.console.print(f"[red]Action {action_id[:8]} rejected.[/]")
            self._chat(self._format_action_rejected(action))

        elif command == "/retry":
            previous_id = argument.strip()
            if not previous_id:
                self.console.print("[yellow]Usage: /retry <action-id>[/]")
            else:
                retried = self.services.actions.retry(previous_id, self.session.id)
                if retried.status.value == "rejected":
                    self.console.print(
                        Panel(
                            "The action was re-evaluated and is still blocked by current policy.",
                            title="Retry Rejected",
                            border_style="red",
                        )
                    )
                else:
                    self.console.print(
                        f"[green]Retry proposed:[/] {retried.id[:8]} \u2014 "
                        f"{retried.operation} ({retried.status.value})"
                    )

        elif command == "/action-result":
            self._action_result(argument)

        elif command == "/refresh":
            action = self.services.context.propose_refresh(
                self.project.id, self.session.id, self.project.canonical_path
            )
            self.console.print(action_panel(action))

        elif command == "/integrations":
            for integration in self.services.integrations.list(
                self.project.id, self.project.canonical_path
            ):
                self.console.print(integration_panel(integration))

        elif command == "/integration":
            integration = self.services.integrations.get(
                self.project.id,
                self.project.canonical_path,
                argument,
            )
            if integration is None:
                self.console.print("[red]Unknown integration. Use /integrations.[/]")
            else:
                self.console.print(integration_panel(integration))

        elif command == "/connect":
            if argument.strip().lower() != "github":
                self.console.print("[yellow]Usage: /connect github[/]")
            else:
                try:
                    action = self.services.integrations.propose_connect_github(
                        self.project.id, self.session.id
                    )
                except ValueError as exc:
                    self.console.print(f"[red]{exc}[/]")
                else:
                    self.console.print(action_panel(action))

        elif command == "/permissions":
            self._list_permissions()

        elif command == "/permission":
            action_id = argument.strip()
            if not action_id:
                self.console.print("[yellow]Usage: /permission <action-id>[/]")
                self.console.print(
                    "[dim]Use /actions pending to find an action ID to create a rule for.[/]"
                )
            else:
                self._add_permission_for_action(action_id)

        elif command in {"/perm", "/perms"}:
            self._list_permissions()

        elif command == "/perm-revoke":
            parts = argument.strip().split()
            if not parts:
                self.console.print("[yellow]Usage: /perm-revoke <rule-id>[/]")
                self.console.print("[dim]Use /permissions to see rule IDs.[/]")
            else:
                self.services.store.revoke_approval_rule(parts[0], self.project.id)
                self.console.print(f"[red]Rule {parts[0][:8]} revoked.[/]")

        else:
            self.console.print("[red]Unknown command. Use /help to see available commands.[/]")

        return False

    def _list_permissions(self) -> None:
        rules = self.services.store.list_approval_rules(self.project.id)
        if not rules:
            self.console.print("[dim]No approval rules configured.[/]")
            self.console.print(
                "[yellow]Tip:[/] Approve an action with 'y' (always approve) "
                "to create a rule, or use /permission <action-id>."
            )
            return
        self.console.print("[bold]Active Approval Rules:[/]")
        for rule in rules:
            parts = (
                f"[cyan]{rule.id[:8]}[/] "
                f"[yellow]{rule.action_type or '*'}:{rule.tool_category or '*'}"
                f":{rule.operation or '*'}[/] "
                f"[dim]{rule.reason}[/]"
            )
            self.console.print(f"  {parts}")

    def _add_permission_for_action(self, action_id: str) -> None:
        action = self.services.store.get_action(action_id)
        if action is None:
            self.console.print(f"[red]Unknown action: {action_id}[/]")
            return
        rule = make_approval_rule(
            self.project.id,
            action,
            reason=f"Always approve {action.operation} ({action.action_type.value})",
        )
        self.services.store.add_approval_rule(rule)
        self.console.print(
            f"[green]Rule created:[/] always approve {action.operation} "
            f"({action.action_type.value})"
        )

    def _manual_decision(self, argument: str) -> None:
        parts = [part.strip() for part in argument.split("|")]
        if len(parts) != 4:
            self.console.print(
                "[yellow]Usage: /decision topic | title | decision | reason[/]"
            )
            return
        decision = self.services.store.add_decision(
            self.project.id,
            self.session.id,
            *parts,
            status=DecisionStatus.PROPOSED,
        )
        self.console.print(f"[green]Decision proposed:[/] {decision.id}")

    def _action_result(self, argument: str) -> None:
        action_id, separator, payload = argument.partition(" ")
        if not separator:
            self.console.print(
                "[yellow]Usage: /action-result <id> <JSON with exit_code/stdout/stderr/result>[/]"
            )
            return
        data = json.loads(payload)
        action = self.services.actions.record_outcome(
            action_id,
            exit_code=data.get("exit_code"),
            stdout=data.get("stdout", ""),
            stderr=data.get("stderr", ""),
            result=data.get("result", {}),
            correlation_id=data.get("correlation_id"),
        )
        render_action_outcome(
            self.console,
            exit_code=data.get("exit_code"),
            stdout=data.get("stdout", ""),
            stderr=data.get("stderr", ""),
            result=data.get("result", {}),
        )
        self.console.print(f"[green]Outcome recorded:[/] {action.status.value}")
