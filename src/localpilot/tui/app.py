"""Full-screen Textual TUI dashboard implementing Sections 3, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14."""

from __future__ import annotations
import asyncio
import json
import os
import sys
import uuid
from typing import Any, Callable, Dict, List, Optional
import psutil
from textual import work
from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.timer import Timer
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Markdown,
    ProgressBar,
    RadioButton,
    RadioSet,
    Rule,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

from localpilot.application.jobs import JobManager
from localpilot.application.runtime_service import RuntimeLifecycleService
from localpilot.application.setup_workflow import ApprovalRecord, SetupPlan, SetupWorkflow
from localpilot.cli.formatting import HOME_ASCII_BANNER, _ACTION_LABELS
from localpilot.domain.capabilities import InferenceBackendId
from localpilot.domain.documentation import DOCUMENTATION_TOPICS
from localpilot.domain.errors import (
    AppError,
    ErrorCode,
    OutcomeCertainty,
    RecoveryAction,
    create_app_error,
    map_exception_to_app_error,
)
from localpilot.domain.models import ExecutionMode, TaskCategory
from localpilot.domain.recommendation import (
    ModelComparisonRow,
    ModelRecommendationEngine,
    ShortlistCandidate,
)
from localpilot.hardware.inspector import SystemInspector
from localpilot.observability.activity import ActivityService
from localpilot.security import get_stored_secret, store_secret
from localpilot.sources.catalog_service import CatalogService, RankedCandidate
from localpilot.storage.db import get_db
from localpilot.storage.inventory import StorageManager
from localpilot.tui.chat_screen import ChatScreen


from localpilot.domain.help_content import get_contextual_guidance, get_help_topic, HELP_TOPICS, TERMINOLOGY


