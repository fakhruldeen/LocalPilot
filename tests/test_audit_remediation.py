"""Regression and verification tests for audit findings LP-P0-01, LP-P1-01, LP-P1-02, LP-P1-03."""

from __future__ import annotations
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import httpx
import pytest

from localpilot.application.chat_service import ChatService
from localpilot.runtimes.docker_model_runner import DockerModelRunnerServiceAdapter
from localpilot.runtimes.llamacpp import LlamaCppRuntimeAdapter


# =============================================================================
# LP-P0-01: LlamaCpp Library Path Discovery
# =============================================================================

def test_llamacpp_runtime_env_detection(tmp_path: Path) -> None:
    """Verifies that LlamaCppRuntimeAdapter.get_runtime_env detects sibling/subfolder

    shared libraries (libllama.so/dylib) and sets LD_LIBRARY_PATH/DYLD_LIBRARY_PATH.
    """
    bin_dir = tmp_path / "bin"
    build_bin = bin_dir / "build" / "bin"
    build_bin.mkdir(parents=True, exist_ok=True)

    fake_server = bin_dir / "llama-server"
    fake_server.write_bytes(b"FAKE_BIN")
    fake_lib = build_bin / "libllama.so"
    fake_lib.write_bytes(b"FAKE_LIB")

    env = LlamaCppRuntimeAdapter.get_runtime_env(fake_server)
    assert "LD_LIBRARY_PATH" in env
    assert str(build_bin) in env["LD_LIBRARY_PATH"]
    assert "DYLD_LIBRARY_PATH" in env
    assert str(build_bin) in env["DYLD_LIBRARY_PATH"]


# =============================================================================
# LP-P1-01: Docker Model Runner Plugin False Positive Rejection
# =============================================================================

def test_docker_model_plugin_rejection_on_standard_docker() -> None:
    """Verifies that check_model_plugin returns False when standard docker

    intercepts --help but outputs 'unknown command' on direct 'docker model'.
    """
    adapter = DockerModelRunnerServiceAdapter()

    # Simulate standard docker where info returns no 'model' plugin
    # and 'docker model' outputs unknown command with code 1
    mock_info = MagicMock(returncode=0, stdout="buildx compose \n")
    mock_run = MagicMock(returncode=1, stdout="", stderr="docker: unknown command: docker model\n")

    def mock_subprocess_run(cmd, *args, **kwargs):
        if "info" in cmd:
            return mock_info
        if cmd == ["/usr/bin/docker", "model"]:
            return mock_run
        return MagicMock(returncode=0, stdout="Usage: docker", stderr="")

    with patch("shutil.which", return_value="/usr/bin/docker"), \
         patch("subprocess.run", side_effect=mock_subprocess_run):
        assert adapter.check_model_plugin() is False


# =============================================================================
# LP-P1-02: Real-Time Chat Token Streaming
# =============================================================================

@pytest.mark.asyncio
async def test_chat_service_stream_message_ollama() -> None:
    """Verifies ChatService.stream_message yields progressive tokens from Ollama JSON lines."""
    lines = [
        json.dumps({"message": {"content": "Hello"}}),
        json.dumps({"message": {"content": " "}}),
        json.dumps({"message": {"content": "World!"}}),
    ]

    async def fake_aiter_lines():
        for line in lines:
            yield line

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.aiter_lines = fake_aiter_lines

    class MockStreamContext:
        async def __aenter__(self):
            return mock_resp
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    mock_client = MagicMock()
    mock_client.stream = MagicMock(return_value=MockStreamContext())

    class MockAsyncClientContext:
        async def __aenter__(self):
            return mock_client
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    with patch("httpx.AsyncClient", return_value=MockAsyncClientContext()):
        tokens = []
        async for t in ChatService.stream_message("http://127.0.0.1:11434", "ollama", "test-m", [{"role": "user", "content": "hi"}]):
            tokens.append(t)

        assert "".join(tokens) == "Hello World!"


@pytest.mark.asyncio
async def test_chat_service_stream_message_llamacpp() -> None:
    """Verifies ChatService.stream_message yields progressive tokens from SSE data lines."""
    sse_lines = [
        'data: {"choices": [{"delta": {"content": "Fast"}}]}',
        'data: {"choices": [{"delta": {"content": " local"}}]}',
        'data: {"choices": [{"delta": {"content": " AI."}}]}',
        'data: [DONE]',
    ]

    async def fake_aiter_lines():
        for line in sse_lines:
            yield line

    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.aiter_lines = fake_aiter_lines

    class MockStreamContext:
        async def __aenter__(self):
            return mock_resp
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    mock_client = MagicMock()
    mock_client.stream = MagicMock(return_value=MockStreamContext())

    class MockAsyncClientContext:
        async def __aenter__(self):
            return mock_client
        async def __aexit__(self, exc_type, exc_val, exc_tb):
            pass

    with patch("httpx.AsyncClient", return_value=MockAsyncClientContext()):
        tokens = []
        async for t in ChatService.stream_message("http://127.0.0.1:8080", "llamacpp", "test-m", [{"role": "user", "content": "hi"}]):
            tokens.append(t)

        assert "".join(tokens) == "Fast local AI."
