"""Unified CatalogService coordinating sources, caching, compatibility, and scoring."""

from __future__ import annotations
import json
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from localpilot.domain.compatibility import CompatibilityCheckResult, CompatibilityPolicy
from localpilot.domain.execution_plan import ExecutionPlan, ExecutionPlanGenerator
from localpilot.domain.models import NormalizedCandidate, TaskCategory
from localpilot.domain.scoring import ScoreResult, ScoringPolicy
from localpilot.hardware.inspector import SystemInspector
from localpilot.platforms.base import HardwareSnapshot
from localpilot.sources.huggingface import HuggingFaceSourceAdapter
from localpilot.sources.ollama_library import OllamaLibrarySourceAdapter
from localpilot.sources.seed_catalog import get_seed_candidates
from localpilot.storage.db import DatabaseManager, get_db


class RankedCandidate(BaseModel):
    candidate: NormalizedCandidate
    score: ScoreResult
    compatibility: CompatibilityCheckResult
    recommended_plan: Optional[ExecutionPlan] = None
    alternative_plans: List[ExecutionPlan] = Field(default_factory=list)


class CatalogService:
    """Discovers, caches, evaluates compatibility, and ranks model candidates."""

    def __init__(self, db: Optional[DatabaseManager] = None) -> None:
        self.db = db or get_db()
        self.compatibility_policy = CompatibilityPolicy()
        self.scoring_policy = ScoringPolicy()
        self.inspector = SystemInspector()
        self.hf_adapter = HuggingFaceSourceAdapter()
        self.ollama_adapter = OllamaLibrarySourceAdapter()
        self._ensure_seed_cached()

    def _ensure_seed_cached(self) -> None:
        conn = self.db.get_connection()
        count = conn.execute("SELECT COUNT(*) as cnt FROM catalog_models").fetchone()["cnt"]
        if count == 0:
            seeds = get_seed_candidates()
            with conn:
                for s in seeds:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO catalog_models (
                            candidate_id, model_id, source, revision, payload_json,
                            verified_release_at, repo_created_at, repo_modified_at,
                            artifact_published_at, metadata_fetched_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            s.candidate_id,
                            s.model_id,
                            s.source,
                            s.revision,
                            s.model_dump_json(),
                            s.verified_release_at,
                            s.repo_created_at,
                            s.repo_modified_at,
                            s.artifact_published_at,
                            s.metadata_fetched_at,
                        ),
                    )

    def get_cached_candidates(self) -> List[NormalizedCandidate]:
        conn = self.db.get_connection()
        rows = conn.execute("SELECT payload_json FROM catalog_models").fetchall()
        candidates: List[NormalizedCandidate] = []
        for r in rows:
            try:
                candidates.append(NormalizedCandidate.model_validate_json(r["payload_json"]))
            except Exception:
                pass
        return candidates

    def get_candidate(self, candidate_id: str) -> Optional[NormalizedCandidate]:
        conn = self.db.get_connection()
        row = conn.execute(
            "SELECT payload_json FROM catalog_models WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        if row:
            try:
                return NormalizedCandidate.model_validate_json(row["payload_json"])
            except Exception:
                pass
        return None

    def discover(
        self,
        hardware: Optional[HardwareSnapshot] = None,
        task: Optional[TaskCategory] = None,
        query: str = "",
        only_compatible: bool = True,
    ) -> List[RankedCandidate]:
        report = self.inspector.inspect()
        hw = hardware or report.hardware
        available_services = [s.service_id for s in report.discovered_services]
        running_services = [s.service_id for s in report.discovered_services if s.is_service_running]

        all_candidates = self.get_cached_candidates()

        filtered: List[NormalizedCandidate] = []
        q = query.lower().strip()
        for c in all_candidates:
            if task and task not in c.tasks:
                continue
            if q and (q not in c.name.lower() and q not in c.model_id.lower() and q not in c.family.lower()):
                continue
            filtered.append(c)

        scored_items: List[Tuple[NormalizedCandidate, ScoreResult, CompatibilityCheckResult, Optional[ExecutionPlan], List[ExecutionPlan]]] = []
        conn = self.db.get_connection()

        for c in filtered:
            comp = self.compatibility_policy.evaluate(c, hw)
            if only_compatible and not comp.is_compatible:
                continue

            # Generate plans across backend/service/deployment axes
            all_plans = ExecutionPlanGenerator.generate_plans(c, hw, available_services)

            # Evaluate each plan individually
            compatible_plans: List[Tuple[ExecutionPlan, CompatibilityCheckResult]] = []
            for plan in all_plans:
                plan_comp = self.compatibility_policy.evaluate_plan(plan, hw, c)
                if plan_comp.is_compatible:
                    compatible_plans.append((plan, plan_comp))

            # Select recommended plan & alternatives
            if compatible_plans:
                # Rank plans:
                # 1. Services actively running
                # 2. Non-advanced plans before advanced
                # 3. Direct/standard services
                def _plan_priority(item: Tuple[ExecutionPlan, CompatibilityCheckResult]) -> Tuple[int, int]:
                    p, _ = item
                    svc_running_bonus = 0 if p.managed_service in running_services else 1
                    adv_penalty = 1 if p.is_advanced_only else 0
                    return (adv_penalty, svc_running_bonus)

                compatible_plans.sort(key=_plan_priority)
                recommended, recommended_comp = compatible_plans[0]
                alternatives = [p for p, _ in compatible_plans[1:]]
                plan_effective_comp = recommended_comp
            else:
                if only_compatible:
                    continue  # Exclude candidates with no compatible execution plan
                recommended = None
                alternatives = []
                plan_effective_comp = CompatibilityCheckResult(
                    is_compatible=False,
                    verification_stage=VerificationStage.SUPPORTED,
                    reason_code="NO_COMPATIBLE_PLAN",
                    friendly_explanation="No compatible execution plan was found for this system configuration.",
                    checks=[{"name": "execution_plans", "passed": False, "count": len(all_plans)}],
                    projected_ram_utilization=0.0,
                    projected_vram_utilization=0.0,
                    projected_shared_utilization=0.0,
                )

            # Persist plans to database for traceability
            with conn:
                plans_to_persist = ([recommended] if recommended else []) + alternatives
                for p in plans_to_persist:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO execution_plans (
                            plan_id, candidate_id, inference_backend, managed_service,
                            deployment_method, plan_json, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
                        """,
                        (
                            p.plan_id,
                            p.candidate_id,
                            p.inference_backend.value,
                            p.managed_service.value,
                            p.deployment_method.value,
                            p.model_dump_json(),
                        ),
                    )

            # Score using the selected plan's compatibility and context
            if recommended:
                c.usable_context = recommended.usable_context
            scr = self.scoring_policy.score(c, plan_effective_comp)
            scored_items.append((c, scr, plan_effective_comp, recommended, alternatives))

        # Sort candidates using scoring policy (based on primary candidate compatibility)
        scored_triplets = [(c, s, comp) for c, s, comp, _, _ in scored_items]
        ranked_triplets = self.scoring_policy.sort_candidates(scored_triplets)

        # Map back to ranked candidate objects preserving 1 row per candidate
        plan_lookup = {c.candidate_id: (rec, alts) for c, _, _, rec, alts in scored_items}

        results: List[RankedCandidate] = []
        for c, s, comp in ranked_triplets:
            c.verification_stage = comp.verification_stage
            rec, alts = plan_lookup.get(c.candidate_id, (None, []))
            results.append(
                RankedCandidate(
                    candidate=c,
                    score=s,
                    compatibility=comp,
                    recommended_plan=rec,
                    alternative_plans=alts,
                )
            )

        return results
