"""Recommendation scoring engine implementing Section 13."""

from __future__ import annotations
import math
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple
from pydantic import BaseModel

from localpilot.domain.compatibility import CompatibilityCheckResult
from localpilot.domain.models import NormalizedCandidate


class ScoreResult(BaseModel):
    total_score: float
    newness_score: float
    download_score: float
    context_score: float
    headroom_score: float
    explanation: str
    is_date_estimated: bool = False


class ScoringPolicy:
    """Computes explainable suitability scores according to Section 13."""

    POLICY_VERSION = "scoring-v1"

    # Default weights: 40% newness, 25% download size, 20% context, 15% headroom
    WEIGHT_NEWNESS = 0.40
    WEIGHT_DOWNLOAD = 0.25
    WEIGHT_CONTEXT = 0.20
    WEIGHT_HEADROOM = 0.15

    # Download size anchors: 500 MB to 64 GB
    B_MIN = 500 * 1024 * 1024
    B_MAX = 64 * 1024 * 1024 * 1024

    def calculate_newness(self, verified_release_at: Optional[str]) -> Tuple[float, bool]:
        """N = 2^(-d / 180). Default d=365 if unknown."""
        if not verified_release_at:
            # Conservative default: 365 days ago
            d = 365.0
            is_estimated = True
        else:
            try:
                dt = datetime.fromisoformat(verified_release_at.replace("Z", "+00:00"))
                now = datetime.now(timezone.utc)
                delta_days = (now - dt).total_seconds() / 86400.0
                d = max(0.0, delta_days)
                is_estimated = False
            except Exception:
                d = 365.0
                is_estimated = True

        n = math.pow(2.0, -d / 180.0)
        return max(0.0, min(1.0, n)), is_estimated

    def calculate_download_size(self, total_bytes: int) -> float:
        """Z = 1 - clamp((log(B) - log(Bmin)) / (log(Bmax) - log(Bmin)), 0, 1)."""
        b = max(1, total_bytes)
        log_b = math.log(b)
        log_min = math.log(self.B_MIN)
        log_max = math.log(self.B_MAX)

        fraction = (log_b - log_min) / (log_max - log_min)
        clamped = max(0.0, min(1.0, fraction))
        return 1.0 - clamped

    def calculate_context(self, usable_context: int, target_context: int) -> float:
        """C = log(1 + min(usable, target)) / log(1 + target)."""
        c_usable = max(1, usable_context)
        c_target = max(1, target_context)
        num = math.log(1.0 + min(c_usable, c_target))
        den = math.log(1.0 + c_target)
        return max(0.0, min(1.0, num / den))

    def calculate_headroom(self, compatibility: CompatibilityCheckResult) -> float:
        """H = 1 - U, where U is highest projected utilization."""
        peak_u = max(
            compatibility.projected_ram_utilization,
            compatibility.projected_vram_utilization,
            compatibility.projected_shared_utilization,
        )
        return max(0.0, min(1.0, 1.0 - peak_u))

    def score(
        self,
        candidate: NormalizedCandidate,
        compatibility: CompatibilityCheckResult,
    ) -> ScoreResult:
        n, date_estimated = self.calculate_newness(candidate.verified_release_at)
        z = self.calculate_download_size(candidate.total_bytes)
        c = self.calculate_context(candidate.usable_context, candidate.target_context)
        h = self.calculate_headroom(compatibility)

        total = 100.0 * (
            self.WEIGHT_NEWNESS * n
            + self.WEIGHT_DOWNLOAD * z
            + self.WEIGHT_CONTEXT * c
            + self.WEIGHT_HEADROOM * h
        )
        total = round(total, 1)

        size_mb = candidate.total_bytes / (1024 * 1024)
        explanation = (
            f"Score {total:.1f}/100 based on recent release ({n*100:.0f}%), "
            f"download size of {size_mb:.0f}MB ({z*100:.0f}%), "
            f"context window of {candidate.usable_context} ({c*100:.0f}%), "
            f"and memory headroom ({h*100:.0f}%)."
        )

        return ScoreResult(
            total_score=total,
            newness_score=round(n, 3),
            download_score=round(z, 3),
            context_score=round(c, 3),
            headroom_score=round(h, 3),
            explanation=explanation,
            is_date_estimated=date_estimated,
        )

    def sort_candidates(
        self,
        items: List[Tuple[NormalizedCandidate, ScoreResult, CompatibilityCheckResult]],
    ) -> List[Tuple[NormalizedCandidate, ScoreResult, CompatibilityCheckResult]]:
        """Deterministic tie-breaking: score desc -> is_official desc -> confidence desc -> size asc -> ID asc."""
        confidence_order = {"verified": 3, "estimated": 2, "unknown": 1}

        def sort_key(item: Tuple[NormalizedCandidate, ScoreResult, CompatibilityCheckResult]):
            cand, scr, _ = item
            is_off = 1 if getattr(cand, "is_official", False) else 0
            conf_val = confidence_order.get(cand.confidence.value, 0)
            return (-scr.total_score, -is_off, -conf_val, cand.total_bytes, cand.candidate_id)

        return sorted(items, key=sort_key)
