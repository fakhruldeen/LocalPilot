# LocalPilot Independent Acceptance-Test Quality Audit (ACCEPTANCE_TEST_AUDIT.md)

**Audit Date**: 2026-10-17  
**Commit Inspected**: `6d789ba067cf45aa18fa21c96410eb5eba65076e`  
**Test Environment**: Linux 6.8.0-101-generic x86_64 | Python 3.14.6 | Pytest 9.1.1  
**Auditor**: Independent Acceptance-Test Quality Engineer  

---

## 1. Executive Summary & Baseline

LocalPilot was audited to evaluate whether automated tests asserting user-facing acceptance and safety guarantees genuinely exercise public entry points and verify real side effects, or whether they rely on shallow constructor checks, internal SQL queries, and permissive mocks.

### Baseline Metrics
- **Initial Total Collected Tests**: 164 tests
- **Initial Passing Tests**: 164 tests (100%)
- **Identified Test Quality Gaps**:
  1. *Constructor-Only Checks*: Modals and screens tested solely by asserting constructor attributes (e.g. `assert modal.model_name == ...`) without mounting the widget or verifying focus.
  2. *Bypassed Public Entry Points*: CLI tests running private SQL queries directly in the test fixture rather than executing registered CLI commands via `CliRunner`.
  3. *Incomplete Journeys*: Guided wizard tests terminating at Step 3 without asserting approval, acquisition handoff, or chat readiness.

---

## 2. Requirement-to-Test Evidence Matrix

| Requirement | Test Name | Claimed Behavior | Actual Entry Point Exercised | Mock Boundaries | Assertions Made | Inspected Side Effects | Audit Classification | Remediation Status |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Safe Removal Focus** | `test_remove_model_modal_safe_defaults` | Confirms modal composes with safe default focus on 'Keep Model' to prevent data loss. | Modal constructor only (`RemoveModelModal(...)`). | None | Checked attributes `model_name` and `size_str`. | None (widget never mounted in Textual App). | **Unit / Constructor Test** *(Misleadingly claimed safe focus)* | **Remediated**: Mounted with `tui.run_test()`, asserted focused widget is `#keep-btn`. |
| **Friendly Name Resolution** | `test_cli_friendly_name_resolution` | Confirms CLI commands accept friendly recognizable model names. | Raw SQLite connection query (`conn.execute(...)`). | None | Verified SQL `LIKE` operator returned row. | None (CLI command never invoked). | **Incomplete / Fake Entry Point** | **Remediated**: Invoked registered `app` via `CliRunner`, verified command resolution for exact, partial, and ambiguous names. |
| **Hardware Mode Selection** | `test_performance_settings_modal_selection` | Confirms performance modal allows selecting and applying hardware mode. | Modal constructor (`PerformanceSettingsModal(current_pref="gpu")`). | None | Checked `modal.current_pref == "gpu"`. | None (radio buttons never toggled, apply never clicked). | **Unit / Constructor Test** | **Remediated**: Mounted with `tui.run_test()`, clicked `#perf-cpu` and `#perf-apply-btn`, verified returned value. |
| **Guided Onboarding Journey** | `test_tui_guided_setup_wizard_flow` | Confirms full novice onboarding from first run to model chat. | Wizard modal Steps 1 to 3; cancelled before approval. | None | Checked step visibility and goal buttons. | Terminated before plan generation, setup approval, or chat. | **Incomplete Journey** | **Remediated**: Added `test_complete_onboarding_acceptance_journey` covering Step 1 -> Step 6 -> Chat -> Stop. |
| **Chat Bubble Display** | `test_chat_screen_message_widget_append` | Confirms messages render without crashing. | Widget constructor & Textual layout. | None | Checked message role and content. | None. | **Genuine Unit/Component Test** | Valid as component test. |
| **Model Details Disclosure** | `test_model_details_modal_renders_metadata` | Confirms technical modal discloses storage and metadata. | Modal constructor. | None | Checked dictionary mapping. | None. | **Unit Test** | Valid as component test. |
| **Process Ownership Protection** | `test_process_ownership_verifier` | Verifies PID and create time validation prevents PID reuse. | `OwnershipVerifier.verify_process` | `psutil` Process mock | Verified matching vs mismatched create time. | None. | **Genuine Integration Test** | Valid. |
| **Reference-Counted Storage** | `test_storage_reference_counted_protection` | Deleting model decrements ref count without deleting shared file. | `StorageManager.safe_remove_installation` | Filesystem on `tmp_path` | Verified file existence and SQLite `ref_count`. | Filesystem & DB verified. | **Genuine Acceptance Test** | Valid. |
| **Update Rollback** | `test_update_rollback_on_failure` | Failed update restores exact backup file and metadata. | `UpdateWorkflow.apply_update` | Staged file and backup dir | Checked restored file contents and status string. | Filesystem & DB verified. | **Genuine Acceptance Test** | Valid. |
| **Secret Redaction** | `test_support_bundle_redaction` | Diagnostic support export redacts tokens and home directory paths. | `export_support_bundle` | SQLite logs | Checked exported JSON for absence of raw tokens. | File exported and inspected. | **Genuine Acceptance Test** | Valid. |

---

## 3. Mocking Discipline & Isolation Rules

1. **Mocking Boundaries**:
   - **Allowed Mocks**: Network socket streams (`httpx` responses), OS hardware discovery probes (e.g. simulated Apple Silicon vs discrete CUDA), external daemon processes (simulated Ollama service).
   - **Prohibited Mocks**: Mocking internal domain logic, skipping plan approval hash checks, fabricating fake successful inference results without asserting the failure branch.
2. **State Isolation**:
   - Every acceptance test runs in an isolated `tmp_path` directory backed by `LOCALPILOT_HOME`.
   - Global singleton databases (`_global_db`) and event hubs (`_hub`) are reset between test runs to prevent state leakage.

---

## 4. Fault-Sensitivity Demonstrations

To ensure assertions are sensitive to actual regressions, critical tests were subjected to targeted fault injection:
- **Fault 1**: Removing default focus from `#keep-btn` in `RemoveModelModal` caused `test_remove_model_modal_safe_defaults` to fail immediately (`AssertionError: Expected #keep-btn to have focus`).
- **Fault 2**: Disabling friendly name matching in CLI caused `test_cli_friendly_name_resolution` to exit with error code 1.
- **Fault 3**: Returning constant `automatic` from `PerformanceSettingsModal` caused `test_performance_settings_modal_selection` to fail on CPU selection.

---

## 5. Human Usability Limitation

> [!NOTE]
> *Automated acceptance tests verify programmatic interaction contracts, focus traversal, and deterministic side effects. They do not constitute empirical proof of novice user comprehension. Human novice usability testing remains pending.*
