"""Presentation contracts for merchant assistant task starters."""

from types import SimpleNamespace

from fasthtml.common import to_xml

from app import store_agent_admin

CATEGORIES = (
    ("agent-category-read", "Read", "Get a read-only answer", "What should I know about my store right now?"),
    ("agent-category-draft", "Draft", "Prepare a change for review", "Help me draft a change to my store for review."),
    ("agent-category-confirm", "Confirm", "Changes wait for approval", "Which store changes need my approval before they happen?"),
    ("agent-category-route", "Route", "Find the right admin area", "Which admin area should I use to update my store settings?"),
    ("agent-category-layout", "Layout", "Draft a layout change, then preview it",
     "Help me draft a layout change for my store. Keep it private, prepare a preview, and leave the public-store "
     "publication decision to me as the merchant."),
    ("agent-category-media", "Media", "Choose existing media for a draft preview",
     "Help me choose existing media for a page draft. Keep it private, prepare a preview, and leave the public-store "
     "publication decision to me as the merchant."),
    ("agent-category-copy", "Copy", "Draft page copy, then preview it",
     "Help me improve the copy on a page draft. Keep it private, prepare a preview, and leave the public-store "
     "publication decision to me as the merchant."),
)


def configured_markup(monkeypatch):
    site = SimpleNamespace(id="store-one")
    page = SimpleNamespace(id="page-one")
    return (str(to_xml(store_agent_admin.page(site, page, "csrf-token", configured=True)[0])),
            str(store_agent_admin.drawer(site.id, "csrf-token", configured=True)))


def assert_category_starters(html):
    assert 'id="agent-category-starters"' in html
    assert html.count("data-agent-starter=") == len(CATEGORIES)
    assert html.count('class="agent-category-starter"') == len(CATEGORIES)
    assert 'role="group"' in html
    assert 'aria-label="Assistant task categories and design workflow"' in html
    for identifier, category, outcome, prompt in CATEGORIES:
        assert f'id="{identifier}"' in html
        assert f'data-agent-starter="{prompt}"' in html
        assert category in html
        assert outcome in html


def test_configured_full_and_drawer_render_category_starters_without_removing_chat_controls(monkeypatch):
    full, drawer = configured_markup(monkeypatch)
    assert_category_starters(full)
    assert "Conversations" in full
    assert "Task queue" in full
    assert 'data-agent-tasks=""' in full
    assert "Assistant findings become assigned work here." in full
    assert "data-tasks=" in full
    assert 'id="agent-conversations"' in full
    assert 'data-agent-form=""' in full
    assert 'class="agent-composer-row agent-composer"' in full
    assert 'name="attachment"' in full
    assert 'data-drop-ready="true"' in full
    assert 'accept="image/jpeg,image/png,image/webp,.pdf,.docx,.xlsx,.csv,.txt,.md"' in full
    assert 'name="attachment_alt"' in full
    assert "Documents are extracted as private assistant reference text." in full
    assert "Private sources" in full
    assert 'data-agent-sources=""' in full
    assert 'data-agent-draft-review=""' in full
    assert 'data-agent-preview-viewport="desktop"' in full
    assert 'data-agent-preview-viewport="mobile"' in full
    assert 'src="/admin/sites/store-one/preview/page-one"' in full
    assert "Publish remains available only in the page editor for the merchant to decide." in full
    assert_category_starters(drawer)
    assert "data-agent-select" in drawer
    assert 'data-agent-form=""' in drawer
    assert 'class="agent-composer-row agent-composer"' in drawer
    assert 'name="attachment"' in drawer
    assert 'name="attachment_alt"' in drawer
    assert 'data-agent-draft-review=""' not in drawer


def test_design_workflow_starters_keep_draft_preview_and_merchant_publication_separate(monkeypatch):
    full, drawer = configured_markup(monkeypatch)
    for html in (full, drawer):
        assert "Design workflow" in html
        assert "The assistant prepares a private draft." in html
        assert "A merchant, not the assistant, decides whether to publish it to the public storefront." in html
        assert "Draft a layout change, then preview it" in html
        assert "Choose existing media for a draft preview" in html
        assert "Draft page copy, then preview it" in html
        assert "custom section" not in html.lower()


def test_unconfigured_assistant_links_to_its_settings_instead_of_the_chat(monkeypatch):
    site = SimpleNamespace(id="store-one")
    page = SimpleNamespace(id="page-one")
    full = str(to_xml(store_agent_admin.page(site, page, "csrf-token")[0]))
    drawer = str(store_agent_admin.drawer(site.id, "csrf-token"))
    for html in (full, drawer):
        assert "Connect an AI model to use the assistant" in html
        assert 'href="/admin/sites/store-one/settings/assistant"' in html
        assert "operator" not in html
        assert "data-agent-starter" not in html
        assert "agent-category-starter" not in html
        assert 'data-agent-form=""' not in html
    assert 'data-agent-configured="false"' in full and 'data-agent-configured="false"' in drawer


def test_assistant_studio_puts_a_live_draft_preview_beside_the_chat(monkeypatch):
    site = SimpleNamespace(id="store-one")
    home = SimpleNamespace(id="page-one", title="Home", path="/")
    about = SimpleNamespace(id="page-two", title="About us", path="/about")
    html = str(to_xml(store_agent_admin.page(site, home, "csrf-token", [home, about], configured=True)[0]))
    assert 'class="agent-studio"' in html
    assert html.index("agent-studio-chat") < html.index("agent-studio-preview")
    assert 'data-agent-page-select=""' in html and ">About us<" in html
    assert 'data-agent-preview-refresh=""' in html
    assert 'href="/admin/sites/store-one/editor"' in html
    assert "↑" not in html and "＋" not in html
    drawer = str(store_agent_admin.drawer(site.id, "csrf-token", configured=True))
    assert "＋" not in drawer and ">×<" not in drawer


def test_unconfigured_assistant_still_shows_the_draft_preview(monkeypatch):
    site = SimpleNamespace(id="store-one")
    html = str(to_xml(store_agent_admin.page(site, SimpleNamespace(id="page-one"), "csrf-token")[0]))
    assert "Connect an AI model to use the assistant" in html
    assert 'src="/admin/sites/store-one/preview/page-one"' in html
