# LocalPilot Limitations & Operational Boundaries

This document defines the clear boundaries, hardware constraints, platform differences, and graceful fallbacks implemented across LocalPilot's expanded feature set.

## 1. Hardware & Platform Boundaries

### RAM & VRAM
- LocalPilot strictly enforces memory safety budgets:
  - If a model's estimated resident memory exceeds the system's safe free memory, LocalPilot alerts the user before runtime launch.
  - Large models (32B+ or 70B+) require appropriate hardware (e.g. 32GB+ RAM or dedicated GPUs with 16GB+ VRAM). On modest machines (e.g. 8GB-16GB RAM), LocalPilot highlights 1B-8B quantized models (Q4_K_M).

### CPU & Instruction Sets
- llama.cpp execution relies on modern CPU vector extensions (AVX, AVX2, AVX-512, NEON). On machines lacking AVX2, performance will be reduced. LocalPilot's system inspector flags missing instructions.

### Operating Systems
- **Linux**: Primary platform. All process management, port checks, and file watchers operate natively.
- **macOS**: Fully supported via Metal acceleration on Apple Silicon.
- **Windows**: Windows support utilizes native Windows paths and PowerShell/cmd-compatible command invocation. Runtimes must be installed via their Windows binaries or WSL2.

---

## 2. Optional Dependencies & Fallbacks

LocalPilot keeps its core package light and self-contained. The following features utilize optional dependencies with safe fallbacks:

| Feature | Preferred Library | Fallback Behavior When Missing |
|---|---|---|
| Document Parsing (PDF) | `pypdf` or `pymupdf` | Plain text and Markdown files are indexed normally. For PDF files, user is notified that installing `pypdf` enables PDF text extraction. |
| Dense Vector Embeddings | Local embedding model via runtime | Fast built-in BM25 / TF-IDF pure-Python lexical retriever. Ensures document search works 100% offline without downloading additional model weights. |
| Audio Input (Microphone) | `sounddevice` + `numpy` | Warns user that microphone capture requires `sounddevice` and `numpy`. Audio file transcription (`localpilot voice transcribe <file>`) remains functional if CLI tools (e.g. `whisper` / `ffmpeg`) or local endpoints are present. |
| Text-to-Speech (TTS) | `piper` / `espeak-ng` / system `say` | Falls back to printing spoken text to console if no local audio synthesis engine is found. |
| Vision & OCR | Multimodal model (e.g. `llava`, `minicpm-v`) | Checks whether the selected model supports multimodal vision inputs. If a text-only model is active, alerts the user and suggests compatible vision models. |

---

## 3. Privacy & Offline Guarantees

- **No Silent Remote Calls**: All model inference, search indexing, document parsing, and benchmarking run 100% locally on the host machine.
- **No Weight Auto-Downloads in Automated Pipelines**: LocalPilot never automatically triggers gigabyte model downloads without explicit user approval.
- **Local SQLite Storage**: All task history, benchmark runs, and search indexes are stored locally in the user's LocalPilot state directory (`~/.localpilot/`).

---

## 4. Non-Goals

- LocalPilot is **not** a cloud API proxy or SaaS platform.
- LocalPilot does **not** silently fall back to cloud providers (e.g. OpenAI, Anthropic) if local resources are insufficient; it prioritizes transparent local execution and clear diagnostic advice.
