<div align="center">

<img src="docs/img/banner-readme.jpeg" alt="LocalPilot Banner" width="100%" />

# LocalPilot

**Local AI, Made Simple.**

[![PyPI Version](https://img.shields.io/pypi/v/localpilot.svg?style=flat-square&color=blue)](https://pypi.org/project/localpilot/)
[![Python Versions](https://img.shields.io/pypi/pyversions/localpilot.svg?style=flat-square&color=3776AB)](https://pypi.org/project/localpilot/)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg?style=flat-square)](https://www.gnu.org/licenses/gpl-3.0)
[![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20macOS-lightgrey.svg?style=flat-square)](https://fakhruldeen.github.io/LocalPilot/)
[![Documentation](https://img.shields.io/badge/Docs-GitHub%20Pages-success.svg?style=flat-square)](https://fakhruldeen.github.io/LocalPilot/)

*Find an AI that fits your computer, set it up safely, and help you use it.*

[Website](https://fakhruldeen.github.io/LocalPilot/) • [Documentation](docs/SUPPORT_MATRIX.md) • [PyPI](https://pypi.org/project/localpilot/) • [Report Bug](https://github.com/fakhruldeen/LocalPilot/issues)

</div>

---

## 🌟 Why LocalPilot?

Running local AI shouldn't require configuring build flags, figuring out CUDA or Metal layer offloading, calculating KV-cache bytes, or manually nursing broken environment variables.

**LocalPilot** is a friendly, hardware-aware Python application that guides you from discovery to chat. It inspects your real hardware capabilities, scores models based on verified compatibility, sets up runtimes with safe defaults, and monitors resource usage in a calm, full-screen terminal dashboard or intuitive CLI.

---

## ✨ Key Features

- **🧠 Hardware-Aware Recommendation**: Evaluates CPU, RAM, GPU, Apple Silicon Unified Memory vs. discrete VRAM, and storage budgets. Filters out incompatible models before you waste bandwidth.
- **🧭 3-Axis Execution Architecture**: Decouples model computation into three orthogonal dimensions:
  - **Inference Backends**: `llama.cpp` (GGUF, Metal, CUDA), `vLLM` (advanced Linux+GPU).
  - **Managed Execution Services**: `Ollama`, `LM Studio / llmster`, and `Docker Model Runner`.
  - **Deployment Methods**: Direct isolated worker processes (`direct_worker`), local API services (`local_api_service`), and containers.
- **🛡️ External Service & Data Protection**:
  - Respects external daemons: unloads models from memory (`keep_alive=0` / `/v1/models/unload`) without killing shared background services.
  - Reference-counted storage protects your model files and never deletes unowned files.
- **📦 Multi-Scope Installation**: Choose between **Application-Isolated** (default, no root needed), **User-Space** (`~/.local/bin`), or **System-Wide** (`/usr/local/bin`).
- **💻 Dual Interface**:
  - **Interactive TUI**: A beautiful full-screen dashboard powered by [Textual](https://textual.textualize.io/).
  - **Fast Scriptable CLI**: Clean, structured CLI commands powered by [Typer](https://typer.tiangolo.com/) and [Rich](https://rich.readthedocs.io/).
- **📝 Comprehensive Observability**: Structured event logs, audit checkpoints before destructive actions, and support bundle diagnostics.

---

## 🚀 Quick Start

### 1. Installation

#### With `pipx` (Recommended)
```bash
pipx install localpilot
```

#### With `pip` or `uv`
```bash
pip install localpilot
# or
uv tool install localpilot
```

#### Standalone Shell Installer
```bash
curl -fsSL https://raw.githubusercontent.com/fakhruldeen/LocalPilot/main/install-localpilot.sh | bash
```

---

## 🎮 Usage Guide

### Launch the Terminal Dashboard (TUI)
Simply type:
```bash
localpilot
```
Use keyboard shortcuts to navigate:
- <kbd>d</kbd> Find compatible models
- <kbd>i</kbd> View installed models
- <kbd>r</kbd> View running models & resource usage
- <kbd>s</kbd> Inspect managed storage
- <kbd>a</kbd> Activity and decision history
- <kbd>q</kbd> Exit

---

### Command-Line Interface (CLI)

#### 1. Inspect Your Computer's Hardware
```bash
localpilot doctor
```
Outputs CPU cores, system RAM, safe memory budget, GPU accelerators (CUDA/Metal), and discovered runtimes.

#### 2. Discover Compatible Models
```bash
localpilot discover
```
Filter by task or search query:
```bash
localpilot discover --task coding
localpilot discover -q "llama"
```

#### 3. Guided Model Setup
```bash
localpilot setup <candidate-id>
```
LocalPilot checks whether supporting software (such as Ollama or llama.cpp) is already present. If found, it calmly uses it:
> *"LocalPilot found supporting software already installed. We can use it to run this model."*

#### 4. Run & Chat
```bash
localpilot run <model-id-or-install-id>
```

#### 5. Monitor Running Models
```bash
localpilot running
```

#### 6. Storage Inspection & Safe Removal
```bash
# View storage breakdown
localpilot storage

# Safely remove a model without affecting other installations
localpilot remove <installation-id>
```

---

## 📐 Architecture Overview

```text
Application Services (CLI / TUI / Setup / Chat)
                    │
                    ▼
          ┌───────────────────┐
          │   ExecutionPlan   │
          └─────────┬─────────┘
                    │
   ┌────────────────┼────────────────┐
   ▼                ▼                ▼
Backend          Service         Deployment
llama.cpp        Ollama          direct_worker
vLLM             LM Studio       local_api_service
                 Docker Runner   container
```

For full details on platform compatibility and supported backend/service combinations, see the **[Support Matrix](docs/SUPPORT_MATRIX.md)**.

---

## 👤 Author & Maintainer

**Mohamed Fouad Fakhruldeen**  
*Creator & Maintainer of LocalPilot. Working across PMO governance, AI systems, and agile enterprise operating systems.*

- 🌐 **Website**: [fakhr.me](https://fakhr.me/)
- 💼 **LinkedIn**: [linkedin.com/in/fakhruldeen](https://www.linkedin.com/in/fakhruldeen)
- 🐙 **GitHub**: [@fakhruldeen](https://github.com/fakhruldeen)
- 📬 **Email**: fakhruldeen@gmail.com

---

## 📄 License

This project is licensed under the terms of the **GNU General Public License v3 (GPLv3)**. See the [LICENSE](LICENSE) file for details.
