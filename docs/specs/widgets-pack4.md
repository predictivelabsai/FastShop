# Showcase widgets, pack 4

This pack follows packs 1 to 3: widget definitions supply insertion defaults,
validation, picker metadata and editor fields; server renderers supply readable
content; conditional CSS and JavaScript progressively enhance the page. The
implementation references `app/site_widgets_pack1.py` for icons, video URL
conversion and hotspots, pack 2 for social platforms and validation, and pack 3
for scoped catalogue resources and integer minor unit prices. No migration is
required.

Files: `app/site_widgets_pack4.py` and `_definitions.py` own the declarations and
renderers; `app/site_widgets.py`, `app/site_ui.py` and `app/content.py` connect
validation, assets and rendering. `app/site_widget_routes.py` is registered by
`app/site_routes.py`. Pack 3 supplies the shared channel price helper. The picker
icons live in `static/editor/editor.js`; pack 4 assets, tests and browser script
follow the existing numbered pack names.

| Widget | Category | Behaviour |
| --- | --- | --- |
| Marquee | Marketing | One to ten short messages, optional icon separator, three speeds and either direction. The cloned strip is hidden from assistive technology. Hover, focus and an explicit pause control stop motion. Reduced motion and no JavaScript show static wrapping messages. |
| Collage | Media | Two to eight image tiles with alt text, optional headings and links. Four mosaic presets simplify on mobile. |
| Flip cards | Content | One to six cards with image or icon fronts and readable back content. A real button supports pointer, tap, Enter and Space, with `aria-expanded`. Reduced motion avoids rotation. |
| Call to action box | Marketing | Bordered or filled message, optional image and overlay, two actions and inline or stacked layout. |
| Video lightbox | Media | Poster and play link. Opening creates a privacy enhanced YouTube/Vimeo iframe or local MP4 video in a native modal dialog. Closing removes the media and restores focus. No third party video request occurs before opening. Without JavaScript, the link opens the supplied video. |
| Team (`team_members`) | Content | One to twelve generic member cards with photos, names, roles, biographies and up to three validated social links. Two to four desktop columns. Legacy `team` (People) documents retain their original validation, editor and rendering; the two types use distinct keys. |
| Pull quote | Content | An editorial figure with blockquote, optional attribution/source and plain, bordered or accent styling. It has no review rating. |
| Button group | Content | One to four links using existing button styles, alignment and mobile stacking/width choices. |
| Shop the look | Commerce | Lifestyle image with up to eight product hotspots and a plain product list. Products must be published on this site, owned by this tenant and priced in the site's channel. Links lead to product pages. |
| Recently viewed | Commerce | Up to eight published products viewed by the visitor on this site. The section is hidden when empty and explains its behaviour in editor preview. |

All widgets support containers, theme variables, inline text editing and complete
insertion defaults. Placeholder people use “Team member name” and “Role”. Copy
makes no invented merchant or customer claims. Interactive targets are at least
44 pixels and pages must fit a 360 pixel viewport.

## Recently viewed privacy and routing

Published site product pages record the product ID in the visitor's localStorage
only when any published page on that site contains a `recently_viewed` widget,
including inside a container. A tenant- and site-scoped query filters candidate
published documents before checking their block trees. Draft-only widgets do not
enable recording, and preview pages never record. Without a published widget,
product pages emit neither a recording marker nor the pack 4 script (unless
another interactive pack 4 widget on that page needs it). The key is
`fastshop:recently-viewed:{siteId}`. The newest product appears first; duplicates
are removed and the list is capped at eight IDs. Access is guarded with
try/catch so storage denial or corrupt data leaves the page usable. Nothing is
stored server-side. Existing lists are left untouched when the widget is removed.

Open question: should this storage additionally wait for cookie consent? The
existing consent model distinguishes essential, analytics and marketing storage
but does not clearly classify recently viewed preference storage. Consent gating
is unchanged pending that decision.

The widget requests `GET /sites/{slug}/widgets/recently-viewed?ids=...`. Host routed
stores use `/widgets/recently-viewed`, rewritten through `SiteHostMiddleware`.
The fragment route requires a published site; preview and other non-published
sites return 404. The shared storefront password gate also applies.
The route accepts at most eight UUID identifiers, rejects malformed or oversized
lists, preserves visitor order and returns an HTML fragment. Unknown IDs produce
an empty fragment. Responses use `Cache-Control: private, no-store` and `Vary:
Host`. Tenant, site publication and channel checks apply to every returned
product. Demo workspace commerce is a separate model and never supplies cards.

## Validation and verification

`tests/test_site_widgets_pack4.py` covers defaults, ranges, counts, layout choices,
unsafe URLs and video hosts, social links, hotspot coordinates, scoped product
selection, integer minor unit prices, HTML accessibility, conditional assets,
editor metadata, container support, draft/publish CSRF and preservation through
generation and refinement. The new route is exercised with both path and custom
host routing, foreign/sibling/unpublished/channel-less products, empty and unknown
IDs and invalid requests.

`scripts/verify_widgets_pack4_browser.py` creates a disposable SQLite workspace,
seeds products with images and channel prices, drags a widget from the picker,
inserts all ten widgets, saves and publishes. It checks keyboard flipping,
modal lifecycle and restored focus, deferred video requests, mobile hotspots,
recently viewed products, touch targets, no console errors, reduced motion,
360 pixel overflow and no-JavaScript fallback. Desktop and mobile evidence is
stored under `output/playwright/widgets-pack4/`. No browser session state is saved.

Pack 3's featured collection placeholder description is also changed to neutral
merchant instructions. No additional AI features, add-to-cart paths, database
changes or demo commerce integration are introduced.
