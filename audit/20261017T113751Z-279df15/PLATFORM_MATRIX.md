# LocalPilot Platform, Architecture & Packaging Matrix

**Audit Reference**: `AUDIT-20261017T113751Z-279df15`  
**Host Architecture**: `Linux 7.0.0-38-generic x86_64`  

---

## 1. Platform Support & Verification Status

| Platform | Architecture | Memory Model | GPU Acceleration | Advertised Status | Verified Test Level | Remaining Verification Gap |
|---|---|---|---|---|---|---|
| **Linux** | `x86_64` | Separate (System RAM + discrete VRAM) | NVIDIA CUDA, AMD ROCm, CPU fallback | Supported release target | **Native Test Verified** (Host environment) | Physical GPU execution unverified on this host (no CUDA hardware). |
| **Linux** | `aarch64` (ARM64) | Separate / Unified (board-specific) | NVIDIA Tegra/Jetson, CPU fallback | Supported release target | **Simulated / Unit** (CI cross-check) | Physical ARM64 device tests not performed. |
| **macOS** | Apple Silicon (`arm64`: M1-M4) | Unified Memory Model | Apple Metal GPU | Supported release target | **Simulated Unit Tests** (`test_macos.py`) | Physical macOS host verification pending. Metal shaders not executed. |
| **macOS** | Intel (`x86_64`) | Separate (RAM + AMD discrete VRAM) | Discrete AMD GPU, CPU fallback | Supported release target | **Simulated Unit Tests** (`test_macos.py`) | Physical Intel Mac hardware verification pending. |
| **Other Unix** | BSD, Solaris | Platform-dependent | CPU only | Experimental | **Untested** | No execution guarantees; nontechnical installation disabled. |

---

## 2. Platform Adaptation Rules Enforced

### Linux (`localpilot.platforms.linux`)
- **Memory Inspection**: Reads `/proc/meminfo` (`MemTotal`, `MemAvailable`). Prevents crash when `MemAvailable` is absent by falling back to `MemFree + Buffers + Cached`.
- **Hardware Cores**: Inspects `/proc/cpuinfo` for CPU flags (`avx`, `avx2`, `avx512`, `neon`).
- **GPU Probing**: Queries `nvidia-smi` via non-blocking subprocess; gracefully falls back to CPU when absent.
- **Process Management**: Uses `psutil` with PID + `create_time` tuple to prevent PID reuse vulnerabilities.

### macOS (`localpilot.platforms.macos`)
- **Unified Memory Model**: On Apple Silicon (`arm64`), RAM and VRAM share the same physical pool. Enforces that system RAM and VRAM are **never double-counted** in memory budget formulas.
- **Hardware Probing**: Uses `sysctl hw.memsize` and `system_profiler SPDisplaysDataType` for GPU identification.
- **Metal Backend**: Sets `GGML_METAL=1` and flags Metal support for `llama.cpp` plans.
- **Audit Limitation**: Tested via monkeypatched `platform.system() == "Darwin"`. Native dyld and Metal runtime calls have not been run on real macOS hardware.

---

## 3. Packaging & Installation Modes

| Scope | Target Directory | Elevation | Isolation Guarantee | Status |
|---|---|---|---|---|
| **Isolated (Default)** | `~/.local/share/localpilot/runtimes/<name>/` | None (User) | 100% self-contained; zero system pollution; clean deletion. | Verified Working |
| **User Space** | `~/.local/bin/` and `~/.local/lib/localpilot/` | None (User) | Available in user's PATH; preserves user dotfiles. | Verified Working |
| **System Wide** | `/usr/local/bin/` | Required (`sudo`) | System-level binary installation with explicit prompt. | Implemented & Gated |

---

## 4. Packaging Integrity & Prerequisites
- **Python Version Gate**: Declared `>=3.11`. Tested on Python 3.14.6.
- **Executable Entrypoint**: Declared as `console_scripts: localpilot = localpilot.cli.main:app`. Verified via `.venv/bin/localpilot`.
- **Runtime Dependencies**: `typer`, `rich`, `textual`, `httpx`, `psutil`, `pydantic`, `huggingface_hub`, `keyring`. All successfully resolved in environment.
