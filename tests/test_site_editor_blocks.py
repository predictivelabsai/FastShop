"""Visual editor schemas, strict nested documents and theme compatibility."""

import copy
from types import SimpleNamespace

import pytest
from fasthtml.common import to_xml
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.compliance import assert_document_compliant
from app.content import create_page, create_site, site_pages
from app.models import Base, User
from app.services import CommerceError
from app.site_block_schema import BLOCK_SCHEMAS, default_block, editor_schema
from app.site_blocks import (
    BLOCK_TYPES,
    get_block,
    normalize_document,
    patch_block,
    remove_block,
    resolve_document,
    validate_block,
)
from app.site_ui import render_section
from app.site_ui_liquid import render_block


@pytest.fixture()
def context():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        owner = User(email="widgets@example.test", name="Editor")
        db.add(owner)
        db.flush()
        site = create_site(db, owner.id, "Widgets", "widgets")
        page = site_pages(db, site)[0]
        yield db, site, page
    engine.dispose()


def document(*blocks):
    return {"version": 1, "title": "Your page", "blocks": list(blocks)}


def test_schema_completeness_and_copy_isolation():
    assert set(BLOCK_SCHEMAS) == set(BLOCK_TYPES)
    schemas = editor_schema()
    schemas["heading"]["defaults"]["heading"] = "Changed"
    assert default_block("heading")["heading"] == "Your heading"
    for schema in BLOCK_SCHEMAS.values():
        assert schema["category"] and schema["icon"] and schema["description"]
        assert set(schema["groups"]) == {"Content", "Style", "Advanced"}
        for field in schema["fields"]:
            assert {"id", "label", "type", "default", "help"} <= field.keys() or field[
                "type"
            ] == "repeater"
            for item_field in field.get("fields", []):
                if item_field["id"] == "theme":
                    assert item_field["type"] == "select" and item_field["options"]


@pytest.mark.parametrize("kind", BLOCK_TYPES)
def test_every_default_validates_and_renders_in_both_themes(context, kind):
    db, site, page = context
    block = default_block(kind)
    assert validate_block(block) == block
    normalised = normalize_document(document(block))
    if BLOCK_SCHEMAS[kind]["insertable"]:
        assert_document_compliant(normalised)
    default = render_section(db, site, page, site.settings_json, block, "", "csrf", preview=True)
    liquid = render_block(
        db, site, page, site.settings_json, block, "", "csrf", False, preview=True
    )
    for node in (default, liquid):
        assert node is not None
        nodes = node if isinstance(node, list) else [node]
        markup = "".join(to_xml(n) for n in nodes)
        assert f'data-block-id="{block["id"]}"' in markup


def test_nested_operations_localisation_and_inline_hooks(context):
    db, site, page = context
    heading = default_block("heading", "heading") | {"heading": {"en": "Hello", "et": "Tere"}}
    nested = default_block("container", "outer") | {
        "children": [default_block("container", "inner") | {"children": [heading]}]
    }
    original = document(nested)
    snapshot = copy.deepcopy(original)
    changed = patch_block(original, "heading", {"heading": "Welcome"}, locale="en")
    assert get_block(changed, "heading")["heading"] == {"en": "Welcome", "et": "Tere"}
    assert original == snapshot
    resolved = resolve_document(changed, "et")
    rendered = render_section(db, site, page, {}, resolved["blocks"][0], "", "csrf", True)
    markup = to_xml(rendered)
    assert 'data-block-id="heading"' in markup and 'data-field="heading"' in markup
    assert "Tere" in markup
    assert get_block(remove_block(changed, "heading"), "inner")["children"] == []


def test_depth_duplicates_node_caps_and_container_restrictions():
    leaf = default_block("heading", "leaf")
    valid = default_block("container", "outer") | {
        "children": [default_block("container", "inner") | {"children": [leaf]}]
    }
    normalize_document(document(valid))
    invalid = default_block("container", "another") | {"children": [valid]}
    with pytest.raises(CommerceError, match="three levels"):
        normalize_document(document(invalid))
    with pytest.raises(CommerceError, match="unique"):
        normalize_document(document(valid, leaf))
    many = [default_block("heading", f"item-{i}") for i in range(200)]
    with pytest.raises(CommerceError, match="200 total"):
        normalize_document(document(default_block("container") | {"children": many}))
    with pytest.raises(CommerceError, match="Only containers"):
        validate_block(leaf | {"children": []})
    with pytest.raises(CommerceError, match="generic widgets"):
        validate_block(default_block("container") | {"children": [default_block("hero")]})


