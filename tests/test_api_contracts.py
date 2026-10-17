"""Unit tests for Phase 1 & 2 API contracts, security tokens, policies, and schemas."""

import pytest
from datetime import datetime, timezone

from localpilot.domain.api_contract import (
    ApiKeyPolicy,
    ApiKeyRecord,
    ApiRequestRecord,
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    MetricsDictionary,
)


def test_api_key_generation_and_constant_time_verification():
    raw_key, record = ApiKeyRecord.generate(name="OpenCode Integration", app_name="opencode")

    assert raw_key.startswith("sk-local-")
    assert record.prefix.startswith("sk-local-")
    assert record.name == "OpenCode Integration"
    assert record.app_name == "opencode"
    assert record.is_active is True

    # Valid secret verification
    assert record.verify(raw_key) is True

    # Incorrect secret verification
    assert record.verify("sk-local-wrongsecret12345") is False
    assert record.verify("invalid") is False


def test_api_key_revocation_and_expiration():
    raw_key, record = ApiKeyRecord.generate(name="Cursor Integration")

    # Manually revoke
    record.is_active = False
    record.revoked_at = datetime.now(timezone.utc).isoformat()
    assert record.verify(raw_key) is False

    # Expired key
    raw_key2, record2 = ApiKeyRecord.generate(name="Temporary Key")
    record2.expires_at = "2020-01-01T00:00:00Z"
    assert record2.verify(raw_key2) is False


def test_api_key_policy_enforcement():
    policy = ApiKeyPolicy(
        allowed_model_aliases=["writing", "default"],
        max_tokens_per_request=4096,
        rate_limit_rpm=60,
    )

    assert "writing" in policy.allowed_model_aliases
    assert "coding" not in policy.allowed_model_aliases
    assert policy.max_tokens_per_request == 4096


def test_chat_completion_schemas():
    req = ChatCompletionRequest(
        model="writing",
        messages=[
            ChatMessage(role="system", content="You are a helpful assistant."),
            ChatMessage(role="user", content="Hello, local AI!"),
        ],
        temperature=0.7,
        max_tokens=100,
        stream=False,
    )
    assert req.model == "writing"
    assert len(req.messages) == 2
    assert req.messages[1].content == "Hello, local AI!"

    resp = ChatCompletionResponse(
        id="chatcmpl-test1234",
        model="writing",
        choices=[
            {
                "index": 0,
                "message": {"role": "assistant", "content": "Hello! How can I help you today?"},
                "finish_reason": "stop",
            }
        ],
        usage={
            "prompt_tokens": 15,
            "completion_tokens": 9,
            "total_tokens": 24,
        },
    )
    assert resp.choices[0].message.content == "Hello! How can I help you today?"
    assert resp.usage.total_tokens == 24


def test_metrics_dictionary():
    metrics = MetricsDictionary.get_all_definitions()
    assert "output_tokens_per_second" in metrics
    assert "time_to_first_content_ms" in metrics
    assert "queue_wait_ms" in metrics

    tps_def = MetricsDictionary.get_definition("tps")
    assert tps_def is not None
    assert "Generation Speed" in tps_def["name"]
