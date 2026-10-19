#!/usr/bin/env python3
"""Generates the downloadable research-backed staging package for LocalPilot catalog expansion.

Requested cutoff: 2026-10-20.
Staging deliverable components:
- candidates_models.json (82 records)
- candidates_models.csv (82 records)
- candidates_providers.json (13 records)
- candidates_developers.json (3 records)
- README.md (safeguards, checklist, observed issues)
- localpilot_staging_package_20261020.zip
"""

from __future__ import annotations
import csv
import io
import json
from pathlib import Path
import zipfile

REPO_ROOT = Path(__file__).resolve().parent.parent
STAGING_DIRS = [
    REPO_ROOT / "data" / "staging",
    REPO_ROOT / "docs" / "data" / "staging",
    REPO_ROOT / "web" / "data" / "staging",
]

DEVELOPERS_STAGING = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "snapshot_requested": "2026-10-20",
    "package_type": "staging_candidates_not_production_catalog",
    "exhaustive": False,
    "full_existing_catalog_deduplication_completed": False,
    "total_count": 3,
    "records": [
        {
            "developer_id": "mlx-community",
            "name": "MLX Community",
            "role": "MLX model conversion and publishing",
            "huggingface_url": "https://huggingface.co/mlx-community",
            "verification": "primary_profile",
            "evidence_url": "https://huggingface.co/mlx-community",
            "specialties": ["MLX format", "Apple Silicon optimization", "Quantized MLX weights"]
        },
        {
            "developer_id": "lmstudio-community",
            "name": "LM Studio Community",
            "role": "Community quantization and publishing",
            "huggingface_url": "https://huggingface.co/lmstudio-community",
            "verification": "primary_profile",
            "evidence_url": "https://huggingface.co/lmstudio-community",
            "specialties": ["GGUF quantizations", "LM Studio compatibility", "Desktop LLMs"]
        },
        {
            "developer_id": "ggml-org",
            "name": "ggml organization",
            "role": "Inference tools and related model artifacts",
            "huggingface_url": "https://huggingface.co/ggml-org",
            "verification": "primary_profile",
            "evidence_url": "https://huggingface.co/ggml-org",
            "specialties": ["ggml core", "llama.cpp ecosystem", "Official reference artifacts"]
        }
    ]
}