@pytest.mark.parametrize(
    "style",
    [
        {"padding_top": -1},
        {"padding_top": True},
        {"max_width": 2401},
        {"background_colour": "red"},
        {"background_colour": "#12345"},
        {"background_image": "javascript:alert(1)"},
        {"hide_mobile": "true"},
        {"anchor_id": "a onclick=evil"},
        {"css_class": "two classes"},
        {"unknown": 1},
    ],
)
def test_unsafe_styles_rejected(style):
    with pytest.raises(CommerceError):
        validate_block(default_block("heading") | {"style": style})


def test_valid_styles_and_safe_markup(context):
    db, site, page = context
    block = default_block("richtext") | {
        "body": '<p onclick="evil()">Hello <strong>world</strong><script>alert(1)</script><img src=x onerror=evil><a href="javascript:evil">click</a></p>',
        "style": {
            "padding_top": 40,
            "background_colour": "#abc",
            "background_image": "/static/photo.jpg",
            "hide_mobile": True,
            "anchor_id": "story",
            "css_class": "story-copy",
        },
    }
    block = validate_block(block)
    assert "<strong>world</strong>" in block["body"]
    assert (
        "script" not in block["body"]
        and "onclick" not in block["body"]
        and "javascript" not in block["body"]
    )
    markup = to_xml(render_section(db, site, page, {}, block, "", "", True))
    assert "padding-top:40px" in markup and "background-color:#abc" in markup
    assert "fs-hide-mobile" in markup and 'id="story"' in markup


def test_legacy_documents_keep_original_data_and_rendering(context):
    db, site, page = context
    legacy = {
        "title": "Story",
        "sections": [{"type": "text", "heading": "Our story", "body": "Our content."}],
    }
    snapshot = copy.deepcopy(legacy)
    canonical = normalize_document(legacy)
    assert canonical == normalize_document(canonical) and legacy == snapshot
    old = render_section(db, site, page, {}, canonical["blocks"][0], "", "")
    assert "Our story" in to_xml(old) and "data-block-id" not in to_xml(old)
    assert "style=" not in to_xml(old)


def test_seeded_liquid_documents_load_and_render_unchanged(context):
    from app.site_liquid_editorial_content import editorial_documents
    from app.site_liquid_products_content import product_documents
    from app.site_liquid_support_content import support_documents
    from app.site_seed_liquid import documents

    db, site, _ = context
    for path, title, kind, blocks in [
        *documents(),
        *product_documents(),
        *support_documents(),
        *editorial_documents(),
    ]:
        source = document(*blocks) | {"title": title}
        snapshot = copy.deepcopy(source)
        normalised = normalize_document(source)
        assert source == snapshot and normalize_document(normalised) == normalised
        page = SimpleNamespace(path=path, kind=kind, product_id=None)
        for block in normalised["blocks"]:
            assert (
                render_block(db, site, page, site.settings_json, block, "", "", False) is not None
            )
            assert render_section(db, site, page, site.settings_json, block, "", "") is not None


def test_newsletter_reuses_consent_flow_and_disables_preview(context):
    db, site, page = context
    block = default_block("newsletter")
    live = to_xml(render_section(db, site, page, {}, block, "/sites/widgets", "csrf"))
    preview = to_xml(render_section(db, site, page, {}, block, "/sites/widgets", "csrf", True))
    assert 'action="/sites/widgets/newsletter"' in live
    assert 'name="marketing_consent"' in live and 'name="csrf_token"' in live
    assert "disabled" in preview


@pytest.mark.parametrize(
    "kind,patch",
    [
        ("heading", {"columns": 999}),
        ("heading", {"level": []}),
        ("heading", {"alignment": {}}),
        ("image", {"aspect": {}}),
        ("container", {"vertical_alignment": []}),
        ("icon_list", {"items": "bad"}),
        ("icon_list", {"items": [{"theme": {"en": "check"}}]}),
        ("product_grid", {"products": [123]}),
        ("product_card", {"product": "../unsafe"}),
    ],
)
def test_widget_settings_reject_invalid_types_with_domain_errors(kind, patch):
    with pytest.raises(CommerceError):
        validate_block(default_block(kind) | patch)


def test_liquid_theme_settings_use_existing_validated_tokens(context):
    from app.site_theme import validate_theme
    from app.site_ui_liquid import storefront

    db, site, page = context
    page.draft_json = document(default_block("heading"))
    site.settings_json = site.settings_json | {
        "design": validate_theme(
            {"accent": "#123456", "width": "narrow", "font": "humanist", "radius": "rounded"}
        )
    }
    markup = "".join(
        to_xml(node)
        for node in storefront(db, site, page, "", "", "https://example.test/", preview=True)
    )
    assert 'data-themed="true"' in markup
    assert "--store-accent:#123456" in markup and "--store-width:960px" in markup
    assert "--store-font:Verdana, sans-serif" in markup and "--store-radius:18px" in markup


