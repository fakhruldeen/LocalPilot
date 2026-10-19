import pytest
from localpilot.domain.models import NormalizedCandidate, TaskCategory
from localpilot.sources.catalog_service import CatalogService
from localpilot.sources.seed_catalog import SEED_MODELS
from localpilot.application.integration_service import IntegrationTemplateService
from typer.testing import CliRunner
from localpilot.cli.main import app


def test_normalized_candidate_publisher_display() -> None:
    base = SEED_MODELS[0]
    c = base.model_copy(update={
        "candidate_id": "test-nvidia",
        "model_id": "nvidia/nemotron-test",
        "name": "Test Nemotron",
        "source": "huggingface",
        "provenance": {"publisher": "NVIDIA", "is_official": True},
    })
    assert c.publisher_display == "NVIDIA"

    # Inferred from model_id prefix
    c2 = base.model_copy(update={
        "candidate_id": "test-allenai",
        "model_id": "allenai/olmo-test",
        "name": "Test OLMo",
        "source": "huggingface",
        "provenance": {},
    })
    assert c2.publisher_display == "AllenAI"


def test_seed_catalog_expanded_providers() -> None:
    publishers = {c.publisher_display for c in SEED_MODELS}
    sources = {c.source for c in SEED_MODELS}

    # Verify modern major providers exist in catalog
    expected_publishers = {
        "Google",
        "Meta",
        "NVIDIA",
        "Cohere",
        "AllenAI",
        "DeepSeek AI",
        "Mistral AI",
        "Microsoft",
        "Snowflake",
        "Stability AI",
        "Upstage",
        "BigCode",
        "Liquid AI",
    }
    for ep in expected_publishers:
        assert ep in publishers, f"Expected publisher '{ep}' in seed catalog"

    # Verify model download sources
    assert "huggingface" in sources
    assert "ollama" in sources
    assert "modelscope" in sources


def test_catalog_discover_provider_filter() -> None:
    cat = CatalogService()

    # Filter by NVIDIA
    nvidia_res = cat.discover(provider="nvidia", only_compatible=False)
    assert len(nvidia_res) > 0
    assert all("nvidia" in r.candidate.publisher_display.lower() for r in nvidia_res)

    # Filter by Cohere
    cohere_res = cat.discover(provider="cohere", only_compatible=False)
    assert len(cohere_res) > 0
    assert all("cohere" in r.candidate.publisher_display.lower() for r in cohere_res)

    # Filter by AllenAI
    allen_res = cat.discover(provider="allenai", only_compatible=False)
    assert len(allen_res) > 0
    assert any("olmo" in r.candidate.name.lower() for r in allen_res)


def test_catalog_discover_source_filter() -> None:
    cat = CatalogService()

    # Filter by modelscope
    ms_res = cat.discover(source="modelscope", only_compatible=False)
    assert len(ms_res) > 0
    assert all(r.candidate.source == "modelscope" for r in ms_res)

    # Filter by ollama
    ol_res = cat.discover(source="ollama", only_compatible=False)
    assert len(ol_res) > 0
    assert all(r.candidate.source == "ollama" for r in ol_res)


def test_integration_service_new_client_providers() -> None:
    base_url = "http://127.0.0.1:8080/v1"
    key = "lp_test_key_12345"

    # Cline
    cline = IntegrationTemplateService.get_cline_instructions(base_url, key, "coding")
    assert cline["configuration"]["api_provider"] == "OpenAI Compatible"
    assert cline["configuration"]["base_url"] == base_url
    assert cline["configuration"]["api_key"] == key

    # Aider
    aider = IntegrationTemplateService.get_aider_instructions(base_url, key, "coding")
    assert "aider --openai-api-base" in aider["command"]
    assert key in aider["command"]

    # LibreChat
    libre = IntegrationTemplateService.get_librechat_template(base_url, key, "default")
    assert "LocalPilot" in libre
    assert base_url in libre
    assert key in libre

    # Open WebUI
    ow = IntegrationTemplateService.get_openwebui_instructions(base_url, key, "default")
    assert ow["configuration"]["url"] == base_url

    # AnythingLLM
    anyllm = IntegrationTemplateService.get_anythingllm_instructions(base_url, key, "default")
    assert anyllm["configuration"]["provider"] == "Generic OpenAI"

    # Zed
    zed = IntegrationTemplateService.get_zed_template(base_url, key, "coding")
    assert zed["language_models"]["openai"]["api_url"] == base_url


def test_cli_discover_provider_and_source_flags() -> None:
    runner = CliRunner()

    res = runner.invoke(app, ["discover", "--provider", "nvidia", "--plain"])
    assert res.exit_code == 0
    assert "Nemotron" in res.output or "NVIDIA" in res.output

    res_src = runner.invoke(app, ["discover", "--source", "modelscope", "--plain"])
    assert res_src.exit_code == 0
    assert "modelscope" in res_src.output
