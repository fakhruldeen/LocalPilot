"""Hardware compatibility evaluation and candidate exclusion gates (Section 12)."""

from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel

from localpilot.domain.models import (
    ExecutionMode,
    NormalizedCandidate,
    VerificationStage,
)
from localpilot.platforms.base import HardwareSnapshot, MemoryAccountingModel


class CompatibilityCheckResult(BaseModel):
    is_compatible: bool
    verification_stage: VerificationStage
    reason_code: Optional[str] = None
    friendly_explanation: str
    checks: List[Dict[str, Any]]
    projected_ram_utilization: float
    projected_vram_utilization: float
    projected_shared_utilization: float


class CompatibilityPolicy:
    """Evaluates candidate compatibility against current hardware capacity and budgets."""

    POLICY_VERSION = "compatibility-v1"

    # Overhead and buffer constants
    DEFAULT_RUNTIME_OVERHEAD_BYTES = 512 * 1024 * 1024  # 512 MB
    DEFAULT_WORKING_BUFFERS_BYTES = 256 * 1024 * 1024   # 256 MB
    SAFETY_RESERVE_BYTES = 512 * 1024 * 1024           # 512 MB

    def estimate_kv_cache_bytes(
        self,
        context_length: int,
        parameter_count_approx_b: float = 7.0,
        concurrency: int = 1,
    ) -> int:
        """Approximates KV cache size based on context length, model scale, and concurrency."""
        # Baseline ~ 0.5MB per 1k context for 7B models at 16-bit KV, scaled with parameters and concurrency
        bytes_per_token = 512 * max(0.2, (parameter_count_approx_b / 7.0))
        return int(context_length * bytes_per_token * max(1, concurrency))

    def evaluate_plan(
        self,
        candidate_or_plan: Any,
        plan_or_hw: Any,
        hardware_or_candidate: Optional[Any] = None,
        requested_mode: ExecutionMode = ExecutionMode.AUTO,
    ) -> CompatibilityCheckResult:
        """Evaluates hardware fit for a specific ExecutionPlan."""
        if isinstance(candidate_or_plan, NormalizedCandidate):
            candidate = candidate_or_plan
            plan = plan_or_hw
            hardware = hardware_or_candidate
        else:
            plan = candidate_or_plan
            hardware = plan_or_hw
            candidate = hardware_or_candidate

        # Propagate requested mode from plan or candidate if AUTO
        if requested_mode == ExecutionMode.AUTO:
            plan_mode = getattr(plan, "requested_execution_mode", None)
            if plan_mode:
                requested_mode = plan_mode
            elif getattr(candidate, "execution_mode", None):
                requested_mode = candidate.execution_mode

        checks: List[Dict[str, Any]] = []

        format_val = getattr(candidate, "format", getattr(plan, "model_format", "gguf"))
        total_bytes = getattr(candidate, "total_bytes", getattr(plan, "estimated_disk_bytes", 0))

        # 1. Format check
        if format_val not in ("gguf", "ollama_blob"):
            return CompatibilityCheckResult(
                is_compatible=False,
                verification_stage=VerificationStage.SUPPORTED,
                reason_code="UNSUPPORTED_FORMAT",
                friendly_explanation=f"Model format '{format_val}' is not supported yet.",
                checks=[{"name": "format", "passed": False}],
                projected_ram_utilization=0.0,
                projected_vram_utilization=0.0,
                projected_shared_utilization=0.0,
            )
        checks.append({"name": "format", "passed": True, "format": format_val})

        # 2. Disk space check (including temp_disk_bytes)
        temp_disk = getattr(candidate, "temp_disk_bytes", getattr(plan, "temp_disk_bytes", 0))
        required_disk = plan.estimated_disk_bytes + temp_disk + (1024 * 1024 * 1024)
        has_disk = hardware.disk_available_bytes >= required_disk
        checks.append({
            "name": "disk_space",
            "passed": has_disk,
            "required_bytes": required_disk,
            "available_bytes": hardware.disk_available_bytes,
        })
        if not has_disk:
            return CompatibilityCheckResult(
                is_compatible=False,
                verification_stage=VerificationStage.SUPPORTED,
                reason_code="DISK_SPACE_INSUFFICIENT",
                friendly_explanation="Not enough free disk space to safely install and run this model.",
                checks=checks,
                projected_ram_utilization=0.0,
                projected_vram_utilization=0.0,
                projected_shared_utilization=0.0,
            )

        # 3. Memory calculation using plan's usable context, model scale, and overhead
        param_b = getattr(candidate, "parameter_count_approx_b", 7.0)
        concurrency = getattr(plan, "concurrency", 1)
        kv_cache = self.estimate_kv_cache_bytes(
            plan.usable_context,
            parameter_count_approx_b=param_b,
            concurrency=concurrency,
        )
        total_model_memory = (
            total_bytes
            + kv_cache
            + getattr(plan, "runtime_overhead_bytes", self.DEFAULT_RUNTIME_OVERHEAD_BYTES)
            + self.DEFAULT_WORKING_BUFFERS_BYTES
            + self.SAFETY_RESERVE_BYTES
        )

        projected_ram_u = 0.0
        projected_vram_u = 0.0
        projected_shared_u = 0.0

        if hardware.memory_model == MemoryAccountingModel.UNIFIED:
            has_gpu = bool(hardware.gpu_devices) or any(
                g.acceleration_type in ("metal", "cuda", "rocm", "vulkan") for g in hardware.gpu_devices
            )
            if requested_mode in (ExecutionMode.GPU, ExecutionMode.CPU_GPU) and not has_gpu:
                checks.append({"name": "gpu_device", "passed": False})
                return CompatibilityCheckResult(
                    is_compatible=False,
                    verification_stage=VerificationStage.SUPPORTED,
                    reason_code="GPU_NOT_AVAILABLE",
                    friendly_explanation="GPU execution was requested, but no supported graphics device was detected.",
                    checks=checks,
                    projected_ram_utilization=0.0,
                    projected_vram_utilization=0.0,
                    projected_shared_utilization=0.0,
                )

            budget = hardware.safe_ram_budget_bytes
            projected_shared_u = min(1.0, total_model_memory / max(1, budget))
            fits = total_model_memory <= budget
            checks.append({
                "name": "unified_memory",
                "passed": fits,
                "required_bytes": total_model_memory,
                "budget_bytes": budget,
                "utilization": projected_shared_u,
            })
            if not fits:
                return CompatibilityCheckResult(
                    is_compatible=False,
                    verification_stage=VerificationStage.SUPPORTED,
                    reason_code="MEMORY_BUDGET_EXCEEDED",
                    friendly_explanation="This configuration requires more memory than is safely available on this computer.",
                    checks=checks,
                    projected_ram_utilization=projected_shared_u,
                    projected_vram_utilization=projected_shared_u,
                    projected_shared_utilization=projected_shared_u,
                )
        else:
            has_gpu = bool(hardware.gpu_devices and hardware.safe_vram_budget_bytes > 0)

            if requested_mode == ExecutionMode.GPU:
                if not has_gpu:
                    checks.append({"name": "gpu_device", "passed": False})
                    return CompatibilityCheckResult(
                        is_compatible=False,
                        verification_stage=VerificationStage.SUPPORTED,
                        reason_code="GPU_NOT_AVAILABLE",
                        friendly_explanation="GPU execution was explicitly requested, but no supported graphics device was detected.",
                        checks=checks,
                        projected_ram_utilization=0.0,
                        projected_vram_utilization=0.0,
                        projected_shared_utilization=0.0,
                    )
                if hardware.safe_vram_budget_bytes < total_model_memory:
                    projected_vram_u = min(1.0, total_model_memory / max(1, hardware.safe_vram_budget_bytes))
                    checks.append({"name": "gpu_memory", "passed": False, "required": total_model_memory, "available": hardware.safe_vram_budget_bytes})
                    return CompatibilityCheckResult(
                        is_compatible=False,
                        verification_stage=VerificationStage.SUPPORTED,
                        reason_code="INSUFFICIENT_VRAM",
                        friendly_explanation="GPU execution was explicitly requested, but available graphics memory is insufficient.",
                        checks=checks,
                        projected_ram_utilization=0.0,
                        projected_vram_utilization=projected_vram_u,
                        projected_shared_utilization=0.0,
                    )
                projected_vram_u = min(1.0, total_model_memory / max(1, hardware.safe_vram_budget_bytes))
                projected_ram_u = min(1.0, self.DEFAULT_RUNTIME_OVERHEAD_BYTES / max(1, hardware.safe_ram_budget_bytes))
                checks.append({"name": "gpu_memory", "passed": True, "utilization": projected_vram_u})

            elif requested_mode == ExecutionMode.CPU_GPU:
                if not has_gpu:
                    checks.append({"name": "gpu_device", "passed": False})
                    return CompatibilityCheckResult(
                        is_compatible=False,
                        verification_stage=VerificationStage.SUPPORTED,
                        reason_code="GPU_NOT_AVAILABLE",
                        friendly_explanation="Combined CPU+GPU execution requires a supported graphics device.",
                        checks=checks,
                        projected_ram_utilization=0.0,
                        projected_vram_utilization=0.0,
                        projected_shared_utilization=0.0,
                    )

                backend = str(getattr(plan, "inference_backend", getattr(plan, "backend", ""))).lower()
                if "vllm" in backend:
                    checks.append({"name": "offload_backend", "passed": False})
                    return CompatibilityCheckResult(
                        is_compatible=False,
                        verification_stage=VerificationStage.SUPPORTED,
                        reason_code="OFFLOAD_NOT_SUPPORTED",
                        friendly_explanation=f"Runtime backend '{backend}' does not support CPU+GPU partial offload.",
                        checks=checks,
                        projected_ram_utilization=0.0,
                        projected_vram_utilization=0.0,
                        projected_shared_utilization=0.0,
                    )

                if hardware.safe_vram_budget_bytes < 256 * 1024 * 1024:
                    checks.append({"name": "vram_available", "passed": False})
                    return CompatibilityCheckResult(
                        is_compatible=False,
                        verification_stage=VerificationStage.SUPPORTED,
                        reason_code="INSUFFICIENT_VRAM",
                        friendly_explanation="Graphics memory is insufficient to offload layers.",
                        checks=checks,
                        projected_ram_utilization=0.0,
                        projected_vram_utilization=0.0,
                        projected_shared_utilization=0.0,
                    )

                vram_budget = hardware.safe_vram_budget_bytes
                vram_overhead = self.DEFAULT_RUNTIME_OVERHEAD_BYTES // 2
                weight_offloaded_bytes = min(total_bytes, max(0, vram_budget - vram_overhead))
                remaining_weight_bytes = total_bytes - weight_offloaded_bytes

                required_host_ram = (
                    remaining_weight_bytes
                    + kv_cache
                    + getattr(plan, "runtime_overhead_bytes", self.DEFAULT_RUNTIME_OVERHEAD_BYTES)
                    + self.DEFAULT_WORKING_BUFFERS_BYTES
                    + self.SAFETY_RESERVE_BYTES
                )

                if hardware.safe_ram_budget_bytes < required_host_ram:
                    checks.append({
                        "name": "host_ram_for_offload",
                        "passed": False,
                        "required": required_host_ram,
                        "available": hardware.safe_ram_budget_bytes,
                    })
                    return CompatibilityCheckResult(
                        is_compatible=False,
                        verification_stage=VerificationStage.SUPPORTED,
                        reason_code="MEMORY_BUDGET_EXCEEDED",
                        friendly_explanation="System RAM is insufficient for non-offloaded model layers and working buffers.",
                        checks=checks,
                        projected_ram_utilization=min(1.0, required_host_ram / max(1, hardware.safe_ram_budget_bytes)),
                        projected_vram_utilization=min(1.0, weight_offloaded_bytes / max(1, vram_budget)),
                        projected_shared_utilization=0.0,
                    )

                projected_vram_u = min(1.0, weight_offloaded_bytes / max(1, vram_budget))
                projected_ram_u = min(1.0, required_host_ram / max(1, hardware.safe_ram_budget_bytes))
                checks.append({
                    "name": "cpu_gpu_memory",
                    "passed": True,
                    "offload_ratio": weight_offloaded_bytes / max(1, total_bytes),
                    "vram_u": projected_vram_u,
                    "ram_u": projected_ram_u,
                })

            elif requested_mode == ExecutionMode.CPU:
                if hardware.safe_ram_budget_bytes < total_model_memory:
                    projected_ram_u = min(1.0, total_model_memory / max(1, hardware.safe_ram_budget_bytes))
                    checks.append({"name": "cpu_memory", "passed": False, "required": total_model_memory})
                    return CompatibilityCheckResult(
                        is_compatible=False,
                        verification_stage=VerificationStage.SUPPORTED,
                        reason_code="MEMORY_BUDGET_EXCEEDED",
                        friendly_explanation="CPU execution requires more system RAM than is safely available.",
                        checks=checks,
                        projected_ram_utilization=projected_ram_u,
                        projected_vram_utilization=0.0,
                        projected_shared_utilization=0.0,
                    )
                projected_ram_u = min(1.0, total_model_memory / max(1, hardware.safe_ram_budget_bytes))
                checks.append({"name": "cpu_memory", "passed": True, "utilization": projected_ram_u})

            else:  # AUTO
                if has_gpu and hardware.safe_vram_budget_bytes >= total_model_memory:
                    projected_vram_u = min(1.0, total_model_memory / max(1, hardware.safe_vram_budget_bytes))
                    projected_ram_u = min(1.0, self.DEFAULT_RUNTIME_OVERHEAD_BYTES / max(1, hardware.safe_ram_budget_bytes))
                    checks.append({"name": "gpu_memory", "passed": True, "utilization": projected_vram_u})
                elif hardware.safe_ram_budget_bytes >= total_model_memory:
                    projected_ram_u = min(1.0, total_model_memory / max(1, hardware.safe_ram_budget_bytes))
                    checks.append({"name": "cpu_memory", "passed": True, "utilization": projected_ram_u})
                elif has_gpu and (hardware.safe_vram_budget_bytes + hardware.safe_ram_budget_bytes >= total_model_memory):
                    projected_vram_u = 1.0
                    remaining = total_model_memory - hardware.safe_vram_budget_bytes
                    projected_ram_u = min(1.0, remaining / max(1, hardware.safe_ram_budget_bytes))
                    checks.append({"name": "cpu_gpu_memory", "passed": True})
                else:
                    projected_ram_u = min(1.0, total_model_memory / max(1, hardware.safe_ram_budget_bytes))
                    checks.append({"name": "memory_budget", "passed": False, "required_bytes": total_model_memory})
                    return CompatibilityCheckResult(
                        is_compatible=False,
                        verification_stage=VerificationStage.SUPPORTED,
                        reason_code="MEMORY_BUDGET_EXCEEDED",
                        friendly_explanation="This configuration requires more memory than is safely available in RAM or graphics memory.",
                        checks=checks,
                        projected_ram_utilization=projected_ram_u,
                        projected_vram_utilization=projected_vram_u,
                        projected_shared_utilization=0.0,
                    )

        return CompatibilityCheckResult(
            is_compatible=True,
            verification_stage=VerificationStage.EXPECTED_TO_FIT,
            reason_code=None,
            friendly_explanation="Expected to fit. We will test it during setup.",
            checks=checks,
            projected_ram_utilization=projected_ram_u,
            projected_vram_utilization=projected_vram_u,
            projected_shared_utilization=projected_shared_u,
        )

    def evaluate(
        self,
        candidate: NormalizedCandidate,
        hardware: HardwareSnapshot,
        requested_mode: ExecutionMode = ExecutionMode.AUTO,
    ) -> CompatibilityCheckResult:
        if requested_mode == ExecutionMode.AUTO and getattr(candidate, "execution_mode", None):
            requested_mode = candidate.execution_mode

        checks: List[Dict[str, Any]] = []

        # 1. Format check
        if candidate.format not in ("gguf", "ollama_blob"):
            return CompatibilityCheckResult(
                is_compatible=False,
                verification_stage=VerificationStage.SUPPORTED,
                reason_code="UNSUPPORTED_FORMAT",
                friendly_explanation=f"Model format '{candidate.format}' is not supported yet.",
                checks=[{"name": "format", "passed": False}],
                projected_ram_utilization=0.0,
                projected_vram_utilization=0.0,
                projected_shared_utilization=0.0,
            )
        checks.append({"name": "format", "passed": True, "format": candidate.format})

        # 2. Disk space check
        required_disk = candidate.estimated_disk_bytes + candidate.temp_disk_bytes + (1024 * 1024 * 1024)
        has_disk = hardware.disk_available_bytes >= required_disk
        checks.append({
            "name": "disk_space",
            "passed": has_disk,
            "required_bytes": required_disk,
            "available_bytes": hardware.disk_available_bytes,
        })
        if not has_disk:
            return CompatibilityCheckResult(
                is_compatible=False,
                verification_stage=VerificationStage.SUPPORTED,
                reason_code="DISK_SPACE_INSUFFICIENT",
                friendly_explanation="Not enough free disk space to safely install and run this model.",
                checks=checks,
                projected_ram_utilization=0.0,
                projected_vram_utilization=0.0,
                projected_shared_utilization=0.0,
            )

        # 3. Memory calculation
        param_b = getattr(candidate, "parameter_count_approx_b", 7.0)
        kv_cache = self.estimate_kv_cache_bytes(candidate.usable_context, parameter_count_approx_b=param_b)
        total_model_memory = (
            candidate.total_bytes
            + kv_cache
            + self.DEFAULT_RUNTIME_OVERHEAD_BYTES
            + self.DEFAULT_WORKING_BUFFERS_BYTES
            + self.SAFETY_RESERVE_BYTES
        )

        projected_ram_u = 0.0
        projected_vram_u = 0.0
        projected_shared_u = 0.0

        if hardware.memory_model == MemoryAccountingModel.UNIFIED:
            has_gpu = bool(hardware.gpu_devices) or any(
                g.acceleration_type in ("metal", "cuda", "rocm", "vulkan") for g in hardware.gpu_devices
            )
            if requested_mode in (ExecutionMode.GPU, ExecutionMode.CPU_GPU) and not has_gpu:
                checks.append({"name": "gpu_device", "passed": False})
                return CompatibilityCheckResult(
                    is_compatible=False,
                    verification_stage=VerificationStage.SUPPORTED,
                    reason_code="GPU_NOT_AVAILABLE",
                    friendly_explanation="GPU execution was requested, but no supported graphics device was detected.",
                    checks=checks,
                    projected_ram_utilization=0.0,
                    projected_vram_utilization=0.0,
                    projected_shared_utilization=0.0,
                )

            # Apple Silicon / Unified Memory: single shared budget
            budget = hardware.safe_ram_budget_bytes
            projected_shared_u = min(1.0, total_model_memory / max(1, budget))
            fits = total_model_memory <= budget
            checks.append({
                "name": "unified_memory",
                "passed": fits,
                "required_bytes": total_model_memory,
                "budget_bytes": budget,
                "utilization": projected_shared_u,
            })
            if not fits:
                return CompatibilityCheckResult(
                    is_compatible=False,
                    verification_stage=VerificationStage.SUPPORTED,
                    reason_code="MEMORY_BUDGET_EXCEEDED",
                    friendly_explanation="This model requires more memory than is safely available on this computer.",
                    checks=checks,
                    projected_ram_utilization=projected_shared_u,
                    projected_vram_utilization=projected_shared_u,
                    projected_shared_utilization=projected_shared_u,
                )
        else:
            # Separate RAM and dedicated VRAM
            has_gpu = bool(hardware.gpu_devices and hardware.safe_vram_budget_bytes > 0)

            if requested_mode == ExecutionMode.GPU:
                if not has_gpu:
                    checks.append({"name": "gpu_device", "passed": False})
                    return CompatibilityCheckResult(
                        is_compatible=False,
                        verification_stage=VerificationStage.SUPPORTED,
                        reason_code="GPU_NOT_AVAILABLE",
                        friendly_explanation="GPU execution was explicitly requested, but no supported graphics device was detected.",
                        checks=checks,
                        projected_ram_utilization=0.0,
                        projected_vram_utilization=0.0,
                        projected_shared_utilization=0.0,
                    )
                if hardware.safe_vram_budget_bytes < total_model_memory:
                    projected_vram_u = min(1.0, total_model_memory / max(1, hardware.safe_vram_budget_bytes))
                    checks.append({"name": "gpu_memory", "passed": False, "required": total_model_memory, "available": hardware.safe_vram_budget_bytes})
                    return CompatibilityCheckResult(
                        is_compatible=False,
                        verification_stage=VerificationStage.SUPPORTED,
                        reason_code="INSUFFICIENT_VRAM",
                        friendly_explanation="GPU execution was explicitly requested, but available graphics memory is insufficient.",
                        checks=checks,
                        projected_ram_utilization=0.0,
                        projected_vram_utilization=projected_vram_u,
                        projected_shared_utilization=0.0,
                    )
                projected_vram_u = min(1.0, total_model_memory / max(1, hardware.safe_vram_budget_bytes))
                projected_ram_u = min(1.0, self.DEFAULT_RUNTIME_OVERHEAD_BYTES / max(1, hardware.safe_ram_budget_bytes))
                checks.append({"name": "gpu_memory", "passed": True, "utilization": projected_vram_u})

            elif requested_mode == ExecutionMode.CPU_GPU:
                if not has_gpu:
                    checks.append({"name": "gpu_device", "passed": False})
                    return CompatibilityCheckResult(
                        is_compatible=False,
                        verification_stage=VerificationStage.SUPPORTED,
                        reason_code="GPU_NOT_AVAILABLE",
                        friendly_explanation="Combined CPU+GPU execution requires a supported graphics device.",
                        checks=checks,
                        projected_ram_utilization=0.0,
                        projected_vram_utilization=0.0,
                        projected_shared_utilization=0.0,
                    )

                if hardware.safe_vram_budget_bytes < 256 * 1024 * 1024:
                    checks.append({"name": "vram_available", "passed": False})
                    return CompatibilityCheckResult(
                        is_compatible=False,
                        verification_stage=VerificationStage.SUPPORTED,
                        reason_code="INSUFFICIENT_VRAM",
                        friendly_explanation="Graphics memory is insufficient to offload layers.",
                        checks=checks,
                        projected_ram_utilization=0.0,
                        projected_vram_utilization=0.0,
                        projected_shared_utilization=0.0,
                    )

                vram_budget = hardware.safe_vram_budget_bytes
                vram_overhead = self.DEFAULT_RUNTIME_OVERHEAD_BYTES // 2
                weight_offloaded_bytes = min(candidate.total_bytes, max(0, vram_budget - vram_overhead))
                remaining_weight_bytes = candidate.total_bytes - weight_offloaded_bytes

                required_host_ram = (
                    remaining_weight_bytes
                    + kv_cache
                    + self.DEFAULT_RUNTIME_OVERHEAD_BYTES
                    + self.DEFAULT_WORKING_BUFFERS_BYTES
                    + self.SAFETY_RESERVE_BYTES
                )

                if hardware.safe_ram_budget_bytes < required_host_ram:
                    checks.append({
                        "name": "host_ram_for_offload",
                        "passed": False,
                        "required": required_host_ram,
                        "available": hardware.safe_ram_budget_bytes,
                    })
                    return CompatibilityCheckResult(
                        is_compatible=False,
                        verification_stage=VerificationStage.SUPPORTED,
                        reason_code="MEMORY_BUDGET_EXCEEDED",
                        friendly_explanation="System RAM is insufficient for non-offloaded model layers and working buffers.",
                        checks=checks,
                        projected_ram_utilization=min(1.0, required_host_ram / max(1, hardware.safe_ram_budget_bytes)),
                        projected_vram_utilization=min(1.0, weight_offloaded_bytes / max(1, vram_budget)),
                        projected_shared_utilization=0.0,
                    )

                projected_vram_u = min(1.0, weight_offloaded_bytes / max(1, vram_budget))
                projected_ram_u = min(1.0, required_host_ram / max(1, hardware.safe_ram_budget_bytes))
                checks.append({
                    "name": "cpu_gpu_memory",
                    "passed": True,
                    "offload_ratio": weight_offloaded_bytes / max(1, candidate.total_bytes),
                    "vram_u": projected_vram_u,
                    "ram_u": projected_ram_u,
                })

            elif requested_mode == ExecutionMode.CPU:
                if hardware.safe_ram_budget_bytes < total_model_memory:
                    projected_ram_u = min(1.0, total_model_memory / max(1, hardware.safe_ram_budget_bytes))
                    checks.append({"name": "cpu_memory", "passed": False, "required": total_model_memory})
                    return CompatibilityCheckResult(
                        is_compatible=False,
                        verification_stage=VerificationStage.SUPPORTED,
                        reason_code="MEMORY_BUDGET_EXCEEDED",
                        friendly_explanation="CPU execution requires more system RAM than is safely available.",
                        checks=checks,
                        projected_ram_utilization=projected_ram_u,
                        projected_vram_utilization=0.0,
                        projected_shared_utilization=0.0,
                    )
                projected_ram_u = min(1.0, total_model_memory / max(1, hardware.safe_ram_budget_bytes))
                checks.append({"name": "cpu_memory", "passed": True, "utilization": projected_ram_u})

            else:  # AUTO
                if has_gpu and hardware.safe_vram_budget_bytes >= total_model_memory:
                    projected_vram_u = min(1.0, total_model_memory / max(1, hardware.safe_vram_budget_bytes))
                    projected_ram_u = min(1.0, self.DEFAULT_RUNTIME_OVERHEAD_BYTES / max(1, hardware.safe_ram_budget_bytes))
                    checks.append({"name": "gpu_memory", "passed": True, "utilization": projected_vram_u})
                elif hardware.safe_ram_budget_bytes >= total_model_memory:
                    projected_ram_u = min(1.0, total_model_memory / max(1, hardware.safe_ram_budget_bytes))
                    checks.append({"name": "cpu_memory", "passed": True, "utilization": projected_ram_u})
                elif has_gpu and (hardware.safe_vram_budget_bytes + hardware.safe_ram_budget_bytes >= total_model_memory):
                    projected_vram_u = 1.0
                    remaining = total_model_memory - hardware.safe_vram_budget_bytes
                    projected_ram_u = min(1.0, remaining / max(1, hardware.safe_ram_budget_bytes))
                    checks.append({"name": "cpu_gpu_memory", "passed": True})
                else:
                    projected_ram_u = min(1.0, total_model_memory / max(1, hardware.safe_ram_budget_bytes))
                    checks.append({"name": "memory_budget", "passed": False, "required_bytes": total_model_memory})
                    return CompatibilityCheckResult(
                        is_compatible=False,
                        verification_stage=VerificationStage.SUPPORTED,
                        reason_code="MEMORY_BUDGET_EXCEEDED",
                        friendly_explanation="This model requires more memory than is safely available in RAM or graphics memory.",
                        checks=checks,
                        projected_ram_utilization=projected_ram_u,
                        projected_vram_utilization=projected_vram_u,
                        projected_shared_utilization=0.0,
                    )

        return CompatibilityCheckResult(
            is_compatible=True,
            verification_stage=VerificationStage.EXPECTED_TO_FIT,
            reason_code=None,
            friendly_explanation="Expected to fit. We will test it during setup.",
            checks=checks,
            projected_ram_utilization=projected_ram_u,
            projected_vram_utilization=projected_vram_u,
            projected_shared_utilization=projected_shared_u,
        )
