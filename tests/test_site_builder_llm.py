import json
from types import SimpleNamespace

import pytest

from app.integrations import site_builder_llm as adapter
from app.services import CommerceError


def test_model_receives_scoped_brief_and_returns_proposals(monkeypatch):
    monkeypatch.setattr(adapter, "settings", SimpleNamespace(xai_api_key="fixture-only", xai_base_url="https://fixture.invalid", model_name="fixture-model"))
    context = {"page_id": "owned-page", "section_id": "owned-section", "commerce": {"catalog": []},
        "snapshot": {"pages": {"owned-page": {"document": {"title": "Our site"}}},
            "settings": {"name": "Our site", "builder_brief": {"business": "Tea"}, "not_for_model": "private sentinel"}}}
    def fake_post(url, **kwargs):
        assert url == "https://fixture.invalid/chat/completions"
        data = kwargs["json"]
        serialized = json.dumps(data)
        assert "private sentinel" not in serialized
        assert "owned-page" in serialized and "owned-section" in serialized and "Tea" in serialized
        assert data["max_tokens"] == 4000 and kwargs["timeout"] == 35
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"choices": [{"message": {"content": json.dumps({
            "answer": "Review this", "operations": [], "proposals": [{"kind": "merchant", "values": {"shipping_minor": 1600}}]})}}]})
    monkeypatch.setattr(adapter.httpx, "post", fake_post)
    response, provider = adapter.respond("Shipping is $16", context, [])
    assert provider == "llm" and response["proposals"][0]["values"]["shipping_minor"] == 1600


def test_model_malformed_response_is_a_safe_error(monkeypatch):
    monkeypatch.setattr(adapter, "settings", SimpleNamespace(xai_api_key="fixture-only", xai_base_url="https://fixture.invalid", model_name="fixture-model"))
    monkeypatch.setattr(adapter.httpx, "post", lambda *a, **k: SimpleNamespace(raise_for_status=lambda: None,
        json=lambda: {"choices": [{"message": {"content": "not JSON"}}]}))
    with pytest.raises(CommerceError, match="draft is unchanged"):
        adapter.respond("Hello", {"page_id": "page", "snapshot": {"pages": {}, "settings": {}}}, [])
