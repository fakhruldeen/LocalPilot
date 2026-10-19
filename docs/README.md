# LocalPilot Documentation Hub

Welcome to the **LocalPilot** documentation hub. This directory organizes all user guides, hardware compatibility specifications, and technical architecture references.

---

## 🟢 Beginner Documentation (Start Here)

Designed for users who want to run AI models on their computer without prior machine learning or systems engineering experience:

1. **[Quick Overview & Purpose](../README.md)**: What LocalPilot does, what you can do with it, and who it is for.
2. **[Your First Conversation Guide](FIRST_CONVERSATION.md)**: Step-by-step walkthrough covering terminal opening, installation, first-run wizard, first prompt, and everyday commands.
3. **[Interactive Website Guide (guide.html)](guide.html)**: Visual, jargon-free guide with plain-English AI glossary and example prompts.
4. **[Choosing Models & Hardware Fit (models.html)](models.html)**: How memory budgeting works and how to pick models suited for your RAM.
5. **[Stopping & Removing Models](FIRST_CONVERSATION.md#section-11--finishing-or-stopping)**: Understanding the difference between leaving chat, stopping a model to free RAM, and removing weights to free disk space.
6. **[Privacy & Internet Access](FIRST_CONVERSATION.md#section-1--before-you-begin)**: Clear boundary explanations: internet for setup/downloads, private local loopback for inference.
7. **[Common Problems & FAQ (faq.html)](faq.html)**: Searchable answers to connection, download, memory, and permissions questions.

---

## 🔵 Advanced & Developer Documentation

For system administrators, software developers, and users configuring automated local pipelines:

1. **[Conceptual Guide & Memory Architecture](CONCEPTUAL_GUIDE.md)**: Deep dive into LocalPilot's 3-axis execution model, memory budgeting formulas, inference runtimes (llama.cpp and Ollama), and local loopback isolation.
2. **[Advanced Installation Alternatives](INSTALLATION.md)**: Scope management (isolated, user, system-wide), custom installation prefixes, `pipx`, `uv`, and manual uninstallation.
3. **[Hardware Support Matrix](SUPPORT_MATRIX.md)**: Operating systems, architectures (x86_64, ARM64), Apple Silicon (M1–M4), NVIDIA CUDA, AMD ROCm, and Intel Arc.
4. **[Verified Support Matrix](VERIFIED_SUPPORT_MATRIX.md)**: Verified platform tiers, test evidence, runtime guarantees, and application connector specifications (OpenCode, VS Code Continue, Cursor).
5. **[Known Limitations & Operational Boundaries](KNOWN_LIMITATIONS.md)**: Explicit boundaries covering loopback interface constraints, context scaling, and unprivileged user init services.
6. **[Local API Reference (OpenAPI 3.1)](openapi.json)**: Complete OpenAPI schema for the local OpenAI-compatible inference gateway and telemetry endpoints.
7. **[Technical Architecture Manual (architecture.html)](architecture.html)**: Interactive subsystem dataflow diagram and lifecycle state machine.
8. **[Audit & Assurance Trail (`../audit/`)](../audit/README.md)**: Independent testing logs, security scans, failure recovery verification, and release readiness matrices.

---

## 🌐 التوثيق باللغة العربية (Arabic Documentation)

* **[مركز التوثيق العربي (ar/README.md)](ar/README.md)**: الفهرس الشامل والمترجم لكافة الأدلة والوثائق.
  * [محادثتك الأولى مع LocalPilot (دليل المبتدئين)](ar/FIRST_CONVERSATION.md)
  * [الدليل المفاهيمي وإدارة الذاكرة](ar/CONCEPTUAL_GUIDE.md)
  * [دليل التثبيت وخيارات الحزم](ar/INSTALLATION.md)
  * [مصفوفة دعم العتاد](ar/SUPPORT_MATRIX.md)
  * [الأسئلة الشائعة وحل المشكلات](ar/FAQ.md)
  * [معمارية النظام الفنية](ar/ARCHITECTURE.md)
