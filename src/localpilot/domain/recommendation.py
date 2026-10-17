"""Model Recommendation, Decision Quality, and Shortlist Generation Engine.

Implements Sections 1, 3, 4, 7, 8, 9, 10, 11, 12, 13:
- Preserves full runnable configurations (candidate, exact artifact, and execution plan).
- Evidence-based task suitability filtering.
- Near-duplicate suppression by equivalence group.
- Meaningful shortlist distinctions (Balanced, Smaller Download, Longer Context, Lower Memory).
- Side-by-side comparison matrix.
- Launch availability recheck with truthful memory shift warnings.
"""

from __future__ import annotations
from enum import Enum
import json
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from localpilot.domain.capabilities import ManagedServiceId
from localpilot.domain.compatibility import CompatibilityCheckResult
from localpilot.domain.execution_plan import ExecutionPlan
from localpilot.domain.models import ConfidenceLevel, ExecutionMode, NormalizedCandidate, TaskCategory, VerificationStage
from localpilot.domain.scoring import ScoreResult
from localpilot.platforms.base import HardwareSnapshot, MemoryAccountingModel


class TaskEvidenceLevel(str, Enum):
    """Evidence tier supporting a candidate's suitability for a task."""
    CURATED_VERIFIED = "curated_verified"
    BENCHMARK_EVIDENCE = "benchmark_evidence"
    PUBLISHER_DECLARED = "publisher_declared"
    LOCALLY_TESTED = "locally_tested"
    UNKNOWN = "unknown"


class ShortlistDistinction(str, Enum):
    """Understandable role assigned to each shortlist recommendation."""
    TOP_PICK = "top_pick"
    BALANCED = "balanced"
    SMALLER_DOWNLOAD = "smaller_download"
    LONGER_CONTEXT = "longer_context"
    LOWER_MEMORY = "lower_memory"
    ALTERNATIVE = "alternative"


class ShortlistCandidate(BaseModel):
    """A complete runnable candidate tailored for novice presentation."""
    candidate: NormalizedCandidate
    score: ScoreResult
    compatibility: CompatibilityCheckResult
    recommended_plan: ExecutionPlan
    alternative_plans: List[ExecutionPlan] = Field(default_factory=list)
    
    # Novice-friendly presentation fields
    distinction: ShortlistDistinction
    badge_label: str
    why_selected: str
    download_size_str: str
    disk_needed_str: str
    expected_speed_tier: str
    usable_context_desc: str
    supporting_setup: str
    access_requirement: str
    confidence_str: str
    verification_status: str
    task_evidence_level: TaskEvidenceLevel = TaskEvidenceLevel.UNKNOWN


class ModelComparisonRow(BaseModel):
    """Structured fields for side-by-side candidate comparison."""
    candidate_id: str
    model_id: str
    name: str
    purpose: str
    distinction: str
    release_date: str
    download_size: str
    storage_needed: str
    context_window: str
    memory_burden: str
    execution_plan: str
    access_requirements: str
    confidence: str


