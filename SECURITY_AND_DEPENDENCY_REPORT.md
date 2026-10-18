# LocalPilot v0.1.9 Security and Dependency Audit Report

**Date**: October 18, 2026  
**Commit**: `9006eb8a0ec12404c72acff5dceb4f31b6d1c882` (with Phase 7 release hardening)  
**Classification**: High-Assurance Production Security Review  
**Auditor**: Independent Security and Cross-Layer Correctness Engineering  

---

## 1. Secrets Handling & Cryptographic Controls

| Security Boundary | Design Rule | Implementation Mechanism | Verification Test |
| :--- | :--- | :--- | :--- |
| **API Key Storage** | Never store plaintext API keys in the database. | SHA-256 cryptographic hashing (`hashlib.sha256(token.encode()).hexdigest()`). Only hashed digests and key prefixes (`sk-local-xxxx`) are persisted. | `tests/test_api_key_service.py::test_key_generation_and_hashing` |
| **Authentication Timing** | Prevent timing attacks on token lookup. | Constant-time string comparison via `secrets.compare_digest()` during token validation. | `tests/test_api_key_service.py::test_key_authentication` |
| **Secret Display in UI & Diffs** | Never expose raw secret keys in configuration diffs, logs, or TUI screens. | Masking with `sk-local-••••••••` in all unified diffs, JSON previews, and activity logs. | `tests/test_phase6_automation_and_integrations.py::test_preview_diff_masks_secrets` |
| **Support Bundle Sanitization** | Diagnostic exports must never leak credentials or internal username paths. | Regex redaction of tokens (`sk-[a-zA-Z0-9_\-\.]+` -> `[REDACTED_API_KEY]`) and replacement of `/home/<user>` or `/Users/<user>` with `~`. | `tests/test_phase7_release_hardening.py::test_support_bundle_redaction` |

---

## 2. Subprocess Execution & Process Isolation

| Control Check | Security Requirement | Audit Findings & Verification | Status |
| :--- | :--- | :--- | :--- |
| **Shell Injection (`shell=True`)** | Zero occurrences of `shell=True` across the entire codebase. | Codebase search confirmed 0 occurrences of `shell=True` in `src/localpilot/`. All invocations use strict argument lists (`List[str]`). | **VERIFIED CLEAN** |
| **Environment Variable Leakage** | Child hook processes must not inherit sensitive parent environment variables (e.g. `HF_TOKEN`, `OPENAI_API_KEY`, credentials). | `HookRunner` constructs a stripped minimal environment containing only essential POSIX variables (`PATH`, `SYSTEMROOT`, `TEMP`, `USER`, `LOCALPILOT_HOME`). | `tests/test_phase6_automation_and_integrations.py::test_hook_runner_isolation` |
| **Execution Timeouts** | Child processes must never hang indefinitely or block the main event loop. | Strict timeouts (5.0s on lifecycle hooks, 10.0s on diagnostic probes, bounded kill on model processes) enforced via `subprocess.run(..., timeout=...)`. | `tests/test_phase6_automation_and_integrations.py::test_hook_timeout` |
| **Circuit Breakers** | Faulty or malicious external hooks must not continuously execute and degrade system performance. | Automatic circuit breaker trips after 3 consecutive failures, disabling the hook until manual administrative reset via `localpilot hooks reset-circuit`. | `tests/test_phase6_automation_and_integrations.py::test_hook_circuit_breaker` |
| **Process Ownership & PID Reuse** | Terminating a model must never kill an unrelated process if the OS reuses the PID. | `OwnershipVerifier` validates that the running process's creation timestamp matches the timestamp recorded at launch before issuing any signals (`SIGINT`/`SIGTERM`). | `tests/test_security_audit_cases.py::test_pid_ownership_verification` |

---

## 3. Filesystem Security & Path Traversal Defenses

| Threat Vector | Defense Mechanism | Evidence & Test Coverage |
| :--- | :--- | :--- |
| **Symlink Traversal** | Rejects symlinks pointing outside the designated home directory (`LOCALPILOT_HOME`) or client config directories. | Checked via `Path.resolve()` against boundary roots. Symlinks pointing to `/etc/passwd` or outside boundaries are explicitly rejected with `SecurityError`. |
| **Insecure File Permissions** | Sensitive configuration files containing connection parameters must not be world-readable. | File creation sets POSIX file mode `0600` (`-rw-------`) using `os.umask(0o077)` and `os.chmod(target, 0o600)`. |
| **Torn Writes / Corruption** | Power loss or abrupt crash during file writing must not corrupt existing configurations. | Atomic file writes: configuration written to temporary sibling file (`config.json.tmp.xxxx`), flushed to disk (`os.fsync`), and atomically swapped via `os.replace`. |

---

## 4. Network & Telemetry Isolation

- **Interface Binding**: The local inference gateway strictly binds to `127.0.0.1`. Binding to `0.0.0.0` or public network adapters is rejected.
- **CORS Policy**: Configured with restrictive localhost origins (`http://localhost:*`, `http://127.0.0.1:*`) to prevent cross-origin drive-by attacks from arbitrary web pages in the user's browser.
- **Zero Telemetry**: LocalPilot contains zero remote telemetry, tracking beacons, analytics pings, or background update downloaders. All network activity is explicitly user-initiated (e.g. downloading a model upon approval).

---

## 5. Dependency Inventory & License Compatibility

All runtime dependencies have been inspected for license compliance and security vulnerabilities:

| Dependency | Installed Version | License | Security & Purpose Assessment |
| :--- | :--- | :--- | :--- |
| `typer` | 0.27.3 | MIT | Fast, typed CLI framework. Pure Python, no binary vulnerabilities. |
| `rich` | 15.0.0 | MIT | Terminal formatting and syntax highlighting. Safe text rendering. |
| `textual` | 8.2.8 | MIT | Terminal user interface (TUI) engine. Isolated in-process event loop. |
| `httpx` | 0.28.1 | BSD-3-Clause | Async and sync HTTP client for local gateway and verified downloads. |
| `pydantic` | 2.14.1 | MIT | Schema validation and serialization for API contracts and execution plans. |
| `psutil` | 7.2.2 | BSD-3-Clause | OS process discovery, memory tracking, and PID verification. |
| `huggingface_hub` | 2.2.0 | Apache-2.0 | Metadata and GGUF artifact acquisition. Local verification only. |
| `keyring` | 25.7.0 | MIT | Platform credential storage adapter (optional keychain fallback). |
| `platformdirs` | 4.13.3 | MIT | XDG and standard OS directory discovery. |

**License Compatibility**: All libraries use permissive open-source licenses (MIT, BSD-3-Clause, Apache-2.0), fully compatible with LocalPilot's GNU GPLv3 license.
