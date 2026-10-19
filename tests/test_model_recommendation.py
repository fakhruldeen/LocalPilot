"""Tests for model recommendation, decision quality, scoring invariants, shortlist generation, and comparison."""

from __future__ import annotations
import os
import tempfile
import pytest

from localpilot.domain.capabilities import (
    DeploymentMethod,
    InferenceBackendId,
    ManagedServiceId,
)
from localpilot.domain.compatibility import CompatibilityCheckResult, CompatibilityPolicy
from localpilot.domain.execution_plan import ExecutionPlan, ExecutionPlanGenerator
from localpilot.domain.models import (
    ConfidenceLevel,
    ExecutionMode,
    NormalizedCandidate,
    TaskCategory,
    VerificationStage,
)
from localpilot.domain.recommendation import (
    ModelComparisonRow,
    ModelRecommendationEngine,
    ShortlistCandidate,
    ShortlistDistinction,
    TaskEvidenceLevel,
)
from localpilot.domain.scoring import ScoringPolicy
from localpilot.platforms.base import HardwareSnapshot, MemoryAccountingModel
from localpilot.sources.catalog_service import CatalogService, RankedCandidate
from localpilot.storage.db import DatabaseManager


def _mock_hardware(
    ram_gb: float = 16.0,
    vram_gb: float = 0.0,
    disk_gb: float = 100.0,
) -> HardwareSnapshot:
    total_ram = int(ram_gb * (1024**3))
    avail_ram = int(ram_gb * 0.8 * (1024**3))
    safe_ram = int(ram_gb * 0.65 * (1024**3))
    vram_bytes = int(vram_gb * (1024**3))
    return HardwareSnapshot(
        os_name="Linux",
        os_version="6.8.0",
        cpu_arch="x86_64",
        cpu_cores_physical=8,
        cpu_cores_logical=16,
        cpu_features=["avx2", "fma"],
        ram_total_bytes=total_ram,
        ram_available_bytes=avail_ram,
        safe_ram_budget_bytes=safe_ram,
        safe_vram_budget_bytes=vram_bytes,
        memory_model=MemoryAccountingModel.SEPARATE,
        measured_at="2026-10-17T00:00:00Z",
        gpu_devices=[],
        disk_total_bytes=int(disk_gb * 2 * (1024**3)),
        disk_available_bytes=int(disk_gb * (1024**3)),
    )


def _mock_candidate(
    cid: str,
    name: str,
    size_mb: float = 1000.0,
    context: int = 4096,
    family: str = "qwen",
    equivalence_group: str = "",
    tasks: list[TaskCategory] = None,
    verified_release_at: str = "2024-09-01T00:00:00Z",
    repo_created_at: str = "2024-08-01T00:00:00Z",
    repo_modified_at: str = "2026-10-01T00:00:00Z",
) -> NormalizedCandidate:
    return NormalizedCandidate(
        candidate_id=cid,
        model_id=f"test/{cid}",
        name=name,
        source="huggingface",
        artifact_id=f"{cid}.gguf",
        family=family,
        architecture="transformer",
        format="gguf",
        runtime="llamacpp",
        parameter_count_approx_b=1.5,
        total_bytes=int(size_mb * (1024 * 1024)),
        context_window=context,
        usable_context=context,
        tasks=tasks or [TaskCategory.CHAT],
        equivalence_group=equivalence_group or family,
        verified_release_at=verified_release_at,
        repo_created_at=repo_created_at,
        repo_modified_at=repo_modified_at,
        metadata_fetched_at="2024-09-01T00:00:00Z",
        estimated_ram_bytes=int(size_mb * 1.2 * 1024 * 1024),
        estimated_disk_bytes=int(size_mb * 1024 * 1024),
    )


def _mock_plan(cand: NormalizedCandidate, ram_mb: float = 1200.0) -> ExecutionPlan:
    plans = ExecutionPlanGenerator.generate_plans(cand, _mock_hardware(), [])
    if plans:
        p = plans[0]
        p.estimated_ram_bytes = int(ram_mb * 1024 * 1024)
        return p
    return ExecutionPlan(
        candidate_id=cand.candidate_id,
        source=cand.source,
        model_id=cand.model_id,
        artifact_id=f"{cand.candidate_id}.gguf",
        model_format=cand.format,
        inference_backend=InferenceBackendId.LLAMA_CPP,
        deployment_method=DeploymentMethod.STANDALONE_BINARY,
        advertised_max_context=cand.context_window,
        usable_context=cand.usable_context,
        target_context=cand.usable_context,
        memory_accounting_model=MemoryAccountingModel.SEPARATE,
        estimated_ram_bytes=int(ram_mb * 1024 * 1024),
        estimated_disk_bytes=cand.total_bytes,
        storage_location="/tmp/test",
    )


