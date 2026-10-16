"""Local files and folders source adapter."""

from __future__ import annotations
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from localpilot.domain.models import (
    ConfidenceLevel,
    ExecutionMode,
    NormalizedCandidate,
    TaskCategory,
    VerificationStage,
)
from localpilot.sources.base import BaseSourceAdapter, SourceCapabilities


class LocalFilesSourceAdapter(BaseSourceAdapter):
    """Inspects local GGUF models on disk."""

    def get_source_name(self) -> str:
        return "local"

    def get_capabilities(self) -> SourceCapabilities:
        return SourceCapabilities(
            search=True,
            recent_discovery=False,
            metadata_inspection=True,
            authentication=False,
            download=False,
            exact_version_identity=True,
            update_checks=False,
            integrity_verification=True,
        )

    def scan_directory(self, directory: Path | str) -> List[NormalizedCandidate]:
        candidates: List[NormalizedCandidate] = []
        p = Path(directory).resolve()
        if not p.exists() or not p.is_dir():
            return candidates

        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        for item in p.glob("*.gguf"):
            if item.is_file():
                sz = item.stat().st_size
                name = item.stem
                cand = NormalizedCandidate(
                    candidate_id=f"local-{item.name}",
                    model_id=f"local/{item.name}",
                    name=f"Local: {name}",
                    source="local",
                    revision="local",
                    artifact_id=item.name,
                    architecture="transformer",
                    family=name,
                    tasks=[TaskCategory.CHAT],
                    languages=["en"],
                    format="gguf",
                    total_bytes=sz,
                    files=[str(item)],
                    runtime="llamacpp",
                    execution_mode=ExecutionMode.AUTO,
                    advertised_max_context=8192,
                    usable_context=4096,
                    target_context=4096,
                    metadata_fetched_at=now_iso,
                    requires_auth=False,
                    estimated_ram_bytes=int(sz * 1.3),
                    estimated_vram_bytes=int(sz * 1.1),
                    estimated_disk_bytes=sz,
                    confidence=ConfidenceLevel.ESTIMATED,
                    verification_stage=VerificationStage.SUPPORTED,
                    provenance={"path": str(item)},
                )
                candidates.append(cand)
        return candidates

    async def search(
        self,
        query: str = "",
        task: Optional[TaskCategory] = None,
        limit: int = 20,
    ) -> List[NormalizedCandidate]:
        return []

    async def get_candidate_details(self, model_id: str, revision: Optional[str] = None) -> Optional[NormalizedCandidate]:
        return None