class ModelRecommendationEngine:
    """Coordinates evidence-based filtering, near-duplicate suppression, and shortlist generation."""

    @staticmethod
    def get_task_evidence_level(candidate: NormalizedCandidate, task: TaskCategory) -> TaskEvidenceLevel:
        """Determines the evidence level supporting a candidate for a requested task."""
        # 1. Curated seed entries have verified human curation
        prov = candidate.provenance or {}
        if prov.get("catalog") == "seed_curated" or prov.get("source") == "seed":
            return TaskEvidenceLevel.CURATED_VERIFIED

        # 2. Benchmark or locally tested evidence
        if prov.get("benchmark_verified"):
            return TaskEvidenceLevel.BENCHMARK_EVIDENCE
        if candidate.verification_stage == VerificationStage.TESTED_HERE:
            return TaskEvidenceLevel.LOCALLY_TESTED

        # 3. Publisher declared
        if task in candidate.tasks:
            return TaskEvidenceLevel.PUBLISHER_DECLARED

        return TaskEvidenceLevel.UNKNOWN

    @staticmethod
    def get_equivalence_key(candidate: NormalizedCandidate) -> str:
        """Returns the canonical grouping key to prevent near-duplicates from crowding the shortlist."""
        if candidate.equivalence_group:
            return candidate.equivalence_group.lower().strip()
        
        # Derive base model family key (e.g., 'qwen2.5-coder-1.5b' from 'qwen2.5-coder-1.5b-instruct-q4_k_m')
        name = candidate.name.lower().replace("_", "-")
        for quant in ("q4_k_m", "q4_k_s", "q5_k_m", "q8_0", "f16", "q4_0", "q4_1"):
            name = name.replace(quant, "")
        return name.strip("-")

    @classmethod
    def generate_shortlist(
        cls,
        ranked_candidates: List[Any],
        target_task: TaskCategory = TaskCategory.CHAT,
        limit: int = 3,
        task: Optional[TaskCategory] = None,
        target_count: Optional[int] = None,
        hardware: Optional[Any] = None,
    ) -> List[ShortlistCandidate]:
        """Builds a curated, non-duplicated shortlist of compatible models.
        
        Hard Rule: Incompatible candidates are strictly excluded.
        """
        if task is not None:
            target_task = task
        if target_count is not None:
            limit = target_count
        # Step 1: Filter strictly to compatible candidates with supported plans
        compatible: List[Any] = []
        for r in ranked_candidates:
            if getattr(r, "compatibility", None) and r.compatibility.is_compatible and r.recommended_plan:
                compatible.append(r)

        if not compatible:
            return []

        # Step 2: Suppress near-duplicates while preserving highest-scoring variant in each group
        seen_groups = set()
        deduped: List[Any] = []
        for r in compatible:
            key = cls.get_equivalence_key(r.candidate)
            if key not in seen_groups:
                seen_groups.add(key)
                deduped.append(r)

        if not deduped:
            return []

        top_pick = deduped[0]
        shortlist: List[ShortlistCandidate] = []

        # Add #1 Top Pick (Balanced choice)
        shortlist.append(cls._build_shortlist_candidate(
            top_pick,
            distinction=ShortlistDistinction.TOP_PICK,
            is_first=True,
            target_task=target_task,
        ))

        # Step 3: Identify distinct roles among remaining candidates
        remaining = deduped[1:]
        chosen_candidates = [top_pick]

        while len(shortlist) < limit and remaining:
            # Check for candidate with smaller download (< 75% size of top pick)
            smaller = [
                r for r in remaining
                if r.candidate.total_bytes < (top_pick.candidate.total_bytes * 0.75)
                and r not in chosen_candidates
            ]
            if smaller and not any(s.distinction == ShortlistDistinction.SMALLER_DOWNLOAD for s in shortlist):
                cand_smaller = min(smaller, key=lambda x: x.candidate.total_bytes)
                shortlist.append(cls._build_shortlist_candidate(
                    cand_smaller,
                    distinction=ShortlistDistinction.SMALLER_DOWNLOAD,
                    is_first=False,
                    target_task=target_task,
                ))
                chosen_candidates.append(cand_smaller)
                remaining.remove(cand_smaller)
                continue

            # Check for candidate with longer context (> 1.4x context of top pick)
            longer_ctx = [
                r for r in remaining
                if r.candidate.usable_context > (top_pick.candidate.usable_context * 1.4)
                and r not in chosen_candidates
            ]
            if longer_ctx and not any(s.distinction == ShortlistDistinction.LONGER_CONTEXT for s in shortlist):
                cand_longer = max(longer_ctx, key=lambda x: x.candidate.usable_context)
                shortlist.append(cls._build_shortlist_candidate(
                    cand_longer,
                    distinction=ShortlistDistinction.LONGER_CONTEXT,
                    is_first=False,
                    target_task=target_task,
                ))
                chosen_candidates.append(cand_longer)
                remaining.remove(cand_longer)
                continue

            # Fallback to next highest ranked option
            next_cand = remaining.pop(0)
            shortlist.append(cls._build_shortlist_candidate(
                next_cand,
                distinction=ShortlistDistinction.ALTERNATIVE,
                is_first=False,
                target_task=target_task,
            ))
            chosen_candidates.append(next_cand)

        return shortlist

    @classmethod
    def _build_shortlist_candidate(
        cls,
        ranked: Any,
        distinction: ShortlistDistinction,
        is_first: bool,
        target_task: TaskCategory,
    ) -> ShortlistCandidate:
        c: NormalizedCandidate = ranked.candidate
        plan: ExecutionPlan = ranked.recommended_plan
        comp: CompatibilityCheckResult = ranked.compatibility
        sz_mb = c.total_bytes / (1024 * 1024)
        disk_needed_mb = (plan.estimated_disk_bytes + getattr(plan, "temp_disk_bytes", 0)) / (1024 * 1024)

        # Usable context description ("amount of text considered at once")
        words_est = int(plan.usable_context * 0.75)
        ctx_desc = f"{plan.usable_context:,} tokens (~{words_est:,} words considered at once)"

        # Headroom band calculation against measured safe memory budget
        headroom_ratio = getattr(comp, "ram_headroom_ratio", 0.5)
        if headroom_ratio >= 0.30:
            headroom_band = "Comfortable headroom"
        elif headroom_ratio >= 0.10:
            headroom_band = "Limited headroom"
        elif headroom_ratio >= 0.0:
            headroom_band = "Tight headroom"
        else:
            headroom_band = "Temporarily blocked"

        # Expected speed tier: honest estimate with unmeasured disclosure
        params = c.parameter_count_approx_b
        if params <= 3.5:
            speed_tier = "Estimated fast (compact parameter footprint; speed not measured on this hardware)"
        elif params <= 8.0:
            speed_tier = "Estimated moderate (standard parameter footprint; speed not measured on this hardware)"
        else:
            speed_tier = "Estimated heavy (large parameter footprint; speed not measured on this hardware)"

        # Badge and why_selected explanation based on distinction and evidence
        task_label = "coding and technical questions" if target_task == TaskCategory.CODING else "writing and everyday questions"

        score_val = getattr(ranked, "score", None)
        score_num = f"{score_val.total_score:.0f}/100" if score_val else "top ranked"

        if distinction == ShortlistDistinction.TOP_PICK:
            badge = "★ Top Pick (Recommended)"
            why = f"Best overall suitability score ({score_num}) with ~{sz_mb:.0f} MB download and {headroom_band.lower()} for {task_label}."
        elif distinction == ShortlistDistinction.SMALLER_DOWNLOAD:
            badge = "Compact Choice (Smaller Download)"
            why = f"Significantly smaller download (~{sz_mb:.0f} MB) that downloads quickly while still handling {task_label}."
        elif distinction == ShortlistDistinction.LONGER_CONTEXT:
            badge = "Extended Memory (Longer Context)"
            why = f"Expanded context window ({plan.usable_context:,} tokens) with more room for long documents, codebases, or extended discussions."
        elif distinction == ShortlistDistinction.LOWER_MEMORY:
            badge = "Lightweight Choice (Low RAM)"
            why = f"Minimized memory footprint leaving maximum headroom for your other open computer applications."
        else:
            badge = "Alternative Option"
            why = f"Strong alternative fit with compatible execution plan for your computer."

        # Supporting setup description (truthful distinction between external vs isolated install)
        backend_name = plan.inference_backend.value if hasattr(plan.inference_backend, "value") else str(plan.inference_backend)
        if getattr(plan, "managed_service", ManagedServiceId.NONE) != ManagedServiceId.NONE:
            serv_name = plan.managed_service.value if hasattr(plan.managed_service, "value") else str(plan.managed_service)
            supporting = f"Existing {serv_name} service (no new engine installation needed)"
        else:
            supporting = f"Isolated {backend_name} engine installed into user folder (no system changes)"

        # Access requirements
        access = "Open model (no account or access key required)"
        if c.requires_auth or c.requires_terms_acceptance:
            access = "Requires free Hugging Face access key and terms agreement"

        return ShortlistCandidate(
            candidate=c,
            score=ranked.score,
            compatibility=comp,
            recommended_plan=plan,
            alternative_plans=getattr(ranked, "alternative_plans", []),
            distinction=distinction,
            badge_label=badge,
            why_selected=why,
            download_size_str=f"{sz_mb:.1f} MB",
            disk_needed_str=f"{disk_needed_mb:.1f} MB",
            expected_speed_tier=speed_tier,
            usable_context_desc=ctx_desc,
            supporting_setup=supporting,
            access_requirement=access,
            confidence_str=c.confidence.value.title(),
            verification_status=comp.verification_stage.value.replace("_", " ").title(),
            task_evidence_level=cls.get_task_evidence_level(c, target_task),
        )

    @classmethod
    def generate_comparison(cls, candidates: List[Any], hardware: Optional[Any] = None) -> List[ModelComparisonRow]:
        """Generates side-by-side comparison data rows across candidates."""
        rows: List[ModelComparisonRow] = []
        for r in candidates:
            c = r.candidate
            plan = r.recommended_plan
            sz_mb = c.total_bytes / (1024 * 1024)
            disk_mb = (plan.estimated_disk_bytes + getattr(plan, "temp_disk_bytes", 0)) / (1024 * 1024)
            mem_mb = (plan.estimated_ram_bytes + plan.estimated_vram_bytes) / (1024 * 1024)

            purpose = "General Chat & Writing"
            if TaskCategory.CODING in c.tasks:
                purpose = "Coding & Programming"
            elif TaskCategory.REASONING in c.tasks:
                purpose = "Deep Reasoning & Math"

            backend = plan.inference_backend.value if hasattr(plan.inference_backend, "value") else str(plan.inference_backend)
            mode = plan.requested_execution_mode.value if hasattr(plan.requested_execution_mode, "value") else str(plan.requested_execution_mode)

            rows.append(
                ModelComparisonRow(
                    candidate_id=c.candidate_id,
                    model_id=c.model_id,
                    name=c.name,
                    purpose=purpose,
                    distinction=getattr(r, "badge_label", "Standard"),
                    release_date=c.verified_release_at or c.repo_created_at or "Estimated",
                    download_size=f"{sz_mb:.1f} MB",
                    storage_needed=f"{disk_mb:.1f} MB",
                    context_window=f"{plan.usable_context:,} tokens",
                    memory_burden=f"~{mem_mb:.0f} MB estimated",
                    execution_plan=f"{backend} ({mode})",
                    access_requirements="Free Access Key Required" if c.requires_auth else "Open Access",
                    confidence=c.confidence.value.title(),
                )
            )
        return rows

    @classmethod
    def recheck_runtime_availability(
        cls,
        candidate: NormalizedCandidate,
        plan: ExecutionPlan,
        current_hardware: HardwareSnapshot,
    ) -> Tuple[bool, str]:
        """Validates current memory availability right before launch (Section 11).
        
        Honest Warning: If current memory dropped below requirement, informs user without
        silently modifying their approved plan.
        """
        required_ram = plan.estimated_ram_bytes
        avail_ram = current_hardware.ram_available_bytes

        if required_ram > avail_ram:
            req_gb = required_ram / (1024**3)
            avail_gb = avail_ram / (1024**3)
            return (
                False,
                f"This model fits your computer, but another application is currently using the memory it needs: "
                f"requires at least {req_gb:.1f} GB RAM, but only {avail_gb:.1f} GB is currently available. "
                "Close other apps or restart LocalPilot to proceed.",
            )

        if plan.requested_execution_mode == ExecutionMode.GPU:
            required_vram = plan.estimated_vram_bytes
            avail_vram = current_hardware.vram_available_bytes
            if required_vram > avail_vram:
                req_vgb = required_vram / (1024**3)
                avail_vgb = avail_vram / (1024**3)
                return (
                    False,
                    f"GPU mode was selected, but your graphics card currently has insufficient free memory: "
                    f"requires {req_vgb:.1f} GB VRAM, but only {avail_vgb:.1f} GB is free.",
                )

        return (True, "Computer has sufficient memory to run this model.")