# ---------------------------------------------------------------------------
# 1. Scoring Invariants & Policies
# ---------------------------------------------------------------------------


def test_scoring_weights_sum_to_one() -> None:
    policy = ScoringPolicy()
    total_weights = (
        policy.WEIGHT_NEWNESS
        + policy.WEIGHT_DOWNLOAD
        + policy.WEIGHT_CONTEXT
        + policy.WEIGHT_HEADROOM
    )
    assert abs(total_weights - 1.0) < 1e-6, f"Weights must sum to 1.0, got {total_weights}"
    assert policy.WEIGHT_NEWNESS == 0.40
    assert policy.WEIGHT_DOWNLOAD == 0.25
    assert policy.WEIGHT_CONTEXT == 0.20
    assert policy.WEIGHT_HEADROOM == 0.15


def test_score_strictly_bounded_zero_to_hundred() -> None:
    policy = ScoringPolicy()
    cand = _mock_candidate("c1", "Candidate 1", size_mb=500.0, context=16384)
    comp = CompatibilityCheckResult(
        is_compatible=True,
        verification_stage=VerificationStage.SUPPORTED,
        reason_code="COMPATIBLE",
        friendly_explanation="Compatible",
        checks=[],
        projected_ram_utilization=0.3,
        projected_vram_utilization=0.0,
        projected_shared_utilization=0.0,
    )
    result = policy.score(cand, comp)
    assert 0.0 <= result.total_score <= 100.0
    for comp_name, comp_val in [
        ("newness", result.newness_score),
        ("download", result.download_score),
        ("context", result.context_score),
        ("headroom", result.headroom_score),
    ]:
        assert 0.0 <= comp_val <= 1.0, f"Component {comp_name} out of bounds: {comp_val}"


def test_date_integrity_modified_at_ignored_for_newness() -> None:
    """Updating repo_modified_at (e.g. README edits) must NOT artificially inflate newness score."""
    policy = ScoringPolicy()
    comp = CompatibilityCheckResult(
        is_compatible=True,
        verification_stage=VerificationStage.SUPPORTED,
        reason_code="COMPATIBLE",
        friendly_explanation="Compatible",
        checks=[],
        projected_ram_utilization=0.5,
        projected_vram_utilization=0.0,
        projected_shared_utilization=0.0,
    )

    # Base candidate released in 2024
    cand_base = _mock_candidate(
        "c_base",
        "Base",
        verified_release_at="2024-01-01T00:00:00Z",
        repo_created_at="2024-01-01T00:00:00Z",
        repo_modified_at="2024-01-01T00:00:00Z",
    )
    # Identical candidate with bumped repo_modified_at in 2026
    cand_bumped_modified = _mock_candidate(
        "c_bumped",
        "Bumped",
        verified_release_at="2024-01-01T00:00:00Z",
        repo_created_at="2024-01-01T00:00:00Z",
        repo_modified_at="2026-10-17T00:00:00Z",
    )

    score_base = policy.score(cand_base, comp)
    score_bumped = policy.score(cand_bumped_modified, comp)

    assert score_base.newness_score == score_bumped.newness_score
    assert score_base.total_score == score_bumped.total_score


def test_context_benefit_capped_at_target() -> None:
    """Context score increases with context but caps once target context is satisfied."""
    policy = ScoringPolicy()
    comp = CompatibilityCheckResult(
        is_compatible=True,
        verification_stage=VerificationStage.SUPPORTED,
        reason_code="COMPATIBLE",
        friendly_explanation="Compatible",
        checks=[],
        projected_ram_utilization=0.5,
        projected_vram_utilization=0.0,
        projected_shared_utilization=0.0,
    )
    c_small_ctx = _mock_candidate("c_small", "Small Ctx", context=2048)
    c_target_ctx = _mock_candidate("c_tgt", "Target Ctx", context=8192)
    c_huge_ctx = _mock_candidate("c_huge", "Huge Ctx", context=65536)

    s_small = policy.score(c_small_ctx, comp)
    s_target = policy.score(c_target_ctx, comp)
    s_huge = policy.score(c_huge_ctx, comp)

    assert s_small.context_score < s_target.context_score
    assert s_target.context_score == pytest.approx(1.0, abs=1e-4)
    assert s_huge.context_score == pytest.approx(1.0, abs=1e-4)


# ---------------------------------------------------------------------------
# 2. Hard Compatibility Gating
# ---------------------------------------------------------------------------


