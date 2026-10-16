# LocalPilot 3-Axis Platform, Backend & Service Support Matrix

This document defines the supported platforms, inference backends, managed execution services, deployment methods, and model sources for LocalPilot.

---

## 1. Operating Systems & Hardware Architectures

| Platform | Architecture | Status | Memory Model | Acceleration |
|---|---|---|---|---|
| **Linux** | x86-64 | Supported release target | Separate (System RAM + VRAM) | CPU baseline, NVIDIA CUDA, AMD ROCm |
| **Linux** | ARM64 | Supported release target | Separate / Unified (board-dependent) | CPU baseline, NVIDIA CUDA (Jetson/Tegra) |
| **macOS** | Apple Silicon (M1/M2/M3/M4) | Supported release target | Unified Memory Model | CPU baseline, Apple Metal GPU |
| **macOS** | Intel (x86-64) | Supported release target | Separate Memory Model | CPU baseline; discrete AMD GPUs verified separately |
| **Other Unix** | BSD / Solaris | Experimental | Platform-dependent | CPU only (no execution guarantees) |

---

## 2. The Three Orthogonal Execution Dimensions

LocalPilot cleanly decouples how models run into three independent dimensions:

```text
                  ┌───────────────────────────────┐
                  │       Execution Plan          │
                  └──────────────┬────────────────┘
                                 │
         ┌───────────────────────┼────────────────────────┐
         │                       │                        │
         ▼                       ▼                        ▼
┌──────────────────┐   ┌───────────────────┐    ┌───────────────────┐
│ Inference Backend│   │  Managed Service  │    │ Deployment Method │
├──────────────────┤   ├───────────────────┤    ├───────────────────┤
│ • llama.cpp      │   │ • None            │    │ • Direct Worker   │
│ • vLLM (Advanced)│   │ • Ollama          │    │ • Local Service   │
│ • mlx-lm (Plan)  │   │ • LM Studio       │    │ • Container       │
│ • Service-Managed│   │   (Desktop/llmster│    │ • Browser (Plan)  │
│                  │   │    /lms CLI)      │    │                   │
│                  │   │ • Docker Model    │    │                   │
│                  │   │   Runner          │    │                   │
└──────────────────┘   └───────────────────┘    └───────────────────┘
```

### A. Inference Backends (Computation Engine)

| Backend ID | Supported Platforms | Supported Formats | Hardware Requirements | Advanced Only | Implementation Status |
|---|---|---|---|---|---|
| `llama.cpp` | Linux (x86_64, ARM64), macOS (Apple Silicon, Intel) | GGUF | CPU, Apple Metal, NVIDIA CUDA, ROCm | No (Default) | Implemented & Tested |
| `vllm` | Linux (x86_64) | Safetensors, HF Checkpoints | NVIDIA CUDA or AMD ROCm GPU (Unsupported on macOS) | Yes | Implemented & Tested |
| `mlx-lm` | macOS (Apple Silicon) | MLX checkpoints | Apple Silicon GPU/Unified Memory | No | Planned |
| `transformers`| Linux, macOS | PyTorch / SafeTensors | High RAM / VRAM | Yes | Planned |
| `unknown/service-managed`| Any supported by managed service | Service-managed | Managed by daemon | No | Implemented & Tested |

### B. Managed Execution Services (Model & Daemon Lifecycle)

| Service ID | Components Detected | Discovery Port / Probe | Model Unload Mechanism | Stop Daemon Policy | Implementation Status |
|---|---|---|---|---|---|
| `none` | N/A | N/A | Process exit | N/A | Implemented & Tested |
| `ollama` | `ollama` CLI, daemon | `http://127.0.0.1:11434` (`/api/tags`) | `POST /api/generate` (`keep_alive=0`) | Protects external daemon; only stops LocalPilot-owned instances | Implemented & Tested |
| `lm_studio` | LM Studio Desktop, `llmster` daemon, `lms` CLI | `http://127.0.0.1:1234` (`/v1/models`) | `POST /v1/models/unload` | Protected external daemon (never killed automatically) | Implemented & Tested |
| `docker_model_runner` | `docker` CLI, `docker model` plugin | `http://127.0.0.1:11434` / configured | Service unload endpoint | Protected external daemon (never mutates docker groups/daemon) | Implemented & Tested |

> [!NOTE]
> `llmster` is the headless daemon of LM Studio, and `lms` is its CLI. They are unified under `ManagedServiceId.LM_STUDIO` to prevent duplicate provider listings.

### C. Deployment Methods (Hosting & Control)

| Deployment Method | Execution Mechanism | Isolation / IPC | Crash Recovery | Status |
|---|---|---|---|---|
| `direct_worker` | Isolated child subprocess | JSON-lines over stdin/stdout pipes | Handled by `DirectWorkerController` without taking down UI | Implemented & Tested |
| `local_api_service` | Localhost HTTP server (`127.0.0.1`) | HTTP REST / SSE | Reconnection & health check | Implemented & Tested |
| `container` | Isolated container execution | Host port forwarding | Container status monitoring without assuming host PID | Implemented & Tested |
| `browser` | In-browser WebAssembly execution | Web worker message passing | Tab / worker recovery | Extension point (Planned) |

---

## 3. Model Source Adapters

| Source | Model Format | Weights Download | Identity / Digest | Integrity Check |
|---|---|---|---|---|
| **Hugging Face Hub** | GGUF | HTTP Range Resume | Commit SHA | SHA256 |
| **Ollama Library** | Ollama Blob / Manifest | Managed pull | Manifest Digest | SHA256 Digest |
| **Local Import** | GGUF | Direct read | File content hash | SHA256 |

---

## 4. Multi-Scope Installation

When software or runtimes are installed, LocalPilot supports three distinct scopes:
1. **Isolated (`isolated`, default)**: `~/.local/share/localpilot/runtimes/<name>/`. Zero system side-effects, no root elevation.
2. **User Space (`user`)**: `~/.local/bin/` and `~/.local/lib/localpilot/`. Available in user shell; no root elevation.
3. **System Wide (`system`)**: `/usr/local/bin/`. Requires explicit user consent and elevation (`sudo`).