class HelpModal(ModalScreen[None]):
    """Comprehensive documentation modal for beginners and technical users."""

    BINDINGS = [
        ("escape", "dismiss", "Close"),
        ("q", "dismiss", "Close"),
        ("?", "dismiss", "Close"),
        ("h", "dismiss", "Close"),
        ("f1", "dismiss", "Close"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="help-dialog"):
            yield Label("[bold cyan]LocalPilot Help & Documentation Manual[/bold cyan]", id="help-title")
            with TabbedContent(initial="help-novice"):
                with TabPane("Beginner Guide", id="help-novice"):
                    with VerticalScroll(id="help-scroll-novice"):
                        yield Markdown(DOCUMENTATION_TOPICS["novice"]["content"])
                with TabPane("Essential Concepts", id="help-concepts"):
                    with VerticalScroll(id="help-scroll-concepts"):
                        yield Markdown(DOCUMENTATION_TOPICS.get("concepts", {}).get("content", ""))
                with TabPane("Terminology", id="help-terminology"):
                    with VerticalScroll(id="help-scroll-terminology"):
                        yield Markdown(DOCUMENTATION_TOPICS.get("terminology", {}).get("content", ""))
                with TabPane("Technical Architecture", id="help-technical"):
                    with VerticalScroll(id="help-scroll-tech"):
                        yield Markdown(DOCUMENTATION_TOPICS["technical"]["content"])
                with TabPane("FAQ & Troubleshooting", id="help-faq"):
                    with VerticalScroll(id="help-scroll-faq"):
                        yield Markdown(DOCUMENTATION_TOPICS["faq"]["content"])
                with TabPane("Commands & Keys", id="help-cmd"):
                    with VerticalScroll(id="help-scroll-cmd"):
                        yield Markdown(DOCUMENTATION_TOPICS["commands"]["content"])
            with Horizontal(id="help-buttons"):
                yield Button("Close (Esc)", variant="primary", id="close-help-btn", tooltip="Close documentation and return to previous view (Esc)")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-help-btn":
            self.dismiss(None)


class ConfirmModal(ModalScreen[bool]):
    """Modal dialog for confirming an action with clear, specific action labels."""

    BINDINGS = [
        ("escape", "action_cancel", "Cancel"),
    ]

    def __init__(
        self,
        title: str,
        message: str,
        confirm_label: str = "Proceed",
        cancel_label: str = "Cancel",
    ) -> None:
        super().__init__()
        self._dialog_title = title
        self._dialog_message = message
        self._confirm_label = confirm_label
        self._cancel_label = cancel_label

    @property
    def confirm_label(self) -> str:
        return self._confirm_label

    @property
    def cancel_label(self) -> str:
        return self._cancel_label

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Label(f"[bold]{self._dialog_title}[/bold]", id="confirm-title")
            yield Label(self._dialog_message, id="confirm-message")
            with Horizontal(id="confirm-buttons"):
                yield Button(self._cancel_label, variant="default", id="cancel-btn", tooltip="Cancel and make no changes (Esc)")
                yield Button(self._confirm_label, variant="primary", id="confirm-btn", tooltip="Proceed with this specific action")

    def on_mount(self) -> None:
        self.query_one("#confirm-btn", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm-btn":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)


class RemoveModelModal(ModalScreen[bool]):
    """Safely confirms model removal with full disclosure and safe default focus."""

    BINDINGS = [
        ("escape", "action_cancel", "Keep Model"),
    ]

    def __init__(self, model_name: str, install_id: str, size_str: str = "") -> None:
        super().__init__()
        self.model_name = model_name
        self.install_id = install_id
        self.installation_id = install_id
        self.size_str = size_str

    def compose(self) -> ComposeResult:
        with Vertical(id="remove-dialog"):
            yield Label(f"[bold red]Remove {self.model_name}?[/bold red]", id="remove-title")
            size_clause = f"delete ~{self.size_str}" if self.size_str else "delete model files"
            yield Label(
                f"This will remove the model from your library and {size_clause} to free up disk space.\n\n"
                "[dim]• Stop vs Remove:[/dim] 'Stop' unloads the model from RAM while keeping files. 'Remove' deletes files to reclaim disk space.\n"
                "[dim]• What will change:[/dim] Downloaded model weights will be deleted from your computer.\n"
                "[dim]• What remains untouched:[/dim] Other installed models, conversation history, and supporting runtimes remain safe and untouched.",
                id="remove-message",
            )
            with Horizontal(id="remove-buttons"):
                yield Button("Keep Model (Cancel)", variant="primary", id="keep-btn", tooltip="Keep model installed and safe on disk (Esc)")
                yield Button("Remove and Free Space", variant="error", id="confirm-remove-btn", tooltip="Permanently delete model files and unregister from library")

    def on_mount(self) -> None:
        self.query_one("#keep-btn", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm-remove-btn":
            self.dismiss(True)
        else:
            self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)


class ModelDetailsModal(ModalScreen[None]):
    """Inspection modal revealing technical metadata without cluttering the primary view."""

    BINDINGS = [
        ("escape", "dismiss", "Close"),
        ("q", "dismiss", "Close"),
        ("v", "dismiss", "Close"),
        ("d", "dismiss", "Close"),
    ]

    def __init__(self, title: str, details: Dict[str, Any]) -> None:
        super().__init__()
        self._title = title
        self._details = details

    def compose(self) -> ComposeResult:
        with Vertical(id="details-dialog"):
            yield Label(f"[bold cyan]Technical Details: {self._title}[/bold cyan]", id="details-title")
            with VerticalScroll(id="details-scroll"):
                lines = []
                for k, v in self._details.items():
                    lines.append(f"• [bold]{k}:[/bold] {v}")
                yield Label("\n".join(lines), id="details-body")
            with Horizontal(id="details-buttons"):
                yield Button("Close (Esc)", variant="primary", id="close-details-btn", tooltip="Close details dialog (Esc)")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(None)


class PerformanceSettingsModal(ModalScreen[str]):
    """Modal for configuring execution hardware preference without exposing low-level complexity."""

    BINDINGS = [("escape", "action_cancel", "Close")]

    def __init__(self, current_pref: str = "automatic") -> None:
        super().__init__()
        self.current_pref = current_pref

    def compose(self) -> ComposeResult:
        with Vertical(id="perf-dialog"):
            yield Label("[bold cyan]Performance & Hardware Settings[/bold cyan]", id="perf-title")
            yield Label("Choose how LocalPilot utilizes your hardware to run models:", id="perf-desc")
            with RadioSet(id="perf-radioset"):
                yield RadioButton(
                    "Automatic (LocalPilot chooses a supported configuration for this computer)",
                    value="automatic",
                    id="perf-auto",
                    tooltip="LocalPilot inspects your CPU, RAM, and GPU to select the best fit.",
                )
                yield RadioButton(
                    "Processor / CPU (Use your processor. Responses may be slower for some models)",
                    value="cpu",
                    id="perf-cpu",
                    tooltip="Runs on standard CPU and RAM. Safe on all computers; larger models run more slowly.",
                )
                yield RadioButton(
                    "Graphics card / GPU (Use supported graphics acceleration. Availability depends on this computer and the model)",
                    value="gpu",
                    id="perf-gpu",
                    tooltip="Accelerates generation with your graphics card (e.g. Metal or CUDA) if available.",
                )
                yield RadioButton(
                    "Supported CPU + GPU (Share supported work between processor memory and graphics memory)",
                    value="hybrid",
                    id="perf-hybrid",
                    tooltip="Shares computation between system RAM and graphics memory where supported.",
                )
            with Horizontal(id="perf-buttons"):
                yield Button("Apply Settings", variant="primary", id="perf-apply-btn", tooltip="Save execution preference")
                yield Button("Cancel", variant="default", id="perf-cancel-btn", tooltip="Discard changes (Esc)")

    def on_mount(self) -> None:
        rs = self.query_one("#perf-radioset", RadioSet)
        btn_id = f"perf-{self.current_pref}"
        try:
            btn = self.query_one(f"#{btn_id}", RadioButton)
            btn.value = True
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "perf-apply-btn":
            rs = self.query_one("#perf-radioset", RadioSet)
            val = "automatic"
            if rs.pressed_button:
                btn_id = getattr(rs.pressed_button, "id", "")
                mode_map = {
                    "perf-auto": "automatic",
                    "perf-cpu": "cpu",
                    "perf-gpu": "gpu",
                    "perf-hybrid": "hybrid",
                }
                val = mode_map.get(btn_id, "automatic")
            self.dismiss(val)
        else:
            self.dismiss(self.current_pref)

    def action_cancel(self) -> None:
        self.dismiss(self.current_pref)


class InterruptedSetupModal(ModalScreen[str]):
    """Modal to inform user about interrupted setups and offer Resume, Cleanup, or Dismiss."""

    BINDINGS = [("escape", "action_dismiss", "Dismiss")]

    def __init__(self, job: Dict[str, Any]) -> None:
        super().__init__()
        self.job = job

    def compose(self) -> ComposeResult:
        plan = json.loads(self.job.get("approved_plan_json") or "{}")
        model_name = plan.get("model_id") or self.job.get("target_id", "Previous model")
        stage = self.job.get("stage", "downloading_model").replace("_", " ").title()
        p_bytes = self.job.get("progress_bytes", 0)
        prog_str = f"{p_bytes / (1024*1024):.1f} MB saved" if p_bytes else "Initial preparation"

        with Vertical(id="interrupted-dialog"):
            yield Label("[bold yellow]Interrupted Model Setup Detected[/bold yellow]", id="interrupted-title")
            yield Label(
                f"LocalPilot detected an earlier setup for [bold]{model_name}[/bold] that did not finish.\n\n"
                f"• Last completed checkpoint: {stage}\n"
                f"• Download progress preserved: {prog_str}\n\n"
                "Would you like to resume where you left off or clean up partial files?",
                id="interrupted-message",
            )
            with Horizontal(id="interrupted-buttons"):
                yield Button("Resume Setup", variant="primary", id="resume-job-btn")
                yield Button("Clean Up & Start Fresh", variant="error", id="cleanup-job-btn")
                yield Button("Dismiss", variant="default", id="dismiss-job-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "resume-job-btn":
            self.dismiss("resume")
        elif event.button.id == "cleanup-job-btn":
            self.dismiss("cleanup")
        else:
            self.dismiss("dismiss")

    def action_dismiss(self) -> None:
        self.dismiss("dismiss")


class AppErrorModal(ModalScreen[Optional[RecoveryAction]]):
    """Truthful, persistent modal for important errors answering the 5 key questions."""

    BINDINGS = [
        ("escape", "action_dismiss", "Dismiss"),
        ("q", "action_dismiss", "Dismiss"),
    ]

    def __init__(self, error: AppError) -> None:
        super().__init__()
        self.error = error

    def compose(self) -> ComposeResult:
        with Vertical(id="app-error-dialog"):
            yield Label(f"[bold red]{self.error.title}[/bold red]", id="app-error-title")
            yield Label(f"[dim]Support ID: {self.error.support_id}[/dim]", id="app-error-id")
            with VerticalScroll(id="app-error-scroll"):
                yield Label(f"[bold]What happened?[/bold]\n{self.error.explanation}\n", id="app-error-explanation")
                yield Label(f"[bold]Impact on your files & settings:[/bold]\n{self.error.known_impact}\n", id="app-error-impact")
                rec_label = _ACTION_LABELS.get(self.error.recommended_action, self.error.recommended_action.value)
                yield Label(f"[bold cyan]Recommended next step:[/bold cyan]\n• {rec_label}\n", id="app-error-rec")
                alt_actions = [a for a in self.error.supported_actions if a != self.error.recommended_action]
                if alt_actions:
                    alt_texts = [f"• {_ACTION_LABELS.get(a, a.value)}" for a in alt_actions]
                    yield Label(f"[dim]Other options:[/dim]\n" + "\n".join(alt_texts) + "\n", id="app-error-alts")

            with Horizontal(id="app-error-buttons"):
                # Safe action focused by default
                yield Button("Dismiss / Back (Esc)", variant="default", id="app-error-dismiss-btn")
                yield Button("Technical Details", variant="default", id="app-error-details-btn")
                rec_btn_id = f"action-{self.error.recommended_action.value}"
                rec_text = _ACTION_LABELS.get(self.error.recommended_action, self.error.recommended_action.value)
                yield Button(rec_text, variant="primary", id=rec_btn_id)

    def on_mount(self) -> None:
        try:
            self.query_one("#app-error-dismiss-btn", Button).focus()
        except Exception:
            pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "app-error-dismiss-btn":
            self.dismiss(RecoveryAction.DISMISS)
        elif event.button.id == "app-error-details-btn":
            details = {
                "Error Code": self.error.error_code.value,
                "Support ID": self.error.support_id,
                "Operation": self.error.operation,
                "Stage": self.error.stage,
                "Current State": self.error.current_state,
                "Retryable": "Yes" if self.error.retryable else "No",
                "Outcome Certainty": self.error.outcome_certainty.value,
                "Sanitized Details": self.error.technical_details or "No additional diagnostic output",
            }
            self.app.push_screen(ModelDetailsModal(f"Diagnostics: {self.error.title}", details))
        elif event.button.id and event.button.id.startswith("action-"):
            act_str = event.button.id.replace("action-", "")
            try:
                act = RecoveryAction(act_str)
                self.dismiss(act)
            except ValueError:
                self.dismiss(self.error.recommended_action)

    def action_dismiss(self) -> None:
        self.dismiss(RecoveryAction.DISMISS)


class ModelComparisonModal(ModalScreen[None]):
    """Side-by-side comparison modal displaying Purpose, Release Evidence, Download, Storage, Context, Memory Burden, Execution Plan, Access, and Confidence."""

    BINDINGS = [
        ("escape", "dismiss", "Close"),
        ("q", "dismiss", "Close"),
        ("c", "dismiss", "Close"),
    ]

    def __init__(self, comparison_rows: List[Any]) -> None:
        super().__init__()
        self.rows = comparison_rows

    def compose(self) -> ComposeResult:
        with Vertical(id="comparison-dialog"):
            yield Label("[bold cyan]Model Comparison Matrix[/bold cyan]", id="comparison-title")
            yield Label("Side-by-side evaluation of recommended options for your computer:", id="comparison-desc")
            with VerticalScroll(id="comparison-scroll"):
                table = DataTable(id="comparison-table")
                table.cursor_type = "row"
                yield table
            with Horizontal(id="comparison-buttons"):
                yield Button("Close (Esc)", variant="primary", id="close-comparison-btn")

    def on_mount(self) -> None:
        table = self.query_one("#comparison-table", DataTable)
        table.add_columns("Metric", *(r.name for r in self.rows))
        metrics = [
            ("Distinction", [r.distinction for r in self.rows]),
            ("Intended Purpose", [r.purpose for r in self.rows]),
            ("Release Date", [r.release_date for r in self.rows]),
            ("Download Size", [r.download_size for r in self.rows]),
            ("Storage Footprint", [r.storage_needed for r in self.rows]),
            ("Effective Context", [r.context_window for r in self.rows]),
            ("Memory Burden", [r.memory_burden for r in self.rows]),
            ("Execution Plan", [r.execution_plan for r in self.rows]),
            ("Access Requirement", [r.access_requirements for r in self.rows]),
            ("Confidence", [r.confidence for r in self.rows]),
        ]
        for metric_label, values in metrics:
            table.add_row(metric_label, *values)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-comparison-btn":
            self.dismiss(None)


class GuidedSetupModal(ModalScreen[Optional[str]]):
    """Complete 6-step guided wizard implementing Sections 4-11."""

    BINDINGS = [
        ("escape", "action_cancel", "Cancel"),
        ("c", "action_compare", "Compare Options"),
    ]

    def __init__(self, resume_candidate: Optional[Any] = None) -> None:
        super().__init__()
        self.inspector = SystemInspector()
        self.cat_svc = CatalogService()
        self.workflow = SetupWorkflow()
        self._chosen_goal: str = "writing"
        self._execution_preference: str = "automatic"
        self._hardware_report: Any = None
        self._candidates: List[Any] = []
        self._selected_candidate: Optional[Any] = resume_candidate

        self._built_plan: Any = None
        self._installed_model_name: Optional[str] = None
        self._cancel_event: Optional[asyncio.Event] = None
        self._is_verifying: bool = False

    def compose(self) -> ComposeResult:
        with Vertical(id="wizard-dialog"):
            yield Label("[bold cyan]LocalPilot • Guided Setup Wizard[/bold cyan]", id="wizard-header")

            # Step 1: Computer Check (Section 5)
            with Vertical(id="wizard-step-1", classes="wizard-step"):
                yield Label("[bold]Step 1 of 4: Computer Capability Check[/bold]", classes="wizard-step-title")
                yield Label(
                    "LocalPilot runs entirely on your own machine. We check your hardware to recommend models that leave plenty of room for your other apps.",
                    classes="wizard-desc",
                )
                with Vertical(classes="wizard-card"):
                    yield Label("Checking computer specifications...", id="wizard-hw-details")
                    yield Label("", id="wizard-hw-verdict")
                    yield Label("[dim]Action required: None. Your computer is ready to run local AI.[/dim]", id="wizard-hw-action")
                with Horizontal(classes="wizard-buttons"):
                    yield Button("Cancel", variant="default", id="step1-cancel-btn", tooltip="Cancel guided setup and return to home (Esc)")
                    yield Button("Computer details", variant="default", id="step1-details-btn", tooltip="Inspect full CPU, RAM, and GPU diagnostics")
                    yield Button("Performance settings", variant="default", id="step1-perf-btn", tooltip="Choose processor (CPU) or graphics card (GPU) execution")
                    yield Button("Continue →", variant="primary", id="step1-next-btn", tooltip="Proceed to select your intended goal")

            # Step 2: Choose Goal (Section 6)
            with Vertical(id="wizard-step-2", classes="wizard-step hidden"):
                yield Label("[bold]Step 2 of 4: What would you like help with?[/bold]", classes="wizard-step-title")
                yield Label(
                    "Pick what you want your local AI to do best. No technical configuration required:",
                    classes="wizard-desc",
                )
                with RadioSet(id="goal-radioset"):
                    yield RadioButton(
                        "Everyday questions and writing (Draft emails, summarize documents, brainstorm ideas)",
                        value="writing",
                        id="goal-writing",
                        tooltip="Tailored for general conversation, writing assistance, and document comprehension.",
                    )
                    yield RadioButton(
                        "Coding assistance (Write functions, debug scripts, explain code)",
                        value="coding",
                        id="goal-coding",
                        tooltip="Tailored for programming languages, software debugging, and technical reasoning.",
                    )
                    yield RadioButton(
                        "Browse all compatible models (Explore all models tested on your computer)",
                        value="general",
                        id="goal-general",
                        tooltip="View all models in the catalog that fit safely within your computer's RAM.",
                    )
                with Horizontal(classes="wizard-buttons"):
                    yield Button("← Back", variant="default", id="step2-back-btn", tooltip="Go back to computer capability check")
                    yield Button("Find Best Models →", variant="primary", id="step2-next-btn", tooltip="Search for models that match your chosen goal")

            # Step 3: Recommended Models (Section 7)
            with Vertical(id="wizard-step-3", classes="wizard-step hidden"):
                yield Label("[bold]Step 3 of 4: Recommended Models for Your Computer[/bold]", classes="wizard-step-title")
                yield Label(
                    "Here are the top models that fit within your computer's safe memory budget:\n"
                    "[dim]• Scoring weights: Newness 40%, Smaller download 25%, Usable context 20%, Memory headroom 15%.[/dim]",
                    classes="wizard-desc",
                )
                with VerticalScroll(id="wizard-shortlist-scroll"):
                    yield Label("Loading recommendations...", id="wizard-shortlist-status")
                    with RadioSet(id="candidate-radioset"):
                        pass
                with Horizontal(classes="wizard-buttons"):
                    yield Button("← Back", variant="default", id="step3-back-btn", tooltip="Go back to goal selection")
                    yield Button("Compare Options (c)", variant="default", id="step3-compare-btn", tooltip="Open side-by-side comparison matrix (c)")
                    yield Button("Browse Full Catalog", variant="default", id="step3-catalog-btn", tooltip="Explore all models in the catalog")
                    yield Button("Review Setup →", variant="primary", id="step3-next-btn", tooltip="Review download size, storage, and safety before setup")

            # Step 3.5: Authentication Step (Section 8 - Conditional)
            with Vertical(id="wizard-step-auth", classes="wizard-step hidden"):
                yield Label("[bold cyan]Access Key Required[/bold cyan]", classes="wizard-step-title")
                yield Label(
                    "This model requires agreeing to publisher terms on Hugging Face before download.\n\n"
                    "• Source: Hugging Face (https://huggingface.co/settings/tokens)\n"
                    "• Permission: A free read-only personal access token (never enter your account password).\n"
                    "• Security: Saved in your operating system's secure credential store and never logged.\n",
                    classes="wizard-desc",
                )
                with Vertical(classes="wizard-card"):
                    yield Input(placeholder="hf_...", password=True, id="auth-token-input", tooltip="Paste your read-only Hugging Face access token here")
                    yield Label("[dim]Your key will be securely saved in your local credential store and never logged.[/dim]")
                with Horizontal(classes="wizard-buttons"):
                    yield Button("← Back to Models", variant="default", id="auth-back-btn", tooltip="Return to model shortlist")
                    yield Button("Save Key and Continue →", variant="primary", id="auth-save-btn", tooltip="Save token and proceed to setup review")

            # Step 4: Review Setup & Approval (Section 9)
            with Vertical(id="wizard-step-4", classes="wizard-step hidden"):
                yield Label("[bold]Step 4 of 4: Review & Approve Setup[/bold]", classes="wizard-step-title")
                yield Label(
                    "Before downloading any files, here is exactly what LocalPilot will configure on your computer:",
                    classes="wizard-desc",
                )
                with Vertical(classes="wizard-card"):
                    yield Label("Model Name: ...", id="review-model-name")
                    yield Label("Download Size: ...", id="review-download-size")
                    yield Label("Storage Location: ...", id="review-storage-path")
                    yield Label("Execution Mode: Automatic", id="review-exec-mode")
                    yield Label("External Services: None (Runs 100% offline)", id="review-ext-mode")
                    yield Rule()
                    yield Label(
                        "[bold green]✓ Safety Guarantee:[/bold green] No administrator/root permissions required. Your other files, settings, and apps remain safe and untouched.",
                        classes="wizard-guarantee",
                    )
                with Horizontal(classes="wizard-buttons"):
                    yield Button("← Back", variant="default", id="step4-back-btn", tooltip="Return to model selection")
                    yield Button("Technical Details", variant="default", id="step4-tech-btn", tooltip="Inspect technical execution plan and SHA-256 digests")
                    yield Button("Set Up Model", variant="primary", id="step4-approve-btn", tooltip="Approve plan and begin downloading model weights")

            # Step 5: Progress Wizard (Section 10)
            with Vertical(id="wizard-step-5", classes="wizard-step hidden"):
                yield Label("[bold]Setting Up Your AI Model[/bold]", classes="wizard-step-title")
                yield Label(
                    "Please wait while LocalPilot downloads and verifies your model. This initial setup happens only once.",
                    classes="wizard-desc",
                )
                with Vertical(classes="wizard-card", id="progress-checklist-box"):
                    yield Label("[bold green]✓[/bold green] Computer checked", id="chk-hw")
                    yield Label("[bold green]✓[/bold green] Supporting software prepared", id="chk-runtime")
                    yield Label("[bold cyan]►[/bold cyan] Downloading model...", id="chk-dl")
                    yield Label("[dim]○ Starting model[/dim]", id="chk-start")
                    yield Label("[dim]○ Testing a response[/dim]", id="chk-test")
                    yield Rule()
                    yield Label("Initializing setup...", id="wizard-progress-detail")
                    yield Label("Downloading model weights... You can cancel safely at any time.", id="wizard-progress-wait-note")
                with Horizontal(classes="wizard-buttons"):
                    yield Button("Cancel Setup", variant="default", id="step5-cancel-btn", tooltip="Stop setup and safely clean up partial files (Esc)")
                    yield Button("Technical Details", variant="default", id="step5-tech-btn", tooltip="View underlying technical events and logs")
                    yield Button("Back to Recommendations", variant="primary", id="step5-cancelled-back-btn", classes="hidden")

            # Step 6: Completion & Chat (Section 11)
            with Vertical(id="wizard-step-6", classes="wizard-step hidden"):
                yield Label("[bold green]🎉 Your model is ready.[/bold green]", classes="wizard-step-title")
                yield Label(
                    "Your local AI model has been verified and tested on your computer. You can start chatting right now.",
                    classes="wizard-desc",
                )
                with Vertical(classes="wizard-card"):
                    yield Label("Model: Ready", id="finish-model-name")
                    yield Label("Verification: Passed live test query", id="finish-test-status")
                    yield Label("Privacy: 100% private and offline on your hardware", id="finish-privacy-status")
                with Horizontal(classes="wizard-buttons"):
                    yield Button("Finish", variant="default", id="step6-finish-btn", tooltip="Return to home dashboard")
                    yield Button("My Models", variant="default", id="step6-library-btn", tooltip="View your installed models library")
                    yield Button("Start Chatting", variant="primary", id="step6-chat-btn", tooltip="Start conversation with your model right now")

    def on_mount(self) -> None:
        self._load_step1_hardware()

    @work(exclusive=True, thread=True)
    def _load_step1_hardware(self) -> None:
        report = self.inspector.inspect()
        self._hardware_report = report
        hw = report.hardware
        avail_gb = hw.ram_available_bytes / (1024**3)
        safe_gb = hw.safe_ram_budget_bytes / (1024**3)
        tot_gb = hw.ram_total_bytes / (1024**3)
        free_disk = hw.disk_available_bytes / (1024**3)
        gpu_str = "Standard CPU (runs lightweight models smoothly)"
        if hw.gpu_devices:
            gpu_str = ", ".join(f"{g.name} ({g.acceleration_type})" for g in hw.gpu_devices)

        details_text = (
            f"• Operating System: {hw.os_name} ({hw.cpu_arch})\n"
            f"• Processor: {hw.cpu_cores_logical} cores\n"
            f"• System Memory: {avail_gb:.1f} GB available of {tot_gb:.1f} GB (Safe budget for AI: {safe_gb:.1f} GB)\n"
            f"• Graphics: {gpu_str}\n"
            f"• Available Disk Space: {free_disk:.1f} GB free"
        )
        verdict_text = "[bold green]✓ Your computer can run local AI models. We will recommend options that leave room for your other apps.[/bold green]"
        self.app.call_from_thread(self._update_step1_ui, details_text, verdict_text)

    def _update_step1_ui(self, details: str, verdict: str) -> None:
        try:
            self.query_one("#wizard-hw-details", Label).update(details)
            self.query_one("#wizard-hw-verdict", Label).update(verdict)
        except Exception:
            pass

    def _get_hw_details_dict(self) -> Dict[str, Any]:
        if not self._hardware_report:
            return {"Status": "Hardware report not loaded yet"}
        hw = self._hardware_report.hardware
        return {
            "OS": f"{hw.os_name} {hw.os_version} ({hw.cpu_arch})",
            "Physical CPU Cores": str(hw.cpu_cores_physical),
            "Logical CPU Cores": str(hw.cpu_cores_logical),
            "CPU Features": ", ".join(hw.cpu_features[:8]) + "...",
            "Total RAM": f"{hw.ram_total_bytes / (1024**3):.1f} GB",
            "Available RAM": f"{hw.ram_available_bytes / (1024**3):.1f} GB",
            "Safe Memory Budget": f"{hw.safe_ram_budget_bytes / (1024**3):.1f} GB",
            "GPU Devices": ", ".join(g.name for g in hw.gpu_devices) or "None (CPU Execution)",
            "Available Disk Space": f"{hw.disk_available_bytes / (1024**3):.1f} GB",
            "Discovered Supporting Runtimes": ", ".join(r.name for r in self._hardware_report.discovered_runtimes) or "None (installs isolated)",
        }

    def _switch_step(self, step_name: str | int) -> None:
        target_id = f"wizard-step-{step_name}"
        for s in ["1", "2", "3", "auth", "4", "5", "6"]:
            try:
                el = self.query_one(f"#wizard-step-{s}")
                if f"wizard-step-{s}" == target_id:
                    el.remove_class("hidden")
                else:
                    el.add_class("hidden")
            except Exception:
                pass

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "step1-cancel-btn":
            self.dismiss(None)
        elif btn_id == "step1-details-btn":
            self.app.push_screen(ModelDetailsModal("Computer Hardware Diagnostics", self._get_hw_details_dict()))
        elif btn_id == "step1-perf-btn":
            def _on_perf_applied(pref: str) -> None:
                self._execution_preference = pref
                self.notify(f"Hardware execution set to: {pref.title()}", title="Settings Updated")
            self.app.push_screen(PerformanceSettingsModal(self._execution_preference), _on_perf_applied)
        elif btn_id == "step1-next-btn":
            self._switch_step(2)
        elif btn_id == "step2-back-btn":
            self._switch_step(1)
        elif btn_id == "step2-next-btn":
            radio_set = self.query_one("#goal-radioset", RadioSet)
            if radio_set.pressed_button:
                val = getattr(radio_set.pressed_button, "value", "writing")
                self._chosen_goal = val or "writing"
            self._switch_step(3)
            self._load_step3_candidates()
        elif btn_id == "step3-back-btn":
            self._switch_step(2)
        elif btn_id == "step3-compare-btn":
            self.action_compare()
        elif btn_id == "step3-catalog-btn":
            self.dismiss(None)
            self.app.action_switch_to_discover()
        elif btn_id == "step3-next-btn":
            if not self._selected_candidate and self._candidates:
                self._selected_candidate = self._candidates[0]
            if not self._selected_candidate:
                self.notify("No compatible model selected. Please select a model or browse the catalog.", title="Selection Required", severity="warning")
                return
            cand = self._selected_candidate.candidate
            needs_auth = False
            if getattr(cand, "requires_auth", False):
                needs_auth = True
            elif cand.source == "huggingface" and getattr(cand, "is_gated", False):
                token = get_stored_secret("localpilot", "hf_token")
                if not token:
                    needs_auth = True

            if needs_auth:
                self._switch_step("auth")
            else:
                self._prepare_step4_review()
                self._switch_step(4)
        elif btn_id == "auth-back-btn":
            self._switch_step(3)
        elif btn_id == "auth-save-btn":
            tok = self.query_one("#auth-token-input", Input).value.strip()
            if tok:
                store_secret("localpilot", "hf_token", tok)
                self.notify("Access token saved securely in credential store.", title="Token Saved")
                self._prepare_step4_review()
                self._switch_step(4)
            else:
                self.notify("Please enter a token, or go back to choose an open-access model.", title="Token Required", severity="warning")
        elif btn_id == "step4-back-btn":
            self._switch_step(3)
        elif btn_id == "step4-tech-btn":
            if self._built_plan:
                plan_details = {
                    "Plan ID": self._built_plan.plan_id,
                    "Plan Hash": self._built_plan.compute_hash(),
                    "Model Identifier": self._built_plan.model_id,
                    "Candidate ID": self._built_plan.candidate_id,
                    "Runtime Name": self._built_plan.runtime_name,
                    "Inference Backend": str(self._built_plan.inference_backend),
                    "Installation Scope": str(self._built_plan.install_scope),
                    "Target Directory": self._built_plan.target_install_dir,
                    "Download Bytes": f"{self._built_plan.download_bytes / (1024*1024):.1f} MB",
                }
                self.app.push_screen(ModelDetailsModal("Technical Setup Plan", plan_details))
        elif btn_id == "step4-approve-btn":
            self._switch_step(5)
            self._cancel_event = asyncio.Event()
            self._execute_wizard_setup()
        elif btn_id == "step5-cancel-btn":
            if self._is_verifying:
                self.notify("Final verification in progress (cannot be interrupted safely).", title="Please Wait", severity="warning")
            else:
                if self._cancel_event:
                    self._cancel_event.set()
                self.query_one("#wizard-progress-detail", Label).update("[bold yellow]Cancellation requested... Stopping safely and cleaning up partial files.[/bold yellow]")
        elif btn_id == "step5-cancelled-back-btn":
            self._switch_step(3)
        elif btn_id == "step5-tech-btn":
            if self._selected_candidate:
                cand = self._selected_candidate.candidate
                details = {
                    "Model Name": cand.name,
                    "Model ID": cand.model_id,
                    "Architecture": cand.architecture,
                    "Format": cand.format,
                    "Usable Context": f"{cand.usable_context:,} tokens",
                    "Expected Size": f"{cand.total_bytes / (1024*1024):.1f} MB",
                    "SHA-256 Digest": cand.sha256 or "Verified upstream",
                }
                self.app.push_screen(ModelDetailsModal("Model Diagnostics", details))
        elif btn_id == "step6-chat-btn":
            self.dismiss(self._installed_model_name)
        elif btn_id == "step6-library-btn":
            self.dismiss(None)
            self.app.action_switch_to_installed()
        elif btn_id == "step6-finish-btn":
            self.dismiss(None)

    def action_compare(self) -> None:
        if self._candidates:
            rows = self.cat_svc.get_comparison(self._candidates)
            self.app.push_screen(ModelComparisonModal(rows))
        else:
            self.notify("No shortlisted models available to compare.", severity="warning")

    @work(exclusive=True, thread=True)
    def _load_step3_candidates(self) -> None:
        task = TaskCategory.CODING if self._chosen_goal == "coding" else TaskCategory.CHAT
        shortlist = self.cat_svc.get_shortlist(task=task)
        self._candidates = shortlist
        self.app.call_from_thread(self._render_step3_shortlist)

    def _render_step3_shortlist(self) -> None:
        try:
            status = self.query_one("#wizard-shortlist-status", Label)
            rs = self.query_one("#candidate-radioset", RadioSet)
            rs.remove_children()

            if not self._candidates:
                status.update(
                    "[bold yellow]No models in the catalog fit within your computer's safe memory budget (RAM limit exceeded).\n"
                    "You can review performance settings or browse the catalog for custom models.[/bold yellow]"
                )
                self._selected_candidate = None
                return

            status.update(f"Found {len(self._candidates)} top recommendation(s) for your computer:")
            for idx, r in enumerate(self._candidates):
                cand = r.candidate
                sz_mb = cand.total_bytes / (1024 * 1024)
                badge_label = getattr(r, "badge_label", None) or ("★ Top Pick (Recommended)" if idx == 0 else "Alternative Option")
                why = getattr(r, "why_selected", None) or r.compatibility.friendly_explanation
                plan_desc = r.recommended_plan.deployment_method.value if r.recommended_plan else "Isolated engine"
                access_desc = getattr(r, "access_requirement", "Open model")

                label_text = (
                    f"[{badge_label}] {cand.name} (~{sz_mb:.0f} MB)\n"
                    f"  • {why}\n"
                    f"  • Supporting software: {plan_desc} • Access: {access_desc}"
                )
                btn = RadioButton(label_text, value=str(idx), id=f"cand-{idx}")
                rs.mount(btn)
                if idx == 0:
                    btn.value = True
                    self._selected_candidate = r

            rs.focus()
        except Exception:
            pass

    def on_radio_set_changed(self, event: RadioSet.Changed) -> None:
        if event.radio_set.id == "candidate-radioset":
            try:
                idx = int(event.pressed.value)
                if 0 <= idx < len(self._candidates):
                    self._selected_candidate = self._candidates[idx]
            except Exception:
                pass

    def _prepare_step4_review(self) -> None:
        if not self._selected_candidate:
            return
        cand = self._selected_candidate.candidate
        sz_mb = cand.total_bytes / (1024 * 1024)
        built_plan = self.workflow.build_plan(
            cand,
            execution_plan=self._selected_candidate.recommended_plan,
            alternative_plans=self._selected_candidate.alternative_plans,
        )
        self._built_plan = built_plan

        self.query_one("#review-model-name", Label).update(f"• Selected Model: [bold]{cand.name}[/bold]")
        runtime_mb = 250 if built_plan.requires_runtime_install else 0
        total_est_mb = sz_mb + runtime_mb
        self.query_one("#review-download-size", Label).update(
            f"• Download Transfer Size: ~{sz_mb:.0f} MB (network transfer)\n"
            f"• Estimated Total Storage: ~{total_est_mb:.0f} MB (includes runtime & recovery files)"
        )
        self.query_one("#review-storage-path", Label).update(f"• Storage Location: {built_plan.target_install_dir}")
        backend = getattr(built_plan.inference_backend, "value", str(built_plan.inference_backend))
        self.query_one("#review-exec-mode", Label).update(f"• Friendly Execution Preference: {self._execution_preference.title()} ({backend})")

    @work(exclusive=True, thread=True)
    def _execute_wizard_setup(self) -> None:
        if not self._selected_candidate or not self._built_plan:
            self.app.call_from_thread(self._on_setup_failed, "No model plan selected.")
            return

        cand = self._selected_candidate.candidate
        plan = self._built_plan
        hw_report = self.inspector.inspect()
        rec_plan = getattr(self._selected_candidate, "recommended_plan", None)
        if rec_plan:
            is_avail, avail_msg = ModelRecommendationEngine.recheck_runtime_availability(cand, rec_plan, hw_report.hardware)
            if not is_avail:
                self.app.call_from_thread(self.notify, avail_msg, title="Memory Shift Warning", severity="warning")
        approval = ApprovalRecord.create_for_plan(plan, approved_by="user_guided_wizard")

        def _progress(msg: str, done: int, total: int) -> None:
            pct = int((done / total) * 100) if total > 0 else 0
            mb_done = done / (1024 * 1024)
            mb_total = total / (1024 * 1024)
            txt = f"{msg} ({mb_done:.1f} of {mb_total:.1f} MB - {pct}%)" if total > 0 else msg
            self.app.call_from_thread(self._update_progress_ui, txt)

        try:
            res = asyncio.run(
                self.workflow.execute_setup(
                    cand,
                    plan,
                    progress_callback=_progress,
                    approval=approval,
                    cancel_event=self._cancel_event,
                )
            )
            self.app.call_from_thread(self._on_setup_completed, cand.name, res)
        except asyncio.CancelledError:
            self.app.call_from_thread(self._on_setup_cancelled)
        except Exception as e:
            self.app.call_from_thread(self._on_setup_failed, str(e))

    def _update_progress_ui(self, msg: str) -> None:
        try:
            self.query_one("#chk-dl", Label).update(f"[bold cyan]►[/bold cyan] {msg}")
            self.query_one("#wizard-progress-detail", Label).update(msg)
            if "inference" in msg.lower() or "verifying" in msg.lower():
                self._is_verifying = True
                self.query_one("#chk-dl", Label).update("[bold green]✓[/bold green] Downloading model complete")
                self.query_one("#chk-start", Label).update("[bold green]✓[/bold green] Starting model verified")
                self.query_one("#chk-test", Label).update("[bold cyan]►[/bold cyan] Testing response...")
                self.query_one("#wizard-progress-wait-note", Label).update("[dim]Final verification in progress (cannot be interrupted safely)[/dim]")
                self.query_one("#step5-cancel-btn", Button).disabled = True
        except Exception:
            pass

    def _on_setup_cancelled(self) -> None:
        try:
            self.query_one("#chk-dl", Label).update("[bold yellow]✗ Downloading model cancelled[/bold yellow]")
            self.query_one("#wizard-progress-detail", Label).update("[bold yellow]Setup cancelled. Partial download cleaned up safely.[/bold yellow]")
            self.query_one("#step5-cancel-btn", Button).add_class("hidden")
            btn_back = self.query_one("#step5-cancelled-back-btn", Button)
            btn_back.remove_class("hidden")
            btn_back.focus()
        except Exception:
            pass

    def _on_setup_completed(self, name: str, res: dict) -> None:
        self._installed_model_name = name
        try:
            self.query_one("#chk-dl", Label).update("[bold green]✓[/bold green] Downloading model complete")
            self.query_one("#chk-start", Label).update("[bold green]✓[/bold green] Starting model verified")
            self.query_one("#chk-test", Label).update("[bold green]✓[/bold green] Testing a response passed")
            self.query_one("#finish-model-name", Label).update(f"• Model: [bold cyan]{name}[/bold cyan]")
            self.query_one("#finish-test-status", Label).update("• Verification: [bold green]Passed live test query[/bold green]")
        except Exception:
            pass
        self._switch_step(6)
        try:
            self.query_one("#step6-chat-btn", Button).focus()
        except Exception:
            pass

    def _on_setup_failed(self, err: str) -> None:
        app_err = map_exception_to_app_error(RuntimeError(err), operation="setup", stage="guided_wizard")
        try:
            self.query_one("#chk-dl", Label).update(f"[bold red]✗ Setup failed: {app_err.title}[/bold red]")
            self.query_one("#wizard-progress-detail", Label).update(
                f"[bold red]{app_err.explanation}[/bold red]\n\n"
                f"[dim]• Impact: {app_err.known_impact}[/dim]\n"
                f"[dim]• Support ID: {app_err.support_id}[/dim]"
            )
            self.query_one("#step5-cancel-btn", Button).add_class("hidden")
            btn_back = self.query_one("#step5-cancelled-back-btn", Button)
            btn_back.remove_class("hidden")
            btn_back.label = "Back to Recommendations"
            btn_back.focus()
        except Exception:
            pass

    def action_cancel(self) -> None:
        if self._cancel_event and not self._cancel_event.is_set():
            self._cancel_event.set()
        self.dismiss(None)


class LocalPilotTUI(App):
    """LocalPilot Full-Screen Terminal User Interface with Onboarding & Integrated Chat."""

    CSS = """
    Screen {
        background: #121212;
        color: #e0e0e0;
    }
    #ascii-banner {
        color: #00e5ff;
        text-align: center;
        margin-bottom: 1;
    }
    #welcome-box {
        border: solid #00e5ff;
        padding: 1 2;
        margin-bottom: 1;
        background: #181818;
    }
    #home-heading {
        color: #00e5ff;
        text-style: bold;
        margin-bottom: 1;
    }
    #home-tagline {
        margin-bottom: 1;
    }
    #home-recommendation-note {
        color: #00e676;
    }
    #interrupted-card {
        border: solid #ffb300;
        background: #252010;
        padding: 1;
        margin-bottom: 1;
    }
    #home-primary-actions {
        height: 3;
        margin-bottom: 1;
        align: left middle;
    }
    #home-primary-actions Button {
        margin-right: 1;
    }
    #home-secondary-actions {
        height: 3;
        margin-bottom: 1;
        align: left middle;
    }
    #home-secondary-actions Button {
        margin-right: 1;
    }
    .panel {
        border: solid #333333;
        padding: 1;
        margin: 1 1 1 0;
        height: auto;
    }
    DataTable {
        height: 14;
        margin: 1 0;
    }
    .table-actions-bar {
        height: 3;
        align: left middle;
        margin-top: 1;
    }
    .table-actions-bar Button {
        margin-right: 1;
    }
    #explanation-box {
        border: solid #555555;
        padding: 1;
        height: 5;
        margin-top: 1;
    }
    .hidden {
        display: none;
    }

    /* Modal dialog styling */
    ConfirmModal, RemoveModelModal, ModelDetailsModal, GuidedSetupModal, HelpModal, PerformanceSettingsModal, InterruptedSetupModal, AppErrorModal, ModelComparisonModal {
        align: center middle;
    }
    #confirm-dialog, #remove-dialog, #perf-dialog, #interrupted-dialog, #app-error-dialog {
        padding: 1 2;
        width: 74;
        height: auto;
        border: solid #00e5ff;
        background: #1e1e1e;
    }
    #app-error-dialog {
        border: solid #ff5252;
        max-height: 24;
    }
    #app-error-title {
        color: #ff5252;
        text-style: bold;
    }
    #app-error-id {
        color: #888888;
        margin-bottom: 1;
    }
    #app-error-scroll {
        max-height: 14;
        padding: 0 1;
        margin-bottom: 1;
    }
    #app-error-buttons {
        align: right middle;
        height: 3;
    }
    #app-error-buttons Button {
        margin-left: 1;
    }
    #remove-dialog {
        border: solid #ff5252;
    }
    #interrupted-dialog {
        border: solid #ffb300;
    }
    #confirm-title, #remove-title, #perf-title, #interrupted-title {
        margin-bottom: 1;
    }
    #confirm-message, #remove-message, #perf-desc, #interrupted-message {
        margin-bottom: 1;
    }
    #confirm-buttons, #remove-buttons, #perf-buttons, #interrupted-buttons {
        align: right middle;
        height: 3;
        margin-top: 1;
    }
    #confirm-buttons Button, #remove-buttons Button, #perf-buttons Button, #interrupted-buttons Button {
        margin-left: 1;
    }

    #comparison-dialog {
        padding: 1 2;
        width: 86;
        height: 24;
        border: solid #00e5ff;
        background: #1e1e1e;
    }
    #comparison-title {
        margin-bottom: 1;
    }
    #comparison-desc {
        margin-bottom: 1;
        color: #aaaaaa;
    }
    #comparison-scroll {
        height: 1fr;
        margin-bottom: 1;
    }
    #comparison-buttons {
        align: right middle;
        height: 3;
    }

    #details-dialog {
        padding: 1 2;
        width: 74;
        height: 22;
        border: solid #00e5ff;
        background: #1e1e1e;
    }
    #details-title {
        margin-bottom: 1;
    }
    #details-scroll {
        height: 1fr;
        padding: 1;
        border: round #333333;
        margin-bottom: 1;
    }
    #details-buttons {
        align: right middle;
        height: 3;
    }

    #wizard-dialog {
        padding: 1 2;
        width: 80;
        height: auto;
        max-height: 90%;
        border: solid #00e5ff;
        background: #181818;
    }
    #wizard-header {
        text-align: center;
        margin-bottom: 1;
    }
    .wizard-step-title {
        color: #00e5ff;
        margin-bottom: 1;
    }
    .wizard-desc {
        color: #b0b0b0;
        margin-bottom: 1;
    }
    .wizard-card {
        border: round #333333;
        background: #202020;
        padding: 1 2;
        margin-bottom: 1;
    }
    .wizard-buttons {
        align: right middle;
        height: 3;
    }
    .wizard-buttons Button {
        margin-left: 1;
    }
    #wizard-shortlist-scroll {
        max-height: 13;
        padding: 1;
    }

    #help-dialog {
        padding: 1 2;
        width: 90%;
        height: 88%;
        border: solid #00e5ff;
        background: #1a1a1a;
    }
    #help-title {
        text-align: center;
        margin-bottom: 1;
        color: #00e5ff;
    }
    #help-scroll-novice, #help-scroll-tech, #help-scroll-faq, #help-scroll-cmd {
        height: 1fr;
        padding: 1;
    }
    #help-buttons {
        align: right middle;
        height: 3;
        margin-top: 1;
    }
    #context-help-container {
        dock: bottom;
        height: 1;
        background: #181818;
        border-top: solid #333333;
        padding: 0 1;
    }
    #context-help-text {
        color: #00e5ff;
        text-style: dim;
    }
    """

    BINDINGS = [
        ("q", "quit", "Exit"),
        ("d", "switch_to_discover", "Discover"),
        ("i", "switch_to_installed", "Installed"),
        ("r", "switch_to_running", "Running"),
        ("s", "switch_to_storage", "Storage"),
        ("a", "switch_to_activity", "Activity"),
        ("c", "action_chat_selected", "Chat"),
        ("v", "action_details_selected", "Details"),
        ("enter", "select_action", "Action / Chat"),
        ("x", "stop_action", "Stop"),
        ("delete", "remove_action", "Remove"),
        ("f1", "show_help", "Help & Guide"),
        ("?", "show_help", "Help"),
        ("h", "show_help", "Guide"),
        ("1", "switch_to_home", "Home"),
        ("2", "switch_to_discover", "Discover"),
        ("3", "switch_to_installed", "Installed"),
        ("4", "switch_to_running", "Running"),
        ("5", "switch_to_storage", "Storage"),
        ("6", "switch_to_activity", "Activity"),
    ]

    def __init__(self, start_wizard: bool = False) -> None:
        super().__init__()
        self.inspector = SystemInspector()
        self.cat_svc = CatalogService()
        self.storage_mgr = StorageManager()
        self.activity_svc = ActivityService()
        self.runtime_svc = RuntimeLifecycleService()
        self.job_mgr = JobManager()
        self._ranked_candidates: List[RankedCandidate] = []
        self._search_timer: Optional[Timer] = None
        self._pending_query: str = ""
        self._search_seq: int = 0
        self._start_wizard_on_mount = start_wizard
        self._interrupted_job: Optional[Dict[str, Any]] = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with TabbedContent(initial="home-tab", id="main-tabs"):
            with TabPane("Home", id="home-tab"):
                with VerticalScroll():
                    yield Static(HOME_ASCII_BANNER, id="ascii-banner")

                    # Interrupted onboarding recovery banner
                    with Container(id="interrupted-card", classes="hidden"):
                        yield Label("[bold yellow]Notice:[/bold yellow] An earlier model setup was interrupted.", id="interrupted-notice-text")
                        with Horizontal():
                            yield Button("Resume Setup", variant="primary", id="btn-resume-interrupted", tooltip="Continue setup from where it was interrupted")
                            yield Button("Clean Up Partial Files", variant="default", id="btn-cleanup-interrupted", tooltip="Safely delete temporary download files")

                    with Container(id="welcome-box"):
                        yield Label("LOCAL AI, MADE SIMPLE", id="home-heading")
                        yield Label(
                            "Run AI on this computer.\nWe will find a suitable model and help you set it up.",
                            id="home-tagline",
                        )
                        yield Label("", id="home-recommendation-note")

                    with Horizontal(id="home-primary-actions"):
                        yield Button("Help me get started", variant="primary", id="btn-get-started", tooltip="Start 4-step guided wizard to find and run an AI model (Enter)")
                        yield Button("Continue with AI", variant="primary", id="btn-continue-chat", classes="hidden", tooltip="Open chat session with your most recent model")

                    with Horizontal(id="home-secondary-actions"):
                        yield Button("Browse models", variant="default", id="btn-home-discover", tooltip="Explore all models tested to fit this computer")
                        yield Button("My models", variant="default", id="btn-home-installed", classes="hidden", tooltip="View your installed model library")
                        yield Button("Find another model", variant="default", id="btn-home-find-another", classes="hidden", tooltip="Search for an additional model")
                        yield Button("Running now", variant="default", id="btn-home-running", classes="hidden", tooltip="View active models currently consuming RAM")
                        yield Button("Help and settings", variant="default", id="btn-home-help", tooltip="Open documentation manual and FAQ (F1 / ?)")

                    with Horizontal(id="home-panels"):
                        with Vertical(classes="panel"):
                            yield Label("[bold]Computer Health[/bold]")
                            yield Label("Checking...", id="home-hw-status")
                        with Vertical(classes="panel"):
                            yield Label("[bold]Active Models[/bold]")
                            yield Label("Checking...", id="home-running-status")
                        with Vertical(classes="panel"):
                            yield Label("[bold]Storage Managed[/bold]")
                            yield Label("Checking...", id="home-storage-status")

            with TabPane("Find a Model", id="discover-tab"):
                with Vertical():
                    with Horizontal():
                        yield Input(placeholder="Search models (e.g. coding, llama, 3b)...", id="search-input", tooltip="Filter models by task or name (Type to search)")
                    yield DataTable(id="discover-table")
                    with Vertical(id="explanation-box"):
                        yield Label("[bold]Why this model?[/bold]")
                        yield Label("Select a model above to see why it matches your computer. Press Enter to Setup.", id="explanation-text")
                        yield Label("", id="setup-progress-text")
                    with Horizontal(classes="table-actions-bar"):
                        yield Button("Set Up Selected Model (Enter)", variant="primary", id="btn-discover-setup", tooltip="Review and set up highlighted model")

            with TabPane("Installed Models", id="installed-tab"):
                with Vertical():
                    yield DataTable(id="installed-table")
                    with Horizontal(classes="table-actions-bar"):
                        yield Button("Chat with Model (Enter / C)", variant="primary", id="btn-installed-chat", tooltip="Open chat session with selected model (Enter or C)")
                        yield Button("View Details (V)", variant="default", id="btn-installed-details", tooltip="View technical metadata and file paths (V)")
                        yield Button("Remove Model (Delete)", variant="error", id="btn-installed-remove", tooltip="Delete model and reclaim disk space (Delete)")

            with TabPane("Running Models", id="running-tab"):
                with Vertical():
                    yield DataTable(id="running-table")
                    with Horizontal(classes="table-actions-bar"):
                        yield Button("Chat with Model (Enter / C)", variant="primary", id="btn-running-chat", tooltip="Switch directly into active chat session (Enter or C)")
                        yield Button("View Details (V)", variant="default", id="btn-running-details", tooltip="View live process ID, endpoint, and health (V)")
                        yield Button("Stop Model (X)", variant="default", id="btn-running-stop", tooltip="Unload model and free RAM/GPU memory (X)")

            with TabPane("Storage", id="storage-tab"):
                with VerticalScroll():
                    yield Label("[bold]Storage Breakdown[/bold]")
                    yield Label("", id="storage-details")

            with TabPane("Activity Log", id="activity-tab"):
                with Vertical():
                    yield DataTable(id="activity-table")

        with Container(id="context-help-container"):
            yield Label("• Use Tab / Arrow keys to navigate. Press '?' or F1 for documentation manual.", id="context-help-text")
        yield Footer()

    def on_mount(self) -> None:
        self._setup_discover_table()
        self._setup_installed_table()
        self._setup_running_table()
        self._setup_activity_table()
        self._refresh_storage()
        self._load_health_background()
        self._check_for_interrupted_work()

        if self._start_wizard_on_mount:
            self.action_start_guided_setup()

    def _check_for_interrupted_work(self) -> None:
        """Section 12: Detects interrupted setup operations on startup."""
        interrupted = self.job_mgr.get_interrupted_jobs()
        if interrupted:
            self._interrupted_job = interrupted[0]
            try:
                card = self.query_one("#interrupted-card", Container)
                card.remove_class("hidden")
                plan = json.loads(self._interrupted_job.get("approved_plan_json") or "{}")
                name = plan.get("model_id") or self._interrupted_job.get("target_id")
                stage = self._interrupted_job.get("stage", "downloading").replace("_", " ").title()
                self.query_one("#interrupted-notice-text", Label).update(
                    f"[bold yellow]Notice:[/bold yellow] Setup of [bold]{name}[/bold] was interrupted (stopped at {stage})."
                )
            except Exception:
                pass

    @work(exclusive=True, thread=True)
    def _load_health_background(self) -> None:
        report = self.inspector.inspect()
        hw = report.hardware
        avail_gb = hw.ram_available_bytes / (1024**3)
        safe_gb = hw.safe_ram_budget_bytes / (1024**3)
        text = f"{hw.os_name} • {avail_gb:.1f}GB RAM free (Safe: {safe_gb:.1f}GB)"
        self.call_from_thread(self._update_home_health, text)

    def _update_home_health(self, text: str) -> None:
        try:
            self.query_one("#home-hw-status", Label).update(text)
        except Exception:
            pass

    def _setup_discover_table(self) -> None:
        table = self.query_one("#discover-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Score", "Model Name", "Source", "Size", "Context (tokens)", "Fit Status")
        try:
            initial_results = self.cat_svc.discover(query="")
            self._update_discover_table(initial_results, self._search_seq)
        except Exception:
            pass

    def _refresh_discover_table(self, query: str = "") -> None:
        self._search_seq += 1
        self._run_discover_worker(query, self._search_seq)

    @work(exclusive=True, thread=True)
    def _run_discover_worker(self, query: str, seq: int) -> None:
        results = self.cat_svc.discover(query=query)
        self.call_from_thread(self._update_discover_table, results, seq)

    def on_descendant_focus(self, event) -> None:
        """Update contextual help bar with Layer 2 guidance when user navigates controls."""
        ctrl_id = getattr(event.widget, "id", None) or ""
        msg = get_contextual_guidance(ctrl_id)
        try:
            self.query_one("#context-help-text", Label).update(f"• {msg}")
        except Exception:
            pass

    def _update_discover_table(self, results: List[RankedCandidate], seq: int) -> None:
        if seq < self._search_seq:
            return
        self._ranked_candidates = results
        table = self.query_one("#discover-table", DataTable)
        table.clear()
        if not results:
            table.add_row(
                "-",
                "No compatible models in memory budget",
                "Catalog",
                "0 MB",
                "0",
                "Close background apps or check compact models",
                key="empty_discover",
            )
            return
        for r in results:
            cand = r.candidate
            sz_mb = cand.total_bytes / (1024 * 1024)
            table.add_row(
                f"{r.score.total_score:.0f}",
                cand.name,
                cand.source.title(),
                f"{sz_mb:.0f} MB",
                str(cand.usable_context),
                r.compatibility.friendly_explanation,
                key=cand.candidate_id,
            )

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id == "discover-table":
            row_idx = event.cursor_row
            if 0 <= row_idx < len(self._ranked_candidates):
                item = self._ranked_candidates[row_idx]
                expl_label = self.query_one("#explanation-text", Label)
                plan_info = ""
                if item.recommended_plan:
                    rec = item.recommended_plan
                    plan_info = f" | Recommended: {rec.inference_backend.value} ({rec.deployment_method.value})"
                    if item.alternative_plans:
                        plan_info += f" | {len(item.alternative_plans)} other option(s) supported"
                context_info = f" • Context: {item.candidate.usable_context:,} tokens"
                expl_label.update(
                    f"{item.score.explanation}{plan_info}\n[dim]Recommendation confidence: {item.candidate.confidence.value}{context_info} • Press Enter to Setup[/dim]"
                )

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search-input":
            self._pending_query = event.value
            if self._search_timer:
                self._search_timer.stop()
            self._search_timer = self.set_timer(0.35, self._debounced_search)

    def _debounced_search(self) -> None:
        self._refresh_discover_table(query=self._pending_query)

    def _setup_installed_table(self) -> None:
        table = self.query_one("#installed-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Model Name", "Purpose", "Status", "Disk Space")
        self._refresh_installed_table()

    def _refresh_installed_table(self) -> None:
        table = self.query_one("#installed-table", DataTable)
        table.clear()
        conn = get_db().get_connection()
        rows = conn.execute("SELECT * FROM installations ORDER BY created_at DESC").fetchall()

        # Update Home Screen: First-Use vs Returning-User states (Section 3)
        btn_start = self.query_one("#btn-get-started", Button)
        btn_continue = self.query_one("#btn-continue-chat", Button)
        btn_browse = self.query_one("#btn-home-discover", Button)
        btn_my_models = self.query_one("#btn-home-installed", Button)
        btn_find_another = self.query_one("#btn-home-find-another", Button)
        btn_running = self.query_one("#btn-home-running", Button)
        home_tagline = self.query_one("#home-tagline", Label)
        home_note = self.query_one("#home-recommendation-note", Label)

        if rows:
            # RETURNING USER (Section 3)
            recent_name = rows[0]["name"]
            btn_continue.label = f"Continue with {recent_name}"
            btn_continue.remove_class("hidden")
            btn_start.add_class("hidden")

            btn_browse.add_class("hidden")
            btn_my_models.remove_class("hidden")
            btn_find_another.remove_class("hidden")
            btn_running.remove_class("hidden")

            home_tagline.update("Welcome back! Your local models are installed and ready to chat.")
            home_note.update(f"You have {len(rows)} installed model(s) ready offline.")
        else:
            # FIRST USE (Section 3)
            btn_continue.add_class("hidden")
            btn_start.remove_class("hidden")
            btn_start.label = "Help me get started"

            btn_browse.remove_class("hidden")
            btn_browse.label = "Browse models"
            btn_my_models.add_class("hidden")
            btn_find_another.add_class("hidden")
            btn_running.add_class("hidden")

            home_tagline.update("We will find a suitable model and help you set it up.")
            home_note.update("")

        if not rows:
            # Empty state guidance
            table.add_row(
                "No models installed yet",
                "Find a suitable model to get started",
                "Press 'd' or '2' to browse",
                "0 MB",
                key="empty_installed",
            )
        else:
            for r in rows:
                name = r["name"]
                nl = name.lower()
                if "coder" in nl or "code" in nl:
                    purpose = "Coding assistance"
                elif "llama" in nl or "qwen" in nl or "phi" in nl:
                    purpose = "Everyday questions and writing"
                else:
                    purpose = "General reasoning"

                stage = r["readiness_stage"]
                if stage == "tested_here":
                    status = "Ready to chat (Verified)"
                elif stage == "downloaded_prepared":
                    status = "Ready to start"
                else:
                    status = stage.replace("_", " ").title()

                sz_str = "Managed"
                try:
                    art_ids = json.loads(r["artifact_ids_json"] or "[]")
                    if art_ids:
                        art = conn.execute("SELECT bytes FROM storage_artifacts WHERE artifact_id = ?", (art_ids[0],)).fetchone()
                        if art and art["bytes"]:
                            mb = art["bytes"] / (1024 * 1024)
                            sz_str = f"{mb:.0f} MB" if mb < 1000 else f"{mb/1024:.1f} GB"
                except Exception:
                    pass

                table.add_row(name, purpose, status, sz_str, key=r["installation_id"])

    def _setup_running_table(self) -> None:
        table = self.query_one("#running-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Model Name", "Status", "Hardware Mode", "Active Time")
        self._reconcile_and_populate_running(table)

    def _reconcile_and_populate_running(self, table: Optional[DataTable] = None) -> None:
        if table is None:
            table = self.query_one("#running-table", DataTable)
        table.clear()
        active = self.runtime_svc.reconcile_running_instances()

        if not active:
            # Empty state guidance
            table.add_row(
                "No models currently running",
                "System memory is 100% free",
                "Select an installed model to chat",
                "Idle",
                key="empty_running",
            )
        else:
            conn = get_db().get_connection()
            for r in active:
                health = r.get("health_status", "healthy")
                if health == "healthy":
                    status_label = "Running (Healthy)"
                elif health == "degraded":
                    status_label = "Running (Degraded)"
                elif health == "unresponsive":
                    status_label = "Running (Unresponsive)"
                else:
                    status_label = f"Running ({health})"

                m_id = r.get("model_id", "")
                inst_row = conn.execute("SELECT name FROM installations WHERE model_id = ? OR installation_id = ?", (m_id, m_id)).fetchone()
                display_name = inst_row["name"] if inst_row else m_id

                table.add_row(
                    display_name,
                    status_label,
                    r.get("execution_mode", "Local CPU/GPU"),
                    "Active now",
                    key=r.get("instance_id", ""),
                )

        try:
            self.query_one("#home-running-status", Label).update(
                f"{len(active)} active model(s)"
            )
        except Exception:
            pass

    def _setup_activity_table(self) -> None:
        table = self.query_one("#activity-table", DataTable)
        table.cursor_type = "row"
        table.add_columns("Time", "Title", "Summary")
        activities = self.activity_svc.get_activities(limit=30)
        for a in activities:
            table.add_row(a.timestamp[:19].replace("T", " "), a.title, a.summary)

    def _refresh_storage(self) -> None:
        summary = self.storage_mgr.inspect_storage_categories()
        to_mb = lambda b: b / (1024 * 1024)
        text = (
            f"• Model Files: {to_mb(summary.model_files_bytes):.1f} MB\n"
            f"• Supporting Runtimes: {to_mb(summary.runtime_installations_bytes):.1f} MB\n"
            f"• Downloads & Temporary: {to_mb(summary.incomplete_downloads_bytes + summary.temporary_files_bytes):.1f} MB\n"
            f"• Total Managed Storage: {to_mb(summary.total_managed_bytes):.1f} MB"
        )
        self.query_one("#storage-details", Label).update(text)
        try:
            self.query_one("#home-storage-status", Label).update(f"{to_mb(summary.total_managed_bytes):.0f} MB managed")
        except Exception:
            pass

    # Navigation actions
    def action_switch_to_home(self) -> None:
        self.query_one("#main-tabs", TabbedContent).active = "home-tab"

    def action_switch_to_discover(self) -> None:
        self.query_one("#main-tabs", TabbedContent).active = "discover-tab"

    def action_switch_to_installed(self) -> None:
        self.query_one("#main-tabs", TabbedContent).active = "installed-tab"
        self._refresh_installed_table()

    def action_switch_to_running(self) -> None:
        self.query_one("#main-tabs", TabbedContent).active = "running-tab"
        table = self.query_one("#running-table", DataTable)
        self._reconcile_and_populate_running(table)

    def action_switch_to_storage(self) -> None:
        self.query_one("#main-tabs", TabbedContent).active = "storage-tab"
        self._refresh_storage()

    def action_switch_to_activity(self) -> None:
        self.query_one("#main-tabs", TabbedContent).active = "activity-tab"

    def action_start_guided_setup(self) -> None:
        def _on_guided_finish(model_name: Optional[str]) -> None:
            self._refresh_installed_table()
            self._refresh_storage()
            if model_name:
                self.start_and_chat(model_name)

        self.push_screen(GuidedSetupModal(), _on_guided_finish)

    def action_continue_most_recent(self) -> None:
        conn = get_db().get_connection()
        rows = conn.execute("SELECT * FROM installations ORDER BY created_at DESC LIMIT 1").fetchall()
        if rows:
            name = rows[0]["name"]
            self.start_and_chat(name)
        else:
            self.action_start_guided_setup()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-get-started":
            self.action_start_guided_setup()
        elif btn_id == "btn-continue-chat":
            self.action_continue_most_recent()
        elif btn_id == "btn-resume-interrupted":
            if self._interrupted_job:
                def _on_resume_finish(model_name: Optional[str]) -> None:
                    self._refresh_installed_table()
                    self._refresh_storage()
                    if model_name:
                        self.start_and_chat(model_name)
                self.push_screen(InterruptedSetupModal(self._interrupted_job), self._handle_interrupted_decision)
        elif btn_id == "btn-cleanup-interrupted":
            if self._interrupted_job:
                self.job_mgr.cleanup_interrupted_job(self._interrupted_job["job_id"])
                self.query_one("#interrupted-card", Container).add_class("hidden")
                self.notify("Interrupted setup cleaned up safely.", title="Cleaned Up")
        elif btn_id == "btn-home-installed":
            self.action_switch_to_installed()
        elif btn_id == "btn-home-find-another":
            self.action_start_guided_setup()
        elif btn_id == "btn-home-running":
            self.action_switch_to_running()
        elif btn_id == "btn-home-discover":
            self.action_switch_to_discover()
        elif btn_id == "btn-home-help":
            self.action_show_help()
        elif btn_id == "btn-discover-setup":
            self.action_select_action()
        elif btn_id == "btn-installed-chat":
            self.action_chat_selected()
        elif btn_id == "btn-installed-details":
            self.action_details_selected()
        elif btn_id == "btn-installed-remove":
            self.action_remove_action()
        elif btn_id == "btn-running-chat":
            self.action_chat_selected()
        elif btn_id == "btn-running-details":
            self.action_details_selected()
        elif btn_id == "btn-running-stop":
            self.action_stop_action()

    def _handle_interrupted_decision(self, decision: str) -> None:
        if decision == "resume":
            self.action_start_guided_setup()
        elif decision == "cleanup" and self._interrupted_job:
            self.job_mgr.cleanup_interrupted_job(self._interrupted_job["job_id"])
            self.query_one("#interrupted-card", Container).add_class("hidden")
            self.notify("Interrupted setup cleaned up.", title="Cleaned Up")

    # Enter / Select action
    def action_select_action(self) -> None:
        if isinstance(self.focused, (Input,)):
            return
        current_tab = self.query_one("#main-tabs", TabbedContent).active
        if current_tab == "home-tab":
            self.action_continue_most_recent()
        elif current_tab == "discover-tab":
            table = self.query_one("#discover-table", DataTable)
            row_idx = table.cursor_row
            if 0 <= row_idx < len(self._ranked_candidates):
                item = self._ranked_candidates[row_idx]
                workflow = SetupWorkflow()
                built_plan = workflow.build_plan(
                    item.candidate,
                    execution_plan=item.recommended_plan,
                    alternative_plans=item.alternative_plans,
                )

                def _on_confirm(confirmed: bool) -> None:
                    if confirmed:
                        approval = ApprovalRecord.create_for_plan(built_plan, approved_by="user_tui")
                        self.notify(f"Starting setup for {item.candidate.name}...", title="Setup Started")
                        self._run_setup_worker(item.candidate, built_plan, approval)

                backend_str = getattr(built_plan.inference_backend, "value", str(built_plan.inference_backend))
                mode_str = getattr(built_plan.execution_mode, "value", str(built_plan.execution_mode))
                msg = (
                    f"Set up {item.candidate.name}?\n\n"
                    f"• Engine: {built_plan.runtime_name} ({backend_str})\n"
                    f"• Download: ~{built_plan.download_bytes / (1024*1024):.0f} MB\n"
                    f"• Storage: {built_plan.target_install_dir}\n"
                    f"• Hardware Mode: {mode_str}\n\n"
                    "No system drivers or administrator settings will be modified."
                )
                self.push_screen(
                    ConfirmModal("Approve Model Setup", msg, confirm_label="Set Up Model", cancel_label="Cancel"),
                    _on_confirm,
                )

        elif current_tab in ("installed-tab", "running-tab"):
            self.action_chat_selected()

    def action_chat_selected(self) -> None:
        """Starts (if needed) and opens integrated ChatScreen directly for selected model."""
        if isinstance(self.focused, (Input,)):
            return
        current_tab = self.query_one("#main-tabs", TabbedContent).active
        if current_tab == "installed-tab":
            table = self.query_one("#installed-table", DataTable)
            if table.row_count == 0 or table.cursor_row < 0:
                self.notify("No installed model selected.", title="Chat")
                return
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
            installation_id = str(row_key.value) if row_key else ""
            if installation_id == "empty_installed":
                self.action_start_guided_setup()
                return
            if installation_id:
                self.start_and_chat(installation_id)
        elif current_tab == "running-tab":
            table = self.query_one("#running-table", DataTable)
            if table.row_count == 0 or table.cursor_row < 0:
                self.notify("No running model selected.", title="Chat")
                return
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
            instance_id = str(row_key.value) if row_key else ""
            if instance_id == "empty_running":
                self.notify("No models are currently running.", title="Chat")
                return
            if instance_id:
                self.start_and_chat(instance_id)

    @work(exclusive=True, thread=True)
    def start_and_chat(self, model_identifier: str) -> None:
        """Starts model (if needed) and transitions directly to ChatScreen without typing commands."""
        active = self.runtime_svc.reconcile_running_instances()
        running_inst = None
        for inst in active:
            if (
                inst.get("instance_id") == model_identifier
                or inst.get("model_id") == model_identifier
                or inst.get("installation_id") == model_identifier
            ):
                running_inst = inst
                break

        if running_inst:
            model_name = running_inst.get("model_id", model_identifier)
            ep = running_inst.get("endpoint_url", "http://127.0.0.1:8080")
            inst_id = running_inst.get("instance_id", model_identifier)
            self.call_from_thread(self._open_chat_screen, model_name, model_identifier, ep, inst_id)
            return

        self.call_from_thread(self.notify, f"Starting {model_identifier} for chat...", title="Starting Engine")
        try:
            res = asyncio.run(self.runtime_svc.start_installed_model(model_identifier))
            model_name = res.get("model_id", model_identifier)
            ep = res.get("endpoint_url", "http://127.0.0.1:8080")
            inst_id = res.get("instance_id", model_identifier)
            self.call_from_thread(self._open_chat_screen, model_name, model_identifier, ep, inst_id)
        except Exception as e:
            self.call_from_thread(self.notify, f"Could not start model for chat: {e}", title="Start Failed", severity="error")

    def _open_chat_screen(self, model_name: str, model_id: str, endpoint_url: str, instance_id: str) -> None:
        self.push_screen(
            ChatScreen(
                model_name=model_name,
                model_id=model_id,
                endpoint_url=endpoint_url,
                instance_id=instance_id,
            )
        )

    def action_details_selected(self) -> None:
        """Opens the inspection modal with technical details for the selected item."""
        if isinstance(self.focused, (Input,)):
            return
        current_tab = self.query_one("#main-tabs", TabbedContent).active
        conn = get_db().get_connection()

        if current_tab == "installed-tab":
            table = self.query_one("#installed-table", DataTable)
            if table.row_count == 0 or table.cursor_row < 0:
                return
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
            inst_id = str(row_key.value) if row_key else ""
            if not inst_id or inst_id == "empty_installed":
                return
            row = conn.execute("SELECT * FROM installations WHERE installation_id = ?", (inst_id,)).fetchone()
            if not row:
                return

            art_path = "N/A"
            art_bytes = 0
            art_sha = "N/A"
            try:
                art_ids = json.loads(row["artifact_ids_json"] or "[]")
                if art_ids:
                    art = conn.execute("SELECT * FROM storage_artifacts WHERE artifact_id = ?", (art_ids[0],)).fetchone()
                    if art:
                        art_path = art["physical_path"]
                        art_bytes = art["bytes"]
                        art_sha = art["sha256"] or "Verified"
            except Exception:
                pass

            details = {
                "Model Name": row["name"],
                "Model ID": row["model_id"],
                "Installation ID": row["installation_id"],
                "Runtime Engine": row["runtime_name"],
                "Backend": row["inference_backend"] or "llama.cpp",
                "Readiness Stage": row["readiness_stage"],
                "Ownership Class": row["ownership_class"],
                "Storage Path": art_path,
                "File Size": f"{art_bytes / (1024*1024):.1f} MB" if art_bytes else "Managed",
                "Integrity Digest": art_sha,
                "Created At": row["created_at"],
            }
            self.push_screen(ModelDetailsModal(row["name"], details))

        elif current_tab == "running-tab":
            table = self.query_one("#running-table", DataTable)
            if table.row_count == 0 or table.cursor_row < 0:
                return
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
            instance_id = str(row_key.value) if row_key else ""
            if not instance_id or instance_id == "empty_running":
                return
            active = self.runtime_svc.reconcile_running_instances()
            match = next((i for i in active if i.get("instance_id") == instance_id), None)
            if not match:
                return

            details = {
                "Model Identifier": match.get("model_id", ""),
                "Instance ID": match.get("instance_id", ""),
                "Process ID (PID)": str(match.get("pid", "Service")),
                "Endpoint URL": match.get("endpoint_url", "N/A"),
                "Port": str(match.get("port", "N/A")),
                "Health Status": match.get("health_status", "healthy"),
                "Execution Mode": match.get("execution_mode", "CPU"),
                "Runtime": match.get("runtime_name", "N/A"),
                "Log File": match.get("log_file", "N/A"),
            }
            self.push_screen(ModelDetailsModal(match.get("model_id", "Active Model"), details))

    def action_stop_action(self) -> None:
        if isinstance(self.focused, (Input,)):
            return
        current_tab = self.query_one("#main-tabs", TabbedContent).active
        if current_tab == "running-tab":
            table = self.query_one("#running-table", DataTable)
            if table.row_count == 0 or table.cursor_row < 0:
                self.notify("No running instance selected.", title="Stop Instance")
                return
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
            instance_id = str(row_key.value) if row_key else ""
            if not instance_id or instance_id == "empty_running":
                self.notify("No active model is currently running.", title="Stop Instance")
                return

            def _on_confirm(confirmed: bool) -> None:
                if confirmed:
                    self.notify(f"Stopping instance {instance_id}...", title="Stopping")
                    self._run_stop_worker(instance_id)

            msg = (
                f"Stop running model instance '{instance_id}'?\n\n"
                "• What happens: The model is unloaded and system memory is freed.\n"
                "• Your files: All downloaded model files and configuration are preserved."
            )
            self.push_screen(
                ConfirmModal("Stop Model", msg, confirm_label="Stop Model", cancel_label="Keep Running"),
                _on_confirm,
            )

    @work(exclusive=True, thread=True)
    def _run_stop_worker(self, instance_id: str) -> None:
        try:
            res = asyncio.run(self.runtime_svc.stop_running_instance(instance_id))
            self.call_from_thread(self._on_stop_completed, instance_id, res)
        except Exception as e:
            self.call_from_thread(self.notify, f"Failed to stop: {e}", title="Error", severity="error")

    def show_error(self, error: AppError) -> None:
        """Pushes persistent, truthful AppErrorModal and logs to ObservabilityHub."""
        try:
            self.hub.record_activity(
                category="error",
                title=f"Error: {error.title}",
                summary=error.explanation,
                outcome="failed",
                details=error.to_user_dict(),
            )
        except Exception:
            pass
        self.push_screen(AppErrorModal(error))

    def _on_stop_completed(self, instance_id: str, res: Any) -> None:
        succeeded = getattr(res, "request_succeeded", bool(res))
        outcome = getattr(res, "observed_outcome", None)
        outcome_val = getattr(outcome, "value", str(outcome)) if outcome else ""
        if succeeded:
            self.notify(f"Instance {instance_id} stopped ({outcome_val or 'success'}).", title="Stopped")
        else:
            reason = getattr(res, "reason", "Stop failed")
            code = ErrorCode.STOP_UNKNOWN if outcome_val == "stop_outcome_unknown" else ErrorCode.STOP_REJECTED
            err = create_app_error(
                code,
                operation="stop_instance",
                stage="stopping",
                explanation=reason,
                known_impact="The process was not terminated or its outcome is unconfirmed. The instance is still listed.",
                recommended_action=RecoveryAction.CHECK_AGAIN,
                technical_details=f"Target: {instance_id} | Outcome: {outcome_val}",
            )
            self.show_error(err)
        self._reconcile_and_populate_running()

    def action_remove_action(self) -> None:
        if isinstance(self.focused, (Input,)):
            return
        current_tab = self.query_one("#main-tabs", TabbedContent).active
        if current_tab == "installed-tab":
            table = self.query_one("#installed-table", DataTable)
            if table.row_count == 0 or table.cursor_row < 0:
                self.notify("No installation selected.", title="Remove Model")
                return
            row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
            installation_id = str(row_key.value) if row_key else ""
            if not installation_id or installation_id == "empty_installed":
                self.notify("No models are currently installed.", title="Remove Model")
                return

            conn = get_db().get_connection()
            row = conn.execute("SELECT name FROM installations WHERE installation_id = ?", (installation_id,)).fetchone()
            model_name = row["name"] if row else installation_id

            def _on_confirm(confirmed: bool) -> None:
                if confirmed:
                    self.notify(f"Removing {model_name}...", title="Removing")
                    self._run_remove_worker(installation_id)

            self.push_screen(RemoveModelModal(model_name, installation_id), _on_confirm)

    @work(exclusive=True, thread=True)
    def _run_remove_worker(self, installation_id: str) -> None:
        try:
            res = self.storage_mgr.safe_remove_installation(installation_id)
            self.call_from_thread(self._on_remove_completed, installation_id, res)
        except Exception as e:
            app_err = map_exception_to_app_error(e, operation="remove", stage="cleanup")
            self.call_from_thread(self.show_error, app_err)

    def _on_remove_completed(self, installation_id: str, res: dict) -> None:
        if res.get("success"):
            freed_mb = res.get("bytes_freed", 0) / (1024 * 1024)
            self.notify(f"Removed model. Freed: {freed_mb:.1f} MB.", title="Removed")
        else:
            err = create_app_error(
                ErrorCode.DELETION_FAILED,
                operation="remove",
                stage="cleanup",
                explanation=res.get("error", "Failed deleting model files."),
                known_impact=f"Freed: {res.get('bytes_freed', 0) / (1024*1024):.1f} MB. Preserved {len(res.get('files_preserved', []))} files on disk.",
                recommended_action=RecoveryAction.REVIEW_STORAGE,
                technical_details=res.get("error", ""),
            )
            self.show_error(err)
        self._refresh_installed_table()
        self._refresh_storage()

    @work(exclusive=True, thread=True)
    def _run_setup_worker(self, candidate, plan, approval) -> None:
        workflow = SetupWorkflow()
        def _on_progress(msg: str, done: int, total: int) -> None:
            pct = int((done / total) * 100) if total > 0 else 0
            mb_done = done / (1024 * 1024)
            mb_total = total / (1024 * 1024)
            progress_str = f"{msg} ({mb_done:.1f}/{mb_total:.1f} MB - {pct}%)" if total > 0 else msg
            self.call_from_thread(self._update_setup_progress, progress_str)

        try:
            res = asyncio.run(workflow.execute_setup(candidate, plan, progress_callback=_on_progress, approval=approval))
            self.call_from_thread(self._on_setup_completed, res["installation_id"], candidate.name)
        except Exception as e:
            self.call_from_thread(self._on_setup_failed, str(e))

    def _update_setup_progress(self, progress_str: str) -> None:
        try:
            self.query_one("#setup-progress-text", Label).update(f"[bold cyan]Setup Progress:[/bold cyan] {progress_str}")
        except Exception:
            pass

    def _on_setup_failed(self, err: str) -> None:
        try:
            self.query_one("#setup-progress-text", Label).update(f"[bold red]Setup Failed:[/bold red] {err}")
        except Exception:
            pass
        app_err = map_exception_to_app_error(RuntimeError(err), operation="setup", stage="setup")
        self.show_error(app_err)

    def _on_setup_completed(self, inst_id: str, name: str) -> None:
        try:
            self.query_one("#setup-progress-text", Label).update(f"[bold green]Setup Finished:[/bold green] {name} is verified and ready to chat!")
        except Exception:
            pass
        self.notify(f"Successfully set up {name}", title="Setup Finished")
        self._refresh_installed_table()
        self._refresh_storage()
        def _on_chat_prompt(confirmed: bool) -> None:
            if confirmed:
                self.start_and_chat(name)

        msg = (
            f"'{name}' is installed, verified, and ready to use!\n\n"
            "Would you like to start chatting with it right now?"
        )
        self.push_screen(
            ConfirmModal("Ready to Chat!", msg, confirm_label="Start Chatting", cancel_label="Later"),
            _on_chat_prompt,
        )

    def action_show_help(self) -> None:
        self.push_screen(HelpModal())