def test_incompatible_candidates_strictly_excluded_no_fallback() -> None:
    """Candidates exceeding safe memory budget must never appear in the shortlist."""
    hw = _mock_hardware(ram_gb=4.0)  # Safe budget is ~2.6 GB
    # Candidate requires 10 GB RAM
    cand_huge = _mock_candidate("huge", "Huge Model", size_mb=10000.0)
    comp_incompatible = CompatibilityCheckResult(
        is_compatible=False,
        verification_stage=VerificationStage.SUPPORTED,
        reason_code="RAM_BUDGET_EXCEEDED",
        friendly_explanation="Exceeds safe RAM budget",
        checks=[{"name": "ram_budget", "passed": False}],
        projected_ram_utilization=3.5,
        projected_vram_utilization=0.0,
        projected_shared_utilization=0.0,
    )
    plan_huge = _mock_plan(cand_huge, ram_mb=12000.0)

    ranked_item = RankedCandidate(
        candidate=cand_huge,
        score=ScoringPolicy().score(cand_huge, comp_incompatible),
        compatibility=comp_incompatible,
        recommended_plan=plan_huge,
    )

    shortlist = ModelRecommendationEngine.generate_shortlist(
        ranked_candidates=[ranked_item],
        target_task=TaskCategory.CHAT,
        limit=3,
    )
    assert len(shortlist) == 0, "Incompatible candidates must strictly produce an empty shortlist"


# ---------------------------------------------------------------------------
# 3. Near-Duplicate Suppression (Equivalence Grouping)
# ---------------------------------------------------------------------------


def test_near_duplicate_suppression_collapses_variants() -> None:
    """Multiple quantizations of the same model must collapse to one entry."""
    q4 = _mock_candidate("qwen_q4", "Qwen2.5-Coder-1.5B-Q4_K_M", size_mb=980.0, equivalence_group="qwen2.5-coder-1.5b")
    q5 = _mock_candidate("qwen_q5", "Qwen2.5-Coder-1.5B-Q5_K_M", size_mb=1200.0, equivalence_group="qwen2.5-coder-1.5b")
    q8 = _mock_candidate("qwen_q8", "Qwen2.5-Coder-1.5B-Q8_0", size_mb=1800.0, equivalence_group="qwen2.5-coder-1.5b")
    other = _mock_candidate("llama", "Llama-3.2-1B-Instruct", size_mb=1100.0, equivalence_group="llama-3.2-1b")

    comp_ok = CompatibilityCheckResult(
        is_compatible=True,
        verification_stage=VerificationStage.SUPPORTED,
        reason_code="COMPATIBLE",
        friendly_explanation="Fits comfortably",
        checks=[],
        projected_ram_utilization=0.2,
        projected_vram_utilization=0.0,
        projected_shared_utilization=0.0,
    )

    policy = ScoringPolicy()
    ranked = [
        RankedCandidate(candidate=q4, score=policy.score(q4, comp_ok), compatibility=comp_ok, recommended_plan=_mock_plan(q4)),
        RankedCandidate(candidate=q5, score=policy.score(q5, comp_ok), compatibility=comp_ok, recommended_plan=_mock_plan(q5)),
        RankedCandidate(candidate=q8, score=policy.score(q8, comp_ok), compatibility=comp_ok, recommended_plan=_mock_plan(q8)),
        RankedCandidate(candidate=other, score=policy.score(other, comp_ok), compatibility=comp_ok, recommended_plan=_mock_plan(other)),
    ]

    shortlist = ModelRecommendationEngine.generate_shortlist(ranked, limit=3)

    # Should have at most 2 items: 1 Qwen variant and 1 Llama variant
    assert len(shortlist) == 2
    groups_in_shortlist = [c.candidate.equivalence_group for c in shortlist]
    assert groups_in_shortlist.count("qwen2.5-coder-1.5b") == 1
    assert groups_in_shortlist.count("llama-3.2-1b") == 1


# ---------------------------------------------------------------------------
# 4. Shortlist Distinctions & Presentation
# ---------------------------------------------------------------------------


def test_shortlist_assigns_distinct_roles() -> None:
    """Engine assigns Top Pick, Smaller Download, and Longer Context appropriately."""
    c_top = _mock_candidate("c_top", "Balanced Top Pick", size_mb=2000.0, context=4096, equivalence_group="g1")
    c_small = _mock_candidate("c_small", "Tiny Fast Model", size_mb=600.0, context=4096, equivalence_group="g2")  # < 75% of top
    c_long = _mock_candidate("c_long", "Long Context Model", size_mb=2100.0, context=16384, equivalence_group="g3")  # > 1.4x context

    comp = CompatibilityCheckResult(
        is_compatible=True,
        verification_stage=VerificationStage.SUPPORTED,
        reason_code="COMPATIBLE",
        friendly_explanation="Comfortable memory fit",
        checks=[],
        projected_ram_utilization=0.2,
        projected_vram_utilization=0.0,
        projected_shared_utilization=0.0,
    )

    policy = ScoringPolicy()
    ranked = [
        RankedCandidate(candidate=c_top, score=policy.score(c_top, comp), compatibility=comp, recommended_plan=_mock_plan(c_top)),
        RankedCandidate(candidate=c_small, score=policy.score(c_small, comp), compatibility=comp, recommended_plan=_mock_plan(c_small)),
        RankedCandidate(candidate=c_long, score=policy.score(c_long, comp), compatibility=comp, recommended_plan=_mock_plan(c_long)),
    ]

    shortlist = ModelRecommendationEngine.generate_shortlist(ranked, limit=3)
    assert len(shortlist) == 3
    assert shortlist[0].distinction == ShortlistDistinction.TOP_PICK
    assert "★ Top Pick" in shortlist[0].badge_label

    distinctions = {s.distinction for s in shortlist}
    assert ShortlistDistinction.SMALLER_DOWNLOAD in distinctions or ShortlistDistinction.LONGER_CONTEXT in distinctions


