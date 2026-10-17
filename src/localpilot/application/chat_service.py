"""Interactive chat service with running model instances (Section 24 privacy preserved)."""

from __future__ import annotations
import json
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

    @staticmethod
    async def stream_message(
        endpoint_url: str,
        runtime_name: str,
        model_id: str,
        messages: List[Dict[str, str]],
    ) -> AsyncGenerator[str, None]:
        """Streams assistant response token-by-token from running model endpoint."""
        if runtime_name == "ollama":
            url = endpoint_url.rstrip("/") + "/api/chat"
            payload = {
                "model": model_id,
                "messages": messages,
                "stream": True,
            }
            async with httpx.AsyncClient(timeout=60.0) as client:
                async with client.stream("POST", url, json=payload) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        try:
                            chunk = json.loads(line)
                            token = chunk.get("message", {}).get("content", "")
                            if token:
                                yield token
                        except Exception:
                            continue
        else:
            # llama.cpp (OpenAI compatible)
            url = endpoint_url.rstrip("/") + "/v1/chat/completions"
            payload = {
                "model": model_id,
                "messages": messages,
                "stream": True,
            }
            async with httpx.AsyncClient(timeout=60.0) as client:
                async with client.stream("POST", url, json=payload) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        line_str = line.strip()
                        if line_str.startswith("data: "):
                            data_part = line_str[6:].strip()
                            if data_part == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data_part)
                                choices = chunk.get("choices", [])
                                if choices:
                                    delta = choices[0].get("delta", {})
                                    token = delta.get("content", "")
                                    if token:
                                        yield token
                            except Exception:
                                continue
