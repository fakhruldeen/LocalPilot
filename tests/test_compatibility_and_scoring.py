"""Tests for CompatibilityPolicy and ScoringPolicy (Sections 12 & 13)."""

from datetime import datetime, timezone
import pytest

from localpilot.domain.compatibility import CompatibilityPolicy
from localpilot.domain.models import (
    ConfidenceLevel,
    ExecutionMode,
    NormalizedCandidate,
    TaskCategory,
    VerificationStage,
)
from localpilot.domain.scoring import ScoringPolicy
from localpilot.platforms.base import HardwareSnapshot, MemoryAccountingModel


@pytest.fixture
def sample_hardware() -> HardwareSnapshot:
    return HardwareSnapshot(
        os_name="Linux",
        os_version="6.8.0",
        cpu_arch="x86_64",
        cpu_cores_logical=16,
        ram_total_bytes=16 * 1024**3,        # 16 GB
        ram_available_bytes=10 * 1024**3,    # 10 GB
        safe_ram_budget_bytes=8 * 1024**3,   # 8 GB
        safe_vram_budget_bytes=0,
        memory_model=MemoryAccountingModel.SEPARATE,
        gpu_devices=[],
        disk_total_bytes=500 * 1024**3,
        disk_available_bytes=200 * 1024**3,  # 200 GB
        measured_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    )


@pytest.fixture
def sample_candidate() -> NormalizedCandidate:
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    return NormalizedCandidate(
        candidate_id="cand-1",
        model_id="test/model",
        name="Test Model 3B",
        source="huggingface",
        artifact_id="model-q4.gguf",
        architecture="llama",
        family="Test",
        tasks=[TaskCategory.CHAT],
        format="gguf",
        total_bytes=2 * 1024**3,  # 2 GB
        runtime="llamacpp",
        usable_context=4096,
        target_context=4096,
        verified_release_at=now_iso,
        metadata_fetched_at=now_iso,
        estimated_ram_bytes=3 * 1024**3,
        estimated_disk_bytes=2 * 1024**3,
        confidence=ConfidenceLevel.VERIFIED,
        verification_stage=VerificationStage.SUPPORTED,
    )


def test_compatibility_evaluation_success(sample_hardware: HardwareSnapshot, sample_candidate: NormalizedCandidate) -> None:
    policy = CompatibilityPolicy()
    result = policy.evaluate(sample_candidate, sample_hardware)

    assert result.is_compatible is True
    assert result.verification_stage == VerificationStage.EXPECTED_TO_FIT
    assert result.reason_code is None
    assert "Expected to fit" in result.friendly_explanation


def test_compatibility_insufficient_disk(sample_hardware: HardwareSnapshot, sample_candidate: NormalizedCandidate) -> None:
    sample_hardware.disk_available_bytes = 100 * 1024 * 1024  # only 100MB
    policy = CompatibilityPolicy()
    result = policy.evaluate(sample_candidate, sample_hardware)

    assert result.is_compatible is False
    assert result.reason_code == "DISK_SPACE_INSUFFICIENT"


def test_compatibility_memory_budget_exceeded(sample_hardware: HardwareSnapshot, sample_candidate: NormalizedCandidate) -> None:
    sample_candidate.total_bytes = 30 * 1024**3  # 30 GB model
    policy = CompatibilityPolicy()
    result = policy.evaluate(sample_candidate, sample_hardware)

    assert result.is_compatible is False
    assert result.reason_code == "MEMORY_BUDGET_EXCEEDED"


def test_scoring_formula_exact(sample_hardware: HardwareSnapshot, sample_candidate: NormalizedCandidate) -> None:
    comp_policy = CompatibilityPolicy()
    comp_res = comp_policy.evaluate(sample_candidate, sample_hardware)

    scoring = ScoringPolicy()
    score_res = scoring.score(sample_candidate, comp_res)

    # Candidate was just released (d=0) so N=1.0
    assert pytest.approx(score_res.newness_score, 0.05) == 1.0
    assert score_res.is_date_estimated is False
    assert 0 <= score_res.total_score <= 100
    assert "Score" in score_res.explanation


def test_scoring_unknown_release_date(sample_hardware: HardwareSnapshot, sample_candidate: NormalizedCandidate) -> None:
    sample_candidate.verified_release_at = None
    comp_policy = CompatibilityPolicy()
    comp_res = comp_policy.evaluate(sample_candidate, sample_hardware)

    scoring = ScoringPolicy()
    score_res = scoring.score(sample_candidate, comp_res)

    # d=365 default fallback -> 2^(-365/180) ~ 0.245
    assert pytest.approx(score_res.newness_score, 0.05) == 0.245
    assert score_res.is_date_estimated is True
