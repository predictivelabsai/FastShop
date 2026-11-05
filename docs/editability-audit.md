# Storefront editability audit

Scope: Liquid and generic theme renderer paths reviewed on 5 November 2026. A
storefront value is considered editable when it is backed by a block field,
shell setting/copy group, store label, catalogue record or merchant-authored
policy/content. Static accessibility labels and application state copy are
included because they are customer-visible.

| File and line | String or asset | Status before | Fix / disposition |
| --- | --- | --- | --- |
| `app/site_ui_liquid.py:231` | Hero body, including the trailing `*` marker | Block `body` was split into plain text plus a separate superscript, so the preview's automatic exact-value matcher could not identify the field. | Mark the rendered hero body wrapper as `data-field="body"` in preview. |
| `app/site_ui_liquid.py:240` | Hero disclaimer | Already block-backed (`disclaimer`, schema default from canonical FDA copy) and marked in preview. | No change needed. |
| `app/site_ui_liquid.py:248` | `Ⅱ Pause` and `Pause background motion` | Static control text and accessible label, with no merchant field. | Open: requires a block or shell copy field while preserving the current default. |
| `app/site_ui_liquid.py:155-183` | `View product`, `Flavor`, `Flavor` group label, `Add to cart` | Static product action/form labels; no associated block or shell field. | Open: requires a store-label or block copy group and preview markers. |
| `app/site_ui_liquid.py:262-276` | Statistic item `label`, `value`, `body` and block source text | Values come from item data, but the bespoke stats renderer does not attach item `data-field` markers in preview. | Open: add `items.N.*` markers for rendered item text and `body`. |
| `app/site_ui_liquid.py:287-337` | Collection card heading, label, body, image and alt | Values come from block item data; bespoke collection renderer omits inline markers. Product price is intentionally sourced from the catalogue where available. | Open: add `items.N.*` markers, including the existing image-field mechanism. |
| `app/site_ui_liquid.py:345-365` | Benefit heading, body, disclaimer and CTA | Values come from block/item data; bespoke renderer omits field markers. | Open: add `heading`, `items.N.heading/body`, `body`, `button` and `link` markers. |
| `app/site_ui_liquid.py:371-446` | Reviews, carousel labels, item text and portrait assets | Some labels have block schema defaults, but the bespoke renderer omits inline markers; portrait `alt` is a separate block value. | Open: mark block/item fields and image/alt fields in preview. |
| `app/site_ui_liquid.py:449-470` | Article card label, heading, body, image, alt and `Read more →` | Item fields/default-backed `read_more` are rendered without inline markers. | Open: add `items.N.*` and `read_more` markers. |
| `app/site_ui_liquid.py:492-614` | Product, choice and trust block content | Bespoke blocks render schema/item data without consistent inline markers. | Open: enumerate each renderer and mark every visible `items.N.*` and block field. |
| `app/site_ui_liquid.py:687, 775` | Logo and favicon image paths | Logo can be configured through settings; fallback theme logo and favicon are application assets. The image itself does not expose an image field in the editor shell. | Open: expose logo/favicons through merchant media settings or document as fixed brand chrome only if owner approves that exception. |
| `app/site_ui.py:95-96, 167-171` | Generic CTA fallback, `PLACEHOLDER IMAGE`, `Price to be confirmed`, `Your collection is coming soon.` | Static generic theme text, not settings or block data. | Open: move text into store labels or block defaults, retaining exact current strings. |
| `app/site_ui.py:189-253` | Video labels, placeholder media, research filters/source text, contact-form labels and consent explanation | Static generic theme UI strings; no complete store-label group or preview markers. | Open: add defaults to the store labels group and mark applicable block fields. |
| `app/site_ui.py:278-296` | Product placeholder asset alts, purchase labels and price/payment notices | Mostly static product UI copy; catalogue values are dynamic but surrounding labels are not editable. | Open: move copy to the checkout/store labels group and expose product block fields. |
| `app/site_ui.py:434-484` | Preview banner, header controls, offer, footer headings/links and offer-form labels | Static shell strings mixed with configurable settings; generic shell does not expose all of these values as editable fields. | Open: register missing shell fields and render them from resolved shell settings. |
| `app/storefront_ui.py:29-107, 180-185` | 404 body, checkout summary strings and empty-cart copy | Page headings and recovery labels read the editable Liquid store-pages group. Supporting message and checkout summary copy remain literals. | Open: add defaults in the same store-pages group and use them in message/checkout renderers. |
| `app/subscription_portal.py:183-208` | `Calculated when prepared` (shipping and total rows) | Adjacent labels use `DEFAULT_PORTAL_COPY`; these two visible value descriptions remain literals. | Open: add matching portal copy keys with byte-identical defaults. |
| `app/subscription_portal.py` / `app/subscription_portal_copy.py` | Portal form labels and statuses | Most visible copy resolves through `DEFAULT_PORTAL_COPY`; Liquid theme registers these as shell fields. Status values, catalogue names, quantities and prices are domain data. | No general copy migration needed; review literal exceptions individually. |
| `app/account_copy.py`, `app/checkout_copy.py` | Account and checkout field labels | Defaults are stored in their corresponding settings groups and used by customer/checkout routes. | No change needed for defaults; no inline block editing applies to transactional pages. |
| `app/site_ui_liquid.py:672-1008` | Header, footer, cookie banner, announcement and navigation | Most user-facing values resolve via Liquid shell fields and store labels; menu/footer link targets are merchant navigation data. | No change needed for those settings-backed values. Static icon glyphs and punctuation carry no independent editable content. |

## Inline-field audit status

The shared widget packs attach `data-field` to their rendered schema fields.
The generic `decorate_block` matcher handles exact top-level `heading`,
`body`, `eyebrow`, `button` and `image` matches. Bespoke Liquid renderers do not
consistently mark item fields, and the hero body required an explicit marker
because rendering removes its trailing disclaimer asterisk. This report records
the confirmed gaps above. It is not a claim that all Liquid editorial layouts
or application-generated transactional states have been exhaustively converted;
the open rows require follow-up implementation before the owner's rule is met.

## Explicit catalogue/domain data allowance

Product and variant names, prices, quantities, order/subscription state, dates,
and calculated totals originate from merchant catalogue or customer transaction
records. Those are edited in catalogue/order administration or by the customer
through the associated form, not in the page canvas. This allowance does not
cover surrounding labels, helper copy, link labels, image alt text or fallback
placeholders.
