"""Packaged initial databases for sources, providers, developers, and models."""

from __future__ import annotations
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

DATA_DIR = Path(__file__).resolve().parent


def get_data_dir() -> Path:
    """Returns the directory containing packaged initial database JSON files."""
    return DATA_DIR


def load_initial_json(filename: str) -> Dict[str, Any]:
    """Loads a packaged initial JSON database file."""
    target = DATA_DIR / filename
    if not target.exists():
        # Fallback to repo root data/ directory
        alt = DATA_DIR.parent.parent.parent / "data" / filename
        if alt.exists():
            target = alt
        else:
            raise FileNotFoundError(f"Initial database file '{filename}' not found in {DATA_DIR} or {alt}")
    return json.loads(target.read_text(encoding="utf-8"))


def get_initial_sources() -> List[Dict[str, Any]]:
    """Returns the list of initial sources/registries."""
    return load_initial_json("sources.json").get("sources", [])


def get_initial_providers() -> List[Dict[str, Any]]:
    """Returns the list of initial model providers/laboratories."""
    return load_initial_json("providers.json").get("providers", [])


def get_initial_developers() -> List[Dict[str, Any]]:
    """Returns the list of initial developers, quantizers, and maintainers."""
    return load_initial_json("developers.json").get("developers", [])


def get_initial_models() -> List[Dict[str, Any]]:
    """Returns the list of initial curated model definitions."""
    return load_initial_json("models.json").get("models", [])