def test_liquid_theme_default_preserves_original_settings(context):
    from app.site_ui_liquid import storefront

    db, site, page = context
    page.draft_json = document(default_block("heading"))
    markup = "".join(
        to_xml(node)
        for node in storefront(db, site, page, "", "", "https://example.test/", preview=True)
    )
    assert 'data-themed="' not in markup and "--store-accent:" not in markup


def test_each_liquid_repeater_variant_has_valid_defaults():
    for kind, schema in BLOCK_SCHEMAS.items():
        if not schema["insertable"]:
            continue
        for field in schema["fields"]:
            if field["type"] != "repeater":
                continue
            for entry in field.get("theme_defaults", {}).values():
                validate_block(default_block(kind) | {"items": [entry]})


def test_every_addable_repeater_entry_renders_in_both_themes(context):
    db, site, page = context
    for kind, schema in BLOCK_SCHEMAS.items():
        if not schema["insertable"]:
            continue
        for field in schema["fields"]:
            if field["id"] != "items":
                continue
            variants = list(field.get("theme_defaults", {}).values()) or [field["item_default"]]
            for entry in variants:
                block = validate_block(default_block(kind) | {"items": [entry]})
                assert render_section(db, site, page, {}, block, "", "", True) is not None
                assert render_block(db, site, page, {}, block, "", "", False, True) is not None


@pytest.mark.parametrize("kind", ["manual", "automated", "legacy"])
def test_product_grid_collection_membership_and_legacy_ids(context, kind):
    from app.catalog_admin import save_collection
    from app.models import Category, Product, ProductType

    db, site, page = context
    category = Category(tenant_id=site.tenant_id, name="Legacy", slug="legacy")
    product_type = ProductType(tenant_id=site.tenant_id, name="Type", slug="type")
    db.add_all([category, product_type])
    db.flush()
    products = [Product(tenant_id=site.tenant_id, category_id=category.id,
        product_type_id=product_type.id, name=name, slug=name.lower(), is_published=True)
        for name in ("Included", "Excluded")]
    db.add_all(products)
    db.flush()
    for product in products:
        published = create_page(
            db, site, product.name, "/products/" + product.slug, "product")
        published.product_id = product.id
        published.published_json = dict(published.draft_json)
    if kind == "legacy":
        identifier = category.id
    else:
        collection = save_collection(db, site, {
            "title": "Featured", "handle": "featured", "kind": kind,
            "product_ids": [products[0].id],
            "rules_json": {"match": "all", "rules": [
                {"field": "title", "operator": "equals", "value": "Included"}]},
        })
        identifier = collection.id
    block = default_block("product_grid") | {"collection": identifier, "limit": 1}
    markup = to_xml(render_section(db, site, page, {}, block, "", "csrf", preview=True))
    assert "Included" in markup and "Excluded" not in markup
    if kind != "legacy":
        block = default_block("collection_list") | {"collections": [identifier]}
        markup = to_xml(render_section(db, site, page, {}, block, "", "csrf", preview=True))
        assert "Featured" in markup and "/collections/featured" in markup


def test_collection_list_schema_and_id_validation():
    field = next(f for f in editor_schema()["collection_list"]["fields"] if f["id"] == "collections")
    assert field["value_list"] and field["fields"][0]["type"] == "collection"
    with pytest.raises(CommerceError, match="valid collections"):
        validate_block(default_block("collection_list") | {"collections": ["../private"]})


def test_collection_widgets_do_not_render_foreign_or_private_collections(context):
    from app.models import Collection

    db, site, page = context
    foreign_user = User(email="foreign-widget@example.test", name="Other owner")
    db.add(foreign_user)
    db.flush()
    other = create_site(db, foreign_user.id, "Foreign", "foreign-widget-store")
    foreign = Collection(tenant_id=other.tenant_id, title="Foreign collection", handle="foreign")
    private = Collection(tenant_id=site.tenant_id, title="Private collection", handle="private",
                         is_published=False)
    db.add_all([foreign, private])
    db.flush()
    for collection in (foreign, private):
        for kind in ("product_grid", "collection_list"):
            values = {"collection": collection.id} if kind == "product_grid" else {"collections": [collection.id]}
            markup = to_xml(render_section(db, site, page, {}, default_block(kind) | values,
                                           "", "csrf", preview=True))
            assert "Choose " in markup
            assert collection.title not in markup