# ---------------------------------------------------------------------------
# 5. Side-by-Side Comparison Matrix
# ---------------------------------------------------------------------------


def test_side_by_side_comparison_matrix() -> None:
    c1 = _mock_candidate("c1", "Model Alpha", size_mb=1200.0, context=8192)
    p1 = _mock_plan(c1, ram_mb=1500.0)
    comp = CompatibilityCheckResult(
        is_compatible=True,
        verification_stage=VerificationStage.SUPPORTED,
        reason_code="COMPATIBLE",
        friendly_explanation="Fits comfortably",
        checks=[],
        projected_ram_utilization=0.2,
        projected_vram_utilization=0.0,
        projected_shared_utilization=0.0,
    )
    ranked = RankedCandidate(candidate=c1, score=ScoringPolicy().score(c1, comp), compatibility=comp, recommended_plan=p1)

    rows = ModelRecommendationEngine.generate_comparison([ranked])
    assert len(rows) == 1
    row = rows[0]
    assert row.name == "Model Alpha"
    assert "MB" in row.download_size
    assert "8,192 tokens" in row.context_window
    assert "llama.cpp" in row.execution_plan
    assert "Open Access" in row.access_requirements


# ---------------------------------------------------------------------------
# 6. Launch Recheck & Truthful Memory Shift Warning
# ---------------------------------------------------------------------------


def test_launch_recheck_detects_memory_drop() -> None:
    cand = _mock_candidate("c1", "Test Model", size_mb=1500.0)
    plan = _mock_plan(cand, ram_mb=4000.0)  # Requires 4 GB RAM

    # 1. Plenty of memory available -> PASS
    hw_ok = _mock_hardware(ram_gb=16.0)
    is_ok, msg = ModelRecommendationEngine.recheck_runtime_availability(cand, plan, hw_ok)
    assert is_ok is True

    # 2. Memory dropped (another app opened) -> Honest warning
    hw_low_ram = HardwareSnapshot(
        os_name="Linux",
        os_version="6.8.0",
        cpu_arch="x86_64",
        cpu_cores_physical=8,
        cpu_cores_logical=16,
        cpu_features=["avx2"],
        ram_total_bytes=16 * (1024**3),
        ram_available_bytes=2 * (1024**3),  # Only 2 GB available, needs 4 GB
        safe_ram_budget_bytes=2 * (1024**3),
        safe_vram_budget_bytes=0,
        memory_model=MemoryAccountingModel.SEPARATE,
        measured_at="2026-10-17T00:00:00Z",
        gpu_devices=[],
        disk_total_bytes=100 * (1024**3),
        disk_available_bytes=50 * (1024**3),
    )
    is_ok2, msg2 = ModelRecommendationEngine.recheck_runtime_availability(cand, plan, hw_low_ram)
    assert is_ok2 is False
    assert "another application is currently using the memory it needs" in msg2
    assert "requires at least 3.9 GB RAM, but only 2.0 GB is currently available" in msg2


# ---------------------------------------------------------------------------
# 7. Audit Trail Logging via CatalogService.get_shortlist
# ---------------------------------------------------------------------------


def test_catalog_service_get_shortlist_logs_audit_record() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = os.path.join(tmpdir, "test.db")
        db = DatabaseManager(db_path)
        cat_svc = CatalogService(db=db)

        # Ensure seed cache is populated
        candidates = cat_svc.get_cached_candidates()
        assert len(candidates) > 0

        # Get shortlist
        shortlist = cat_svc.get_shortlist(task=TaskCategory.CHAT, limit=3)
        assert len(shortlist) > 0

        # Verify audit records in audit_decisions table
        conn = db.get_connection()
        records = conn.execute(
            "SELECT * FROM audit_decisions WHERE decision_type = 'recommendation'"
        ).fetchall()
        assert len(records) == len(shortlist)
        for r in records:
            assert r["outcome"] == "recommended"
            assert r["reason_code"] is not None
            assert r["payload_json"] is not None
