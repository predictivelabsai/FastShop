"""OpenAI-compatible transport for the store agent."""

from __future__ import annotations

import httpx

from app.config import settings


class XAITransport:
    def complete(self, messages: list[dict], tools: list[dict]) -> dict:
        response = httpx.post(
            settings.xai_base_url.rstrip("/") + "/chat/completions",
            headers={"Authorization": "Bearer " + settings.xai_api_key},
            json={
                "model": settings.model_name,
                "temperature": 0.2,
                "max_tokens": 4000,
                "messages": messages,
                "tools": tools,
                "tool_choice": "auto",
            },
            timeout=90,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]
