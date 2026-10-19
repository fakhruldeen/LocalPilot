# Understanding Local AI: A Plain-Language Conceptual Guide

If you are new to running artificial intelligence models on your own machine, this guide answers the core questions without unnecessary technical jargon.

---

## ❓ Common Questions Answered

### 1. What actually happens when I run an AI on my computer?
When you use cloud chatbots (like ChatGPT or Claude), your computer sends your questions across the internet to massive data centers.

When you run an AI **locally with LocalPilot**:
1. You download an AI model file onto your hard drive once.
2. When you want to chat, LocalPilot loads that model into your computer's memory (RAM).
3. Supporting software on your computer processes your prompt, thinks through the patterns, and writes back the reply word by word.
4. **Nothing leaves your machine**: your prompts and replies travel only through a private connection inside your computer (`127.0.0.1`).

---

### 2. What is an "AI model file" (or "weights")?
Think of an AI model like a giant, specialized encyclopedia file. During training, researchers feed billions of documents into an algorithm to calculate mathematical patterns (called "weights").

* The resulting file (often ending in `.gguf`) contains all the knowledge and language ability of that model.
* Having the file stored on your disk does not consume memory or slow down your computer until you actually open and run it.

---

### 3. Why does my computer need memory (RAM) to run AI?
Unlike a text document or photo that sits quietly on your disk, an AI model needs to be actively read in real time to generate answers:
* When you start a model, its weights are loaded into your computer's **short-term memory (RAM)**.
* In addition, the model uses a small slice of memory (the **context buffer** or "KV cache") to remember recent questions and replies in your conversation.
* If a model needs 12 GB of RAM, but your computer only has 8 GB, your operating system will struggle, causing other applications to slow down.
* **How LocalPilot protects you**: It measures your computer's memory first and calculates a **Safe Memory Budget**—leaving plenty of room for your operating system, web browser, and other open apps.

---

### 4. Do I need an expensive graphics card (GPU)?
**No.** Many capable models run smoothly on standard computer processors (CPUs):
* **Standard Processors (CPU + RAM)**: Any modern computer can run lightweight models (such as 1.5B to 3B parameter models) smoothly using standard RAM.
* **Apple Silicon Macs (M1, M2, M3, M4)**: Apple laptops feature "Unified Memory," where the processor and graphics chip share the same ultra-fast memory pool. This allows modern Macs to run medium and large models very efficiently.
* **Dedicated Graphics Cards (NVIDIA CUDA / AMD ROCm)**: If you have a computer with a dedicated graphics card and video memory (VRAM), LocalPilot can use it to generate text faster.

---

### 5. What is a "supporting runtime" (like llama.cpp or Ollama)?
A model file by itself is just data on a disk; it needs an "engine" to read it and perform the mathematical calculations. This engine is called an **inference runtime**:
* **`llama.cpp`**: A fast, lightweight engine written in C++ that reads `.gguf` files. LocalPilot uses this directly to run models in isolated worker processes.
* **`Ollama`**: A popular background service that some users already have on their computer. If you have Ollama installed, LocalPilot can connect to it peacefully without disrupting it.
* **You do not need to install or configure these yourself**: LocalPilot prepares the appropriate software automatically during setup.

---

### 6. What does "Expected to Fit" mean?
In LocalPilot, models are evaluated against your actual hardware. "Expected to fit" means:
* The model's download size and memory footprint fit within your computer's safe memory budget.
* There is adequate free storage space on your hard drive.
* LocalPilot tests the model with a live test query before marking the setup complete.

---

## ⚙️ Advanced Technical Architecture (For Developers)

For software engineers, system administrators, and technical users, this section outlines how LocalPilot decouples model computation across three orthogonal axes.

### The 3-Axis Architecture

```text
Application Interface (CLI / TUI / HTTP)
                   │
                   ▼
         ┌───────────────────┐
         │   ExecutionPlan   │
         └─────────┬─────────┘
                   │
  ┌────────────────┼────────────────┐
  ▼                ▼                ▼
Axis 1: Backend  Axis 2: Service  Axis 3: Deployment
llama.cpp        Ollama           direct_worker
vLLM             LM Studio        local_api_service
                 Docker Runner    container
```

1. **Inference Backend (Computation Engine)**:
   * **`llama.cpp`**: Primary C/C++ engine supporting GGUF artifacts with CPU, Apple Metal, NVIDIA CUDA, and Vulkan acceleration.
   * **`vLLM`**: High-throughput server engine designed for discrete multi-GPU Linux installations.
2. **Managed Execution Service (Process Lifecycle)**:
   * **`none` (Direct)**: LocalPilot manages the runtime process directly.
   * **`ollama`**: Connects via loopback REST API (`http://127.0.0.1:11434`) without killing the shared background daemon on model unload.
   * **`lm_studio` / `docker_model_runner`**: External service adapters.
3. **Deployment Method (Isolation Boundary)**:
   * **`direct_worker`**: Spawns an isolated child process with tracked PID and timestamp. Clean process isolation ensures the main CLI/TUI remains unaffected if a model runs out of memory.
   * **`local_api_service`**: Binds to local loopback interface (`127.0.0.1`) for OpenAI-compatible HTTP streaming.

---

### Safe Memory Budget Formula

LocalPilot uses an empirical formula to determine the safe allocation budget:

$$\text{Safe RAM Budget} = \max\left(0, \text{Available RAM} - \text{OS Buffer}\right)$$

* On systems with $\le 8\text{ GB}$ total RAM: Reserves at least $2.5\text{ GB}$ for the OS and essential applications.
* On systems with $16\text{ GB}$ total RAM: Reserves at least $4.0\text{ GB}$.
* On systems with $\ge 32\text{ GB}$ total RAM: Reserves at least $6.0\text{ GB}$.

### Context Window Memory Scaling
Each active conversation requires a Key-Value (KV) cache proportional to the configured context length:
$$\text{Memory}_{\text{total}} \approx \text{Weights Size} + \left(2 \times N_{\text{layers}} \times D_{\text{model}} \times N_{\text{tokens}} \times \text{bytes per element}\right)$$

LocalPilot sets conservative default context lengths ($2{,}048$ to $4{,}096$ tokens) to prevent runaway memory allocation.

---

### Additional References
* [Verified Support Matrix](VERIFIED_SUPPORT_MATRIX.md)
* [Known Limitations & Operational Boundaries](KNOWN_LIMITATIONS.md)
* [API Reference (OpenAPI 3.1)](openapi.json)
* [Interactive Architecture Diagram (architecture.html)](architecture.html)