PROVIDERS_STAGING = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "snapshot_requested": "2026-10-20",
    "package_type": "staging_candidates_not_production_catalog",
    "exhaustive": False,
    "full_existing_catalog_deduplication_completed": False,
    "total_count": 13,
    "records": [
        {
            "provider_id": "reflection",
            "huggingface_namespace": "reflection",
            "role": "model_author",
            "verification": "candidate_namespace_requires_review",
            "evidence_url": "https://huggingface.co/blog/reflection-beam",
            "existing_catalog_membership": "not_in_catalog"
        },
        {
            "provider_id": "stepfun",
            "huggingface_namespace": "stepfun-ai",
            "role": "model_author",
            "verification": "candidate_namespace_requires_review",
            "evidence_url": "https://www.developersdigest.tech/local-models",
            "existing_catalog_membership": "present_in_catalog"
        },
        {
            "provider_id": "xiaomi-mimo",
            "huggingface_namespace": "XiaomiMiMo",
            "role": "model_author",
            "verification": "candidate_namespace_requires_review",
            "evidence_url": "https://www.developersdigest.tech/local-models",
            "existing_catalog_membership": "not_in_catalog"
        },
        {
            "provider_id": "tencent",
            "huggingface_namespace": "tencent",
            "role": "model_author",
            "verification": "candidate_namespace_requires_review",
            "evidence_url": "https://www.developersdigest.tech/local-models",
            "existing_catalog_membership": "not_in_catalog"
        },
        {
            "provider_id": "minimax",
            "huggingface_namespace": "MiniMaxAI",
            "role": "model_author",
            "verification": "candidate_namespace_requires_review",
            "evidence_url": "https://www.developersdigest.tech/local-models",
            "existing_catalog_membership": "present_in_catalog"
        },
        {
            "provider_id": "nomic-ai",
            "huggingface_namespace": "nomic-ai",
            "role": "embedding_author",
            "verification": "candidate_namespace_requires_review",
            "evidence_url": "https://docs.vllm.ai/en/v0.9.2/models/supported_models.html",
            "existing_catalog_membership": "present_in_catalog"
        },
        {
            "provider_id": "mixedbread",
            "huggingface_namespace": "mixedbread-ai",
            "role": "embedding_and_reranking_author",
            "verification": "candidate_namespace_requires_review",
            "evidence_url": "https://docs.vllm.ai/en/v0.9.2/models/supported_models.html",
            "existing_catalog_membership": "not_in_catalog"
        },
        {
            "provider_id": "jina-ai",
            "huggingface_namespace": "jinaai",
            "role": "embedding_author",
            "verification": "candidate_namespace_requires_review",
            "evidence_url": "https://docs.vllm.ai/en/v0.9.2/models/supported_models.html",
            "existing_catalog_membership": "not_in_catalog"
        },
        {
            "provider_id": "intfloat",
            "huggingface_namespace": "intfloat",
            "role": "embedding_publisher",
            "verification": "candidate_namespace_requires_review",
            "evidence_url": "https://docs.vllm.ai/en/v0.9.2/models/supported_models.html",
            "existing_catalog_membership": "not_in_catalog"
        },
        {
            "provider_id": "allam-ai",
            "huggingface_namespace": "ALLaM-AI",
            "role": "arabic_model_publisher",
            "verification": "candidate_namespace_requires_review",
            "evidence_url": "https://huggingface.co/spaces/OALL/Open-Arabic-LLM-Leaderboard",
            "existing_catalog_membership": "not_in_catalog"
        },
        {
            "provider_id": "inception",
            "huggingface_namespace": "inceptionai",
            "role": "arabic_model_publisher_identity_review_required",
            "verification": "candidate_namespace_requires_review",
            "evidence_url": "https://huggingface.co/spaces/OALL/Open-Arabic-LLM-Leaderboard",
            "existing_catalog_membership": "not_in_catalog"
        },
        {
            "provider_id": "silma-ai",
            "huggingface_namespace": "silma-ai",
            "role": "arabic_model_publisher",
            "verification": "candidate_namespace_requires_review",
            "evidence_url": "https://huggingface.co/spaces/OALL/Open-Arabic-LLM-Leaderboard",
            "existing_catalog_membership": "not_in_catalog"
        },
        {
            "provider_id": "mbzuai-paris",
            "huggingface_namespace": "MBZUAI-Paris",
            "role": "arabic_model_publisher",
            "verification": "candidate_namespace_requires_review",
            "evidence_url": "https://huggingface.co/spaces/OALL/Open-Arabic-LLM-Leaderboard",
            "existing_catalog_membership": "not_in_catalog"
        }
    ]
}

