# LocalPilot Test Execution Results and Coverage Report

**Date of Execution**: `2026-10-17T11:37:51Z`  
**Git HEAD**: `279df156f34e7eb42485fe5fb96c30e778ef65a4`  
**Test Runner**: Pytest 9.1.1 (`.venv/bin/pytest`)  
**Python Version**: 3.14.6 (`cpython-3.14-linux-x86_64-gnu`)  

---

## 1. Summary of Execution

```bash
.venv/bin/pytest -v --cov=localpilot --cov-report=term-missing
```

- **Collected Items**: 73 tests
- **Passed**: 73 tests (100%)
- **Failed**: 0
- **Skipped**: 0
- **Duration**: 5.31 seconds

---

## 2. Test Layer Breakdown

| Layer | Test Count | Scope & Verification Nature | Status |
|---|---|---|---|
| **Layer A: Unit Tests** | 28 | Scoring formulas, hardware inspection, path normalization, redaction rules, candidate models. | 100% Passed |
| **Layer B: Contract Tests** | 14 | CLI / TUI interface signatures, UpdateWorkflow return types, SetupWorkflow plan hashing. | 100% Passed |
| **Layer C: Integration Tests** | 25 | Controlled HTTP mock servers (Ollama, llama.cpp), SQLite isolation, temporary directory unlinking. | 100% Passed |
| **Layer D: User-Journey Tests** | 6 | CLI subcommands (`doctor`, `discover`, `installed`, `running`, `stop`, `storage`). | 100% Passed |
| **Layer E: Real-Runtime Smoke Tests** | 0 | Live execution of physical model weights on host hardware. | Blocked by environment (Simulated) |

---

## 3. Test Suites & Pass Rates

| Test Suite File | Passed | Total | Focus Area |
|---|---|---|---|
| `tests/test_architecture_v2.py` | 10 | 10 | 3-axis decoupled architecture, service deduplication, schema v1->v2. |
| `tests/test_cli_and_tui.py` | 7 | 7 | CLI plain/JSON outputs, Textual TUI smoke execution. |
| `tests/test_compatibility_and_scoring.py` | 5 | 5 | Compatibility evaluation, RAM/VRAM budgets, scoring formula. |
| `tests/test_cross_layer_remediation.py` | 13 | 13 | Cross-layer gates: stop model, update contracts, deletion honesty, streaming errors. |
| `tests/test_hardware_and_platforms.py` | 4 | 4 | Linux, macOS Apple Silicon, Intel platform inspection logic. |
| `tests/test_jobs_ownership_and_storage.py` | 3 | 3 | Background job locks, process ownership verification, storage ref-counting. |
| `tests/test_observability_and_security.py` | 7 | 7 | Path traversal guards, localhost binding, JSONL log rotation, redaction. |
| `tests/test_remediation_p0_p1.py` | 7 | 7 | GGUF integrity, GPU mode fallback prevention, safe zip extraction. |
| `tests/test_remediation_round2.py` | 9 | 9 | Artifact resolution, update truthfulness, dead process pruning. |
| `tests/test_scenario_remaining.py` | 6 | 6 | Memory attribute, failed unlink protection, update sync, OptionInfo unwrapping. |
| `tests/test_workflows_and_updates.py` | 2 | 2 | End-to-end setup workflow and rollback on update failure. |
| **Total** | **73** | **73** | **100% Pass Rate** |

---

## 4. Statement Coverage by Component

| Module Name | Total Statements | Missed Statements | Coverage % |
|---|---|---|---|
| `localpilot/__init__.py` | 1 | 0 | 100% |
| `localpilot/application/jobs.py` | 76 | 1 | 99% |
| `localpilot/application/runtime_service.py` | 152 | 16 | 89% |
| `localpilot/application/setup_workflow.py` | 315 | 102 | 68% |
| `localpilot/application/update_workflow.py` | 133 | 9 | 93% |
| `localpilot/cli/formatting.py` | 17 | 5 | 71% |
| `localpilot/cli/main.py` | 497 | 295 | 41% |
| `localpilot/domain/capabilities.py` | 71 | 4 | 94% |
| `localpilot/domain/compatibility.py` | 205 | 90 | 56% |
| `localpilot/domain/documentation.py` | 17 | 9 | 47% |
| `localpilot/domain/execution_plan.py` | 67 | 2 | 97% |
| `localpilot/domain/models.py` | 109 | 3 | 97% |
| `localpilot/domain/ownership.py` | 47 | 11 | 77% |
| `localpilot/domain/scoring.py` | 72 | 3 | 96% |
| `localpilot/hardware/inspector.py` | 139 | 24 | 83% |
| `localpilot/observability/*` | 372 | 28 | 92% |
| `localpilot/platforms/*` | 206 | 57 | 72% |
| `localpilot/runtimes/base.py` | 83 | 17 | 80% |
| `localpilot/runtimes/deployments.py` | 84 | 35 | 58% |
| `localpilot/runtimes/docker_model_runner.py` | 61 | 31 | 49% |
| `localpilot/runtimes/llamacpp.py` | 161 | 105 | 35% |
| `localpilot/runtimes/lm_studio.py` | 92 | 37 | 60% |
| `localpilot/runtimes/ollama.py` | 248 | 153 | 38% |
| `localpilot/runtimes/vllm_backend.py` | 47 | 15 | 68% |
| `localpilot/security/__init__.py` | 85 | 38 | 55% |
| `localpilot/sources/*` | 295 | 124 | 58% |
| `localpilot/storage/db.py` | 94 | 5 | 95% |
| `localpilot/storage/inventory.py` | 170 | 25 | 85% |
| `localpilot/tui/app.py` | 353 | 153 | 57% |
| **TOTAL CODEBASE** | **4269** | **1397** | **67%** |

---

## 5. Critical Coverage Observations
- **Low CLI / TUI Coverage**: `cli/main.py` (41%) and `tui/app.py` (57%) have significant untested interactive branches, particularly user prompts, interactive chat loops, and error dialogs.
- **Low Runtime Adapter Coverage**: `llamacpp.py` (35%) and `ollama.py` (38%) have mock-tested entry points, but extensive binary preparation, download unpacking, and subprocess streaming paths remain uncovered by integration tests.
