# LocalPilot Audit Evidence & Sanitized Traces

**Audit Timestamp**: `2026-10-17T11:37:51Z`  
**Git Commit**: `279df156f34e7eb42485fe5fb96c30e778ef65a4`  

---

## 1. Baseline Environment Probe

### Command:
```bash
python3 -c "import psutil, shutil, platform; print('Platform:', platform.platform()); print('CPU Count:', psutil.cpu_count(logical=True)); print('RAM GB:', round(psutil.virtual_memory().total / (1024**3), 2)); print('Free Disk GB:', round(shutil.disk_usage('.').free / (1024**3), 2))"
```
### Output:
```text
Platform: Linux-7.0.0-38-generic-x86_64-with-glibc2.43
CPU Count: 8
RAM GB: 23.25
Free Disk GB: 18.29
nvidia-smi not available/no GPU
```

---

## 2. CLI Doctor Command Verification

### Command:
```bash
.venv/bin/localpilot doctor --json
```
### Observed Output (Excerpt):
```json
{
  "hardware": {
    "os_name": "Linux",
    "os_version": "7.0.0-38-generic",
    "cpu_arch": "x86_64",
    "cpu_cores_physical": 4,
    "cpu_cores_logical": 8,
    "ram_total_bytes": 24967999488,
    "ram_available_bytes": 11194023936,
    "memory_model": "separate",
    "gpu_devices": [],
    "disk_total_bytes": 146702188544,
    "disk_available_bytes": 19632771072,
    "safe_ram_budget_bytes": 6200424039,
    "safe_vram_budget_bytes": 0
  },
  "discovered_runtimes": [
    {
      "name": "llamacpp",
      "scope": "isolated",
      "binary_path": "/home/[REDACTED]/.local/share/localpilot/runtimes/llamacpp/bin/llama-server",
      "version": null,
      "is_service_running": false,
      "endpoint_url": null
    }
  ],
  "discovered_services": [
    {
      "service_id": "docker_model_runner",
      "components": {
        "docker": "/usr/bin/docker",
        "model_plugin": "true"
      },
      "is_service_running": false,
      "endpoint_url": null,
      "version": null,
      "scope": "system"
    }
  ]
}
```

---

## 3. Defect LP-P0-01 Reproduction: `llama-server` Missing Shared Libraries

### Command:
```bash
/home/[REDACTED]/.local/share/localpilot/runtimes/llamacpp/bin/llama-server --version
```
### Exit Code: 127
### Output:
```text
/home/[REDACTED]/.local/share/localpilot/runtimes/llamacpp/bin/llama-server: error while loading shared libraries: libllama.so: cannot open shared object file: No such file or directory
```

### Verified Fix Command (with LD_LIBRARY_PATH):
```bash
LD_LIBRARY_PATH=/home/[REDACTED]/.local/share/localpilot/runtimes/llamacpp/bin/build/bin /home/[REDACTED]/.local/share/localpilot/runtimes/llamacpp/bin/llama-server --version
```
### Exit Code: 0
### Output:
```text
version: 4800 (cc473cac)
built with cc (Ubuntu 11.4.0-1ubuntu1~22.04) 11.4.0 for x86_64-linux-gnu
```

---

## 4. Defect LP-P1-01 Reproduction: Docker Model Runner False Positive

### Command 1 (`--help` intercept):
```bash
docker model --help
```
### Exit Code: 0
### Output:
```text
Usage:  docker [OPTIONS] COMMAND
A self-sufficient runtime for containers
...
```

### Command 2 (Actual plugin check):
```bash
docker model
```
### Exit Code: 1
### Output:
```text
docker: unknown command: docker model
Run 'docker --help' for more information
```

---

## 5. Automated Test Suite Full Execution Log

### Command:
```bash
.venv/bin/pytest -v
```
### Exit Code: 0
### Summary:
```text
============================== 73 passed in 5.31s ==============================
tests/test_architecture_v2.py ..........                                 [ 13%]
tests/test_cli_and_tui.py .......                                        [ 23%]
tests/test_compatibility_and_scoring.py .....                            [ 30%]
tests/test_cross_layer_remediation.py .............                      [ 47%]
tests/test_hardware_and_platforms.py ....                                [ 53%]
tests/test_jobs_ownership_and_storage.py ...                             [ 57%]
tests/test_observability_and_security.py .......                         [ 67%]
tests/test_remediation_p0_p1.py .......                                  [ 76%]
tests/test_remediation_round2.py .........                               [ 89%]
tests/test_scenario_remaining.py ......                                  [ 97%]
tests/test_workflows_and_updates.py ..                                   [100%]
```
