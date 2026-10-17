# LocalPilot Security & Software-Supply-Chain Audit Report

**Audit Identifier**: `SEC-AUDIT-20261017T120429Z-279df15`  
**Date**: `2026-10-17T12:04:29Z`  
**Repository**: `https://github.com/fakhruldeen/LocalPilot`  
**Git Commit**: `279df156f34e7eb42485fe5fb96c30e778ef65a4` (branch: `main` with tracked remediation)  
**Evaluator**: Independent Security & Software-Supply-Chain Audit Agent  

---

## 1. Executive Summary & Security Posture

### Overall Security Verdict
**DEFENSIVE POSTURE: GENERALLY ROBUST WITH NOTABLE SUPPLY-CHAIN AND INPUT-VALIDATION GAPS**

LocalPilot implements several industry-standard defensive controls that protect users from common local AI security risks:
- **Zero Remote Code Execution**: No `eval()`, `exec()`, `pickle`, or `trust_remote_code` in production code. Models are treated purely as static weight files (GGUF format).
- **Subprocess Hygiene**: 100% of subprocess calls pass argument lists (`shell=False`), eliminating shell injection risks.
- **Strict Localhost Binding**: Application and runtime endpoints are validated to bind exclusively to `127.0.0.1`, `localhost`, or `::1`.
- **Automatic Secret Redaction**: Structured logging (`events.jsonl`) automatically scrubs HuggingFace tokens, Bearer tokens, passwords, and user home paths.
- **Process Ownership Verification**: Prevents PID reuse attacks by matching PID creation timestamps before termination.

However, several supply chain and input validation gaps require hardening before nontechnical user deployment:
1. **Unvalidated `download_url` in Model Setup (SSRF / Arbitrary URL Fetching)**: Model candidates define arbitrary download URLs which are requested via HTTP GET without scheme restrictions (`https://` only) or internal network / cloud metadata SSRF protections.
2. **Unverified Runtime Binary Release Archives**: Binary downloads for `llama-server` and `ollama` from GitHub releases are extracted without verifying cryptographic SHA256 checksums.
3. **Archive Extraction Hardening**: `safe_extract_zip` lacks compression ratio / uncompressed size limits (zip bomb defense), and `safe_extract_tar` omits Python 3.12+ `filter="data"`.

---

## 2. Threat Model

### A. Assets & Boundaries
- **User Filesystem**: `~/.local/share/localpilot/` (managed directory). Must never be escaped.
- **Local Model Weights**: Downloaded GGUF files. Must not be overwritten or corrupted by untrusted inputs.
- **Keyring Credentials**: User API tokens (e.g. HuggingFace token). Must never be leaked to logs, error dialogs, or child process arguments.
- **System Memory & GPU**: Must not be monopolized or corrupted by unchecked model configurations.
- **Local Network / Host Services**: Must not be scanned or probed by attacker-controlled model candidate metadata.

### B. Attacker Profiles & Vectors
1. **Malicious Remote Model Publisher**:
   - Publishes a model repository with crafted metadata: path traversal in filenames (`../../../etc/passwd`), null bytes, or internal URLs (`http://169.254.169.254/latest/meta-data`).
2. **Compromised Upstream Dependency / Release**:
   - Compromised binary release on GitHub releases (e.g. `llama.cpp` or `ollama` release archive substituted or intercepted).
3. **Local Unprivileged Malicious Process**:
   - Attempts to send requests to LocalPilot's local HTTP inference endpoints or hijack PIDs.
4. **Accidental User Operation**:
   - Running installer with unintended privileges or path collisions.

---

## 3. Supply Chain & Dependency Review

- **Direct Production Dependencies**: 8 direct packages (`typer`, `rich`, `textual`, `httpx`, `psutil`, `pydantic`, `huggingface_hub`, `keyring`).
- **Total Installed Packages**: 50 packages in `.venv`.
- **License Compatibility**: All 50 installed dependencies use OSI-approved permissive licenses (MIT, BSD-2/3-Clause, Apache-2.0, MPL-2.0, PSF-2.0, ISC) compatible with LocalPilot's `GPL-3.0-only` license.
- **External Binaries**:
  - `llama-server` (llama.cpp release tag `b4800` from GitHub).
  - `ollama` (Ollama release tag `v0.5.4` from GitHub).
  - *Risk*: Both binaries are downloaded over HTTPS but lack hardcoded SHA256 integrity verification in `RuntimeInstallPlan`.

---

## 4. Key Findings Summary

| Finding ID | Severity | Category | Affected Component | Description |
|---|---|---|---|---|
| **SEC-P1-01** | P1 (High) | SSRF / Input Validation | `setup_workflow.py:392` | Unvalidated `download_url` permits non-HTTPS schemes and internal/cloud metadata requests. |
| **SEC-P1-02** | P1 (High) | Supply Chain Integrity | `llamacpp.py:88`, `ollama.py:86` | Runtime binary release archives are downloaded and extracted without cryptographic hash verification. |
| **SEC-P2-01** | P2 (Med) | Resource Exhaustion | `security/__init__.py:87` | `safe_extract_zip` lacks uncompressed size and compression ratio checks (zip bomb vulnerability). |
| **SEC-P2-02** | P2 (Med) | Archive Security | `security/__init__.py:84` | `safe_extract_tar` omits Python 3.12+ `filter="data"`, triggering deprecation warnings and potential link edge cases. |
| **SEC-P2-03** | P2 (Med) | Contract Inconsistency | `security/__init__.py:18` | `sanitize_artifact_subpath` strips path components via `Path.name` instead of explicitly rejecting slashes as claimed in docstring. |
| **SEC-P3-01** | P3 (Low) | Installer Overwrite | `install-localpilot.sh:215` | Symlink `~/.local/bin/localpilot` is created with `ln -sf` without checking for existing non-LocalPilot files. |
| **SEC-P3-02** | P3 (Low) | Credential Fallback | `security/__init__.py:101` | Headless environments without DBus/keyring fail silently without a secure configuration file fallback. |

---

## 5. Security Deliverables Index

The following audit deliverables accompany this report:
- `DEPENDENCY_INVENTORY.csv`: Complete SBOM of all 50 installed dependencies and licenses.
- `SECURITY_FINDINGS.csv`: Structured findings catalog with line numbers, preconditions, impact, and mitigations.
- `SECURITY_REGRESSION_TESTS.md`: Test evidence from the 8 targeted security test cases.
- `REMEDIATION_RECOMMENDATIONS.md`: Prioritized remediation guide for engineering.
