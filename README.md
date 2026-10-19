<div align="center">

<img src="https://raw.githubusercontent.com/fakhruldeen/LocalPilot/main/docs/img/banner-readme.jpeg" alt="LocalPilot Banner" width="100%" />

<br/><br/>

<img src="https://raw.githubusercontent.com/fakhruldeen/LocalPilot/main/docs/img/logo.png" alt="LocalPilot Logo" width="120" />

# LocalPilot

**Run AI on your own computer.**

**[English](README.md)** • **[العربية (Arabic)](README.ar.md)**

[![PyPI Version](https://img.shields.io/pypi/v/localpilot.svg?style=flat-square&color=blue)](https://pypi.org/project/localpilot/)
[![Python Versions](https://img.shields.io/pypi/pyversions/localpilot.svg?style=flat-square&color=3776AB)](https://pypi.org/project/localpilot/)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg?style=flat-square)](https://www.gnu.org/licenses/gpl-3.0)
[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23424540.svg)](https://doi.org/10.5281/zenodo.23424540)
[![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20macOS-lightgrey.svg?style=flat-square)](https://fakhr.me/LocalPilot/)
[![Documentation](https://img.shields.io/badge/Docs-GitHub%20Pages-success.svg?style=flat-square)](https://fakhr.me/LocalPilot/)

*LocalPilot checks your computer, helps you choose a suitable AI model, and guides you through setup.*

[Website](https://fakhr.me/LocalPilot/) • [Beginner Guide](https://fakhr.me/LocalPilot/guide.html) • [Installation](https://fakhr.me/LocalPilot/install.html) • [Model Catalog](https://fakhr.me/LocalPilot/models.html) • [FAQ](https://fakhr.me/LocalPilot/faq.html) • [التوثيق بالعربية](docs/ar/README.md) • [PyPI](https://pypi.org/project/localpilot/)

</div>

---

## 💡 What Can LocalPilot Help You Do?

LocalPilot lets you download and run artificial intelligence models directly on your laptop or desktop computer.

You can use it to:
* **Draft and edit writing**: Write emails, outline essays, polish documents, and rephrase text.
* **Ask everyday questions**: Summarize articles, explain concepts in simple terms, and brainstorm ideas.
* **Get programming help**: Explain unfamiliar code, write helper functions, and troubleshoot errors.
* **Keep conversations on your machine**: Chat through an offline terminal window where your words are processed locally on your computer rather than sent to cloud servers.

---

## 👥 Who Is It For?

* **Everyday users & beginners**: You do not need an engineering degree or prior AI experience. LocalPilot handles memory calculations and configuration for you.
* **Privacy-conscious writers & researchers**: Keep your personal notes, drafts, and sensitive documents on your own computer.
* **Developers & students**: Experiment with modern open-weights models (like Llama, Qwen, Gemma, and Mistral) with reliable local API access.

---

## 💻 What Do You Need?

* **Operating System**: **Linux** (Ubuntu, Debian, Fedora, Arch, etc.) or **macOS** (Apple Silicon M1–M4 or Intel). Windows is supported through Windows Subsystem for Linux (WSL2).
* **Terminal**: A standard command-line terminal window where you can paste commands.
* **Internet Connection**: Needed to download LocalPilot, download your chosen model, and update model lists. (Once a model is downloaded, chat runs locally without internet).
* **Memory & Storage**:
  * **8 GB of RAM** and **10 GB of free disk space** for lightweight models (1.5B to 3B parameters).
  * **16 GB of RAM** or more for standard models (7B to 8B parameters).
* **Python**: Python 3.11 or higher is required and is checked automatically during installation.

---

## 🚀 Get Started

### Recommended Installation (Automated Installer)

Open your terminal window and paste this single command:

```bash
curl -fsSL https://raw.githubusercontent.com/fakhruldeen/LocalPilot/main/install-localpilot.sh | bash
```

**What this command does:**
1. Checks that your operating system and Python version are compatible.
2. Installs LocalPilot into an isolated application folder (`~/.local/share/localpilot` on Linux, `~/Library/Application Support/LocalPilot` on macOS).
3. Leaves your existing system files untouched and never asks for administrator (`sudo` or root) passwords.

---

### Alternative Installation Methods (For Python Developers)

If you manage Python command-line tools with `pipx`, `pip`, or `uv`:

```bash
# Using pipx (recommended for Python CLI users)
pipx install localpilot

# Using pip in your active environment
pip install localpilot

# Using uv
uv tool install localpilot
```

👉 *For custom scopes, offline bundles, and uninstallation instructions, see the **[Installation Guide](docs/INSTALLATION.md)**.*

---

## 🖥️ What Happens After Launch?

Start LocalPilot by typing:

```bash
localpilot
```

This opens a friendly, full-screen dashboard inside your terminal. If this is your first time, the guided setup wizard walks you through:

1. **Computer Capability Check**: LocalPilot measures your processor, memory, and graphics hardware, reserving a safe buffer so your operating system and web browser stay smooth.
2. **Choose What You Want Help With**: Pick what you want your model to do best—*Everyday questions and writing*, *Coding assistance*, or *Browse all compatible models*.
3. **Recommended Models**: LocalPilot shows models that are tested to fit within your computer's safe memory budget.
4. **Review & Approve**: You see the exact model name, download size in gigabytes, and storage location. Nothing downloads until you select **Set Up Model**.
5. **Setup & Verification**: LocalPilot downloads the weights file, prepares the supporting software, and runs a quick test question to confirm that everything works properly.
6. **Start Chatting**: Select **Start Chatting** to begin your first conversation immediately.

---

## 💬 Everyday Actions

Once set up, you can manage your local AI using simple terminal commands:

| Action | Command | What it does |
| :--- | :--- | :--- |
| **Open Visual Dashboard** | `localpilot` | Launches the full-screen interactive terminal dashboard with dedicated tabs for models, tools, search, benchmarks, and health. |
| **Start Chatting** | `localpilot chat` | Opens an interactive conversation with your installed model. Type `/exit` to leave. |
| **Run Offline AI Tools** | `localpilot tools <task>` | Run 18 focused task tools: `summarize`, `rewrite`, `translate`, `explain-code`, `extract`, `classify`, `review-table`, `describe-image`, `read-image`, and more. |
| **Search Local Documents** | `localpilot search ask "q"` | Offline RAG: Index local folders and get grounded answers with precise file and line citations. |
| **Voice & Speech Chat** | `localpilot voice chat` | Conversational voice turn loop with offline speech-to-text transcription and text-to-speech reading. |
| **Measure & Benchmark** | `localpilot bench run` | Test streaming performance (TTFT, tokens/sec) and compare models side-by-side with `bench compare`. |
| **Health & Self-Repair** | `localpilot doctor --repair` | Inspect system health, detect misconfigurations, and apply safe, reviewed repairs. |
| **Connect External Apps** | `localpilot connect` | Interactive wizard to configure VS Code (Continue), OpenCode, Cursor, and custom tools. Manage with `localpilot connections`. |
| **Stop Model (Free RAM)** | `localpilot stop` | Unloads the model from memory so all RAM and graphics memory return to other apps. |
| **View Installed Models** | `localpilot installed` | Lists all models currently downloaded on your computer. |
| **Find Compatible Models** | `localpilot discover` | Scans the catalog for models that fit your memory budget. |
| **Remove a Model** | `localpilot remove <name>` | Deletes model weight files from your hard drive to free disk space. |
| **Star a Favorite** | `localpilot favorite add <alias>` | Adds a model to your favorites list for quick access. |

> [!TIP]
> **Leaving Chat vs. Stopping a Model**:
> * Typing `/exit` leaves the chat screen but keeps the model loaded in memory so you can return to it instantly.
> * Running `localpilot stop` completely unloads the model from memory, freeing up your RAM for games, browsers, or heavy work.

---

## 🔒 Privacy, Internet Access & Limitations

* **Local Inference**: Supported conversations are processed entirely on your computer through a local loopback address (`127.0.0.1`). Your messages and files are not uploaded to cloud servers.
* **Internet Requirements**: An internet connection is used during installation, when downloading a model for the first time, and when updating the model catalog. Once a model is saved locally, chat runs without an active internet connection.
* **Accuracy Notice**: Like all artificial intelligence systems, local models can make mistakes, produce inaccurate answers, or provide flawed code. Always double-check important facts and test code before using it.
* **Hardware Boundaries**: Local models share your computer's memory. Running models that exceed your available RAM can make your desktop sluggish. LocalPilot checks your memory first to recommend models that run smoothly.

---

## 🔌 Optional: Connect Other Applications

LocalPilot provides a standard, local OpenAI-compatible API endpoint (`http://127.0.0.1:8080/v1`).

You can connect popular coding assistants and tools:
* **VS Code (Continue extension)**: Connect your local model to chat with your codebase.
* **OpenCode**: Automated configuration generator for local coding tasks.
* **Cursor & Custom Tools**: Point any OpenAI-compatible client to your local endpoint.

Run `localpilot connect` to view guided instructions for each application.  
*(Note: Connected third-party applications have their own privacy settings and terms).*

---

## 🛠️ Help & Troubleshooting

* **Download was interrupted**: Run `localpilot` again. The installer checks what was already downloaded and resumes safely.
* **Computer is running slowly**: Run `localpilot stop` to unload the model from memory, or choose a smaller model with `localpilot discover`.
* **Command not found (`localpilot: command not found`)**: Make sure `~/.local/bin` is in your terminal's search path. Add `export PATH="$PATH:$HOME/.local/bin"` to your `~/.bashrc` or `~/.zshrc`.
* **Access key requested**: Most models are open and need no accounts. A few models (such as official Llama or Gemma weights on Hugging Face) require a free read-only personal access token to confirm the publisher's terms.
* **Need diagnostic help**: Run `localpilot support` to generate a safe, redacted diagnostic report you can review and share when asking for help.

---

## 📖 Documentation & Guides

Detailed guides and technical references are available across the repository:

* **[Your First Conversation with LocalPilot](docs/FIRST_CONVERSATION.md)**: A step-by-step walkthrough for complete beginners.
* **[Documentation Hub (`docs/`)](docs/README.md)**: Master index of all guides, specifications, and architecture manuals.
* **[Conceptual Guide](docs/CONCEPTUAL_GUIDE.md)**: How local models, memory limits, and inference engines work.
* **[Installation Guide](docs/INSTALLATION.md)**: Detailed platform steps, custom scopes, and uninstallation.
* **[Hardware Support Matrix](docs/SUPPORT_MATRIX.md)**: Supported operating systems, CPUs, and GPUs.
* **[Verified Support Matrix](docs/VERIFIED_SUPPORT_MATRIX.md)**: Release verification tiers and tested configurations.
* **[Known Limitations](docs/KNOWN_LIMITATIONS.md)**: Explicit boundaries, memory considerations, and workarounds.
* **[التوثيق باللغة العربية](docs/ar/README.md)**: الدليل الكامل ومترجم بالكامل للعربية.
* **[Audit & Assurance Trail (`audit/`)](audit/README.md)**: Independent testing logs, security scans, and release readiness audits.

---

## 👤 Author & Maintainer

**Mohamed Fouad Fakhruldeen**  
*Creator & Maintainer of LocalPilot. Working across PMO governance, AI systems, and agile enterprise operating systems.*

- 🌐 **Website**: [fakhr.me](https://fakhr.me/)
- 💼 **LinkedIn**: [linkedin.com/in/fakhruldeen](https://www.linkedin.com/in/fakhruldeen)
- 🐙 **GitHub**: [@fakhruldeen](https://github.com/fakhruldeen)
- 📬 **Email**: fakhruldeen@gmail.com

---

## ⚠️ Disclaimer

> [!WARNING]
> **Personal Use & Testing**: LocalPilot is designed for personal evaluation, learning, and hobbyist exploration. Running local artificial intelligence models uses significant computer memory and processor resources.
>
> This software is provided strictly on an **"AS-IS"** basis. It is **not** certified for safety-critical systems, commercial infrastructure, or emergency services. The author assumes no liability for system instability, data loss, or issues resulting from running local models. Always back up important files and review model responses.

---

## 📚 Citation

If you use LocalPilot in your research, academic study, or open-source projects, please cite it as:

```bibtex
@software{fakhruldeen_2026_localpilot,
  author       = {Mohamed (Fouad) Fakhruldeen},
  title        = {fakhruldeen/LocalPilot: v0.2.5},
  month        = oct,
  year         = 2026,
  publisher    = {Zenodo},
  version      = {v0.2.5},
  doi          = {10.5281/zenodo.23424540},
  url          = {https://doi.org/10.5281/zenodo.23424540}
}
```

---

## 📄 License

This project is licensed under the terms of the **GNU General Public License v3 (GPLv3)**. See the [LICENSE](LICENSE) file for details.
