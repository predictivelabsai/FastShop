"""OpenAI-compatible transport for the store assistant, using each store's own model connection."""

from __future__ import annotations

import json
from collections.abc import Mapping

import httpx

from app.assistant_provider import (
    CHAT_TIMEOUT,
    ModelConfig,
    endpoint,
    platform_config,
    request_options,
)
from app.services import CommerceError


class AssistantProviderError(RuntimeError):
    """A safe boundary for unavailable or malformed assistant-provider responses."""

    message = "The assistant is temporarily unavailable. No changes were made. Please try again."

    def __init__(self) -> None:
        super().__init__(self.message)


def _reject_json_constant(_constant: str) -> None:
    """Keep Python's permissive JSON decoder within the JSON standard."""

    raise ValueError("Non-standard JSON constants are not supported.")


def _declared_tool_names(tools: list[dict]) -> set[str]:
    names = set()
    for tool in tools:
        if not isinstance(tool, Mapping):
            continue
        function = tool.get("function")
        if isinstance(function, Mapping) and isinstance(function.get("name"), str):
            names.add(function["name"])
    return names


def _validated_message(payload: object, tools: list[dict]) -> Mapping[str, object]:
    """Return one safe OpenAI-compatible assistant message or reject the payload."""

    if not isinstance(payload, Mapping):
        raise AssistantProviderError()
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise AssistantProviderError()
    choice = choices[0]
    if not isinstance(choice, Mapping):
        raise AssistantProviderError()
    message = choice.get("message")
    if not isinstance(message, Mapping):
        raise AssistantProviderError()
    if "role" in message and message["role"] != "assistant":
        raise AssistantProviderError()

    if "content" in message and message["content"] is not None and not isinstance(
        message["content"], str
    ):
        raise AssistantProviderError()
    tool_calls = message.get("tool_calls", [])
    if not isinstance(tool_calls, list):
        raise AssistantProviderError()
    if not tool_calls:
        if not isinstance(message.get("content"), str):
            raise AssistantProviderError()
        return message

    declared_tool_names = _declared_tool_names(tools)
    call_ids = set()
    for call in tool_calls:
        if not isinstance(call, Mapping) or not isinstance(call.get("id"), str) or not call["id"].strip():
            raise AssistantProviderError()
        if call["id"] in call_ids:
            raise AssistantProviderError()
        call_ids.add(call["id"])
        if call.get("type") != "function":
            raise AssistantProviderError()
        function = call.get("function")
        if not isinstance(function, Mapping):
            raise AssistantProviderError()
        if not isinstance(function.get("name"), str) or not isinstance(
            function.get("arguments"), str
        ):
            raise AssistantProviderError()
        if not function["name"].strip() or function["name"] not in declared_tool_names:
            raise AssistantProviderError()
        try:
            arguments = json.loads(function["arguments"], parse_constant=_reject_json_constant)
        except (ValueError, RecursionError) as exc:
            raise AssistantProviderError() from exc
        if not isinstance(arguments, dict):
            raise AssistantProviderError()
    return message


class XAITransport:
    """Calls the chat completions API of the resolved store or platform connection."""

    def __init__(self, model_config: ModelConfig | None = None) -> None:
        self.model_config = model_config

    def complete(self, messages: list[dict], tools: list[dict]) -> dict:
        model_config = self.model_config or platform_config()
        if model_config is None:
            raise AssistantProviderError()
        try:
            url = endpoint(model_config)
        except CommerceError as exc:
            raise AssistantProviderError() from exc
        try:
            response = httpx.post(
                url,
                headers={"Authorization": "Bearer " + model_config.api_key},
                json={
                    "model": model_config.model,
                    **request_options(model_config),
                    "messages": messages,
                    "tools": tools,
                    "tool_choice": "auto",
                },
                timeout=CHAT_TIMEOUT,
                follow_redirects=False,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise AssistantProviderError() from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise AssistantProviderError() from exc
        return _validated_message(payload, tools)  # type: ignore[return-value]


OpenAICompatibleTransport = XAITransport