MODELS_STAGING_CSV = """artifact_verification,enabled_for_install,evidence_level,evidence_url,license,localpilot_compatibility,model_id,provider_id,source,source_url,task_group
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,Qwen/Qwen3.5-0.8B,qwen,huggingface,https://huggingface.co/Qwen/Qwen3.5-0.8B,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,Qwen/Qwen3.5-2B,qwen,huggingface,https://huggingface.co/Qwen/Qwen3.5-2B,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,LiquidAI/LFM2.5-2.6B,liquid-ai,huggingface,https://huggingface.co/LiquidAI/LFM2.5-2.6B,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,mistralai/Ministral-3-3B-Instruct-2512,mistral,huggingface,https://huggingface.co/mistralai/Ministral-3-3B-Instruct-2512,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,microsoft/Phi-4-mini-instruct,microsoft,huggingface,https://huggingface.co/microsoft/Phi-4-mini-instruct,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,microsoft/Phi-4-mini-reasoning,microsoft,huggingface,https://huggingface.co/microsoft/Phi-4-mini-reasoning,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,Qwen/Qwen3.5-4B,qwen,huggingface,https://huggingface.co/Qwen/Qwen3.5-4B,generative
pending,False,primary_model_card,https://huggingface.co/google/gemma-4-E2B-it,Apache-2.0,not_tested,google/gemma-4-E2B-it,google,huggingface,https://huggingface.co/google/gemma-4-E2B-it,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,CohereLabs/c4ai-command-r7b-12-2024,cohere,huggingface,https://huggingface.co/CohereLabs/c4ai-command-r7b-12-2024,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,ibm-granite/granite-4.2-8b,ibm,huggingface,https://huggingface.co/ibm-granite/granite-4.2-8b,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,google/gemma-4-E4B-it,google,huggingface,https://huggingface.co/google/gemma-4-E4B-it,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,mistralai/Ministral-3-8B-Instruct-2512,mistral,huggingface,https://huggingface.co/mistralai/Ministral-3-8B-Instruct-2512,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,Qwen/Qwen3.5-9B,qwen,huggingface,https://huggingface.co/Qwen/Qwen3.5-9B,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,google/gemma-4-12B-it,google,huggingface,https://huggingface.co/google/gemma-4-12B-it,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,mistralai/Ministral-3-14B-Instruct-2512,mistral,huggingface,https://huggingface.co/mistralai/Ministral-3-14B-Instruct-2512,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,microsoft/phi-4,microsoft,huggingface,https://huggingface.co/microsoft/phi-4,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,microsoft/Phi-4-reasoning-plus,microsoft,huggingface,https://huggingface.co/microsoft/Phi-4-reasoning-plus,generative
pending,False,primary_model_card,https://huggingface.co/openai/gpt-oss-20b,Apache-2.0,not_tested,openai/gpt-oss-20b,openai,huggingface,https://huggingface.co/openai/gpt-oss-20b,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,LiquidAI/LFM2.5-24B-A2B,liquid-ai,huggingface,https://huggingface.co/LiquidAI/LFM2.5-24B-A2B,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,mistralai/Devstral-Small-2-24B-Instruct-2512,mistral,huggingface,https://huggingface.co/mistralai/Devstral-Small-2-24B-Instruct-2512,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,google/gemma-4-26B-A4B-it,google,huggingface,https://huggingface.co/google/gemma-4-26B-A4B-it,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,allenai/Olmo-Hybrid-Instruct-DPO-7B,allenai,huggingface,https://huggingface.co/allenai/Olmo-Hybrid-Instruct-DPO-7B,generative
pending,False,primary_model_card,https://huggingface.co/Qwen/Qwen3.8-27B,Apache-2.0,not_tested,Qwen/Qwen3.8-27B,qwen,huggingface,https://huggingface.co/Qwen/Qwen3.8-27B,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,ibm-granite/granite-4.2-30b,ibm,huggingface,https://huggingface.co/ibm-granite/granite-4.2-30b,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,google/gemma-4-31B-it,google,huggingface,https://huggingface.co/google/gemma-4-31B-it,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16,nvidia,huggingface,https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-BF16,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,allenai/Olmo-3.1-32B-Think,allenai,huggingface,https://huggingface.co/allenai/Olmo-3.1-32B-Think,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,CohereLabs/aya-expanse-32b,cohere,huggingface,https://huggingface.co/CohereLabs/aya-expanse-32b,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,Qwen/Qwen3.6-35B-A3B,qwen,huggingface,https://huggingface.co/Qwen/Qwen3.6-35B-A3B,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-BF16,nvidia,huggingface,https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-BF16,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,microsoft/Phi-4-reasoning-vision-15B,microsoft,huggingface,https://huggingface.co/microsoft/Phi-4-reasoning-vision-15B,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,ai21labs/AI21-Jamba2-Mini,ai21-labs,huggingface,https://huggingface.co/ai21labs/AI21-Jamba2-Mini,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,Qwen/Qwen3-Coder-Next,qwen,huggingface,https://huggingface.co/Qwen/Qwen3-Coder-Next,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,zai-org/GLM-4.5-Air,zhipu-ai,huggingface,https://huggingface.co/zai-org/GLM-4.5-Air,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,meta-llama/Llama-4-Scout-17B-16E-Instruct,meta,huggingface,https://huggingface.co/meta-llama/Llama-4-Scout-17B-16E-Instruct,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,zai-org/GLM-4.6V,zhipu-ai,huggingface,https://huggingface.co/zai-org/GLM-4.6V,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,openai/gpt-oss-120b,openai,huggingface,https://huggingface.co/openai/gpt-oss-120b,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,CohereLabs/c4ai-command-a-03-2025,cohere,huggingface,https://huggingface.co/CohereLabs/c4ai-command-a-03-2025,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,mistralai/Mistral-Small-4-119B-2603,mistral,huggingface,https://huggingface.co/mistralai/Mistral-Small-4-119B-2603,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,mistralai/Devstral-2-123B-Instruct-2512,mistral,huggingface,https://huggingface.co/mistralai/Devstral-2-123B-Instruct-2512,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,nvidia/NVIDIA-Nemotron-3.5-Super-VL-120B-A12B-BF16,nvidia,huggingface,https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Super-VL-120B-A12B-BF16,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,mistralai/Mistral-Medium-3.5-128B,mistral,huggingface,https://huggingface.co/mistralai/Mistral-Medium-3.5-128B,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,stepfun-ai/Step-3.7-Flash,stepfun,huggingface,https://huggingface.co/stepfun-ai/Step-3.7-Flash,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,XiaomiMiMo/MiMo-V2.6-Flash-RL,xiaomi-mimo,huggingface,https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Flash-RL,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,tencent/Hy3,tencent,huggingface,https://huggingface.co/tencent/Hy3,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,zai-org/GLM-4.6,zhipu-ai,huggingface,https://huggingface.co/zai-org/GLM-4.6,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,zai-org/GLM-4.7,zhipu-ai,huggingface,https://huggingface.co/zai-org/GLM-4.7,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,zai-org/GLM-5.3-Flash,zhipu-ai,huggingface,https://huggingface.co/zai-org/GLM-5.3-Flash,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,MiniMaxAI/MiniMax-M3,minimax,huggingface,https://huggingface.co/MiniMaxAI/MiniMax-M3,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,meta-llama/Llama-4-Maverick-17B-128E-Instruct,meta,huggingface,https://huggingface.co/meta-llama/Llama-4-Maverick-17B-128E-Instruct,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,mistralai/Mistral-Large-3-675B-Instruct-2512,mistral,huggingface,https://huggingface.co/mistralai/Mistral-Large-3-675B-Instruct-2512,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,zai-org/GLM-5.2,zhipu-ai,huggingface,https://huggingface.co/zai-org/GLM-5.2,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,zai-org/GLM-5.3,zhipu-ai,huggingface,https://huggingface.co/zai-org/GLM-5.3,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,zai-org/GLM-5,zhipu-ai,huggingface,https://huggingface.co/zai-org/GLM-5,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,tencent/Hy4-preview,tencent,huggingface,https://huggingface.co/tencent/Hy4-preview,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,zai-org/GLM-5.1,zhipu-ai,huggingface,https://huggingface.co/zai-org/GLM-5.1,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,deepseek-ai/DeepSeek-V4.1-Flash,deepseek,huggingface,https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,XiaomiMiMo/MiMo-V2.6-Pro-RL,xiaomi-mimo,huggingface,https://huggingface.co/XiaomiMiMo/MiMo-V2.6-Pro-RL,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,moonshotai/Kimi-K2.6,moonshot-ai,huggingface,https://huggingface.co/moonshotai/Kimi-K2.6,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,moonshotai/Kimi-K2.7-Code,moonshot-ai,huggingface,https://huggingface.co/moonshotai/Kimi-K2.7-Code,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,ai21labs/AI21-Jamba-Large-1.7,ai21-labs,huggingface,https://huggingface.co/ai21labs/AI21-Jamba-Large-1.7,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,deepseek-ai/DeepSeek-V4-Pro,deepseek,huggingface,https://huggingface.co/deepseek-ai/DeepSeek-V4-Pro,generative
pending,False,secondary_inventory,https://www.developersdigest.tech/local-models,,not_tested,moonshotai/Kimi-K3,moonshot-ai,huggingface,https://huggingface.co/moonshotai/Kimi-K3,generative
pending,False,primary_publication,https://huggingface.co/blog/reflection-beam,,not_tested,reflection/Beam-1.0-501B-A23B,reflection,huggingface,https://huggingface.co/reflection/Beam-1.0-501B-A23B,generative
pending,False,primary_publication,https://huggingface.co/blog/reflection-beam,,not_tested,reflection/Beam-1.0-501B-A23B-FP8,reflection,huggingface,https://huggingface.co/reflection/Beam-1.0-501B-A23B-FP8,generative
pending,False,primary_publication,https://huggingface.co/blog/reflection-beam,,not_tested,reflection/Beam-1.0-501B-A23B-NVFP4,reflection,huggingface,https://huggingface.co/reflection/Beam-1.0-501B-A23B-NVFP4,generative
pending,False,discovery_reference,https://docs.vllm.ai/en/v0.9.2/models/supported_models.html,,not_tested,nomic-ai/nomic-embed-text-v1.5,nomic-ai,huggingface,https://huggingface.co/nomic-ai/nomic-embed-text-v1.5,embedding
pending,False,discovery_reference,https://docs.vllm.ai/en/v0.9.2/models/supported_models.html,,not_tested,nomic-ai/nomic-embed-text-v2-moe,nomic-ai,huggingface,https://huggingface.co/nomic-ai/nomic-embed-text-v2-moe,embedding
pending,False,discovery_reference,https://docs.vllm.ai/en/v0.9.2/models/supported_models.html,,not_tested,nomic-ai/modernbert-embed-base,nomic-ai,huggingface,https://huggingface.co/nomic-ai/modernbert-embed-base,embedding
pending,False,discovery_reference,https://docs.vllm.ai/en/v0.9.2/models/supported_models.html,,not_tested,mixedbread-ai/mxbai-embed-large-v1,mixedbread,huggingface,https://huggingface.co/mixedbread-ai/mxbai-embed-large-v1,embedding
pending,False,discovery_reference,https://docs.vllm.ai/en/v0.9.2/models/supported_models.html,,not_tested,mixedbread-ai/mxbai-rerank-base-v2,mixedbread,huggingface,https://huggingface.co/mixedbread-ai/mxbai-rerank-base-v2,reranking
pending,False,discovery_reference,https://docs.vllm.ai/en/v0.9.2/models/supported_models.html,,not_tested,jinaai/jina-embeddings-v3,jina-ai,huggingface,https://huggingface.co/jinaai/jina-embeddings-v3,embedding
pending,False,discovery_reference,https://docs.vllm.ai/en/v0.9.2/models/supported_models.html,,not_tested,intfloat/multilingual-e5-small,intfloat,huggingface,https://huggingface.co/intfloat/multilingual-e5-small,embedding
pending,False,discovery_reference,https://docs.vllm.ai/en/v0.9.2/models/supported_models.html,,not_tested,intfloat/multilingual-e5-base,intfloat,huggingface,https://huggingface.co/intfloat/multilingual-e5-base,embedding
pending,False,discovery_reference,https://docs.vllm.ai/en/v0.9.2/models/supported_models.html,,not_tested,intfloat/multilingual-e5-large,intfloat,huggingface,https://huggingface.co/intfloat/multilingual-e5-large,embedding
pending,False,discovery_reference,https://docs.vllm.ai/en/v0.9.2/models/supported_models.html,,not_tested,intfloat/multilingual-e5-large-instruct,intfloat,huggingface,https://huggingface.co/intfloat/multilingual-e5-large-instruct,embedding
pending,False,discovery_reference,https://docs.vllm.ai/en/v0.9.2/models/supported_models.html,,not_tested,BAAI/bge-m3,baai,huggingface,https://huggingface.co/BAAI/bge-m3,embedding
pending,False,discovery_reference,https://huggingface.co/spaces/OALL/Open-Arabic-LLM-Leaderboard,,not_tested,ALLaM-AI/ALLaM-7B-Instruct-preview,allam-ai,huggingface,https://huggingface.co/ALLaM-AI/ALLaM-7B-Instruct-preview,arabic_generative
pending,False,discovery_reference,https://huggingface.co/spaces/OALL/Open-Arabic-LLM-Leaderboard,,not_tested,inceptionai/jais-family-30b-8k,inception,huggingface,https://huggingface.co/inceptionai/jais-family-30b-8k,arabic_generative
pending,False,discovery_reference,https://huggingface.co/spaces/OALL/Open-Arabic-LLM-Leaderboard,,not_tested,silma-ai/SILMA-9B-Instruct,silma-ai,huggingface,https://huggingface.co/silma-ai/SILMA-9B-Instruct,arabic_generative
pending,False,discovery_reference,https://huggingface.co/spaces/OALL/Open-Arabic-LLM-Leaderboard,,not_tested,silma-ai/SILMA-Kashif-2B-Instruct-v1.0,silma-ai,huggingface,https://huggingface.co/silma-ai/SILMA-Kashif-2B-Instruct-v1.0,arabic_generative
pending,False,discovery_reference,https://huggingface.co/spaces/OALL/Open-Arabic-LLM-Leaderboard,,not_tested,MBZUAI-Paris/Nile-Chat-12B,mbzuai-paris,huggingface,https://huggingface.co/MBZUAI-Paris/Nile-Chat-12B,arabic_generative
""".strip()

