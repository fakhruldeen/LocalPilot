"""Ollama Library source adapter."""

from __future__ import annotations
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
from localpilot.sources.base import BaseSourceAdapter, SourceCapabilities
from localpilot.sources.seed_catalog import SEED_MODELS


class OllamaLibrarySourceAdapter(BaseSourceAdapter):
    """Adapter for inspecting and discovering models in Ollama Library."""

    def get_source_name(self) -> str:
        return "ollama"

    def get_capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            search=True,
            recent_discovery=False,  # Ollama has no global recent list API
            metadata_inspection=True,
            authentication=False,
            download=True,
            exact_version_identity=True,
            update_checks=True,
            integrity_verification=True,
        )

    def resolve_artifact(self, candidate: NormalizedCandidate) -> ResolvedArtifact:
        """Resolves artifact acquisition as a service-managed pull."""
        return ResolvedArtifact(
            source="ollama",
            model_id=candidate.model_id,
            artifact_id=candidate.artifact_id,
            revision=candidate.revision or "latest",
            filename=f"{candidate.model_id.replace(':', '_')}.json",
            download_url=None,
            expected_bytes=candidate.total_bytes,
            expected_sha256=candidate.sha256,
            acquisition_mode="service_pull",
        )

    async def search(
        self,
        query: str = "",
        task: Optional[TaskCategory] = None,
        limit: int = 20,
    ) -> List[NormalizedCandidate]:
        # Return matching seed models for Ollama
        results = [m for m in SEED_MODELS if m.source == "ollama"]
        if query:
            q = query.lower()
            results = [m for m in results if q in m.model_id.lower() or q in m.name.lower()]
        return results[:limit]

    async def get_candidate_details(self, model_id: str, revision: Optional[str] = None) -> Optional[NormalizedCandidate]:
        for m in SEED_MODELS:
            if m.source == "ollama" and m.model_id == model_id:
                return m
        return None
