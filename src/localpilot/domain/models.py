"""Normalized candidate models and domain value objects."""

from __future__ import annotations
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class VerificationStage(str, Enum):
    SUPPORTED = "supported"              # LocalPilot supports this artifact/runtime/platform
    EXPECTED_TO_FIT = "expected_to_fit"  # Passed estimated resource check
    DOWNLOADED_PREPARED = "downloaded_prepared"  # Model downloaded and prepared on disk
    TESTED_HERE = "tested_here"          # Verified inference completed on this computer


class ConfidenceLevel(str, Enum):
    VERIFIED = "verified"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class TaskCategory(str, Enum):
    CHAT = "chat"
    WRITING = "writing"
    CODING = "coding"
    REASONING = "reasoning"
    VISION = "vision"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    EMBEDDING = "embedding"


class ExecutionMode(str, Enum):
    AUTO = "automatic"
    CPU = "cpu"
    GPU = "gpu"
    CPU_GPU = "cpu_gpu"


from localpilot.domain.capabilities import (
    DeploymentMethod,
    InferenceBackendId,
    ManagedServiceId,
)


class ResolvedArtifact(BaseModel):
    """Explicit artifact resolution contract matching Section 9.1."""
    source: str
    model_id: str
    artifact_id: str
    revision: str = "main"
    filename: str
    download_url: Optional[str] = None
    expected_bytes: int = 0
    expected_sha256: Optional[str] = None
    acquisition_mode: str = "direct_download"  # "direct_download", "service_pull", "local_file"