def parse_models_from_csv(csv_str: str) -> list[dict]:
    reader = csv.DictReader(io.StringIO(csv_str))
    models = []
    for r in reader:
        rec = {
            "model_id": r["model_id"],
            "provider_id": r["provider_id"],
            "source": r["source"],
            "source_url": r["source_url"],
            "task_group": r["task_group"],
            "evidence_url": r["evidence_url"],
            "evidence_level": r["evidence_level"],
            "artifact_verification": r["artifact_verification"],
            "localpilot_compatibility": r["localpilot_compatibility"],
            "enabled_for_install": r["enabled_for_install"].lower() == "true",
        }
        if r.get("license"):
            rec["license"] = r["license"]
        models.append(rec)
    return models

MODELS_STAGING_RECORDS = parse_models_from_csv(MODELS_STAGING_CSV)

MODELS_STAGING = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "snapshot_requested": "2026-10-20",
    "package_type": "staging_candidates_not_production_catalog",
    "exhaustive": False,
    "full_existing_catalog_deduplication_completed": False,
    "total_count": len(MODELS_STAGING_RECORDS),
    "records": MODELS_STAGING_RECORDS
}

README_CONTENT = """# LocalPilot Catalog Expansion — Research Staging Package
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
"""

def main():
    print("Generating LocalPilot Catalog Expansion Staging Package...")
    
    for sdir in STAGING_DIRS:
        sdir.mkdir(parents=True, exist_ok=True)
        
        # 1. candidates_developers.json
        dev_path = sdir / "candidates_developers.json"
        dev_path.write_text(json.dumps(DEVELOPERS_STAGING, indent=2))
        
        # 2. candidates_providers.json
        prov_path = sdir / "candidates_providers.json"
        prov_path.write_text(json.dumps(PROVIDERS_STAGING, indent=2))
        
        # 3. candidates_models.json
        models_json_path = sdir / "candidates_models.json"
        models_json_path.write_text(json.dumps(MODELS_STAGING, indent=2))
        
        # 4. candidates_models.csv
        models_csv_path = sdir / "candidates_models.csv"
        models_csv_path.write_text(MODELS_STAGING_CSV + "\n")
        
        # 5. README.md
        readme_path = sdir / "README.md"
        readme_path.write_text(README_CONTENT)
        
        # 6. Create ZIP package
        zip_path = sdir / "localpilot_staging_package_20261020.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(readme_path, arcname="README.md")
            zf.write(dev_path, arcname="candidates_developers.json")
            zf.write(prov_path, arcname="candidates_providers.json")
            zf.write(models_json_path, arcname="candidates_models.json")
            zf.write(models_csv_path, arcname="candidates_models.csv")
            
        print(f"✓ Created staging package in: {sdir}")
        print(f"  - candidates_developers.json ({len(DEVELOPERS_STAGING['records'])} records)")
        print(f"  - candidates_providers.json ({len(PROVIDERS_STAGING['records'])} records)")
        print(f"  - candidates_models.json ({len(MODELS_STAGING['records'])} records)")
        print(f"  - candidates_models.csv ({len(MODELS_STAGING_CSV.splitlines())} lines)")
        print(f"  - README.md")
        print(f"  - localpilot_staging_package_20261020.zip ({zip_path.stat().st_size} bytes)")

if __name__ == "__main__":
    main()
