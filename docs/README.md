# LocalPilot Documentation

Welcome to the **LocalPilot** documentation hub. This directory contains detailed architectural specifications, hardware compatibility matrices, installation guides, API references, and internationalization resources.

---

## 📚 Table of Contents

### 1. Guides & Conceptual Architecture
* **[Conceptual Guide](CONCEPTUAL_GUIDE.md)**: Deep dive into LocalPilot's architecture, memory budgeting model, runtimes (llama.cpp & Ollama), and local-first offline safety guarantees.
* **[Installation Guide](INSTALLATION.md)**: Manual and script-based installation instructions across Linux, macOS, and Windows.
* **[API Reference (OpenAPI)](openapi.json)**: Complete OpenAPI 3.1 schema specification for LocalPilot's local OpenAI-compatible inference and telemetry endpoints.

### 2. Compatibility & Hardware Matrices
* **[Hardware Support Matrix](SUPPORT_MATRIX.md)**: Operating systems, architectures (x86_64, ARM64), Apple Silicon (M1–M4), NVIDIA CUDA, AMD ROCm, and Intel Arc matrix.
* **[Verified Support Matrix](VERIFIED_SUPPORT_MATRIX.md)**: Rigorous matrix documenting verified tiers, partially supported setups, and unsupported platforms (including pure native Windows console limitations vs. WSL2).
* **[Known Limitations](KNOWN_LIMITATIONS.md)**: Known platform limits, memory budgeting constraints, sandbox restrictions, and workarounds.

### 3. Internationalization (العربية / Arabic)
* **[دليل التوثيق باللغة العربية (Arabic Docs Hub)](ar/README.md)**: الدليل الشامل والمترجم بالكامل للغة العربية.
  * [الدليل المفاهيمي (Conceptual Guide)](ar/CONCEPTUAL_GUIDE.md)
  * [دليل التثبيت (Installation)](ar/INSTALLATION.md)
  * [دليل البدء السريع (Quickstart)](ar/QUICKSTART.md)
  * [مصفوفة دعم العتاد (Support Matrix)](ar/SUPPORT_MATRIX.md)
  * [الأسئلة الشائعة (FAQ)](ar/FAQ.md)
  * [معمارية النظام (Architecture)](ar/ARCHITECTURE.md)

### 4. Interactive Documentation & Web Pages
LocalPilot provides a clean static documentation site deployed with GitHub Pages:
* **[index.html](index.html)**: Interactive landing page and feature overview.
* **[guide.html](guide.html)**: Interactive quickstart and workflow walkthrough.
* **[install.html](install.html)**: Guided interactive installation selector.
* **[models.html](models.html)**: Interactive model catalog and hardware fit calculator.
* **[architecture.html](architecture.html)**: Interactive subsystem diagram and dataflow viewer.
* **[faq.html](faq.html)**: Comprehensive searchable troubleshooting and FAQ.

---

## 🛡️ Audits & Quality Assurance
For complete test audits, failure recovery validations, performance baselines, and security reviews, refer to the [LocalPilot Audit Directory](../audit/README.md).
