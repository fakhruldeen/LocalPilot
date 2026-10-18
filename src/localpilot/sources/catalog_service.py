"""Unified CatalogService coordinating sources, caching, compatibility, and scoring."""

from __future__ import annotations
import json
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, Field

from localpilot.domain.compatibility import CompatibilityCheckResult, CompatibilityPolicy
from localpilot.domain.execution_plan import ExecutionPlan, ExecutionPlanGenerator
from localpilot.domain.models import NormalizedCandidate, TaskCategory, VerificationStage
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
    is_favorite: bool = False


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
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        conn = self.db.get_connection()
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
            # Ensure catalog_sources has seed entry
            conn.execute(
                """
                INSERT OR IGNORE INTO catalog_sources (source_id, name, last_refreshed_at, status, details_json)
                VALUES ('seed', 'Bundled Seed Catalog', ?, 'healthy', ?)
                """,
                (now_iso, json.dumps({"description": "Built-in curated model candidates", "count": len(seeds)})),
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO catalog_sources (source_id, name, last_refreshed_at, status, details_json)
                VALUES ('huggingface', 'Hugging Face Hub', NULL, 'ready', '{}')
                """
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO catalog_sources (source_id, name, last_refreshed_at, status, details_json)
                VALUES ('ollama_library', 'Ollama Library', NULL, 'ready', '{}')
                """
            )

    def is_first_run(self) -> bool:
        """Returns True if the catalog has never been refreshed from online sources."""
        conn = self.db.get_connection()
        row = conn.execute(
            "SELECT last_refreshed_at FROM catalog_sources WHERE source_id = 'huggingface'"
        ).fetchone()
        if not row or row["last_refreshed_at"] is None:
            return True
        return False

    def add_favorite(self, model_ref: str, notes: Optional[str] = None) -> None:
        self.db.add_favorite(model_ref, notes)

    def remove_favorite(self, model_ref: str) -> None:
        self.db.remove_favorite(model_ref)

    def toggle_favorite(self, model_ref: str) -> bool:
        if self.is_favorite(model_ref):
            self.remove_favorite(model_ref)
            return False
        else:
            self.add_favorite(model_ref)
            return True

    def list_favorites(self) -> List[str]:
        return self.db.list_favorites()

    def list_favorites_detailed(self) -> List[Dict[str, Any]]:
        return self.db.list_favorites_detailed()

    def is_favorite(self, candidate_or_ref: Any) -> bool:
        if isinstance(candidate_or_ref, str):
            return self.db.is_favorite(candidate_or_ref)
        if hasattr(candidate_or_ref, "candidate_id") and self.db.is_favorite(candidate_or_ref.candidate_id):
            return True
        if hasattr(candidate_or_ref, "short_alias") and candidate_or_ref.short_alias and self.db.is_favorite(candidate_or_ref.short_alias):
            return True
        if hasattr(candidate_or_ref, "display_alias") and self.db.is_favorite(candidate_or_ref.display_alias):
            return True
        if hasattr(candidate_or_ref, "model_id") and self.db.is_favorite(candidate_or_ref.model_id):
            return True
        return False

    async def refresh_catalog_async(self, sources: Optional[List[str]] = None) -> Dict[str, Any]:
        """Refreshes catalog models from active sources and updates catalog_sources status."""
        from datetime import datetime, timezone
        now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        target_sources = sources or ["seed", "huggingface", "ollama_library"]
        results: Dict[str, Any] = {}
        conn = self.db.get_connection()

        for src in target_sources:
            if src == "seed":
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
                    conn.execute(
                        "UPDATE catalog_sources SET last_refreshed_at = ?, status = 'healthy', details_json = ? WHERE source_id = 'seed'",
                        (now_iso, json.dumps({"count": len(seeds)})),
                    )
                results["seed"] = {"status": "healthy", "count": len(seeds)}

            elif src == "huggingface":
                try:
                    search_queries = [
                        "google gemma gguf",
                        "meta-llama gguf",
                        "qwen2.5 gguf",
                        "deepseek-ai gguf",
                        "mistralai gguf",
                        "microsoft phi gguf",
                        "moonlight gguf",
                        "glm-4 gguf",
                        "gguf",
                    ]
                    all_candidates: List[NormalizedCandidate] = []
                    seen_cids = set()
                    for sq in search_queries:
                        try:
                            batch = await self.hf_adapter.search(query=sq, limit=15)
                            for c in batch:
                                if c.candidate_id not in seen_cids:
                                    seen_cids.add(c.candidate_id)
                                    all_candidates.append(c)
                        except Exception:
                            continue

                    with conn:
                        for c in all_candidates:
                            conn.execute(
                                """
                                INSERT OR REPLACE INTO catalog_models (
                                    candidate_id, model_id, source, revision, payload_json,
                                    verified_release_at, repo_created_at, repo_modified_at,
                                    artifact_published_at, metadata_fetched_at
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    c.candidate_id,
                                    c.model_id,
                                    c.source,
                                    c.revision,
                                    c.model_dump_json(),
                                    c.verified_release_at,
                                    c.repo_created_at,
                                    c.repo_modified_at,
                                    c.artifact_published_at,
                                    c.metadata_fetched_at,
                                ),
                            )
                        conn.execute(
                            "UPDATE catalog_sources SET last_refreshed_at = ?, status = 'healthy', details_json = ? WHERE source_id = 'huggingface'",
                            (now_iso, json.dumps({"count": len(all_candidates)})),
                        )
                    results["huggingface"] = {"status": "healthy", "count": len(all_candidates)}
                except Exception as e:
                    with conn:
                        conn.execute(
                            "UPDATE catalog_sources SET status = 'degraded', details_json = ? WHERE source_id = 'huggingface'",
                            (json.dumps({"error": str(e)}),),
                        )
                    results["huggingface"] = {"status": "degraded", "error": str(e)}

            elif src == "ollama_library":
                try:
                    candidates = await self.ollama_adapter.search(limit=25)
                    with conn:
                        for c in candidates:
                            conn.execute(
                                """
                                INSERT OR REPLACE INTO catalog_models (
                                    candidate_id, model_id, source, revision, payload_json,
                                    verified_release_at, repo_created_at, repo_modified_at,
                                    artifact_published_at, metadata_fetched_at
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                """,
                                (
                                    c.candidate_id,
                                    c.model_id,
                                    c.source,
                                    c.revision,
                                    c.model_dump_json(),
                                    c.verified_release_at,
                                    c.repo_created_at,
                                    c.repo_modified_at,
                                    c.artifact_published_at,
                                    c.metadata_fetched_at,
                                ),
                            )
                        conn.execute(
                            "UPDATE catalog_sources SET last_refreshed_at = ?, status = 'healthy', details_json = ? WHERE source_id = 'ollama_library'",
                            (now_iso, json.dumps({"count": len(candidates)})),
                        )
                    results["ollama_library"] = {"status": "healthy", "count": len(candidates)}
                except Exception as e:
                    with conn:
                        conn.execute(
                            "UPDATE catalog_sources SET status = 'degraded', details_json = ? WHERE source_id = 'ollama_library'",
                            (json.dumps({"error": str(e)}),),
                        )
                    results["ollama_library"] = {"status": "degraded", "error": str(e)}

        return {"status": "completed", "sources": results}

    def refresh_catalog(self, sources: Optional[List[str]] = None) -> Dict[str, Any]:
        """Synchronous wrapper for refresh_catalog_async."""
        import asyncio
        return asyncio.run(self.refresh_catalog_async(sources=sources))

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

    def get_candidate(self, candidate_or_alias: str) -> Optional[NormalizedCandidate]:
        conn = self.db.get_connection()
        # 1. Direct candidate_id lookup
        row = conn.execute(
            "SELECT payload_json FROM catalog_models WHERE candidate_id = ?",
            (candidate_or_alias,),
        ).fetchone()
        if row:
            try:
                return NormalizedCandidate.model_validate_json(row["payload_json"])
            except Exception:
                pass

        # 2. Check all cached candidates for short_alias or loose match
        candidates = self.get_cached_candidates()
        target = candidate_or_alias.lower().strip()

        # Check exact short_alias match
        for c in candidates:
            if c.short_alias and c.short_alias.lower() == target:
                return c

        # Check user-defined model_aliases in DB
        alias_row = conn.execute(
            "SELECT target_version_id, logical_model_id FROM model_aliases WHERE alias_name = ?",
            (target,),
        ).fetchone()
        if alias_row:
            target_id = alias_row["target_version_id"] or alias_row["logical_model_id"]
            for c in candidates:
                if c.candidate_id == target_id or (c.short_alias and c.short_alias.lower() == target_id.lower()):
                    return c

        # Check display_alias or equivalence group
        for c in candidates:
            if c.display_alias.lower() == target:
                return c
            if c.equivalence_group and c.equivalence_group.lower() == target:
                return c

        # Loose match: candidate name, model_id, or candidate_id
        for c in candidates:
            if target == c.name.lower() or target == c.model_id.lower():
                return c
        for c in candidates:
            if target in (c.short_alias or "").lower() or target in c.candidate_id.lower():
                return c

        return None

    def discover(
        self,
        hardware: Optional[HardwareSnapshot] = None,
        task: Optional[TaskCategory] = None,
        query: str = "",
        only_compatible: bool = True,
        only_favorites: bool = False,
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
            if only_favorites and not self.is_favorite(c):
                continue
            if q:
                pub = (c.provenance or {}).get("publisher", "").lower()
                langs = [l.lower() for l in c.languages]
                alias = (c.short_alias or "").lower()
                disp_alias = c.display_alias.lower()
                match = (
                    q in c.name.lower()
                    or q in c.model_id.lower()
                    or q in c.family.lower()
                    or q in alias
                    or q in disp_alias
                    or q in pub
                    or (q in ("chinese", "zh") and "zh" in langs)
                )
                if not match:
                    continue
            filtered.append(c)

        conn = self.db.get_connection()
        if q and len(filtered) == 0:
            try:
                import asyncio
                loop = None
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    pass
                if not (loop and loop.is_running()):
                    async def _quick_fetch():
                        return await self.hf_adapter.search(query=q, limit=5)
                    online_cands = asyncio.run(asyncio.wait_for(_quick_fetch(), timeout=2.5))
                    if online_cands:
                        with conn:
                            for oc in online_cands:
                                conn.execute(
                                    """
                                    INSERT OR REPLACE INTO catalog_models (
                                        candidate_id, model_id, source, revision, payload_json,
                                        verified_release_at, repo_created_at, repo_modified_at,
                                        artifact_published_at, metadata_fetched_at
                                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                                    """,
                                    (
                                        oc.candidate_id,
                                        oc.model_id,
                                        oc.source,
                                        oc.revision,
                                        oc.model_dump_json(),
                                        oc.verified_release_at,
                                        oc.repo_created_at,
                                        oc.repo_modified_at,
                                        oc.artifact_published_at,
                                        oc.metadata_fetched_at,
                                    ),
                                )
                        for oc in online_cands:
                            if task and task not in oc.tasks:
                                continue
                            filtered.append(oc)
            except Exception:
                pass

        scored_items: List[Tuple[NormalizedCandidate, ScoreResult, CompatibilityCheckResult, Optional[ExecutionPlan], List[ExecutionPlan]]] = []

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
                    is_favorite=self.is_favorite(c),
                )
            )

        return results

    def get_shortlist(
        self,
        task: Optional[TaskCategory] = None,
        hardware: Optional[HardwareSnapshot] = None,
        limit: int = 3,
    ) -> List[Any]:
        """Returns non-redundant, compatibility-gated shortlisted candidates with distinctions."""
        from localpilot.domain.recommendation import ModelRecommendationEngine, ShortlistCandidate
        from localpilot.observability.schema import RecommendationDecisionRecord

        report = self.inspector.inspect()
        hw = hardware or report.hardware
        target_task = task or TaskCategory.CHAT

        ranked = self.discover(hardware=hw, task=task, only_compatible=True)
        shortlist = ModelRecommendationEngine.generate_shortlist(
            ranked_candidates=ranked,
            task=target_task,
            hardware=hw,
            target_count=limit,
        )

        conn = self.db.get_connection()
        for idx, item in enumerate(shortlist):
            cand = item.candidate
            plan = item.recommended_plan
            score_comp_dict = {
                "newness": item.score.newness_score,
                "download": item.score.download_score,
                "context": item.score.context_score,
                "headroom": item.score.headroom_score,
            } if item.score else {}
            decision_record = RecommendationDecisionRecord(
                candidate_id=cand.candidate_id,
                policy_version="compatibility-v1",
                scoring_version="scoring-v1",
                hardware_snapshot=hw.model_dump(),
                execution_plan=plan.model_dump() if plan else {},
                checks=item.compatibility.checks if item.compatibility else [],
                score_components=score_comp_dict,
                total_score=item.score.total_score if item.score else 0.0,
                confidence=item.task_evidence_level.value,
                rank=idx + 1,
                outcome="recommended",
                reason_code=item.distinction.value,
            )
            try:
                with conn:
                    conn.execute(
                        """
                        INSERT INTO audit_decisions (
                            decision_id, timestamp, decision_type, target_id, payload_json, outcome, reason_code
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            decision_record.decision_id,
                            decision_record.timestamp,
                            "recommendation",
                            cand.candidate_id,
                            decision_record.model_dump_json(),
                            decision_record.outcome,
                            decision_record.reason_code,
                        ),
                    )
            except Exception:
                pass

        return shortlist

    def get_comparison(
        self,
        candidates: List[NormalizedCandidate],
        hardware: Optional[HardwareSnapshot] = None,
    ) -> List[Any]:
        """Generates side-by-side comparison rows for candidates."""
        from localpilot.domain.recommendation import ModelRecommendationEngine

        report = self.inspector.inspect()
        hw = hardware or report.hardware
        return ModelRecommendationEngine.generate_comparison(candidates, hw)

