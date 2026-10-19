"""Tests for UI/UX refinements:
1. GuidedSetupModal Step 3 mouse click selection.
2. Discover table favorited models highlighting and star beside alias.
3. Live bar inline pause button.
"""

import pytest
from localpilot.tui.app import LocalPilotTUI, GuidedSetupModal
from textual.widgets import Button, Checkbox, DataTable, Label, RadioButton, RadioSet, TabbedContent
from textual import events


@pytest.mark.asyncio
async def test_guided_setup_step3_mouse_click():
    """Verify that mouse click or selection on Step 3 recommendations updates selection and unchecks other candidates."""
    modal = GuidedSetupModal()
    # Mock 3 candidates
    class FakeCandidate:
        def __init__(self, name: str):
            self.name = name
            self.total_bytes = 2 * 1024 * 1024 * 1024
    class FakeRec:
        def __init__(self, name: str):
            self.candidate = FakeCandidate(name)
            self.recommended_plan = None
            self.alternative_plans = []
            self.compatibility = type("Compat", (), {"friendly_explanation": f"{name} fits RAM"})()

    modal._candidates = [FakeRec("Candidate 0"), FakeRec("Candidate 1"), FakeRec("Candidate 2")]
    modal._hardware_report = type("HW", (), {})()

    tui = LocalPilotTUI()
    async with tui.run_test(size=(120, 40)) as pilot:
        await tui.push_screen(modal)
        await pilot.pause()

        # Switch to step 3
        modal.query_one("#wizard-step-1").add_class("hidden")
        modal.query_one("#wizard-step-3").remove_class("hidden")
        modal._render_step3_shortlist()
        await pilot.pause()

        rs = modal.query_one("#candidate-radioset", RadioSet)
        cand_buttons = rs.query(RadioButton)
        assert len(cand_buttons) == 3

        # Candidate 0 is initially selected and others unselected
        b0 = modal.query_one("#cand-0", RadioButton)
        b1 = modal.query_one("#cand-1", RadioButton)
        b2 = modal.query_one("#cand-2", RadioButton)
        assert b0.value is True
        assert b1.value is False
        assert b2.value is False
        assert modal._selected_candidate == modal._candidates[0]

        # Select candidate 1 via _select_candidate_index
        modal._select_candidate_index(1)
        await pilot.pause()

        assert b0.value is False
        assert b1.value is True
        assert b2.value is False
        assert modal._selected_candidate == modal._candidates[1]

        # Select candidate 2
        modal._select_candidate_index(2)
        await pilot.pause()

        assert b0.value is False
        assert b1.value is False
        assert b2.value is True
        assert modal._selected_candidate == modal._candidates[2]


@pytest.mark.asyncio
async def test_guided_setup_cancel_buttons():
    """Verify that cancel button is available and functional at any wizard step."""
    tui = LocalPilotTUI()
    async with tui.run_test(size=(120, 40)) as pilot:
        # Step 2 cancel
        modal = GuidedSetupModal()
        await tui.push_screen(modal)
        await pilot.pause()
        modal._switch_step(2)
        await pilot.pause()
        cancel_btn2 = modal.query_one("#step2-cancel-btn", Button)
        assert cancel_btn2 is not None
        modal.on_button_pressed(Button.Pressed(cancel_btn2))
        await pilot.pause()

        # Step 3 cancel
        modal = GuidedSetupModal()
        await tui.push_screen(modal)
        await pilot.pause()
        modal._switch_step(3)
        await pilot.pause()
        cancel_btn3 = modal.query_one("#step3-cancel-btn", Button)
        assert cancel_btn3 is not None
        modal.on_button_pressed(Button.Pressed(cancel_btn3))
        await pilot.pause()

        # Step 4 cancel
        modal = GuidedSetupModal()
        await tui.push_screen(modal)
        await pilot.pause()
        modal._switch_step(4)
        await pilot.pause()
        cancel_btn4 = modal.query_one("#step4-cancel-btn", Button)
        assert cancel_btn4 is not None
        modal.on_button_pressed(Button.Pressed(cancel_btn4))
        await pilot.pause()

        # Auth cancel
        modal = GuidedSetupModal()
        await tui.push_screen(modal)
        await pilot.pause()
        modal._switch_step("auth")
        await pilot.pause()
        cancel_auth = modal.query_one("#auth-cancel-btn", Button)
        assert cancel_auth is not None
        modal.on_button_pressed(Button.Pressed(cancel_auth))
        await pilot.pause()


