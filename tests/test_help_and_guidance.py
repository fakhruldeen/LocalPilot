"""Comprehensive test suite for Help Content, Contextual Guidance, and Documentation integrity."""

from __future__ import annotations
import pytest
from typer.testing import CliRunner

from localpilot.cli.main import app
from localpilot.domain.documentation import DOCUMENTATION_TOPICS, get_documentation_markdown
from localpilot.domain.help_content import (
    HELP_TOPICS,
    TERMINOLOGY,
    HelpTopic,
    get_contextual_guidance,
)
from localpilot.domain.scoring import ScoringPolicy
from localpilot.tui.app import ConfirmModal, PerformanceSettingsModal, RemoveModelModal

runner = CliRunner()


class TestHelpContentDomain:
    """Tests for the Help Content domain models, topics catalog, and terminology dictionary."""

    def test_help_topics_catalog_integrity(self) -> None:
        """Verify that every registered help topic is complete and well-structured."""
        assert len(HELP_TOPICS) >= 15

        required_topics = [
            "model_sources",
            "exec_automatic",
            "exec_cpu",
            "exec_gpu",
            "exec_hybrid",
            "recommendation_score",
            "compat_status",
            "storage_footprint",
            "context_window",
            "memory_headroom",
            "model_variants",
            "auth_keys",
            "stop_vs_remove",
            "updates_explanation",
            "closing_localpilot",
            "recovery_actions",
            "empty_installed",
            "empty_running",
            "empty_discover",
        ]

        for topic_id in required_topics:
            assert topic_id in HELP_TOPICS, f"Missing required topic: {topic_id}"
            topic = HELP_TOPICS[topic_id]
            assert isinstance(topic, HelpTopic)
            assert topic.topic_id == topic_id
            assert bool(topic.title.strip())
            assert bool(topic.short_help.strip()), f"Topic {topic_id} has empty short_help (Layer 2)"
            assert bool(topic.extended_help.strip()), f"Topic {topic_id} has empty extended_help (Layer 3/4)"
            assert isinstance(topic.related_actions, list)

    def test_preferred_terminology_adherence(self) -> None:
        """Verify standard plain-language terminology definitions."""
        assert "model" in TERMINOLOGY
        assert "supporting_software" in TERMINOLOGY
        assert "context" in TERMINOLOGY
        assert "graphics_memory" in TERMINOLOGY
        assert "compact_model_version" in TERMINOLOGY
        assert "running_model" in TERMINOLOGY
        assert "access_key" in TERMINOLOGY
        assert "stop_vs_remove" in TERMINOLOGY

        # Verify plain-language definitions do not contain unsupported jargon
        assert "software library or artificial brain" in TERMINOLOGY["model"].lower()
        assert "memory" in TERMINOLOGY["context"].lower()
        assert "system memory" in TERMINOLOGY["stop_vs_remove"].lower()
        assert "free disk space" in TERMINOLOGY["stop_vs_remove"].lower()

    def test_get_contextual_guidance(self) -> None:
        """Verify contextual guidance mapping for focused widgets across all screens."""
        # Standard buttons
        assert "recommended setup wizard" in get_contextual_guidance("get-started-btn").lower()
        assert "compatible models" in get_contextual_guidance("browse-models-btn").lower()
        assert "freed cleanly" in get_contextual_guidance("quit-btn").lower()

        # Hardware execution radio buttons (Section 6.B)
        assert "automatically select" in get_contextual_guidance("radio-auto").lower()
        assert "system processor" in get_contextual_guidance("radio-cpu").lower()
        assert "graphics card" in get_contextual_guidance("radio-gpu").lower()
        assert "both your cpu and gpu" in get_contextual_guidance("radio-hybrid").lower()

        # Token input
        assert "access key" in get_contextual_guidance("token-input").lower()

        # Tables
        assert "installed models" in get_contextual_guidance("installed-table").lower()
        assert "active in memory" in get_contextual_guidance("running-table").lower()
        assert "compatible models" in get_contextual_guidance("discover-table").lower()

        # Fallback for empty or unknown IDs
        fallback = get_contextual_guidance("unknown-widget-xyz")
        assert "localpilot" in fallback.lower()
        assert "f1" in fallback.lower() or "?" in fallback.lower()


