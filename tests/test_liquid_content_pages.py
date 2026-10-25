"""M3 source fidelity, editable content, link integrity and genuine contact flow."""

import copy
import html
import json
import re
from html.parser import HTMLParser
from urllib.parse import urldefrag

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from starlette.testclient import TestClient

from app import site_routes, store_checkout_routes
from app.models import Base, SiteContact, SitePage, User
from app.services import CommerceError
from app.site_blocks import normalize_document, patch_block, validate_block
from app.site_liquid_editorial_content import editorial_documents
from app.site_liquid_support_content import CONTENT, support_documents
from app.site_seed_liquid import seed_h24you_liquid
from app.site_ui_liquid_support import faq_structured_data

DOCUMENTS = support_documents() + editorial_documents()
BASE = "/sites/h24you-liquid"


class Links(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.links = []
        self.ids = set()
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.add(attrs["id"])
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app.main import app

    engine = create_engine(
        f"sqlite:///{tmp_path / 'content.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(site_routes, "SessionLocal", sessions)
    monkeypatch.setattr(store_checkout_routes, "SessionLocal", sessions)
    with Session(engine) as db:
        owner = User(email="content-owner@example.test", name="Content fixture")
        db.add(owner)
        db.flush()
        seed_h24you_liquid(db, owner.email)
        db.commit()
    with TestClient(app) as browser:
        yield browser, sessions
    engine.dispose()


@pytest.mark.parametrize("path,title,kind,blocks", DOCUMENTS)
def test_canonical_m3_documents_render(client, path, title, kind, blocks):
    browser, sessions = client
    doc = normalize_document({"version": 1, "title": title, "blocks": blocks})
    assert doc["blocks"] == blocks
    assert all("\u2014" not in json.dumps(b, ensure_ascii=False) for b in blocks)
    response = browser.get(BASE + path)
    assert response.status_code == 200
    assert 'data-pack="liquid"' in response.text
    assert response.text.count("<h1") == 1
    assert "not intended to diagnose, treat, cure, or prevent any disease." in response.text
    with sessions() as db:
        page = db.scalar(select(SitePage).where(SitePage.path == path))
        assert page.kind == kind and page.published_json == page.draft_json


def test_faq_registry_html_and_jsonld_parity_after_merchant_edit(client):
    browser, sessions = client
    expected = [(q["question"], q["answer"])
                for c in CONTENT["faqPage"]["categories"] for q in c["questions"]]
    with sessions() as db:
        page = db.scalar(select(SitePage).where(SitePage.path == "/faq"))
        doc = copy.deepcopy(page.published_json)
        question = doc["blocks"][1]["items"][0]
        assert (question["heading"], question["body"]) == expected[0]
        question["heading"] = "Merchant question <example>?"
        question["body"] = "Merchant answer </script><script>alert(1)</script>"
        page.published_json = doc
        db.commit()
        expected[0] = (question["heading"], question["body"])
    response = browser.get(BASE + "/faq")
    payload = re.search(r'<script type="application/ld\+json">(.*?)</script>',
                        response.text, re.S).group(1)
    schema = json.loads(payload)
    assert [(q["name"], q["acceptedAnswer"]["text"]) for q in schema["mainEntity"]] == expected
    assert "<script>alert" not in response.text
    for question, answer in expected:
        assert html.escape(question, quote=False) in response.text
        # Inline product-name markup can split running text in the HTML.
        visible = html.unescape(re.sub(r"<[^>]+>", "", response.text))
        assert answer in visible


def test_empty_and_hidden_faq_categories_do_not_invent_answers(client):
    browser, sessions = client
    with sessions() as db:
        page = db.scalar(select(SitePage).where(SitePage.path == "/faq"))
        document = copy.deepcopy(page.published_json)
        document["blocks"][1]["items"] = []
        document["blocks"][2]["hidden"] = True
        page.published_json = document
        db.commit()
    response = browser.get(BASE + "/faq")
    assert 'href="' + BASE + '/contact"' in response.text
    schema = faq_structured_data(document["blocks"])
    assert all(q["name"] not in {x["question"] for c in CONTENT["faqPage"]["categories"][:2]
                                for x in c["questions"]} for q in schema["mainEntity"])


def test_every_internal_site_link_and_fragment_resolves(client):
    browser, sessions = client
    with sessions() as db:
        paths = list(db.scalars(select(SitePage.path)))
    seen = {}
    for path in paths:
        response = browser.get(BASE + path)
        assert response.status_code == 200, path
        seen[BASE + path] = Links(response.text)
        assert not {"#", BASE + "/#"} & set(seen[BASE + path].links), path
    for markup in list(seen.values()):
        for link in markup.links:
            path, fragment = urldefrag(link)
            if not path.startswith(BASE):
                continue
            if path not in seen:
                response = browser.get(path)
                assert response.status_code < 400, link
                seen[path] = Links(response.text)
            if fragment:
                assert fragment in seen[path].ids, link
    redirect = browser.get(BASE + "/4", follow_redirects=False)
    assert redirect.status_code == 301 and redirect.headers["location"] == BASE + "/"


def test_newsletter_uses_a_configured_policy_destination():
    from fasthtml.common import to_xml

    from app.site_seed_liquid import CONTENT as SHELL
    from app.site_ui_liquid import newsletter

    shell = copy.deepcopy(SHELL)
    shell["offer"]["privacyHref"] = "/pages/privacy-policy"
    markup = str(to_xml(newsletter(shell, BASE, "csrf", "test-email", False)))
    assert BASE + "/pages/privacy-policy" in Links(markup).links
    shell["offer"]["privacyHref"] = "#"
    markup = str(to_xml(newsletter(shell, BASE, "csrf", "test-email", False)))
    assert "#" not in Links(markup).links


@pytest.mark.parametrize("state", ["sent", "failed", "awaiting_configuration"])
def test_contact_stores_before_delivery_with_topic_and_honest_status(client, monkeypatch, state):
    browser, sessions = client
    delivered = []

    def deliver(entry, config):
        with sessions() as db:
            assert db.get(SiteContact, entry.id) is not None
        delivered.append((entry.message, config["email"]))
        return state

    monkeypatch.setattr(site_routes, "deliver", deliver)
    page = browser.get(BASE + "/contact")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    form = {"csrf_token": token, "name": "Visitor", "email": "visitor@example.test",
            "topic": CONTENT["contactPage"]["topics"][0], "order": "#10234",
            "message": "A real validation test message.", "website": ""}
    response = browser.post(BASE + "/contact", data=form)
    assert response.status_code == 200 and "Your message is saved in our inbox." in response.text
    assert "contact-form" in response.text and "Quick answers" in response.text
    assert ("Email delivery is pending" in response.text) is (state != "sent")
    assert delivered[0][0] == f"Topic: {form['topic']}\nOrder number: #10234\n\n{form['message']}"
    with sessions() as db:
        entry = db.scalar(select(SiteContact))
        assert entry.status == state and entry.attempts == 1
    assert browser.post(BASE + "/contact", data=form).status_code == 429


@pytest.mark.parametrize("patch", [
    {"csrf_token": "wrong"}, {"website": "bot"}, {"name": ""},
    {"email": "bad@email"}, {"message": "short"}, {"message": "x" * 5001},
    {"topic": ""}, {"topic": "Invented topic"}, {"order": "x" * 161},
    {"order": "one\ntwo"}, {"email": "x@x.a"},
])
def test_contact_rejections_never_save_or_send(client, monkeypatch, patch):
    browser, sessions = client
    monkeypatch.setattr(site_routes, "deliver", lambda *_: pytest.fail("Must not send"))
    page = browser.get(BASE + "/contact")
    token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
    form = {"csrf_token": token, "name": "Visitor", "email": "visitor@example.test",
            "topic": CONTENT["contactPage"]["topics"][0], "order": "",
            "message": "A validation test message.", "website": ""} | patch
    assert browser.post(BASE + "/contact", data=form).status_code == 400
    with sessions() as db:
        assert db.scalar(select(func.count()).select_from(SiteContact)) == 0


def test_m3_seed_preserves_merchant_pages(client):
    _, sessions = client
    with sessions() as db:
        page = db.scalar(select(SitePage).where(SitePage.path == "/about"))
        site = seed_h24you_liquid(db, "content-owner@example.test")
        page.draft_json = patch_block(page.draft_json, page.draft_json["blocks"][0]["id"],
                                     {"body": "Merchant founder story"})
        expected = copy.deepcopy(page.draft_json)
        seed_h24you_liquid(db, "content-owner@example.test")
        assert page.draft_json == expected
        assert site.slug == "h24you-liquid"


@pytest.mark.parametrize("path,title,kind,blocks", DOCUMENTS)
def test_m3_blocks_reject_executable_urls(client, path, title, kind, blocks):
    for block in blocks:
        with pytest.raises(CommerceError):
            validate_block(block | {"link": "javascript:alert(1)"})
        for entry in block.get("items", []):
            if "url" in entry:
                invalid = copy.deepcopy(block)
                invalid["items"][block["items"].index(entry)]["url"] = "javascript:alert(1)"
                with pytest.raises(CommerceError):
                    validate_block(invalid)


@pytest.mark.parametrize("change", ["layout", "duplicate", "missing", "theme", "number"])
def test_editorial_structure_rejects_invalid_entries(change):
    blocks = editorial_documents()[1][3]
    block = copy.deepcopy(next(b for b in blocks if b["layout"] == "research"))
    if change == "layout":
        block["layout"] = "custom-script"
    elif change == "duplicate":
        block["items"].append(copy.deepcopy(block["items"][0]))
    elif change == "missing":
        block["items"].pop()
    elif change == "theme":
        block["items"][0]["theme"] = {"en": "unsafe"}
    else:
        next(i for i in block["items"] if i["theme"].endswith(".people"))["body"] = "NaN"
    with pytest.raises(CommerceError):
        validate_block(block)


@pytest.mark.parametrize("change", ["theme", "label", "duplicate", "link-target"])
def test_support_structure_rejects_invalid_entries(change):
    blocks = support_documents()[1][3]
    block = copy.deepcopy(blocks[1])
    if change == "theme":
        block["items"][0]["theme"] = {"en": "question"}
    elif change == "duplicate":
        block["items"].append(copy.deepcopy(block["items"][0]))
    elif change == "label":
        block["items"].append({"theme": "inline-link", "heading": block["items"][0]["value"],
                               "label": "", "url": "/faq"})
    else:
        block["items"].append({"theme": "inline-link", "heading": "missing-question",
                               "label": "Read more", "url": "/faq"})
    with pytest.raises(CommerceError):
        validate_block(block)


def test_optional_editorial_media_is_editable_and_does_not_render_list_literals():
    from fastcore.xml import to_xml

    from app.site_ui_liquid_editorial import render_editorial

    block = editorial_documents()[0][3][0]
    block = validate_block(block | {"image": "/static/h24you/liquid/science-bubbles-hero.jpg",
                                    "alt": "Bubble photograph"})
    markup = str(to_xml(render_editorial(block, BASE)))
    assert '<img src="/static/h24you/liquid/science-bubbles-hero.jpg"' in markup
    assert 'alt="Bubble photograph"' in markup
    assert "[" not in markup and "]" not in markup
