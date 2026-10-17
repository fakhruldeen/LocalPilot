"""Centralized documentation, user guides, and technical explanations for CLI and TUI.

Provides structured, comprehensive guidance tailored for both non-technical beginners
and technical power users, answering questions for every step of the local AI lifecycle.
"""

from __future__ import annotations
from typing import Dict, List, Optional
from localpilot.domain.help_content import HELP_TOPICS, TERMINOLOGY


DOCUMENTATION_TOPICS: Dict[str, Dict[str, str]] = {
    "overview": {
        "title": "LocalPilot Overview & Privacy Guarantee",
        "audience": "Both Novice and Technical Users",
        "summary": "What LocalPilot is and how it safeguards your computer and data.",
        "content": """
# LocalPilot: Local AI, Made Simple

LocalPilot is an open-source, hardware-aware desktop manager for local Large Language Models (LLMs).
It enables anyone to find, install, run, chat with, and update AI models entirely on their own machine.

## Core Guarantees:
1. **100% Local & Private**: No prompts, model weights, or system diagnostics ever leave your computer. There is zero telemetry or cloud inference.
2. **Hardware Protection**: Before downloading anything, LocalPilot inspects your physical CPU, RAM, GPU, and VRAM to calculate a safe memory budget. It filters out models that would freeze or crash your system.
3. **External Service Safety**: If you use Ollama, LM Studio, or Docker, LocalPilot discovers them without seizing control or terminating background daemons when you finish chatting.
4. **Clean Removal**: Models use reference-counted storage; removing a model frees real disk space without stranding orphaned files.
""",
    },
    "novice": {
        "title": "Beginner's Step-by-Step Guide",
        "audience": "Non-Technical Users",
        "summary": "A friendly guide from zero to your first local AI chat in 5 steps.",
        "content": """
# Beginner's Guide to Local AI

Running AI on your own computer means complete privacy and no monthly subscriptions. Here is how it works:

### What is an AI Model?
An AI model is a file containing neural network weights (like a digital brain). Different models excel at different tasks:
- **Chat & Writing**: General conversation, answering questions, drafting emails.
- **Coding**: Writing Python, JavaScript, debugging software.
- **Reasoning**: Math, logic puzzles, step-by-step problem solving.

### Step 1: Check Your Computer (`localpilot doctor`)
Your computer needs enough memory (RAM) to hold the model.
- **CPU**: Your main processor. All computers have one.
- **RAM**: Your working memory. An 8 GB computer can comfortably run 1B to 3B models. A 16 GB computer runs 7B to 8B models. 32 GB+ runs 14B models.
- **GPU (Graphics Card)**: Optional, but makes generation 3-10x faster.

### Step 2: Find a Model (`localpilot discover` or TUI)
LocalPilot automatically tests every model in its catalog against your computer:
- **Expected to Fit (Green)**: Fits safely within your free RAM/VRAM.
- **Needs Attention (Yellow)**: May run slowly or require closing other apps.
- **Incompatible (Red)**: Exceeds your hardware capacity. LocalPilot excludes these by default so you don't waste hours downloading files that won't work.

### Step 3: Setup & Verification (`localpilot setup`)
When you click setup:
1. LocalPilot downloads the real, verified weights directly from open repositories (e.g. Hugging Face or Ollama).
2. It runs a quick test inference call. Only after inference passes does LocalPilot mark the model as 'Ready & Verified'.

### Step 4: Chatting (`localpilot chat` or TUI)
Start an interactive chat right in your terminal. You can ask questions, brainstorm ideas, write stories, or ask for code help.

### Step 5: Cleaning Up (`localpilot stop` / `localpilot remove`)
- When you stop chatting, the model is unloaded from your memory so other programs run fast again.
- If you run out of disk space, `localpilot remove` safely deletes the files and reclaims storage.
""",
    },
    "concepts": {
        "title": "Essential Concepts & Plain Explanations",
        "audience": "All Users",
        "summary": "Plain explanations of sources, execution modes, recommendation scores, context, and storage.",
        "content": """
# Essential Concepts in Local AI

### 1. Model Sources vs Supporting Software
- **Model Source**: Where LocalPilot gets model files or model information (e.g. Hugging Face or Ollama Library).
- **Supporting Software**: The runtime engine installed locally that executes the model (e.g. llama.cpp or Ollama daemon).
- *Notice*: Not every source supports every execution method. LocalPilot checks compatibility beforehand.

### 2. Execution Preferences
- **Automatic**: LocalPilot chooses a supported configuration for this computer based on measured hardware.
- **CPU (Processor)**: Uses your main processor. Reliable across all computers; responses may be slower for larger models.
- **GPU (Graphics Card)**: Uses supported graphics acceleration (e.g. Metal or CUDA). Availability depends on this computer and the model.
- **Supported CPU + GPU**: Shares supported work between processor memory and graphics memory where the engine supports layer offloading.

### 3. Recommendation Score & Shortlist
- This score ranks how well the proposed model configuration matches your preferences and computer.
- It is NOT an intelligence score.
- Incompatible configurations are strictly excluded before ranking.
- Scoring weights: **Release Newness (40%)**, **Smaller Download (25%)**, **Usable Context (20%)**, and **Memory Headroom (15%)**.

### 4. Download Size vs Total Storage
- **Download Size**: How much data must be transferred across your network.
- **Total Storage**: May also include supporting software, temporary files, and recovery copies.

### 5. Context Window & Memory Headroom
- **Context**: How much text the model can consider at once. Larger settings require additional memory.
- **Memory Headroom**: Memory left available for your operating system and other apps while the model runs.

### 6. Compact Model Versions (Quantization)
- These versions reduce memory use through quantization (e.g. Q4_K_M). They can also subtly change output quality or performance.

### 7. Stop vs Remove
- **Stop**: Unloads or stops the model through its runtime. Memory is freed, but downloaded files are kept.
- **Remove**: Removes the installation and deletes approved files from disk to reclaim storage.

### 8. What Happens When You Close LocalPilot
- Direct workers started by LocalPilot are stopped cleanly to prevent orphaned processes.
- External services (like an existing Ollama service) are left untouched.
""",
    },
    "terminology": {
        "title": "Preferred Terminology Guide",
        "audience": "All Users",
        "summary": "Clear, friendly definitions for local AI terms.",
        "content": """
# LocalPilot Terminology Guide

- **Model**: The AI you download and use on your computer.
- **Supporting software**: The runtime that runs the model (e.g. llama.cpp, Ollama).
- **Context**: How much text the model can consider at once.
- **Graphics memory**: Memory on your graphics card (VRAM).
- **Compact model version**: A version designed to use less memory.
- **Running model**: A model currently loaded or active according to observed state.
- **Access key**: Permission to access a source repository (never an account password).
""",
    },
    "technical": {
        "title": "Technical Architecture & Design Deep Dive",
        "audience": "Technical Users & Developers",
        "summary": "3-Axis execution model, memory accounting math, process ownership, and security.",
        "content": """
# LocalPilot Technical Architecture

LocalPilot is engineered as a decoupled, robust local LLM control plane adhering to clean architectural boundaries.

## 1. The 3-Axis Execution Architecture
Model execution is partitioned into three independent, orthogonal dimensions:
- **Inference Backends**:
  - `llama.cpp`: High-performance GGUF runtime supporting Apple Metal, NVIDIA CUDA, Vulkan, and AVX2/AVX-512 CPU execution.
  - `vLLM`: Advanced Linux+GPU backend utilizing PagedAttention for high-throughput batching.
- **Managed Execution Services**:
  - `ollama`: Discovered or spawned service via local HTTP REST API (`/api/pull`, `/api/generate`, `/api/tags`).
  - `lm_studio` / `llmster`: Discovered local service via OpenAI-compatible endpoints.
  - `docker_model_runner`: Container-managed inference isolated from host system.
- **Deployment Methods**:
  - `direct_worker`: Child subprocess communicating via stdio or UNIX domain sockets.
  - `local_api_service`: Localhost HTTP/REST service bound strictly to loopback (`127.0.0.1`).
  - `container`: OCI container runtime deployment.

## 2. Memory Accounting & Offloading Math
- **Unified Memory (e.g. Apple Silicon)**: Single memory pool shared between CPU and GPU. Safe budget equals available RAM minus OS safety reserve (512 MB).
- **Separate Memory (Linux x86_64 / Discrete GPU)**:
  - RAM Budget: `safe_ram_budget = max(0, available_ram - 512MB)`
  - VRAM Budget: `safe_vram_budget = max(0, available_vram - 256MB)`
- **Partial CPU+GPU Offload (llama.cpp)**:
  - If model weights exceed VRAM budget, layers are partitioned:
    `offload_ratio = min(1.0, max(0.0, (safe_vram - overhead) / total_weight_bytes))`
  - The remaining non-offloaded weights + KV cache + buffers MUST fit inside `safe_ram_budget`.
- **KV-Cache Dynamic Scaling**:
  `kv_cache_bytes = context_tokens * 512 * max(0.2, (params_b / 7.0)) * concurrency`

## 3. Process Identity & Ownership Enforcement
- Running instances are tracked with explicit ownership classes:
  `LOCALPILOT_OWNED`, `EXTERNAL_APPROVED`, `READONLY_DISCOVERED`, `UNKNOWN`.
- Process identity tuple: `(PID, create_time, executable_path, endpoint_url)`.
- When stopping instances:
  - LocalPilot calls `unload_model()` (`POST /api/generate` with `keep_alive: 0` for Ollama) to release VRAM without terminating shared external services.
  - Process SIGTERM/SIGKILL is ONLY permitted if `ownership_class == LOCALPILOT_OWNED` and PID + creation time match exactly to prevent PID reuse hazards.

## 4. SQLite State & Observability
- All state resides in `~/.localpilot/state.db` with WAL mode and foreign key constraints:
  - `installations`: Registered models, readiness stages, execution plans.
  - `storage_artifacts`: Reference-counted files, byte sizes, SHA-256 hashes, deletion methods.
  - `running_instances`: Live process IDs, endpoints, observed execution modes.
  - `jobs`: Workflow stages, progress checkpoints, failure states.
  - `audit_decisions`: Immutable append-only audit trail required before destructive actions.
  - `activity_log`: Redacted timeline of user operations.
- Secret tokens (`hf_*`, `Bearer *`, API keys, passwords, home paths) are automatically redacted prior to database write or JSONL logging.
""",
    },
    "faq": {
        "title": "Frequently Asked Questions (FAQ)",
        "audience": "Both Novice and Technical Users",
        "summary": "Answers to common operational, security, and hardware questions.",
        "content": """
# Frequently Asked Questions

### Q: Why does LocalPilot say a model is 'Incompatible'?
**A:** LocalPilot evaluates whether your physical RAM or GPU VRAM can safely load the model weights plus working buffers and context window. If loading the model would cause your OS to freeze, crash, or swap thrash to disk, LocalPilot marks it incompatible to protect your system.

### Q: Does LocalPilot send any data or prompts over the internet?
**A:** Absolutely not. All downloads are fetched directly from official public model repositories (Hugging Face / Ollama Library). All inference happens strictly on your local CPU/GPU via loopback IP (`127.0.0.1`). There are no third-party telemetry, tracking, or cloud APIs.

### Q: Can I run models without an expensive graphics card?
**A:** Yes! LocalPilot natively supports CPU execution using high-efficiency quantization (GGUF Q4_K_M). Modern multi-core CPUs run 1B to 3B parameter models quickly and smoothly.

### Q: What is the difference between Ollama and llama.cpp in LocalPilot?
**A:** `llama.cpp` is a direct, embedded inference engine with granular control over CPU threads, Metal/CUDA layers, and context size. `Ollama` is a friendly service daemon that manages its own model library. LocalPilot supports both seamlessly and never conflicts with an existing Ollama install.

### Q: How do I update LocalPilot itself?
**A:** If installed via pipx: `pipx upgrade localpilot`. If installed via pip: `pip install -U localpilot`. You can also check status anytime with `localpilot self update`.

### Q: Where are my downloaded models stored?
**A:** By default, in `~/.localpilot/models/`. You can see the exact breakdown anytime by running `localpilot storage`.
""",
    },
    "commands": {
        "title": "CLI Commands & Options Reference",
        "audience": "Both Novice and Technical Users",
        "summary": "Comprehensive reference for every command and flag.",
        "content": """
# LocalPilot Command Reference

- `localpilot tui`
  Launch the full-screen interactive dashboard. Ideal for visual discovery, hardware monitoring, and one-click setup.

- `localpilot doctor [--plain] [--json]`
  Inspect hardware: CPU cores, RAM available/safe budget, GPU VRAM, memory model (unified vs separate), and installed runtimes.

- `localpilot discover [--task chat|coding] [-q query] [--plain] [--json]`
  Browse verified models scored specifically for your hardware. Only displays models guaranteed to fit.

- `localpilot setup [candidate_id] [--scope isolated|user|system] [-y]`
  Download and prepare a model with real weights, verify live inference, and register it in local inventory.

- `localpilot run [installation_id | name]`
  Start an installed model instance in the background and establish a local endpoint.

- `localpilot chat [installation_id | name]`
  Open an interactive terminal chat session with an installed or running model.

- `localpilot stop [instance_id | name] [--force]`
  Safely unload model memory without terminating shared external background daemons.

- `localpilot update <installation_id> [-y]`
  Check upstream repositories for newer revisions and safely apply updates with rollback protection.

- `localpilot remove [installation_id | name] [--delete-files|--keep-files] [-y]`
  Remove an installation and safely delete physical weights if no other model references them.

- `localpilot storage [--plain] [--json]`
  Display storage breakdown across models, supporting runtimes, downloads, and temporary files.

- `localpilot activity [-n limit] [--plain] [--json]`
  View friendly activity timeline of recent actions and decisions.

- `localpilot logs [--level debug|info|warning|error] [-n limit]`
  Inspect structured diagnostic event logs.

- `localpilot support [-o output_path]`
  Export an audit-reviewed, fully redacted diagnostic bundle for troubleshooting.

- `localpilot guide [topic]`
  Display comprehensive documentation and step-by-step guidance.
""",
    },
}


def get_documentation_markdown(topic: Optional[str] = None) -> str:
    """Returns formatted markdown documentation for a given topic or all topics."""
    if not topic or topic.lower() in ("all", "index", "help"):
        sections = []
        for key, data in DOCUMENTATION_TOPICS.items():
            sections.append(data["content"].strip())
        return "\n\n---\n\n".join(sections)

    key = topic.lower().strip()
    if key in DOCUMENTATION_TOPICS:
        return DOCUMENTATION_TOPICS[key]["content"].strip()

    # Partial search
    for k, data in DOCUMENTATION_TOPICS.items():
        if key in k or key in data["title"].lower():
            return data["content"].strip()

    valid_topics = ", ".join(DOCUMENTATION_TOPICS.keys())
    return f"# Topic '{topic}' Not Found\n\nAvailable documentation topics:\n- **{valid_topics}**\n\nRun `localpilot guide <topic>` to view."
