# LocalPilot Expansion Implementation Plan

This plan organizes the implementation across 10 sequential, non-destructive batches adhering to strict reuse of existing components and zero breaking changes.

## Batch Overview

| Batch | Title | Key Deliverables |
|---|---|---|
| **Batch 0** | Shared Domain Contracts & DB Migration v6 | `src/localpilot/domain/task_contract.py`, Migration v6 in `src/localpilot/storage/db.py`, unit tests. |
| **Batch 1** | Application Connections (`connect`, `connections`) | Guided connection wizard, `--preview` dry-run, `connections restore <id>`, granular status reporting. |
| **Batch 2** | Diagnostics & Repairs (`doctor`) | Targeted checks (`--model`, `--connection`, `--tool`), interactive `--repair` flow with human confirmation. |
| **Batch 3** | Benchmarking Suite (`bench`) | Benchmark runs, standardized prompt sets, TTFT/TPS metrics collection, comparison tables. |
| **Batch 4** | Tools Engine & Core Framework (`tools`) | `TaskService`, task registry, execution contracts, capability discovery. |
| **Batch 5** | Text & Writing Task Interfaces | `chat`, `summarize`, `rewrite`, `translate`, `compare`, `explain-code`, `draft`. |
| **Batch 6** | Local Document Search & RAG (`search`) | `search` CLI, collection indexing, chunking, lexical & dense retrieval, `ask-files`, `find-passages`. |
| **Batch 7** | Voice & Audio Interaction (`voice`) | Speech-to-text, TTS, push-to-talk interactive mode, graceful hardware/library fallback. |
| **Batch 8** | Analysis, Vision & Structured Tools | `extract`, `classify`, `describe-image`, `read-image`, `review-table`, `meeting-notes`. |
| **Batch 9** | Goal-Oriented TUI Restructuring | Update Textual UI screens: Chat & Voice, Tools, Search, Connections, Models & Benchmarks, Diagnostics. |
| **Batch 10** | Hardening, Verification & Documentation | End-to-end integration tests, English/Arabic doc parity, limitations documentation. |

---

## Detailed Execution Steps

### Batch 0: Shared Domain Contracts & Database Migration v6
- Define domain models:
  - `TaskDescriptor`, `TaskPlan`, `TaskRun`, `TaskArtifact`
  - `DiagnosticFinding`, `RepairPlan`, `RepairResult`
  - `BenchmarkRun`, `BenchmarkComparison`
  - `SearchCollection`, `SearchPassage`, `SearchResult`
  - `VoiceRun`, `VoiceConfig`
- In `src/localpilot/storage/db.py`, append `migrate_v6` creating:
  - `benchmarks`, `benchmark_runs`, `task_runs`, `task_artifacts`, `search_collections`, `search_passages`, `voice_runs`.
- Write test in `tests/test_schema_migration_v6.py`.

### Batch 1: Application Connections
- Refactor `IntegrationTemplateService` to expose config previews without touching disk.
- Implement backup creation prior to modifying editor or client configs (`.backup` copies).
- Implement `restore` method to revert modified configuration files cleanly.
- Upgrade CLI `connect` with interactive prompt when no arguments are passed.
- Ensure stages report: `Target Detected`, `Config Generated`, `Gateway Running`, `Key Verified`, `Connection Verified`.

### Batch 2: Diagnostics & Self-Healing Doctor
- Implement `DiagnosticService` evaluating:
  - System hardware & memory budget.
  - Runtime daemon health (Ollama / Llama.cpp).
  - Gateway listener & API key validation.
  - Installed models file integrity & alias mappings.
  - External connection configs.
  - File permissions & storage free space.
- Add `--repair` logic with structured confirmation prompts for safety.

### Batch 3: Benchmarking Suite
- Implement `BenchmarkService`:
  - Standardized prompts for reasoning, coding, and short-form generation.
  - Measure Time to First Token (TTFT) and Tokens Per Second (TPS).
  - Track peak resident set size (RSS) during generation.
  - Persist benchmark runs to SQLite database.
- CLI subcommands: `localpilot bench run <model>`, `localpilot bench compare <m1> <m2>`, `localpilot bench results`.

### Batch 4 & 5: Tools Engine & Text Tasks
- Implement `TaskService` handling:
  - Automatic model fallback based on task category (e.g., code model for `explain-code`, fast model for `classify`).
  - Input parsing (file paths, standard input, command line strings).
  - Output formatting (Markdown, JSON, Plain text).
- Implement CLI `localpilot tools <task> ...` and subcommands:
  - `chat`, `summarize`, `rewrite`, `translate`, `compare`, `explain-code`, `draft`.

### Batch 6: Search & Document Retrieval (RAG)
- In `SearchService`:
  - Manage collections of files and directories.
  - Support text, markdown, code, and PDF/CSV (with fallback).
  - Built-in lightweight lexical search (BM25 / TF-IDF scoring) to work 100% locally without external heavy models, plus optional embedding models when present.
  - Format answers with verbatim source references and line/file anchors.

### Batch 7: Voice Interaction
- In `VoiceService`:
  - Detect audio capture/playback devices.
  - Provide clear instructions if audio dependencies (`sounddevice`, `numpy`) are missing.
  - Handle transcribing pre-recorded audio or live microphone clips.
  - Provide text-to-speech output using available local TTS or system utilities.

### Batch 8: Multimodal & Structured Tools
- Add handlers for:
  - `extract`: Regex/schema-guided extraction.
  - `classify`: Labeling against user-provided categories.
  - `review-table`: Markdown/CSV tabular data analysis.
  - `meeting-notes`: Transcript to agenda/action item synthesis.
  - `describe-image` & `read-image`: Vision-capable model checks with clear feedback when the active model is text-only.

### Batch 9: TUI Workflow Alignment
- Restructure `src/localpilot/tui/app.py` into clear goal tabs.
- Incorporate live status indicators and keyboard shortcuts.

### Batch 10: Verification & Docs
- Comprehensive test coverage across all new modules.
- Ensure documentation is updated in both English and Arabic.
