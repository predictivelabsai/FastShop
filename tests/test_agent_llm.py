"""Focused tests for the merchant-assistant provider boundary."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import assistant_provider, content
from app.assistant_provider import CHAT_TIMEOUT, ModelConfig
from app.integrations import agent_llm
from app.integrations.agent_llm import AssistantProviderError, XAITransport
from app.models import AgentConversation, AgentMessage, Base, User
from app.store_agent import run_turn

SAFE_MESSAGE = "The assistant is temporarily unavailable. No changes were made. Please try again."
DECLARED_TOOLS = [{"type": "function", "function": {"name": "get_stock", "parameters": {}}}]


@pytest.fixture
def configured_transport():
    return XAITransport(ModelConfig("xai", "https://fixture.invalid", "fixture-only",
                                    "fixture-model", "store"))


def provider_response(payload):
    return SimpleNamespace(raise_for_status=lambda: None, json=lambda: payload)


def test_complete_returns_valid_text_message_unchanged(configured_transport, monkeypatch):
    message = {"content": "I checked the store data."}

    def fake_post(url, **kwargs):
        assert url == "https://fixture.invalid/chat/completions"
        assert kwargs["timeout"] == CHAT_TIMEOUT
        assert kwargs["follow_redirects"] is False
        assert kwargs["headers"] == {"Authorization": "Bearer fixture-only"}
        assert kwargs["json"]["model"] == "fixture-model"
        assert kwargs["json"]["tools"] == DECLARED_TOOLS
        return provider_response({"choices": [{"message": message}]})

    monkeypatch.setattr(agent_llm.httpx, "post", fake_post)
    assert configured_transport.complete([{"role": "user", "content": "Check sales"}], DECLARED_TOOLS) is message


def test_complete_returns_valid_tool_calls_unchanged(configured_transport, monkeypatch):
    message = {"content": None, "tool_calls": [{
        "id": "read-stock", "type": "function", "function": {"name": "get_stock", "arguments": "{}"},
    }]}
    monkeypatch.setattr(agent_llm.httpx, "post", lambda *args, **kwargs: provider_response({"choices": [{"message": message}]}))
    assert configured_transport.complete([], DECLARED_TOOLS) is message


@pytest.mark.parametrize("payload", [
    [], {}, {"choices": []}, {"choices": [None]}, {"choices": [{}]}, {"choices": [{"message": None}]},
    {"choices": [{"message": {}}]}, {"choices": [{"message": {"role": "user", "content": "Not an assistant reply"}}]},
    {"choices": [{"message": {"content": None}}]}, {"choices": [{"message": {"tool_calls": []}}]},
    {"choices": [{"message": {"content": {}}}]}, {"choices": [{"message": {"tool_calls": None}}]},
    {"choices": [{"message": {"tool_calls": [{"id": "", "type": "function", "function": {"name": "get_stock", "arguments": "{}"}}]}}]},
    {"choices": [{"message": {"tool_calls": [{"id": " ", "type": "function", "function": {"name": "get_stock", "arguments": "{}"}}]}}]},
    {"choices": [{"message": {"tool_calls": [{"id": "call", "type": "function", "function": {"name": " ", "arguments": "{}"}}]}}]},
    {"choices": [{"message": {"tool_calls": [{"id": 1, "type": "function", "function": {}}]}}]},
    {"choices": [{"message": {"tool_calls": [{"id": "call", "type": "other", "function": {}}]}}]},
    {"choices": [{"message": {"tool_calls": [{"id": "call", "type": "function", "function": None}]}}]},
    {"choices": [{"message": {"tool_calls": [{"id": "call", "type": "function", "function": {"name": 1, "arguments": "{}"}}]}}]},
    {"choices": [{"message": {"tool_calls": [{"id": "call", "type": "function", "function": {"name": "read", "arguments": 1}}]}}]},
    {"choices": [{"message": {"tool_calls": [{"id": "call", "type": "function", "function": {"name": "read", "arguments": "not-json"}}]}}]},
    {"choices": [{"message": {"tool_calls": [
        {"id": "same-call", "type": "function", "function": {"name": "get_stock", "arguments": "{}"}},
        {"id": "same-call", "type": "function", "function": {"name": "get_stock", "arguments": "{}"}},
    ]}}]},
])
def test_complete_rejects_malformed_provider_payloads(configured_transport, monkeypatch, payload):
    monkeypatch.setattr(agent_llm.httpx, "post", lambda *args, **kwargs: provider_response(payload))
    with pytest.raises(AssistantProviderError) as error:
        configured_transport.complete([], DECLARED_TOOLS)
    assert str(error.value) == SAFE_MESSAGE


@pytest.mark.parametrize("constant", ("NaN", "Infinity", "-Infinity"))
def test_complete_rejects_non_standard_json_constants(configured_transport, monkeypatch, constant):
    payload = {"choices": [{"message": {"tool_calls": [{
        "id": "invalid-number", "type": "function", "function": {"name": "get_stock", "arguments": f'{{"quantity": {constant}}}'},
    }]}}]}
    monkeypatch.setattr(agent_llm.httpx, "post", lambda *args, **kwargs: provider_response(payload))
    with pytest.raises(AssistantProviderError) as error:
        configured_transport.complete([], DECLARED_TOOLS)
    assert str(error.value) == SAFE_MESSAGE


def test_complete_rejects_an_undeclared_tool_call(configured_transport, monkeypatch):
    payload = {"choices": [{"message": {"tool_calls": [{
        "id": "unknown-call", "type": "function", "function": {"name": "delete_store", "arguments": "{}"},
    }]}}]}
    monkeypatch.setattr(agent_llm.httpx, "post", lambda *args, **kwargs: provider_response(payload))
    with pytest.raises(AssistantProviderError) as error:
        configured_transport.complete([], DECLARED_TOOLS)
    assert str(error.value) == SAFE_MESSAGE


def test_complete_rejects_invalid_json(configured_transport, monkeypatch):
    response = SimpleNamespace(raise_for_status=lambda: None, json=lambda: (_ for _ in ()).throw(json.JSONDecodeError("bad", "not-json", 0)))
    monkeypatch.setattr(agent_llm.httpx, "post", lambda *args, **kwargs: response)
    with pytest.raises(AssistantProviderError) as error:
        configured_transport.complete([], DECLARED_TOOLS)
    assert str(error.value) == SAFE_MESSAGE


def test_complete_translates_timeout_without_provider_detail(configured_transport, monkeypatch):
    monkeypatch.setattr(agent_llm.httpx, "post", lambda *args, **kwargs: (_ for _ in ()).throw(httpx.ReadTimeout("provider timed out")))
    with pytest.raises(AssistantProviderError) as error:
        configured_transport.complete([], DECLARED_TOOLS)
    assert str(error.value) == SAFE_MESSAGE
    assert "provider timed out" not in str(error.value)


def test_complete_translates_non_success_response(configured_transport, monkeypatch):
    request = httpx.Request("POST", "https://fixture.invalid/chat/completions")
    response = httpx.Response(503, request=request)
    monkeypatch.setattr(agent_llm.httpx, "post", lambda *args, **kwargs: SimpleNamespace(
        raise_for_status=lambda: (_ for _ in ()).throw(httpx.HTTPStatusError("provider unavailable", request=request, response=response)),
    ))
    with pytest.raises(AssistantProviderError) as error:
        configured_transport.complete([], DECLARED_TOOLS)
    assert str(error.value) == SAFE_MESSAGE
    assert "provider unavailable" not in str(error.value)


@pytest.fixture
def workspace():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(email="agent-llm@example.test", name="Merchant")
        db.add(user)
        db.flush()
        site = content.create_site(db, user.id, "Agent test", "agent-llm-test")
        db.commit()
        yield db, user, site
    engine.dispose()


def test_malformed_provider_response_rolls_back_before_tools_or_database_writes(configured_transport, monkeypatch, workspace):
    db, user, site = workspace
    page = content.site_pages(db, site)[0]
    original_draft = copy.deepcopy(page.draft_json)
    monkeypatch.setattr(agent_llm.httpx, "post", lambda *args, **kwargs: provider_response({"choices": [{"message": {"tool_calls": [{
        "id": "unknown-call", "type": "function", "function": {"name": "delete_store", "arguments": "{}"},
    }]}}]}))
    events = list(run_turn(db, site, user, "Please update the heading", page_id=page.id, transport=configured_transport))
    assert [event["type"] for event in events] == ["conversation", "step_started", "error"]
    assert events[-1]["message"] == SAFE_MESSAGE
    assert not [event for event in events if event["type"] == "tool_called"]
    assert db.query(AgentConversation).count() == 0
    assert db.query(AgentMessage).count() == 0
    assert page.draft_json == original_draft


def test_openai_requests_use_completion_token_limit_without_temperature(monkeypatch):
    sent = {}

    def fake_post(url, **kwargs):
        sent.update(kwargs["json"], url=url)
        return provider_response({"choices": [{"message": {"content": "Done"}}]})

    monkeypatch.setattr(agent_llm.httpx, "post", fake_post)
    transport = XAITransport(ModelConfig("openai", "https://api.openai.com/v1", "sk-test",
                                         "gpt-4.1-mini", "store"))
    transport.complete([], DECLARED_TOOLS)
    assert sent["url"] == "https://api.openai.com/v1/chat/completions"
    assert sent["max_completion_tokens"] == 4000
    assert "temperature" not in sent and "max_tokens" not in sent


def test_custom_endpoint_is_rechecked_before_every_request(monkeypatch):
    # A hostname that later resolves to a private address must never receive the key.
    monkeypatch.setattr(assistant_provider.socket, "getaddrinfo",
                        lambda *args, **kwargs: [(None, None, None, None, ("10.0.0.5", 443))])
    monkeypatch.setattr(agent_llm.httpx, "post", lambda *args, **kwargs: pytest.fail("request sent"))
    transport = XAITransport(ModelConfig("custom", "https://models.example.com/v1", "secret-key",
                                         "model", "store"))
    with pytest.raises(AssistantProviderError):
        transport.complete([], DECLARED_TOOLS)


def test_transport_without_any_connection_fails_safely(monkeypatch):
    monkeypatch.setattr(assistant_provider, "platform_config", lambda: None)
    monkeypatch.setattr(agent_llm, "platform_config", lambda: None)
    with pytest.raises(AssistantProviderError):
        XAITransport().complete([], DECLARED_TOOLS)
