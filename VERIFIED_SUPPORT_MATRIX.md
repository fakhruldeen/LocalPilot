# LocalPilot v0.1.9 Verified Support Matrix

**Version**: 0.1.9  
**Verification Date**: October 18, 2026  
**Status**: Authoritative Release Specification  

---

## 1. Operating Systems & Architecture

| Operating System | Architecture | Support Classification | Test & Runtime Evidence | Constraints & Notes |
| :--- | :--- | :--- | :--- | :--- |
| **Linux (Kernel 5.15+)** | x86_64 | **Supported & Verified** | Verified on Linux 6.8 (Ubuntu 22.04 / 24.04). Systemd user service support verified. | Loopback network interface (`127.0.0.1`) required. Unprivileged user space only (no root). |
| **macOS (13 Ventura, 14 Sonoma, 15 Sequoia)** | ARM64 (Apple Silicon: M1, M2, M3, M4) | **Supported & Verified** | Tested via platform adapter test suite. Unified memory detection and Metal acceleration profiles verified. Launchd user agent verified. | Intel x86_64 Macs supported via CPU fallback mode only. |
| **Linux (Kernel 5.15+)** | aarch64 (ARM64) | **Tier 2 (Compatible)** | Core engine and Python dependencies compile cleanly. Standard CPU inference verified. | Dedicated GPU offloading depends on local ROCm or custom Vulkan driver setup. |
| **Microsoft Windows** | x86_64 (via WSL2) | **Tier 2 (Compatible)** | Fully operational inside WSL2 Linux environments using Linux systemd or CLI execution. | Windows native services / registry-based autorun are not yet implemented (scheduled post-v0.1.9). |
| **32-bit Operating Systems** | x86 / arm32 | **Explicitly Unsupported** | LLM weights require 64-bit addressing space. | Execution intentionally refused during hardware pre-flight. |

---

## 2. Supported Inference Runtimes & Backends

| Runtime | Execution Mode | Classification | Supported Model Artifacts | Evidence & Safeguards |
| :--- | :--- | :--- | :--- | :--- |
| **llama.cpp (Direct Local Engine)** | Child process or local API | **Supported & Verified (Primary)** | GGUF (Q4_K_M, Q5_K_M, Q8_0, etc.) | Standard inference backend. Process lifetime owned by LocalPilot, PIDs tracked with creation timestamps, graceful SIGINT/SIGTERM handling, memory fully returned to OS on shutdown. |
| **Ollama (External Service)** | External daemon integration | **Experimental** | Ollama local manifests | Unmanaged service lifecycle. LocalPilot connects via loopback REST API (`http://127.0.0.1:11434`), records activity without terminating the shared daemon process on model stop. |
| **vLLM / Container Engines** | Multi-GPU Docker | **Planned (Post-v0.1.9)** | Safetensors / AWQ | Architecture hooks reserved; not part of the v0.1.9 verification baseline. |

---

## 3. Supported Developer Applications & Integrations

| Application | Integration Mechanism | Configuration Target | Masking & Atomic Write | Verification Status |
| :--- | :--- | :--- | :--- | :--- |
| **OpenCode** | Automated configuration injector | `~/.config/opencode/config.json` | Full diff preview; `sk-local-••••••••` masking; `0600` permissions; non-destructive restore. | **Supported & Verified** |
| **VS Code (Continue Extension)** | Automated configuration injector | `~/.continue/config.json` | Full diff preview; `sk-local-••••••••` masking; `0600` permissions; non-destructive restore. | **Supported & Verified** |
| **Cursor** | Manual configuration guide & snippets | UI Settings (`http://127.0.0.1:8080/v1`) | Step-by-step instructions with key generator and model alias mapping. | **Supported & Verified** |
| **Generic OpenAI Clients** | HTTP / REST (`POST /v1/chat/completions`) | `http://127.0.0.1:8080/v1` | Bearer token authentication; standard OpenAI JSON schema; streaming SSE. | **Supported & Verified** |

---

## 4. Hardware Sizing & Memory Fit Tiers

| Hardware Tier | Memory Budget | Recommended Models | Expected Performance | Offload Strategy |
| :--- | :--- | :--- | :--- | :--- |
| **Starter (8 GB RAM)** | ≤ 5.5 GB allocated | Qwen 2.5 1.5B (Q4_K_M)<br/>SmolLM2 1.7B (Q4_K_M) | 25–45 tokens/sec | CPU + RAM with 2.5 GB reserved for OS and browser. |
| **Standard (16 GB RAM)** | ≤ 11.5 GB allocated | Llama 3.2 3B (Q4_K_M)<br/>Qwen 2.5 7B (Q4_K_M) | 18–35 tokens/sec (Apple Silicon)<br/>8–15 tokens/sec (CPU) | Metal acceleration on Apple Silicon; partial GPU layers on discrete NVIDIA (CUDA). |
| **Power User (32+ GB RAM)** | ≤ 24.0 GB allocated | Llama 3.3 8B (Q8_0)<br/>Qwen 2.5 14B (Q4_K_M)<br/>DeepSeek R1 Distill 14B | 20–40 tokens/sec (Metal / CUDA) | Full GPU offload when VRAM permits; hybrid offload with pinned version cache. |

---

## 5. Security & Isolation Boundaries

- **Network Scope**: Exclusively bound to loopback interface `127.0.0.1`. Remote binding (`0.0.0.0`) and public reverse tunnels are strictly prevented by design.
- **Privilege Model**: Zero administrative privilege escalation. LocalPilot never requests `sudo` or root permissions.
- **Startup Automation**: Handled strictly via user-level init services (`~/.config/systemd/user/` on Linux; `~/Library/LaunchAgents/` on macOS).
- **Telemetry Policy**: 100% offline. Zero remote telemetry, zero usage tracking upload, zero background phone-home.
