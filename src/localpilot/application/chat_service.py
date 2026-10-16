"""Interactive chat service with running model instances (Section 24 privacy preserved)."""

from __future__ import annotations
from typing import AsyncGenerator, Dict, List, Optional
import httpx


class ChatService:
    """Provides streaming and non-streaming conversation with running models."""

    @staticmethod
    async def send_message(
        endpoint_url: str,
        runtime_name: str,
        model_id: str,
        messages: List[Dict[str, str]],
    ) -> str:
        """Sends chat request. Note: Prompts/responses are NEVER passed to ObservabilityHub."""
        if runtime_name == "ollama":
            url = endpoint_url.rstrip("/") + "/api/chat"
            payload = {
                "model": model_id,
                "messages": messages,
                "stream": False,
            }
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                return data.get("message", {}).get("content", "")
        else:
            # llama.cpp (OpenAI compatible)
            url = endpoint_url.rstrip("/") + "/v1/chat/completions"
            payload = {
                "model": model_id,
                "messages": messages,
                "stream": False,
            }
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                data = resp.json()
                choices = data.get("choices", [])
                if choices:
                    return choices[0].get("message", {}).get("content", "")
                return ""