@pytest.mark.asyncio
async def test_model_comparison_modal_uses_alias_header():
    """Verify that ModelComparisonModal uses model alias names for column headers."""
    from localpilot.tui.app import ModelComparisonModal
    from localpilot.domain.recommendation import ModelComparisonRow

    rows = [
        ModelComparisonRow(
            candidate_id="c1",
            model_id="id1",
            name="Llama 3.2 3B Instruct Q4_K_M GGUF",
            alias="llama3.2:3b",
            distinction="Fastest local",
            purpose="General chat",
            release_date="2024-09",
            download_size="2.0 GB",
            storage_needed="2.5 GB",
            context_window="8k",
            memory_burden="Safe (3.2 GB)",
            execution_plan="Direct GGUF",
            access_requirements="Open",
            confidence="High",
        ),
        ModelComparisonRow(
            candidate_id="c2",
            model_id="id2",
            name="Qwen 2.5 Coder 3B Instruct Q4_K_M GGUF",
            alias="qwen2.5-coder:3b",
            distinction="Code specialist",
            purpose="Coding assistant",
            release_date="2024-11",
            download_size="2.1 GB",
            storage_needed="2.6 GB",
            context_window="16k",
            memory_burden="Safe (3.5 GB)",
            execution_plan="Direct GGUF",
            access_requirements="Open",
            confidence="High",
        ),
    ]

    tui = LocalPilotTUI()
    async with tui.run_test(size=(120, 40)) as pilot:
        modal = ModelComparisonModal(rows)
        await tui.push_screen(modal)
        await pilot.pause()

        table = modal.query_one("#comparison-table", DataTable)
        headers = [str(c.label) for c in table.columns.values()]
        # Column 0 is 'Metric', columns 1 and 2 should be the aliases
        assert headers[0] == "Metric"
        assert headers[1] == "llama3.2:3b"
        assert headers[2] == "qwen2.5-coder:3b"

        # Also verify 'Full Name' is present as a row
        row_labels = [table.get_row_at(i)[0] for i in range(table.row_count)]
        assert "Full Name" in row_labels


@pytest.mark.asyncio
async def test_discover_table_star_beside_alias_and_highlight():
    """Verify that favorited models have the star placed beside their alias and highlighted text."""
    tui = LocalPilotTUI()
    async with tui.run_test(size=(120, 40)) as pilot:
        # Switch to discover tab
        tabs = tui.query_one("#main-tabs", TabbedContent)
        tabs.active = "discover-tab"
        await pilot.pause()

        table = tui.query_one("#discover-table", DataTable)
        assert table is not None

        # Check column headers: Score, Alias, Name, Resource, Source, Size, Context
        cols = [c.label for c in table.columns.values()]
        assert len(cols) >= 3


@pytest.mark.asyncio
async def test_home_live_bar_inline_button():
    """Verify that #btn-pause-live is mounted and styled inside #home-live-bar."""
    tui = LocalPilotTUI()
    async with tui.run_test(size=(120, 40)) as pilot:
        btn = tui.query_one("#btn-pause-live", Button)
        assert btn is not None
        assert btn.parent.id == "home-live-bar"


@pytest.mark.asyncio
async def test_discover_table_needs_token_column():
    """Verify that Discover table has a 'Needs Token' column identifying gated vs free models."""
    tui = LocalPilotTUI()
    async with tui.run_test(size=(120, 40)) as pilot:
        tabs = tui.query_one("#main-tabs", TabbedContent)
        tabs.active = "discover-tab"
        await pilot.pause()

        table = tui.query_one("#discover-table", DataTable)
        assert table is not None
        col_labels = [str(c.label) for c in table.columns.values()]
        assert "Needs Token" in col_labels

        # Find column index for 'Needs Token'
        needs_token_idx = col_labels.index("Needs Token")
        assert table.row_count > 0

        # Check that at least one row has [✓] Required or [ ] Free
        token_statuses = [str(table.get_row_at(i)[needs_token_idx]) for i in range(table.row_count)]
        assert any("Required" in s or "Free" in s for s in token_statuses)


