"""Model source adapter base definitions and capability flags (Section 9.1)."""

from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from pydantic import BaseModel

from localpilot.domain.models import NormalizedCandidate, TaskCategory


class SourceCapabilities(BaseModel):
    search: bool = True
    recent_discovery: bool = True
    metadata_inspection: bool = True
    authentication: bool = False
    download: bool = True
    exact_version_identity: bool = True
    update_checks: bool = True
    integrity_verification: bool = True


class BaseSourceAdapter(ABC):
    """Abstract adapter for fetching candidates and metadata from a model source."""

    @abstractmethod
    def get_source_name(self) -> str:
        pass

    @abstractmethod
    def get_capabilities(self) -> SourceCapabilities:
        pass

    @abstractmethod
    async def search(
        self,
        query: str = "",
        task: Optional[TaskCategory] = None,
        limit: int = 20,
    ) -> List[NormalizedCandidate]:
        pass

    @abstractmethod
    async def get_candidate_details(self, model_id: str, revision: Optional[str] = None) -> Optional[NormalizedCandidate]:
        pass
