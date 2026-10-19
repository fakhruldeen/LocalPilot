# LocalPilot Capability Inventory

This document provides a comprehensive audit of existing capabilities in LocalPilot versus the newly requested capabilities across CLI commands, TUI screens, application services, database schemas, and external integrations.

## 1. CLI Commands Audit

| Command Area | Existing Status | Target State & Enhancements |
|---|---|---|
| `connect` | Partial (subcommand of `connections` or standalone) | No-argument interactive wizard, `--preview` mode, direct application targeting (`open-code`, `continue`, `cursor`, etc.), dry-run preview, granular stage reporting (`Not tested`, `Testing`, `Connected`, `Failed`). |
| `connections` | Basic listing & remove/disconnect | `list`, `test <id>`, `inspect <id>`, `restore <id>`, `remove <id>`. Displays connected clients, port, token status, config file paths, and active model. |
| `doctor` | Basic hardware check (`_do_doctor`) | Comprehensive diagnostic engine with targeted checks: `--model <alias>`, `--connection <id>`, `--tool <id>`, `--full`, and `--repair` interactive/auto-fix modes. |
| `bench` | Missing (only `benchmark_tps` on record) | Full benchmarking suite: `bench run <model>`, `bench compare <model1> <model2>`, `bench results`, `bench show <run_id>`. Measures TTFT, TPS, memory footprint, cold vs warm start. |
| `tools` | Missing | Task interface suite with 18 core tasks: `chat`, `summarize`, `rewrite`, `translate`, `compare`, `extract`, `classify`, `explain-code`, `transcribe`, `speak`, `voice`, `ask-files`, `find-passages`, `describe-image`, `read-image`, `review-table`, `meeting-notes`, `draft`. |
| `search` | Model catalog search only | Local document retrieval & RAG engine: `add <path>`, `list`, `status`, `update [collection]`, `find <query>`, `ask <query>`, `remove <collection>`. Multi-format support, chunking, local embeddings / lexical retrieval, citations. |
| `voice` | Missing | Speech-to-text, text-to-speech, and full interactive push-to-talk voice conversation loop with fallback when audio devices or libraries are missing. |
| `chat`, `run` | Existing | Preserved and integrated with new runtime/model selection flags. |
| `versions`, `alias`, `keys`, `gateway` | Existing | Preserved; gateway exposes endpoints consumed by `connect` integrations and tools. |

---

## 2. Application Services Audit

| Service | Module | Current State | Required Extensions |
|---|---|---|---|
| `IntegrationTemplateService` / `ConnectionManager` | `src/localpilot/application/integration_service.py` | Basic template generation and connection registration. | Support `--preview`, `restore`, live verification with granular step logging, deep config file backup & rollback. |
| `DiagnosticService` | New: `src/localpilot/application/diagnostic_service.py` | Minimal hardware inspection in CLI `main.py`. | Structured findings (`DiagnosticFinding`), repair plans (`RepairPlan`), target-specific tests (network, gateway, model weights, config files, permissions). |
| `BenchmarkService` | New: `src/localpilot/application/benchmark_service.py` | None (only single TPS count during verification). | Standard prompt suites, repeatable trials, metrics collection (TTFT, TPS, VRAM/RAM delta), run persistence, markdown/tabular comparison formatting. |
| `TaskService` | New: `src/localpilot/application/task_service.py` | Ad-hoc chat logic in CLI/TUI. | Unified execution engine for 18 task types, task capability registry, dynamic model selection by task affinity, streaming output, artifact persistence. |
| `SearchService` | New: `src/localpilot/application/search_service.py` | None. | Document ingestion (PDF, Markdown, TXT, CSV), recursive directory walking, text chunking, inverted index / hybrid dense retrieval, source citation formatting. |
| `VoiceService` | New: `src/localpilot/application/voice_service.py` | None. | Lazy audio backend discovery (`sounddevice`, `numpy`, whisper / piper / system TTS / sherpa-onnx / espeak-ng / ffmpeg), safe error handling when dependencies or audio hardware are absent. |
| `RuntimeLifecycleService` | `src/localpilot/application/runtime_service.py` | Manages Ollama/Llama.cpp processes. | Retain full process supervision, warm-keeping, safe port allocation, and timeout protection for task executions. |
| `DatabaseManager` | `src/localpilot/storage/db.py` | Schema versions v1 to v5. | Schema migration v6 adding tables for benchmarks, tasks, search collections/passages, and voice runs. |

---

## 3. Database Schema Audit (Migration v6)

New tables required in SQLite storage:
- `benchmarks`: Metadata for benchmark definitions and prompt suites.
- `benchmark_runs`: Records of individual benchmark runs (model, TTFT, TPS, memory, prompt tokens, completion tokens, duration, timestamp).
- `task_runs`: Execution history of task invocations (`task_id`, `input_summary`, `model_alias`, `status`, `duration_ms`, `tokens_used`).
- `task_artifacts`: Generated outputs or intermediate artifacts linked to task runs.
- `search_collections`: Registered directories or document sets (`name`, `path`, `file_count`, `last_indexed_at`, `status`).
- `search_passages`: Chunked document passages (`collection_id`, `file_path`, `passage_text`, `chunk_index`, `metadata_json`).
- `voice_runs`: History of voice transcription or TTS runs (`direction`, `duration_ms`, `audio_bytes_length`, `transcript`, `model`).

---

## 4. TUI Capabilities Audit

Current TUI (`src/localpilot/tui/app.py`):
- Tabbed interface: Hardware specs, Catalog / Find a Model, Running models, System logs, Settings.
- Action-oriented rather than goal-oriented.

Target TUI Refactoring:
- Reorganize screens around user goals:
  1. **Chat & Voice**: Interactive text chat, push-to-talk voice trigger, session switching.
  2. **AI Tools**: Task cards (summarize, rewrite, translate, code, images, tables).
  3. **Document Search**: Index folders, browse collections, ask questions over local files with citations.
  4. **Connections**: Visual connection wizard, status badges, quick-test, restore actions.
  5. **Model Hub & Benchmarks**: Discover, download, inspect, compare performance benchmarks.
  6. **Diagnostics & Doctor**: System health radar, one-click repair review and execution.

---

## 5. External Dependency & Packaging Audit

- **Core (Zero extra mandatory dependencies)**: Typer, Rich, Textual, Pydantic, SQLite3, Requests/Httpx.
- **Optional Dependencies (Lazy-loaded with friendly fallbacks)**:
  - Document parsing: `pypdf`, `pymupdf`, or standard fallback text extractors.
  - Search/embeddings: Pure Python TF-IDF / BM25 fallback when vector embedding models are unavailable.
  - Voice STT: Whisper.cpp / OpenAI Whisper / Sherpa-ONNX / sounddevice. When unavailable: explicit warning and instructions on optional dependency installation.
  - Voice TTS: Piper / Espeak-ng / system `say` / browser TTS. Safe fallback when no voice engine is installed.