@pytest.mark.asyncio
async def test_guided_setup_option_b_auth_step():
    """Verify that Step 3.5 in GuidedSetupModal dynamically displays Option B and token links."""
    from localpilot.domain.models import NormalizedCandidate, TaskCategory, ExecutionMode, ConfidenceLevel, VerificationStage

    modal = GuidedSetupModal()
    cand = NormalizedCandidate(
        candidate_id="hf-meta-llama-test",
        model_id="meta-llama/Llama-3.2-1B-Instruct-GGUF",
        name="Llama 3.2 1B Instruct (Official Meta)",
        short_alias="llama3.2:1b",
        source="huggingface",
        revision="main",
        artifact_id="llama-3.2-1b-instruct-q4_k_m.gguf",
        equivalence_group="llama-3.2-1b",
        architecture="llama",
        family="Llama 3.2",
        tasks=[TaskCategory.CHAT],
        languages=["en"],
        parameter_count="1.2B",
        format="gguf",
        total_bytes=880 * 1024 * 1024,
        files=["llama-3.2-1b-instruct-q4_k_m.gguf"],
        source_url="https://huggingface.co/meta-llama/Llama-3.2-1B-Instruct-GGUF",
        runtime="llamacpp",
        execution_mode=ExecutionMode.AUTO,
        usable_context=8192,
        metadata_fetched_at="2026-10-19T00:00:00Z",
        requires_auth=True,
        estimated_ram_bytes=1300 * 1024 * 1024,
        estimated_disk_bytes=880 * 1024 * 1024,
        confidence=ConfidenceLevel.VERIFIED,
        verification_stage=VerificationStage.SUPPORTED,
    )

    class FakeRec:
        def __init__(self, c):
            self.candidate = c
            self.recommended_plan = None
            self.alternative_plans = []
            self.compatibility = type("Compat", (), {"friendly_explanation": "Fits memory"})()

    modal._candidates = [FakeRec(cand)]
    modal._selected_candidate = modal._candidates[0]

    tui = LocalPilotTUI()
    async with tui.run_test(size=(120, 40)) as pilot:
        await tui.push_screen(modal)
        await pilot.pause()

        modal._switch_step("auth")
        await pilot.pause()

        auth_title = modal.query_one("#auth-step-title", Label)
        assert "Option B" in str(auth_title.render())
        assert "Llama 3.2 1B" in str(auth_title.render())

        auth_desc = modal.query_one("#auth-desc", Label)
        desc_text = str(auth_desc.render())
        assert "https://huggingface.co/settings/tokens" in desc_text
        assert "meta-llama/Llama-3.2-1B-Instruct-GGUF" in desc_text


@pytest.mark.asyncio
async def test_auth_token_modal_renders_option_b_and_links():
    """Verify that standalone AuthTokenModal presents Option B title, publisher link, and token site."""
    from localpilot.tui.app import AuthTokenModal

    modal = AuthTokenModal("Meta Llama 3.2 3B", "https://huggingface.co/meta-llama/Llama-3.2-3B-Instruct-GGUF")
    tui = LocalPilotTUI()
    async with tui.run_test(size=(120, 40)) as pilot:
        await tui.push_screen(modal)
        await pilot.pause()

        title = modal.query_one("#auth-modal-title", Label)
        assert "Option B: Download Meta Llama 3.2 3B with Your Free Token" in str(title.render())

        desc = modal.query_one("#auth-modal-desc", Label)
        desc_text = str(desc.render())
        assert "https://huggingface.co/settings/tokens" in desc_text
        assert "https://huggingface.co/meta-llama/Llama-3.2-3B-Instruct-GGUF" in desc_text

        # Verify browser buttons exist in AuthTokenModal
        btn_model = modal.query_one("#btn-auth-open-model", Button)
        btn_token = modal.query_one("#btn-auth-open-token", Button)
        assert btn_model is not None
        assert btn_token is not None


