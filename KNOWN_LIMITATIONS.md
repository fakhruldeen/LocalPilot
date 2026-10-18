# LocalPilot v0.1.9 Known Limitations & Operational Boundaries

**Version**: 0.1.9  
**Date**: October 18, 2026  
**Status**: Authoritative Disclosure of Boundaries  

---

## 1. Network & Deployment Boundaries

1. **Loopback-Only Interface**:
   - The local API gateway is strictly bound to `127.0.0.1`.
   - LocalPilot does not provide remote public tunneling (e.g. ngrok, Cloudflare tunnels, or UPnP port mapping) by design to prevent accidental exposure of your private computer to the public internet.
   - For team or remote access, users must use external encrypted VPNs (such as Tailscale or WireGuard) configured at the OS level.
2. **Zero Cloud Inference Fallback**:
   - LocalPilot does not route unhandled queries to OpenAI, Anthropic, or proprietary cloud APIs. If a local model is offline, requests will fail honestly rather than silently uploading your private documents to third parties.

---

## 2. Platform & Init System Scope

1. **Unprivileged User Services**:
   - Background startup services (`localpilot startup enable`) operate strictly within unprivileged user space:
     - Linux: systemd user units (`~/.config/systemd/user/localpilot-gateway.service`)
     - macOS: launchd LaunchAgents (`~/Library/LaunchAgents/com.localpilot.gateway.plist`)
   - System-wide root init services (`/etc/systemd/system/`) are intentionally not supported and require no `sudo` escalation.
2. **Microsoft Windows Support**:
   - In v0.1.9, Windows is supported via the Windows Subsystem for Linux (WSL2).
   - Native Windows service integration (Windows Service Manager / Task Scheduler) is scheduled for a future milestone.

---

## 3. Inference Engine & Runtime Boundaries

1. **Ollama Integration Status**:
   - Ollama integration is classified as **Experimental (Unmanaged External Service)**.
   - Because Ollama runs as an independent external daemon, stopping a model in LocalPilot communicates with Ollama's API to unload weights, but does not kill the parent Ollama daemon process.
2. **Streaming Metrics Estimation**:
   - When connecting to inference engines that do not emit native token-level timestamps in their SSE streaming chunks, TTFT (Time to First Token) and TPS (Tokens Per Second) are empirically calculated using wall-clock timing between incoming HTTP chunks.
3. **Context Window Memory Scaling**:
   - Default recommended context window is 4,096 tokens. Configuring context windows of 8k, 16k, or 32k tokens increases KV cache memory consumption. Ensure adequate memory budget when increasing context limits.

---

## 4. Application Integration Scope

1. **Supported Automated Connectors**:
   - Automated configuration generation, diff preview, and restoration are verified for **OpenCode** and **VS Code (Continue Extension)**.
2. **Cursor & Other Tools**:
   - Cursor and custom developer tools are supported via standard OpenAI-compatible base URL (`http://127.0.0.1:8080/v1`) and manual configuration instructions provided by `localpilot connect cursor`. Automated configuration rewriting is not yet enabled for Cursor's internal SQLite database.
