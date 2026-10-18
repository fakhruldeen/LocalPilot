# LocalPilot v0.2.0 Performance Baseline & Resource Budgets

**Measurement Date**: October 18, 2026  
**Environment**: Linux 6.8.0-101-generic x86_64 | 12 Cores / 24 Threads | 64 GB RAM  
**Python Runtime**: Python 3.14.6  
**Methodology**: Empirical benchmarks measured over 50 iterations per operation; warm and cold measurements recorded.

---

## 1. CLI Execution Latencies

| Command Invocation | Min Latency | Average Latency | Max Latency | Budget Target | Compliance Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `localpilot --help` | 0.323 s | **0.332 s** | 0.345 s | < 0.600 s | **PASSED (Well Under Budget)** |
| `localpilot doctor --plain` | 0.365 s | **0.368 s** | 0.373 s | < 0.750 s | **PASSED (Well Under Budget)** |
| `localpilot installed` | 0.297 s | **0.303 s** | 0.318 s | < 0.500 s | **PASSED (Well Under Budget)** |
| `localpilot versions list` | 0.298 s | **0.309 s** | 0.319 s | < 0.500 s | **PASSED (Well Under Budget)** |
| `localpilot stats` | 0.300 s | **0.306 s** | 0.311 s | < 0.500 s | **PASSED (Well Under Budget)** |

---

## 2. Memory Footprint (Resident Set Size)

| State / Subsystem | Resident Set Size (RSS) | Maximum Budget | Observations |
| :--- | :--- | :--- | :--- |
| **CLI Cold Readiness** | **51.66 MB** | 100.0 MB | Pure Python startup with Typer, Rich, and SQLite schema verification. |
| **Gateway Server (Idle Background)** | **~58.4 MB** | 120.0 MB | Standard library `ThreadingHTTPServer` with connection pooling and routing table. |
| **TUI Dashboard (Idle)** | **~68.2 MB** | 150.0 MB | Textual reactive application with mounted status widgets and live pollers. |

*Note: Memory consumed by active model weights (GGUF tensors) is managed directly by the underlying inference engine (`llama.cpp`) outside Python GC, adhering to the user's configured hardware budget.*

---

## 3. Local Inference Gateway Internal Latency

Internal routing and authentication overhead (excluding LLM tensor forward-pass computation):

| Gateway Endpoint | Min Overhead | Average Overhead | Max Overhead | Protocol / Transport |
| :--- | :--- | :--- | :--- | :--- |
| `GET /health` | 0.53 ms | **0.73 ms** | 4.79 ms | HTTP/1.1 Loopback (`127.0.0.1:8080`) |
| `GET /v1/models` (Unauthenticated 401) | 0.68 ms | **1.20 ms** | 5.45 ms | Constant-time bearer token check & error envelope |
| `GET /v1/models` (Authenticated 200) | 1.12 ms | **1.85 ms** | 6.10 ms | SQLite query + JSON serialization |
| `POST /v1/chat/completions` (Initial Dispatch) | 1.45 ms | **2.10 ms** | 7.30 ms | Scope verification + lease acquisition |

---

## 4. Resource Allocation & Hardware Protection

1. **OS Memory Preservation**:
   - LocalPilot reserves a minimum of **2.5 GB of RAM** exclusively for the operating system and user desktop applications on 8 GB systems.
   - On 16 GB+ systems, a minimum of **4.0 GB of RAM** is preserved.
   - Models exceeding available memory budgets are scored as `Incompatible` or `Tight` and prevented from silent overcommit.
2. **Disk I/O Pre-Flight**:
   - Before any download begins, LocalPilot validates that target filesystem free space exceeds `download_bytes + 1.0 GB` buffer.
   - Write operations abort cleanly with friendly error advice if storage thresholds are breached.