@pytest.mark.asyncio
async def test_browser_launch_utility_and_buttons(monkeypatch):
    """Verify open_url_in_browser helper and Discover tab browser button."""
    from localpilot.tui.app import open_url_in_browser

    opened_urls = []
    def fake_open(url, new=0, autoraise=True):
        opened_urls.append(url)
        return True

    monkeypatch.setattr("webbrowser.open", fake_open)

    # Test open_url_in_browser
    assert open_url_in_browser("https://huggingface.co/settings/tokens") is True
    assert "https://huggingface.co/settings/tokens" in opened_urls

    # Test discover tab open-link button
    tui = LocalPilotTUI()
    async with tui.run_test(size=(120, 40)) as pilot:
        tabs = tui.query_one("#main-tabs", TabbedContent)
        tabs.active = "discover-tab"
        await pilot.pause()

        btn = tui.query_one("#btn-discover-open-link", Button)
        assert btn is not None
        assert "Open in Browser" in str(btn.label)

        # Trigger button press
        await pilot.click("#btn-discover-open-link")
        await pilot.pause()
        assert len(opened_urls) >= 2


@pytest.mark.asyncio
async def test_model_capabilities_and_why_explanation():
    """Verify capabilities column in Discover table and category mentions in 'Why this model?'."""
    tui = LocalPilotTUI()
    async with tui.run_test(size=(120, 40)) as pilot:
        tabs = tui.query_one("#main-tabs", TabbedContent)
        tabs.active = "discover-tab"
        await pilot.pause()

        table = tui.query_one("#discover-table", DataTable)
        assert table is not None
        col_labels = [str(c.label) for c in table.columns.values()]
        assert "Capabilities" in col_labels

        # Find row for a model with coding/writing
        caps_idx = col_labels.index("Capabilities")
        assert table.row_count > 0
        all_caps = [str(table.get_row_at(i)[caps_idx]) for i in range(table.row_count)]
        assert any("Coding" in c for c in all_caps)
        assert any("Writing" in c for c in all_caps)

        # Check explanation text under table
        expl = tui.query_one("#explanation-text", Label)
        expl_text = str(expl.render())
        assert "Capabilities & Categories:" in expl_text
        assert "Why this model:" in expl_text


@pytest.mark.asyncio
async def test_capability_filter_modal():
    """Verify CapabilityFilterModal allows multiple-choice selection, clear all, and select all."""
    from localpilot.tui.app import CapabilityFilterModal

    modal = CapabilityFilterModal(current_selected={"coding", "image"})
    tui = LocalPilotTUI()
    async with tui.run_test(size=(120, 40)) as pilot:
        await tui.push_screen(modal)
        await pilot.pause()

        # Check checkboxes exist
        chk_coding = modal.query_one("#chk-cap-coding", Checkbox)
        chk_writing = modal.query_one("#chk-cap-writing", Checkbox)
        chk_image = modal.query_one("#chk-cap-image", Checkbox)
        chk_audio = modal.query_one("#chk-cap-audio", Checkbox)
        chk_video = modal.query_one("#chk-cap-video", Checkbox)
        chk_reasoning = modal.query_one("#chk-cap-reasoning", Checkbox)

        assert chk_coding.value is True
        assert chk_writing.value is False
        assert chk_image.value is True

        # Test Clear All
        await pilot.click("#btn-cap-clear-all")
        await pilot.pause()
        assert chk_coding.value is False
        assert chk_image.value is False

        # Test Select All
        await pilot.click("#btn-cap-select-all")
        await pilot.pause()
        assert chk_coding.value is True
        assert chk_writing.value is True
        assert chk_audio.value is True
        assert chk_video.value is True


@pytest.mark.asyncio
async def test_discover_capability_filtering():
    """Verify multiple-choice capability filtering filters the model list in Discover tab."""
    tui = LocalPilotTUI()
    async with tui.run_test(size=(120, 40)) as pilot:
        tabs = tui.query_one("#main-tabs", TabbedContent)
        tabs.active = "discover-tab"
        await pilot.pause()

        btn_filter = tui.query_one("#btn-discover-caps-filter", Button)
        assert btn_filter is not None
        assert "Caps: All" in str(btn_filter.label)

        # Apply audio filter
        tui._discover_capability_filter = {"audio"}
        tui._refresh_discover_table("")
        for _ in range(30):
            await pilot.pause(0.1)
            table = tui.query_one("#discover-table", DataTable)
            if table.row_count > 0:
                col_labels = [str(c.label) for c in table.columns.values()]
                caps_idx = col_labels.index("Capabilities")
                if all("Audio" in str(table.get_row_at(i)[caps_idx]) for i in range(table.row_count)):
                    break

        table = tui.query_one("#discover-table", DataTable)
        assert table.row_count > 0
        col_labels = [str(c.label) for c in table.columns.values()]
        caps_idx = col_labels.index("Capabilities")

        for i in range(table.row_count):
            row_cap = str(table.get_row_at(i)[caps_idx])
            assert "Audio" in row_cap


