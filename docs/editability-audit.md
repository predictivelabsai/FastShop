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
| `app/site_ui_liquid.py:248` | `Ⅱ Pause` and `Pause background motion` | Fixed. Both strings are block fields with byte-identical defaults and preview markers. | Fixed with `pause_label` and `pause_aria_label`. |
| `app/site_ui_liquid.py:155-183` | `View product`, `Flavor`, `Flavor` group label, `Add to cart` | Fixed. Product action labels are block fields with byte-identical defaults and preview markers. | Fixed with `view_product`, `flavor_label` and `add_to_cart` on collection/catalog blocks. |
| `app/site_ui_liquid.py:262-276` | Statistic item `label`, `value`, `body` and block source text | Fixed. Preview markers resolve to `items.N.*` and `body`; public output has no field attributes. | Fixed. |
| `app/site_ui_liquid.py:287-337` | Collection card heading, label, body, image and alt | Fixed. Repeater text, image and alt source paths are marked in preview; public output has no field attributes. Product price remains catalogue sourced where available. | Fixed. |
| `app/site_ui_liquid.py:345-365` | Benefit heading, body, disclaimer and CTA | Fixed. Heading, item text, body, button and link have preview markers; public output has no field attributes. | Fixed. |
| `app/site_ui_liquid.py:371-446` | Reviews, carousel labels, item text and portrait assets | Fixed. Block and item copy, carousel labels, image and alt source paths are marked in preview; public output has no field attributes. | Fixed. |
| `app/site_ui_liquid.py:449-470` | Article card label, heading, body, image, alt and `Read more →` | Fixed. Item fields and `read_more` have preview markers; article body is rendered from its item field. Public output has no field attributes. | Fixed. |
| `app/site_ui_liquid.py:492-614` | Product, choice and trust block content | Fixed. Rendered block and repeater fields resolve to `items.N.*` paths in preview, including image and link paths; public output has no field attributes. | Fixed. |
| `app/site_ui_liquid.py:687, 775` | Logo and favicon image paths | Logo and favicon now resolve from image fields in the Liquid header shell group, with the existing assets as defaults. | Resolved: shell image settings use the existing safe media URL mechanism and render in the header and document head. |
| `app/site_ui.py:95-96, 167-171` | Generic CTA fallback, `PLACEHOLDER IMAGE`, `Price to be confirmed`, `Your collection is coming soon.` | Generic shell labels register defaults and render the CTA fallback, placeholder-image label, product card alt, draft-price suffix, card price fallback and empty collection message from resolved settings. | Complete: added the remaining fallback lookups and confirmed defaults remain byte-identical. |
| `app/site_ui.py:189-253` | Video labels, placeholder media, research filters/source text, contact-form labels and consent explanation | Generic shell label defaults render the video toggle, placeholder media and contact form labels/explanation from resolved settings. | Complete: research filters and source copy, claims/review labels, video alts and team placeholder copy now resolve through shell labels. Research item text exposes preview field markers. |
| `app/site_ui.py:278-306` | Product placeholder asset alts, purchase labels and price/payment notices | Generic shell defaults are registered for these labels. | Complete: product renderer uses the resolved values for image alts/captions, price and payment notices, purchase labels and buttons. Generic editorial image fallback alts also resolve from shell labels. Existing block heading/body decoration remains in place for preview. |
| `app/site_ui.py:434-491` | Preview banner, header controls, offer, footer headings/links and offer-form labels | Generic shell label defaults are registered for this copy. | Complete: header controls, offer copy and forms, footer headings, policy links, copyright and cookie control use resolved generic labels. Announcement, offer heading and footer disclaimer remain editable through existing brand settings. The internal preview/sandbox banner is merchant-only platform copy and is not shown to customers on the public site. |
| `app/storefront_ui.py:29-107, 180-185` | 404 body, checkout summary strings and empty-cart copy | Page headings and recovery labels read the editable Liquid store-pages group. Supporting message and checkout summary copy remained literals. | Resolved: exact defaults are registered in `liquid_shell.pages` and rendered through resolved shell values. |
| `app/subscription_portal.py:183-208` | `Calculated when prepared` (shipping and total rows) | Adjacent labels use `DEFAULT_PORTAL_COPY`; these two visible value descriptions remained literals. | Resolved: `calculated_when_prepared` is a portal default and Liquid shell field, used by both rows. |
| `app/subscription_portal.py` / `app/subscription_portal_copy.py` | Portal form labels and statuses | Most visible copy resolves through `DEFAULT_PORTAL_COPY`; Liquid theme registers these as shell fields. Status values, catalogue names, quantities and prices are domain data. | Reviewed; the identified remaining customer-facing literal is now a portal copy key. Other values are transaction or catalogue data. |
| `app/account_copy.py`, `app/checkout_copy.py` | Account and checkout field labels | Account defaults now cover login, verification, account, order detail, confirmation and sandbox copy, and Liquid shell registers them in the Store pages panel. Checkout labels remain in the existing checkout group. | Resolved for the requested account strings. Login-code mail already maps to the merchant `customer_account` notification template with the current generated message as its default. |
| `app/site_ui_liquid.py:672-1008` | Header, footer, cookie banner, announcement and navigation | Most user-facing values resolve via Liquid shell fields and store labels; menu/footer link targets are merchant navigation data. | No change needed for those settings-backed values. Static icon glyphs and punctuation carry no independent editable content. |

## Inline-field audit status

The shared widget packs attach `data-field` to their rendered schema fields.
The generic `decorate_block` matcher handles exact top-level `heading`,
`body`, `eyebrow`, `button` and `image` matches. Bespoke Liquid renderers do not
consistently marked item fields, and the hero body required an explicit marker
because rendering removes its trailing disclaimer asterisk. The bespoke Liquid
renderers now retain nested text, image, alt and link paths in preview. This report
records the confirmed gaps above. It is not a claim that all Liquid editorial layouts
or application-generated transactional states have been exhaustively converted;
the open rows require follow-up implementation before the owner's rule is met.

## Explicit catalogue/domain data allowance

Product and variant names, prices, quantities, order/subscription state, dates,
and calculated totals originate from merchant catalogue or customer transaction
records. Those are edited in catalogue/order administration or by the customer
through the associated form, not in the page canvas. This allowance does not
cover surrounding labels, helper copy, link labels, image alt text or fallback
placeholders.