class NormalizedCandidate(BaseModel):
    """Normalized runnable candidate configuration matching Section 11."""
    candidate_id: str
    model_id: str
    name: str
    short_alias: Optional[str] = None
    source: str                          # "huggingface", "ollama", "local"
    revision: Optional[str] = None
    artifact_id: str
    equivalence_group: Optional[str] = None
    architecture: str                    # "llama", "qwen2", "phi3", etc.
    family: str                          # "Llama 3", "Qwen 2.5", etc.
    tasks: List[TaskCategory] = Field(default_factory=lambda: [TaskCategory.CHAT])
    languages: List[str] = Field(default_factory=lambda: ["en"])
    parameter_count: Optional[str] = None  # e.g. "3B", "7B", "14B"

    # Files, format and acquisition URLs
    format: str                          # "gguf", "ollama_blob"
    total_bytes: int
    expected_bytes: Optional[int] = None
    files: List[str] = Field(default_factory=list)
    sha256: Optional[str] = None
    download_url: Optional[str] = None
    source_url: Optional[str] = None

    # Runtime & Execution (backward compatible + 3-dimension extensions)
    runtime: str                         # "ollama", "llamacpp" (backward compatible alias)
    preferred_backend: Optional[InferenceBackendId] = None
    preferred_service: Optional[ManagedServiceId] = None
    supported_deployments: List[DeploymentMethod] = Field(
        default_factory=lambda: [DeploymentMethod.LOCAL_API_SERVICE, DeploymentMethod.DIRECT_WORKER]
    )
    min_runtime_version: Optional[str] = None
    execution_mode: ExecutionMode = ExecutionMode.AUTO
    gpu_layers: Optional[int] = None

    # Context
    advertised_max_context: int = 4096
    usable_context: int = 4096
    target_context: int = 4096

    # Timestamps
    verified_release_at: Optional[str] = None
    repo_created_at: Optional[str] = None
    repo_modified_at: Optional[str] = None
    artifact_published_at: Optional[str] = None
    metadata_fetched_at: str

    # Access
    requires_auth: bool = False
    requires_terms_acceptance: bool = False
    license: Optional[str] = None

    # Resource Estimates
    estimated_ram_bytes: int
    estimated_vram_bytes: int = 0
    estimated_disk_bytes: int
    temp_disk_bytes: int = 0

    # Verification, provenance, and official status
    confidence: ConfidenceLevel = ConfidenceLevel.ESTIMATED
    verification_stage: VerificationStage = VerificationStage.SUPPORTED
    provenance: Dict[str, Any] = Field(default_factory=dict)
    is_official: bool = False
    official_resource_url: Optional[str] = None

    # Attribution & Provenance Separation (Base Author vs Artifact Publisher)
    base_model_provider_id: Optional[str] = None
    base_model_id: Optional[str] = None
    artifact_publisher_id: Optional[str] = None
    artifact_repository: Optional[str] = None
    artifact_is_official: Optional[bool] = None
    enabled_for_install: bool = True
    evidence_url: Optional[str] = None
    evidence_level: Optional[str] = None
    artifact_verification: Optional[str] = None
    localpilot_compatibility: Optional[str] = None

    @property
    def parameter_count_approx_b(self) -> float:
        """Parses parameter_count (e.g. '1.5B', '7B') to float billions, default 7.0."""
        if not self.parameter_count:
            return 7.0
        val = self.parameter_count.upper().replace("B", "").replace("M", "").strip()
        try:
            num = float(val)
            if "M" in self.parameter_count.upper():
                return num / 1000.0
            return num
        except ValueError:
            return 7.0

    @property
    def display_alias(self) -> str:
        """User-friendly short alias for concise CLI commands and display."""
        if self.short_alias:
            return self.short_alias
        base = self.model_id.split("/")[-1].lower().replace("-gguf", "").replace(".gguf", "")
        # Pick quantization suffix if present
        for q in ["q4_k_m", "q4_0", "q5_k_m", "q8_0", "f16"]:
            if self.artifact_id and q in self.artifact_id.lower():
                return f"{base}:{q}"
        return base

    @property
    def capability_tags(self) -> List[str]:
        """Normalized string capability identifiers for filtering (e.g. ['coding', 'writing', 'image', 'audio', 'video'])."""
        caps = set()
        for t in self.tasks:
            val = t.value if hasattr(t, "value") else str(t).lower()
            if val in ("chat", "writing"):
                caps.add("writing")
            elif val in ("coding", "code"):
                caps.add("coding")
            elif val in ("vision", "image"):
                caps.add("image")
            elif val in ("audio", "voice", "speech"):
                caps.add("audio")
            elif val == "video":
                caps.add("video")
            elif val in ("reasoning", "math"):
                caps.add("reasoning")
            elif val in ("embedding", "retrieval"):
                caps.add("embedding")
        # Smart fallback inference from name, model_id, architecture, and family
        text = f"{self.name} {self.model_id} {self.family} {self.architecture} {self.short_alias or ''}".lower()
        if any(w in text for w in ("code", "coder", "coding", "starcoder", "deepseek-coder", "dev", "python")):
            caps.add("coding")
        if any(w in text for w in ("vision", "image", "vl", "visual", "ocr", "multimodal", "minicpm-v", "llava")):
            caps.add("image")
        if any(w in text for w in ("video", "videomae")):
            caps.add("video")
        if any(w in text for w in ("audio", "voice", "speech", "whisper", "tts", "asr", "stt")):
            caps.add("audio")
        if any(w in text for w in ("reasoning", "reason", "r1", "math", "thinking", "think", "logic", "qwq")):
            caps.add("reasoning")
        if any(w in text for w in ("embed", "embedding", "bert", "bge", "nomic-embed", "arctic-embed")):
            caps.add("embedding")
        if not caps:
            caps.add("writing")
        return sorted(list(caps))

    @property
    def release_date_display(self) -> str:
        """Formatted release date (YYYY-MM-DD) or 'Recent'."""
        dt = self.verified_release_at or self.repo_created_at or self.artifact_published_at
        if dt:
            clean = str(dt).strip().replace("T", " ")
            if len(clean) >= 10:
                return clean[:10]
        return "Recent"

    @property
    def capabilities_display_text(self) -> str:
        """Formatted display string for UI headers, details and summaries."""
        labels = []
        caps = set(self.capability_tags)
        display_map = [
            ("coding", "💻 Coding"),
            ("writing", "✍️ Writing"),
            ("image", "👁️ Image & Vision"),
            ("audio", "🎙️ Audio & Voice"),
            ("video", "🎬 Video"),
            ("reasoning", "🧠 Reasoning & Math"),
            ("embedding", "🔍 Search & Embedding"),
        ]
        for key, label in display_map:
            if key in caps:
                labels.append(label)
        if not labels:
            return "💬 General AI"
        return " • ".join(labels)

    @property
    def capabilities_table_badge(self) -> str:
        """Concise labels for DataTable columns."""
        badges = []
        caps = set(self.capability_tags)
        display_map = [
            ("coding", "Coding"),
            ("writing", "Writing"),
            ("image", "Image"),
            ("audio", "Audio"),
            ("video", "Video"),
            ("reasoning", "Reasoning"),
        ]
        for key, label in display_map:
            if key in caps:
                badges.append(label)
        if not badges:
            return "General"
        return ", ".join(badges)

    @property
    def why_capabilities_summary(self) -> str:
        """Natural language rationale summarizing the model's specialized categories."""
        parts = []
        caps = set(self.capability_tags)
        if "coding" in caps:
            parts.append("Software Engineering & Code Generation")
        if "writing" in caps:
            parts.append("Nuanced Text Writing & Dialogue")
        if "image" in caps:
            parts.append("Visual Image Analysis & OCR")
        if "audio" in caps:
            parts.append("High-accuracy Audio & Speech Recognition")
        if "video" in caps:
            parts.append("Video Sequence Comprehension")
        if "reasoning" in caps:
            parts.append("Deep Multi-step Reasoning & Mathematics")
        if not parts:
            return "General everyday assistance"
        if len(parts) == 1:
            return f"Specialized for {parts[0]}."
        elif len(parts) == 2:
            return f"Specialized for {parts[0]} and {parts[1]}."
        else:
            return f"Specialized for {', '.join(parts[:-1])}, and {parts[-1]}."

    @property
    def publisher_display(self) -> str:
        """Name of the model provider or creator lab (e.g. Meta, Google, NVIDIA, DeepSeek, etc.)."""
        # If separated attribution is explicitly provided
        if self.base_model_provider_id and self.artifact_publisher_id:
            base = self.base_model_provider_id.title()
            pub = self.artifact_publisher_id
            if base.lower() != pub.lower():
                return f"{base} (via {pub})"
            return base

        pub = self.provenance.get("publisher") if self.provenance else None
        if not pub:
            if "/" in self.model_id:
                prefix = self.model_id.split("/")[0].lower()
                prefix_map = {
                    "google": "Google",
                    "meta-llama": "Meta",
                    "meta": "Meta",
                    "microsoft": "Microsoft",
                    "mistralai": "Mistral AI",
                    "deepseek-ai": "DeepSeek AI",
                    "qwen": "Qwen Team",
                    "nvidia": "NVIDIA",
                    "cohere": "Cohere",
                    "cohereforai": "Cohere",
                    "allenai": "AllenAI",
                    "stabilityai": "Stability AI",
                    "snowflake": "Snowflake",
                    "01-ai": "01.AI",
                    "openbmb": "OpenBMB",
                    "upstage": "Upstage",
                    "bigcode": "BigCode",
                    "liquidai": "Liquid AI",
                    "minimax": "MiniMax",
                    "minimaxi": "MiniMax",
                    "openai": "OpenAI",
                    "gpt-oss": "OpenAI (GPT-OSS)",
                    "moonshotai": "Moonshot AI (Kimi)",
                    "moonshot-ai": "Moonshot AI (Kimi)",
                    "groq": "Groq",
                    "thudm": "Zhipu AI (GLM)",
                    "kt-ai": "KT (KLM)",
                    "sentence-transformers": "Sentence Transformers",
                }
                pub = prefix_map.get(prefix, self.model_id.split("/")[0])
            else:
                pub = self.source.title()
        return pub or "Community"

    @property
    def browser_url(self) -> str:
        """Returns the verified, canonical web URL to open in a browser for this model."""
        if self.source == "ollama":
            slug = self.short_alias or self.model_id.replace("ollama/", "")
            return f"https://ollama.com/library/{slug}"
        if self.source == "modelscope":
            slug = self.model_id.replace("modelscope/", "")
            return f"https://www.modelscope.cn/models/{slug}"

        # If explicit official resource URL is provided and valid, prioritize it
        if self.official_resource_url and self.official_resource_url.startswith("http"):
            return self.official_resource_url

        # Check source_url
        if self.source_url and self.source_url.startswith("http"):
            clean_url = self.source_url
            if clean_url.endswith("-GGUF") and "sentence-transformers" in clean_url:
                clean_url = clean_url[:-5]
            return clean_url

        # Direct Hugging Face repository URL
        if "/" in self.model_id:
            clean_id = self.model_id
            if clean_id.endswith("-GGUF") and "sentence-transformers" in clean_id:
                clean_id = clean_id[:-5]
            return f"https://huggingface.co/{clean_id}"

        return f"https://huggingface.co/models?search={self.model_id}"


class StopStatus(str, Enum):
    STOPPED = "stopped"
    UNLOADED_ONLY = "unloaded_only"
    DAEMON_STOPPED = "daemon_stopped"
    FAILED = "failed"
    PERMISSION_DENIED = "permission_denied"
    ALREADY_STOPPED = "already_stopped"
    UNKNOWN = "unknown"


class StopResult(BaseModel):
    target_instance_id: str
    model_id: Optional[str] = None
    request_succeeded: bool
    observed_outcome: StopStatus
    reason: Optional[str] = None
    remaining_state: Optional[str] = None

    def __bool__(self) -> bool:
        return self.request_succeeded


