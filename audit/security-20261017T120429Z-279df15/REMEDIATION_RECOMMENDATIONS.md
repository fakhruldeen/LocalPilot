# LocalPilot Prioritized Security Remediation Recommendations

**Audit Reference**: `SEC-AUDIT-20261017T120429Z-279df15`  
**Evaluation Scope**: Security Posture Hardening & Supply Chain Verification  

---

## 1. Prioritized Remediation Roadmap

| Priority | Finding ID | Focus Area | Recommended Action | Effort |
|---|---|---|---|---|
| **P1 (Immediate)** | `SEC-P1-01` | SSRF / Input Validation | Implement `validate_download_url()` enforcing HTTPS and blocking private/cloud metadata IP ranges. | Low (1 hour) |
| **P1 (Immediate)** | `SEC-P1-02` | Supply Chain Integrity | Add expected cryptographic SHA256 checksums to `RuntimeInstallPlan` for llama.cpp and Ollama release archives. | Low (1-2 hours) |
| **P2 (Near-Term)** | `SEC-P2-01` | Resource Exhaustion | Add uncompressed size limit and compression ratio checks to `safe_extract_zip`. | Low (1 hour) |
| **P2 (Near-Term)** | `SEC-P2-02` | Archive Hardening | Add `filter="data"` to `tarfile.extractall()` in `safe_extract_tar` on Python 3.12+. | Trivial (15 mins) |
| **P2 (Near-Term)** | `SEC-P2-03` | Contract Alignment | Update `sanitize_artifact_subpath` to explicitly raise `SecurityViolationError` if filename contains slashes. | Trivial (15 mins) |
| **P3 (Maintenance)** | `SEC-P3-01` | Installer Safety | Prompt or check before creating `~/.local/bin/localpilot` symlink in `install-localpilot.sh`. | Trivial (15 mins) |
| **P3 (Maintenance)** | `SEC-P3-02` | Keyring Fallback | Support an encrypted or permission-restricted (0600) local configuration file when OS keyring is absent. | Medium (2-3 hours) |

---

## 2. Technical Remediation Specifications

### Recommendation 1: `validate_download_url` (`SEC-P1-01`)
In `src/localpilot/security/__init__.py`:
```python
import ipaddress
from urllib.parse import urlparse

_FORBIDDEN_METADATA_IPS = {"169.254.169.254", "metadata.google.internal"}

def validate_download_url(url: str) -> bool:
    """Ensures remote download URLs use HTTPS and do not target private/internal networks."""
    parsed = urlparse(url)
    if parsed.scheme.lower() != "https":
        raise SecurityViolationError(f"Insecure scheme: downloads must use HTTPS, got {url}")
    
    hostname = (parsed.hostname or "").lower()
    if hostname in _FORBIDDEN_METADATA_IPS:
        raise SecurityViolationError(f"Access to cloud metadata endpoint forbidden: {hostname}")
        
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_link_local:
            raise SecurityViolationError(f"Access to private/internal network forbidden: {hostname}")
    except ValueError:
        # Hostname is a domain name, not a raw IP
        pass
        
    return True
```
Integrate in `src/localpilot/application/setup_workflow.py` before initiating `httpx.stream("GET", download_url)`.

---

### Recommendation 2: Cryptographic Checksum Pinning for Runtimes (`SEC-P1-02`)
In `src/localpilot/runtimes/llamacpp.py` and `ollama.py`:
Define release archive digests:
```python
LLAMA_CPP_DIGESTS = {
    "b4800": {
        "llama-b4800-bin-ubuntu-x64.zip": "<KNOWN_SHA256>",
        "llama-b4800-bin-macos-arm64.zip": "<KNOWN_SHA256>",
        "llama-b4800-bin-macos-x64.zip": "<KNOWN_SHA256>",
    }
}
```
Verify the hash of `archive_path` before calling `safe_extract_zip` or `safe_extract_tar`.

---

### Recommendation 3: Zip Bomb Protection in `safe_extract_zip` (`SEC-P2-01`)
In `src/localpilot/security/__init__.py`:
```python
MAX_EXTRACT_BYTES = 5 * 1024 * 1024 * 1024  # 5 GB
MAX_MEMBER_COUNT = 10_000

def safe_extract_zip(zip_path: Path | str, target_dir: Path | str) -> None:
    t_dir = Path(target_dir).resolve()
    t_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as z:
        entries = z.infolist()
        if len(entries) > MAX_MEMBER_COUNT:
            raise SecurityViolationError(f"Archive exceeds maximum member count: {len(entries)}")
            
        total_size = sum(e.file_size for e in entries)
        if total_size > MAX_EXTRACT_BYTES:
            raise SecurityViolationError(f"Archive uncompressed size exceeds limit: {total_size} bytes")
            
        for member in entries:
            name = member.filename
            if name.startswith("/") or ".." in Path(name).parts:
                raise SecurityViolationError(f"Path traversal member in zip archive: {name}")
            member_path = t_dir / name
            validate_path_within_root(member_path, t_dir)
        z.extractall(path=t_dir)
```
