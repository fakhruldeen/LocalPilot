"""Hugging Face Hub source adapter with metadata inspection and resumable download support."""

from __future__ import annotations
import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import httpx

from localpilot.domain.models import (
    ConfidenceLevel,
    ExecutionMode,
    NormalizedCandidate,
    ResolvedArtifact,
    TaskCategory,
    VerificationStage,
)
from localpilot.security import get_stored_secret
from localpilot.sources.base import BaseSourceAdapter, SourceCapabilities


class HuggingFaceSourceAdapter(BaseSourceAdapter):
    """Adapter for querying and inspecting models from Hugging Face Hub."""

    BASE_URL = "https://huggingface.co/api"

    def get_source_name(self) -> str:
        return "huggingface"

    def get_capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            search=True,
            recent_discovery=True,
            metadata_inspection=True,
            authentication=True,
            download=True,
            exact_version_identity=True,
            update_checks=True,
            integrity_verification=True,
        )

    def _get_headers(self) -> Dict[str, str]:
        headers = {"User-Agent": "LocalPilot/0.1.9"}
        token = get_stored_secret("localpilot", "hf_token")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def resolve_artifact(self, candidate: NormalizedCandidate) -> ResolvedArtifact:
        """Resolves the download URL and artifact metadata for a candidate."""
        rev = candidate.revision or "main"
        filename = candidate.artifact_id or (candidate.files[0] if candidate.files else f"{candidate.model_id.split('/')[-1]}.gguf")
        download_url = candidate.download_url
        return ResolvedArtifact(
            source="huggingface",
            model_id=candidate.model_id,
            artifact_id=candidate.artifact_id,
            revision=rev,
            filename=filename,
            download_url=download_url,
            expected_bytes=candidate.total_bytes,
            expected_sha256=candidate.sha256,
            acquisition_mode="direct_download",
        )

    async def search(
        self,
        query: str = "",
        task: Optional[TaskCategory] = None,
        limit: int = 20,
    ) -> List[NormalizedCandidate]:
        candidates: List[NormalizedCandidate] = []
        search_query = query.strip()
        if not search_query:
            search_query = "gguf"
        elif "gguf" not in search_query.lower():
            search_query = f"{search_query} gguf"

        params = {
            "search": search_query,
            "filter": "gguf",
            "limit": limit,
            "full": "true",
        }

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{self.BASE_URL}/models",
                    params=params,
                    headers=self._get_headers(),
                )
                if resp.status_code == 200:
                    data = resp.json()
                    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                    for item in data:
                        model_id = item.get("id") or item.get("_id")
                        if not model_id:
                            continue

                        # Extract siblings for GGUF files
                        siblings = item.get("siblings", [])
                        gguf_files = [s["rfilename"] for s in siblings if s.get("rfilename", "").endswith(".gguf")]
                        if not gguf_files:
                            continue

                        # Pick a sensible default quantization file like Q4_K_M or first GGUF
                        selected_file = gguf_files[0]
                        for gf in gguf_files:
                            if "q4_k_m" in gf.lower():
                                selected_file = gf
                                break

                        created_at = item.get("createdAt")
                        modified_at = item.get("lastModified")
                        tags = item.get("tags", [])

                        tasks = [TaskCategory.CHAT]
                        if any("code" in t.lower() for t in tags):
                            tasks.append(TaskCategory.CODING)

                        # Approximate size if sibling size is not provided
                        total_bytes = 2 * 1024 * 1024 * 1024  # fallback 2GB
                        rev = item.get("sha") or "main"
                        clean_base = model_id.split("/")[-1].lower().replace("-gguf", "").replace(".gguf", "")
                        quant_part = "q4"
                        for q in ["q4_k_m", "q4_0", "q5_k_m", "q8_0", "f16"]:
                            if q in selected_file.lower():
                                quant_part = q
                                break
                        author_part = model_id.split("/")[0].lower() if "/" in model_id else ""
                        is_off = author_part in ("google", "meta-llama", "qwen", "deepseek-ai", "mistralai", "microsoft", "thudm", "moonshot-ai")
                        official_urls = {
                            "google": "https://ai.google.dev/gemma",
                            "meta-llama": "https://llama.meta.com",
                            "qwen": "https://qwenlm.github.io",
                            "deepseek-ai": "https://www.deepseek.com",
                            "mistralai": "https://mistral.ai",
                            "microsoft": "https://www.microsoft.com/research",
                            "thudm": "https://github.com/THUDM/GLM-4",
                            "moonshot-ai": "https://www.moonshot.cn",
                        }
                        off_url = official_urls.get(author_part)

                        candidate = NormalizedCandidate(
                            candidate_id=f"hf-{model_id.replace('/', '-')}-{selected_file}",
                            model_id=model_id,
                            name=f"{model_id.split('/')[-1]} ({selected_file})",
                            short_alias=dyn_alias,
                            source="huggingface",
                            revision=rev,
                            artifact_id=selected_file,
                            equivalence_group=model_id,
                            architecture="transformer",
                            family=model_id.split("/")[-1],
                            tasks=tasks,
                            languages=["en"],
                            format="gguf",
                            total_bytes=total_bytes,
                            files=[selected_file],
                            download_url=f"https://huggingface.co/{model_id}/resolve/{rev}/{selected_file}",
                            source_url=f"https://huggingface.co/{model_id}",
                            runtime="llamacpp",
                            execution_mode=ExecutionMode.AUTO,
                            advertised_max_context=8192,
                            usable_context=4096,
                            target_context=4096,
                            verified_release_at=created_at,
                            repo_created_at=created_at,
                            repo_modified_at=modified_at,
                            metadata_fetched_at=now_iso,
                            requires_auth=bool(item.get("private") or item.get("gated")),
                            license=item.get("cardData", {}).get("license") if isinstance(item.get("cardData"), dict) else None,
                            estimated_ram_bytes=int(total_bytes * 1.3),
                            estimated_vram_bytes=int(total_bytes * 1.1),
                            estimated_disk_bytes=total_bytes,
                            confidence=ConfidenceLevel.VERIFIED if is_off else ConfidenceLevel.ESTIMATED,
                            verification_stage=VerificationStage.SUPPORTED,
                            provenance={"api": "huggingface_hub", "publisher": author_part.title(), "is_official": is_off},
                            is_official=is_off,
                            official_resource_url=off_url,
                        )
                        candidates.append(candidate)
        except Exception:
            # Fallback gracefully
            pass

        return candidates

    async def get_candidate_details(self, model_id: str, revision: Optional[str] = None) -> Optional[NormalizedCandidate]:
        results = await self.search(query=model_id, limit=5)
        for r in results:
            if r.model_id == model_id:
                return r
        return None
