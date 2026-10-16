"""15-stage Setup and Test Workflow implementation (Section 15)."""

from __future__ import annotations
import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
import httpx
from pydantic import BaseModel, Field

from localpilot.application.jobs import JobManager
from localpilot.domain.capabilities import (
    DeploymentMethod,
    InferenceBackendId,
    ManagedServiceId,
)
from localpilot.domain.compatibility import CompatibilityPolicy
from localpilot.domain.execution_plan import ExecutionPlan
from localpilot.domain.models import NormalizedCandidate, VerificationStage
from localpilot.domain.ownership import OwnershipClass
from localpilot.hardware.inspector import SystemInspector
from localpilot.observability.context import operation_context
from localpilot.observability.events import ObservabilityHub, get_hub
from localpilot.observability.schema import EventCategory, EventLevel
from localpilot.runtimes.base import InstallationScope
from localpilot.runtimes.docker_model_runner import DockerModelRunnerServiceAdapter
from localpilot.runtimes.llamacpp import LlamaCppRuntimeAdapter
from localpilot.runtimes.lm_studio import LMStudioServiceAdapter
from localpilot.runtimes.ollama import OllamaRuntimeAdapter
from localpilot.runtimes.vllm_backend import VllmBackendAdapter
from localpilot.storage.db import DatabaseManager, get_db
from localpilot.storage.inventory import StorageManager


class SetupPlan(BaseModel):
    model_id: str
    candidate_id: str
    runtime_name: str
    install_scope: InstallationScope = InstallationScope.ISOLATED
    target_install_dir: str
    download_bytes: int
    storage_needed_bytes: int
    execution_mode: str
    requires_runtime_install: bool
    execution_plan: Optional[ExecutionPlan] = None
    inference_backend: str = "llama.cpp"
    managed_service: str = "none"
    deployment_method: str = "local_api_service"
    alternative_plans: List[ExecutionPlan] = Field(default_factory=list)


