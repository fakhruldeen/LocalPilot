"""Context variables and scoping for operation correlation IDs."""

from __future__ import annotations
import contextvars
from contextlib import contextmanager
from typing import Generator, Optional
import uuid

_current_session_id = contextvars.ContextVar[Optional[str]]("session_id", default=None)
_current_operation_id = contextvars.ContextVar[Optional[str]]("operation_id", default=None)
_current_job_id = contextvars.ContextVar[Optional[str]]("job_id", default=None)
_current_installation_id = contextvars.ContextVar[Optional[str]]("installation_id", default=None)
_current_instance_id = contextvars.ContextVar[Optional[str]]("instance_id", default=None)
_current_plan_id = contextvars.ContextVar[Optional[str]]("plan_id", default=None)
_current_backend = contextvars.ContextVar[Optional[str]]("inference_backend", default=None)
_current_service = contextvars.ContextVar[Optional[str]]("managed_service", default=None)
_current_deployment = contextvars.ContextVar[Optional[str]]("deployment_method", default=None)


def get_session_id() -> Optional[str]:
    return _current_session_id.get()


def set_session_id(val: str) -> None:
    _current_session_id.set(val)


def get_operation_id() -> Optional[str]:
    return _current_operation_id.get()


def get_job_id() -> Optional[str]:
    return _current_job_id.get()


def get_installation_id() -> Optional[str]:
    return _current_installation_id.get()


def get_instance_id() -> Optional[str]:
    return _current_instance_id.get()


def get_execution_plan_id() -> Optional[str]:
    return _current_plan_id.get()


def get_inference_backend() -> Optional[str]:
    return _current_backend.get()


def get_managed_service() -> Optional[str]:
    return _current_service.get()


def get_deployment_method() -> Optional[str]:
    return _current_deployment.get()


@contextmanager
def operation_context(
    operation_id: Optional[str] = None,
    job_id: Optional[str] = None,
    installation_id: Optional[str] = None,
    instance_id: Optional[str] = None,
    execution_plan_id: Optional[str] = None,
    inference_backend: Optional[str] = None,
    managed_service: Optional[str] = None,
    deployment_method: Optional[str] = None,
) -> Generator[str, None, None]:
    op_id = operation_id or str(uuid.uuid4())
    tok_op = _current_operation_id.set(op_id)
    tok_job = _current_job_id.set(job_id) if job_id is not None else None
    tok_inst = _current_installation_id.set(installation_id) if installation_id is not None else None
    tok_instance = _current_instance_id.set(instance_id) if instance_id is not None else None
    tok_plan = _current_plan_id.set(execution_plan_id) if execution_plan_id is not None else None
    tok_backend = _current_backend.set(inference_backend) if inference_backend is not None else None
    tok_service = _current_service.set(managed_service) if managed_service is not None else None
    tok_deploy = _current_deployment.set(deployment_method) if deployment_method is not None else None
    try:
        yield op_id
    finally:
        _current_operation_id.reset(tok_op)
        if tok_job:
            _current_job_id.reset(tok_job)
        if tok_inst:
            _current_installation_id.reset(tok_inst)
        if tok_instance:
            _current_instance_id.reset(tok_instance)
        if tok_plan:
            _current_plan_id.reset(tok_plan)
        if tok_backend:
            _current_backend.reset(tok_backend)
        if tok_service:
            _current_service.reset(tok_service)
        if tok_deploy:
            _current_deployment.reset(tok_deploy)
