# LocalPilot Verification & Test Matrix

This matrix tracks the test suites, verification commands, coverage targets, and manual acceptance checkpoints for each capability batch.

## 1. Automated Test Suites

| Test Suite | File | Scope | Status |
|---|---|---|---|
| Existing CLI & TUI Baseline | `tests/test_cli_and_tui.py` | Baseline CLI commands, aliases, catalog | ✅ Passing |
| Existing Gateway & Integration | `tests/test_gateway_and_integration.py` | Gateway server, API keys, connections | ✅ Passing |
| Existing Favorites & Aliases | `tests/test_favorites_and_aliases.py` | Model favoriting and aliasing | ✅ Passing |
| Schema Migration v6 | `tests/test_schema_migration_v6.py` | Migration v5 -> v6, tables creation, constraints | ✅ Passing |
| Connections & Previews | `tests/test_connections_workflow.py` | Guided connect, preview flag, restore, stage logs | ✅ Passing |
| Diagnostics & Self-Healing | `tests/test_diagnostics_and_repairs.py` | Targeted doctor checks, repair plans, execution | ✅ Passing |
| Benchmarking Engine | `tests/test_benchmark_service.py` | Benchmark runs, metrics calculation, comparison table | ✅ Passing |
| Task Framework & Text Tools | `tests/test_tasks_and_tools.py` | 18 task definitions, input normalization, task CLI | ✅ Passing |
| Document Search & RAG | `tests/test_search_service.py` | Collection indexing, chunking, retrieval, citations | ✅ Passing |
| Voice & Audio Engine | `tests/test_voice_service.py` | Audio detection, STT/TTS graceful fallback, transcription | ✅ Passing |
| Vision & Structured Analysis | `tests/test_multimodal_tools.py` | Table review, extraction, classification, vision guard | ✅ Passing |
| TUI Integration & Layout | `tests/test_tui_redesign.py` | Goal-oriented navigation, keybindings, screen mounting | ✅ Passing |

---

## 2. CLI Acceptance Commands

| Capability | Command | Expected Output / Behavior | Status |
|---|---|---|---|
| Connect Preview | `localpilot connect cursor --preview` | Displays proposed configuration diff without modifying files on disk. | ✅ Verified |
| Guided Connect | `localpilot connect` | Interactively prompts for client, port, and model if no arguments provided. | ✅ Verified |
| Connection Restore | `localpilot connections restore <id>` | Reverts modified configuration file back to `.backup` copy. | ✅ Verified |
| Targeted Doctor | `localpilot doctor --model llama3` | Checks integrity of llama3 model file, alias, and runtime readiness. | ✅ Verified |
| Doctor Repair | `localpilot doctor --repair` | Offers concrete fixes (e.g. restart gateway, repair alias) with confirmation. | ✅ Verified |
| Bench Run | `localpilot bench run <model>` | Runs standardized evaluation and reports TTFT, TPS, and RAM usage. | ✅ Verified |
| Bench Compare | `localpilot bench compare <m1> <m2>`| Formats side-by-side performance comparison. | ✅ Verified |
| Tools Discovery | `localpilot tools list` | Lists all 18 available tools with descriptions, requirements, and examples. | ✅ Verified |
| Text Task Execution | `localpilot tools summarize file.txt`| Summarizes file content using the active model with streaming output. | ✅ Verified |
| Search Add & Index | `localpilot search add ./docs` | Indexes files in directory into a named collection. | ✅ Verified |
| Search Retrieval | `localpilot search ask "how to install"` | Returns context-grounded response with explicit file and line citations. | ✅ Verified |
| Voice Push-to-Talk | `localpilot voice` | Enters voice interaction loop or cleanly explains missing audio hardware. | ✅ Verified |

---

## 3. Safety & Resource Verification

- [x] Zero unprompted network calls during unit tests.
- [x] No hardcoded model weight downloads during test execution.
- [x] Graceful fallback when optional libraries (`pypdf`, `sounddevice`, `numpy`, `pyttsx3`) are missing.
- [x] Safe rollback of configuration files during connection testing or restoration.
- [x] Storage quota checks before indexing files in `search`.
