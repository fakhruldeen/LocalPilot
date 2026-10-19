# LocalPilot Catalog Expansion — Research Staging Package
**Cutoff Snapshot**: October 20, 2026

## Overview
This staging package provides research-backed expansion candidates discovered and organized for LocalPilot.
It is **not** a drop-in replacement for the active production catalog (`models.json`), but a safe review and staging buffer.

### Package Contents
- `candidates_models.json`: 82 model candidate records with provenance links, task groups, and verification status.
- `candidates_models.csv`: Tabular CSV representation of all 82 candidates.
- `candidates_providers.json`: 13 organization & publisher namespace candidates.
- `candidates_developers.json`: 3 developer and community quantizer records (`mlx-community`, `lmstudio-community`, `ggml-org`).
- `README.md`: Promotion safeguards, verification guidelines, and developer handoff documentation.
- `localpilot_staging_package_20261020.zip`: Complete archive containing all staging assets.

---

## Promotion Safeguards & Requirements

Before promoting any staging candidate into the installable production catalog:

1. **Authorship & Artifact Attribution Separation**:
   - Separate base model authorship from quantization publishers.
   - Example attribution schema:
     ```json
     {
       "base_model_provider_id": "google",
       "base_model_id": "google/gemma-4-E2B-it",
       "artifact_publisher_id": "bartowski",
       "artifact_repository": "bartowski/gemma-4-E2B-it-GGUF",
       "artifact_is_official": false
     }
     ```
2. **Authoritative Hash Verification**:
   - Do NOT fabricate or mock SHA-256 hashes. Leave `sha256: null` until authoritatively verified against Hugging Face metadata or local download checksums.
3. **Canonical Organization Mapping**:
   - `openai`: Canonical provider for `gpt-oss` releases (`gpt-oss-20b`, `gpt-oss-120b`).
   - `moonshot-ai`: Canonical organization with `kimi` preserved as alias.
   - `zhipu-ai`: Canonical organization with `glm` preserved as alias.
4. **License & Gating Verification**:
   - Check license at the specific model/artifact level (e.g. Gemma 4 is Apache 2.0).
5. **Modality & Task Separation**:
   - Keep text generative, vision/multimodal, embedding (`embedding`), and reranking (`reranking`) as distinct task groups.
6. **Hardware & Context Bounds**:
   - Record active vs total parameters for MoE architectures.
   - Distinguish native usable context from extended hosted configurations.

---

## Evidence Levels
- `primary_model_card`: Direct inspection of official model card and publisher repository.
- `primary_publication`: Formal technical release blog or paper (e.g., Reflection Beam).
- `secondary_inventory`: Validated secondary local AI inventory (October 18-20, 2026).
- `discovery_reference`: Runtime and framework integration listings (vLLM, Ollama, MTEB).