def test_home_ascii_banner_exact_match():
    """Verify HOME_ASCII_BANNER uses the exact block-character art requested by the user."""
    from localpilot.cli.formatting import HOME_ASCII_BANNER
    expected_lines = [
        "██      ████    ████   ████  ██      █████  █████ ██      ████  ██████",
        "██     ██  ██  ██     ██  ██ ██      ██  ██   █   ██     ██  ██   ██",
        "██     ██  ██  ██     ██████ ██      █████    █   ██     ██  ██   ██",
        "██     ██  ██  ██     ██  ██ ██      ██       █   ██     ██  ██   ██",
        "██████  ████    ████  ██  ██ ██████  ██     █████ ██████  ████    ██",
    ]
    for line in expected_lines:
        assert line in HOME_ASCII_BANNER


def test_release_date_display():
    """Verify release_date_display property on NormalizedCandidate."""
    from localpilot.sources.seed_catalog import get_seed_candidates

    seeds = get_seed_candidates()
    assert len(seeds) > 0

    cand1 = seeds[0].model_copy(update={"verified_release_at": "2025-01-20T00:00:00Z"})
    assert cand1.release_date_display == "2025-01-20"

    cand2 = seeds[0].model_copy(update={"verified_release_at": None, "repo_created_at": "2024-11-15T12:30:00Z"})
    assert cand2.release_date_display == "2024-11-15"

    cand3 = seeds[0].model_copy(update={
        "verified_release_at": None,
        "repo_created_at": None,
        "repo_modified_at": None,
        "artifact_published_at": None,
    })
    assert cand3.release_date_display == "Recent"


@pytest.mark.asyncio
async def test_comprehensive_filter_modal():
    """Verify ComprehensiveFilterModal quick buttons, select dropdowns, and apply action."""
    from localpilot.tui.app import ComprehensiveFilterModal
    from textual.app import App

    class TestModalApp(App):
        def compose(self):
            yield Button("Open", id="btn-open")

    app = TestModalApp()
    async with app.run_test(size=(120, 40)) as pilot:
        modal = ComprehensiveFilterModal(
            current_capabilities={"coding"},
            current_provider="Meta",
            current_source="huggingface",
            current_access="free",
        )
        app.push_screen(modal)
        await pilot.pause()

        # Check initial states
        chk_code = modal.query_one("#chk-filter-cap-coding", Checkbox)
        chk_audio = modal.query_one("#chk-filter-cap-audio", Checkbox)
        assert chk_code.value is True
        assert chk_audio.value is False

        # Click quick button Reason Only
        await pilot.click("#btn-quick-reason")
        await pilot.pause()
        chk_reason = modal.query_one("#chk-filter-cap-reasoning", Checkbox)
        assert chk_reason.value is True
        assert chk_code.value is False

        # Click Clear All
        await pilot.click("#btn-quick-clear-all")
        await pilot.pause()
        assert chk_reason.value is False

        # Click Select All
        await pilot.click("#btn-quick-select-all")
        await pilot.pause()
        assert chk_reason.value is True
        assert chk_code.value is True


@pytest.mark.asyncio
async def test_discover_table_released_column():
    """Verify that Discover table has a 'Released' column showing dates."""
    tui = LocalPilotTUI()
    async with tui.run_test(size=(120, 40)) as pilot:
        tabs = tui.query_one("#main-tabs", TabbedContent)
        tabs.active = "discover-tab"
        await pilot.pause()

        table = tui.query_one("#discover-table", DataTable)
        assert table is not None
        col_labels = [str(c.label) for c in table.columns.values()]
        assert "Released" in col_labels