class SetupWorkflow:
    """Orchestrates the 15-stage model setup and verification workflow."""

    def __init__(
        self,
        db: Optional[DatabaseManager] = None,
        hub: Optional[ObservabilityHub] = None,
        job_mgr: Optional[JobManager] = None,
        storage_mgr: Optional[StorageManager] = None,
        data_dir: Optional[Path] = None,
    ) -> None:
        self.db = db or get_db()
        self.hub = hub or ObservabilityHub(db=self.db)
        self.job_mgr = job_mgr or JobManager(self.db, self.hub)
        self.data_dir = data_dir or (self.db.db_path.parent if hasattr(self.db, "db_path") else get_default_data_dir())
        self.storage_mgr = storage_mgr or StorageManager(self.db, data_dir=self.data_dir)
        self.inspector = SystemInspector()
        self.compatibility_policy = CompatibilityPolicy()
        self.ollama_adapter = OllamaRuntimeAdapter()
        self.llamacpp_adapter = LlamaCppRuntimeAdapter()
        self.lm_studio_adapter = LMStudioServiceAdapter()
        self.docker_model_runner_adapter = DockerModelRunnerServiceAdapter()
        self.vllm_adapter = VllmBackendAdapter()

    def _resolve_adapter(self, plan: SetupPlan) -> Any:
        if plan.managed_service == ManagedServiceId.OLLAMA.value or plan.runtime_name == "ollama":
            return self.ollama_adapter
        if plan.managed_service == ManagedServiceId.LM_STUDIO.value or plan.runtime_name == "lm_studio":
            return self.lm_studio_adapter
        if plan.managed_service == ManagedServiceId.DOCKER_MODEL_RUNNER.value or plan.runtime_name == "docker_model_runner":
            return self.docker_model_runner_adapter
        if plan.inference_backend == InferenceBackendId.VLLM.value or plan.runtime_name == "vllm":
            return self.vllm_adapter
        if plan.inference_backend == InferenceBackendId.LLAMACPP.value or plan.runtime_name == "llamacpp":
            return self.llamacpp_adapter
        raise ValueError(
            f"Unsupported execution plan combination: backend={plan.inference_backend}, "
            f"service={plan.managed_service}, deployment={plan.deployment_method}"
        )

    def build_plan(
        self,
        candidate: NormalizedCandidate,
        scope: InstallationScope = InstallationScope.ISOLATED,
        execution_plan: Optional[ExecutionPlan] = None,
        alternative_plans: Optional[List[ExecutionPlan]] = None,
    ) -> SetupPlan:
        # Determine 3-axis execution details
        if execution_plan:
            backend = execution_plan.inference_backend.value
            service = execution_plan.managed_service.value
            deployment = execution_plan.deployment_method.value
            if execution_plan.managed_service == ManagedServiceId.OLLAMA:
                runtime_name = "ollama"
            elif execution_plan.managed_service == ManagedServiceId.LM_STUDIO:
                runtime_name = "lm_studio"
            elif execution_plan.managed_service == ManagedServiceId.DOCKER_MODEL_RUNNER:
                runtime_name = "docker_model_runner"
            elif execution_plan.inference_backend == InferenceBackendId.VLLM:
                runtime_name = "vllm"
            else:
                runtime_name = "llamacpp"
        else:
            cand_runtime = getattr(candidate, "runtime", "") or ""
            if cand_runtime == "ollama" or candidate.source == "ollama":
                backend = InferenceBackendId.UNKNOWN_SERVICE_MANAGED.value
                service = ManagedServiceId.OLLAMA.value
                deployment = DeploymentMethod.LOCAL_API_SERVICE.value
                runtime_name = "ollama"
            else:
                backend = InferenceBackendId.LLAMACPP.value
                service = ManagedServiceId.NONE.value
                deployment = DeploymentMethod.LOCAL_API_SERVICE.value
                runtime_name = "llamacpp"

        # Check existing installation
        temp_plan = SetupPlan(
            model_id=candidate.model_id,
            candidate_id=candidate.candidate_id,
            runtime_name=runtime_name,
            install_scope=scope,
            target_install_dir=str(self.storage_mgr.models_dir / runtime_name),
            download_bytes=candidate.total_bytes,
            storage_needed_bytes=candidate.estimated_disk_bytes,
            execution_mode=candidate.execution_mode.value,
            requires_runtime_install=False,
            execution_plan=execution_plan,
            inference_backend=backend,
            managed_service=service,
            deployment_method=deployment,
            alternative_plans=alternative_plans or [],
        )

        adapter = self._resolve_adapter(temp_plan)
        needs_runtime = False
        if hasattr(adapter, "detect_existing_installation"):
            existing = adapter.detect_existing_installation()
            needs_runtime = existing is None
        elif hasattr(adapter, "detect_installation"):
            needs_runtime = adapter.detect_installation() is None

        target_dir = str(self.storage_mgr.models_dir / runtime_name)
        return SetupPlan(
            model_id=candidate.model_id,
            candidate_id=candidate.candidate_id,
            runtime_name=runtime_name,
            install_scope=scope,
            target_install_dir=target_dir,
            download_bytes=candidate.total_bytes,
            storage_needed_bytes=candidate.estimated_disk_bytes,
            execution_mode=candidate.execution_mode.value,
            requires_runtime_install=needs_runtime,
            execution_plan=execution_plan,
            inference_backend=backend,
            managed_service=service,
            deployment_method=deployment,
            alternative_plans=alternative_plans or [],
        )

    async def execute_setup(
        self,
        candidate: NormalizedCandidate,
        plan: SetupPlan,
        progress_callback: Optional[Callable[[str, int, int], None]] = None,
    ) -> Dict[str, Any]:
        """Executes the setup and test workflow with evidence-based verification."""
        with operation_context() as op_id:
            # Stage 1 & 2: Recheck access and resources
            report = self.inspector.inspect()
            if plan.execution_plan:
                comp = self.compatibility_policy.evaluate_plan(plan.execution_plan, report.hardware, candidate)
            else:
                comp = self.compatibility_policy.evaluate(candidate, report.hardware)

            if not comp.is_compatible:
                raise RuntimeError(f"Compatibility recheck failed: {comp.friendly_explanation}")

            # Stage 4 & 5: Save mandatory audit approval checkpoint before state change
            checkpoint_payload = plan.model_dump()
            if plan.execution_plan:
                checkpoint_payload["execution_plan_id"] = plan.execution_plan.plan_id
                checkpoint_payload["inference_backend"] = plan.execution_plan.inference_backend.value
                checkpoint_payload["managed_service"] = plan.execution_plan.managed_service.value
                checkpoint_payload["deployment_method"] = plan.execution_plan.deployment_method.value
                checkpoint_payload["ownership_class"] = plan.execution_plan.ownership_class.value

            self.hub.record_audit_checkpoint(
                decision_type="model_setup",
                target_id=candidate.candidate_id,
                payload=checkpoint_payload,
                outcome="approved",
                is_critical=True,
            )

            job = self.job_mgr.create_job(
                operation_type="setup",
                target_id=candidate.candidate_id,
                approved_plan=checkpoint_payload,
                total_bytes=plan.download_bytes,
            )

            try:
                # Stage 6: Prepare runtime if required
                runtime_adapter = self._resolve_adapter(plan)
                if plan.requires_runtime_install:
                    if hasattr(runtime_adapter, "prepare_runtime"):
                        self.job_mgr.update_checkpoint(job.job_id, "preparing_runtime", checkpoint_name="runtime_prep")
                        if progress_callback:
                            progress_callback("Preparing supporting software...", 0, 100)
                        await runtime_adapter.prepare_runtime(plan.install_scope)
                        self.hub.record_activity(
                            category="runtime",
                            title="Installed Supporting Runtime",
                            summary=f"Installed {plan.runtime_name} into {plan.install_scope.value} space.",
                        )

                # Stage 7: Download and prepare model files (NO synthetic GGUF writes)
                self.job_mgr.update_checkpoint(job.job_id, "downloading_model", checkpoint_name="download")
                dest_dir = Path(plan.target_install_dir)
                dest_dir.mkdir(parents=True, exist_ok=True)
                model_file = dest_dir / (candidate.artifact_id or f"{candidate.model_id}.gguf")

                if not model_file.exists():
                    download_url = getattr(candidate, "download_url", None) or getattr(candidate, "source_url", None)
                    if download_url:
                        if progress_callback:
                            progress_callback(f"Downloading model {candidate.name}...", 0, 100)
                        async with httpx.AsyncClient(timeout=300.0, follow_redirects=True) as client:
                            async with client.stream("GET", download_url) as resp:
                                resp.raise_for_status()
                                total = int(resp.headers.get("content-length", candidate.total_bytes))
                                downloaded = 0
                                with open(model_file, "wb") as f:
                                    async for chunk in resp.aiter_bytes(chunk_size=65536):
                                        f.write(chunk)
                                        downloaded += len(chunk)
                                        if progress_callback and total > 0:
                                            progress_callback(f"Downloading {candidate.name}...", downloaded, total)
                    elif plan.runtime_name == "ollama" or candidate.source == "ollama":
                        # For Ollama models, record reference or pull through service
                        if progress_callback:
                            progress_callback(f"Preparing Ollama model {candidate.model_id}...", 0, 100)
                        model_file.write_text(f"ollama:{candidate.model_id}\n")
                    else:
                        raise RuntimeError(
                            f"Model file '{model_file.name}' not found at {model_file} and no downloadable source URL is available. "
                            "LocalPilot does not create synthetic weights in production."
                        )

                actual_bytes = model_file.stat().st_size

                # Stage 8: Register installation in SQLite (Stage: DOWNLOADED_PREPARED)
                self.job_mgr.update_checkpoint(job.job_id, "registering_installation", checkpoint_name="registration")
                inst_id = f"inst-{str(uuid.uuid4())[:8]}"
                art_id = f"art-{str(uuid.uuid4())[:8]}"

                self.storage_mgr.register_artifact(
                    artifact_id=art_id,
                    physical_path=model_file,
                    bytes_size=actual_bytes,
                    sha256=candidate.sha256,
                    is_managed=True,
                    install_scope=plan.install_scope.value,
                )

                now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                conn = self.db.get_connection()
                with conn:
                    conn.execute(
                        """
                        INSERT INTO installations (
                            installation_id, model_id, name, runtime_name, artifact_ids_json,
                            readiness_stage, compatibility_status, update_status, ownership_class,
                            inference_backend, managed_service, deployment_method,
                            execution_plan_json, alternative_plans_json,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            inst_id,
                            candidate.model_id,
                            candidate.name,
                            plan.runtime_name,
                            json.dumps([art_id]),
                            VerificationStage.DOWNLOADED_PREPARED.value,
                            "compatible",
                            "unknown",
                            OwnershipClass.LOCALPILOT_OWNED.value,
                            plan.inference_backend,
                            plan.managed_service,
                            plan.deployment_method,
                            plan.execution_plan.model_dump_json() if plan.execution_plan else "{}",
                            json.dumps([p.model_dump() for p in plan.alternative_plans]),
                            now,
                            now,
                        ),
                    )

                    if plan.execution_plan:
                        conn.execute(
                            "UPDATE execution_plans SET installation_id = ? WHERE plan_id = ?",
                            (inst_id, plan.execution_plan.plan_id),
                        )

                # Stage 9: Live Test Inference Verification
                self.job_mgr.update_checkpoint(job.job_id, "verifying_inference", checkpoint_name="inference_test")
                if progress_callback:
                    progress_callback("Running test inference verification...", 50, 100)

                inference_passed = False
                test_error = None
                try:
                    if plan.runtime_name == "ollama":
                        inference_passed = await self.ollama_adapter.test_inference("http://127.0.0.1:11434", candidate.model_id)
                    elif plan.runtime_name == "lm_studio":
                        inference_passed = await self.lm_studio_adapter.test_inference("http://127.0.0.1:1234", candidate.model_id)
                    elif plan.runtime_name == "docker_model_runner":
                        inference_passed = await self.docker_model_runner_adapter.test_inference("http://127.0.0.1:11434", candidate.model_id)
                    elif plan.runtime_name == "vllm":
                        inference_passed = await self.vllm_adapter.test_inference("http://127.0.0.1:8000", candidate.model_id)
                    else:
                        # llama.cpp
                        inference_passed = await self.llamacpp_adapter.test_inference("http://127.0.0.1:8080", str(model_file))
                except Exception as exc:
                    test_error = str(exc)
                    inference_passed = False

                if inference_passed:
                    # Promoted to TESTED_HERE only after successful live inference
                    now_tested = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
                    with self.db.get_connection() as conn:
                        conn.execute(
                            "UPDATE installations SET readiness_stage = ?, updated_at = ? WHERE installation_id = ?",
                            (VerificationStage.TESTED_HERE.value, now_tested, inst_id),
                        )

                    self.job_mgr.complete_job(job.job_id)
                    self.hub.record_activity(
                        category="model",
                        title="Model Ready & Verified",
                        summary=f"Set up and verified inference for {candidate.name} successfully (tested here).",
                        details={
                            "installation_id": inst_id,
                            "model_id": candidate.model_id,
                            "inference_backend": plan.inference_backend,
                            "managed_service": plan.managed_service,
                            "deployment_method": plan.deployment_method,
                            "readiness_stage": "tested_here",
                        },
                    )

                    return {
                        "success": True,
                        "installation_id": inst_id,
                        "model_id": candidate.model_id,
                        "runtime": plan.runtime_name,
                        "inference_backend": plan.inference_backend,
                        "managed_service": plan.managed_service,
                        "deployment_method": plan.deployment_method,
                        "file_path": str(model_file),
                        "readiness_stage": VerificationStage.TESTED_HERE.value,
                    }
                else:
                    # Inference failed: preserve downloaded weights but do NOT mark tested_here
                    fail_msg = test_error or f"Test inference verification did not succeed for {candidate.name}."
                    self.job_mgr.fail_job(job.job_id, "INFERENCE_VERIFICATION_FAILED", fail_msg)
                    self.hub.record_activity(
                        category="model",
                        title="Model Downloaded (Verification Incomplete)",
                        summary=f"{candidate.name} files prepared, but live inference verification failed.",
                        details={
                            "installation_id": inst_id,
                            "readiness_stage": VerificationStage.DOWNLOADED_PREPARED.value,
                            "error": fail_msg,
                        },
                    )
                    raise RuntimeError(
                        f"Model files downloaded and prepared at {model_file}, but test inference verification failed: {fail_msg}. "
                        "The installation remains in 'downloaded_prepared' state and files were preserved."
                    )

            except Exception as e:
                if job.status.value != "failed":
                    self.job_mgr.fail_job(job.job_id, "SETUP_ERROR", str(e))
                self.hub.emit_event(
                    category=EventCategory.WORKFLOW,
                    event_name="setup.failed",
                    level=EventLevel.ERROR,
                    message=f"Setup failed for {candidate.name}: {e}",
                    outcome="failed",
                )
                raise