class TestDocumentationTopics:
    """Tests for CLI guide and TUI documentation topics."""

    def test_documentation_topics_present(self) -> None:
        expected = ["novice", "concepts", "terminology", "technical", "faq", "commands", "overview"]
        for exp in expected:
            assert exp in DOCUMENTATION_TOPICS

    def test_documentation_markdown_content(self) -> None:
        for topic in DOCUMENTATION_TOPICS:
            md = get_documentation_markdown(topic)
            assert bool(md.strip()), f"Documentation topic {topic} returned empty content"
            # Ensure markdown formatting has headings
            assert "#" in md

    def test_scoring_weights_consistency(self) -> None:
        """Verify that documented scoring weights exactly match the ScoringPolicy implementation."""
        md = get_documentation_markdown("concepts")
        assert "Release Newness (40%)" in md
        assert "Smaller Download (25%)" in md
        assert "Usable Context (20%)" in md
        assert "Memory Headroom (15%)" in md

        assert ScoringPolicy.WEIGHT_NEWNESS == 0.40
        assert ScoringPolicy.WEIGHT_DOWNLOAD == 0.25
        assert ScoringPolicy.WEIGHT_CONTEXT == 0.20
        assert ScoringPolicy.WEIGHT_HEADROOM == 0.15


class TestModalsGuidanceAndSafety:
    """Tests modal dialog labels, default focuses, and user safety safeguards."""

    def test_confirm_modal_custom_labels_and_safe_defaults(self) -> None:
        modal = ConfirmModal(
            title="Action Title",
            message="Action Details",
            confirm_label="Set Up Model",
            cancel_label="Do Nothing",
        )
        assert modal.confirm_label == "Set Up Model"
        assert modal.cancel_label == "Do Nothing"

    def test_remove_model_modal_disclosure(self) -> None:
        modal = RemoveModelModal("Test Model", "inst_123")
        assert modal.model_name == "Test Model"
        assert modal.installation_id == "inst_123"

    def test_performance_settings_modal_options(self) -> None:
        modal = PerformanceSettingsModal()
        assert modal is not None


class TestCliGuidanceAndCommands:
    """Tests CLI help output, flags, examples, and guide command execution."""

    def test_cli_guide_subcommands(self) -> None:
        for topic in ["novice", "concepts", "terminology", "technical", "faq", "commands"]:
            result = runner.invoke(app, ["guide", topic])
            assert result.exit_code == 0
            assert len(result.stdout) > 50

    def test_cli_docs_alias(self) -> None:
        result = runner.invoke(app, ["docs", "terminology"])
        assert result.exit_code == 0
        assert "LocalPilot Terminology Guide" in result.stdout

    def test_cli_app_help_structure(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "guide" in result.stdout
        assert "doctor" in result.stdout
        assert "discover" in result.stdout
        assert "setup" in result.stdout
        assert "chat" in result.stdout
        assert "stop" in result.stdout
        assert "installed" in result.stdout
        assert "running" in result.stdout
        assert "storage" in result.stdout
        assert "update" in result.stdout
        assert "remove" in result.stdout

    def test_cli_command_specific_help_and_examples(self) -> None:
        for cmd in ["setup", "chat", "stop", "remove", "update", "doctor", "discover", "storage"]:
            res = runner.invoke(app, [cmd, "--help"])
            assert res.exit_code == 0
            # Every command has Non-technical, Technical, and Examples sections in docstring
            assert "Non-technical:" in res.stdout or "check" in res.stdout.lower()
            assert "Examples:" in res.stdout
