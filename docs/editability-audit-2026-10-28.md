# Liquid storefront editability audit

Audit scope: Liquid theme pack and its shared storefront and checkout surfaces. Content and settings are saved on `SitePage` documents or the per-site `Site.settings_json` / `published_settings_json` JSON columns. No schema change is required.

| Storefront area | Merchant-visible content and assets | Editing path and persistence |
| --- | --- | --- |
| Header, offer bar and navigation | Logo, offer copy, labels, destinations, grouped navigation | Site settings and native header/footer menus; settings are published to the site's published JSON. |
| Footer | Newsletter heading/body, company/address/contact, footer note, help/social links and payment marks | Liquid shell settings, shared site settings and native footer menu. Some payment marks remain code-selected assets based on the supported payment display. |
| Home and shop | Hero, research figures, product/collection cards, benefits, reviewed FDA disclaimer, sample reviews, reading cards, choices and assurances | Page section/block documents in the visual editor. Hiding, ordering and field edits persist in `SitePage.draft_json` and publish to `published_json`. Product prices and variant names come from the catalogue. |
| Product pages | Product headings, descriptions, gallery, labels, directions, flavour/choice cards, FAQ and disclosures | Product page blocks and item fields in the visual editor. Catalogue prices remain authoritative. FDA qualification and other compliance copy are existing seeded content and can be edited through the relevant block fields. |
| Editorial and science pages | Chapter headings/body, figures, citations, links and imagery | Page blocks and widget fields in the visual editor. |
| Support pages | About, FAQ and contact copy, topic labels and imagery | Support page blocks and widget fields in the visual editor. |
| Cart | Cart labels, line item content, quantities, discount code and totals | Product names/variants/prices come from catalogue and cart state. Shared cart interface labels are currently application copy. |
| Checkout | Address, shipping choice, discount/gift card, subscription consent, totals and payment handoff | Cart/checkout state and commerce settings. Checkout copy and the full Shopify-style checkout design are not yet configurable or implemented by this change. Payment continues through the existing provider handoff. |
| Account | Account, order and subscription controls and explanatory copy | Customer and subscription data are database-backed. Most interface labels are application copy and are not exposed as merchant settings. |
| 404 and error states | Not-found explanation and recovery links | Generic application copy and standard shop routes; not currently merchant-editable. |
| Cookie consent | Heading, message, accept/decline/save labels and policy link label | New `liquid_consent` settings in site settings, persisted per site and published with shared settings. The banner remains rendered without a policy. When privacy copy is blank and no privacy page exists, only the link is omitted. |
| Policies | Privacy, terms, returns/refunds and shipping long-form HTML | New policy editors in Settings > General. The existing allow-list sanitizer strips scripts, event attributes and unsafe URL schemes. Policy pages render in the Liquid shell, with a blank-state message. |

## Known gaps

- Checkout remains the existing multi-step FastShop flow. It does not yet match the requested Shopify-style one-page layout, and its copy is not merchant-configurable.
- No direct card fields were added. The existing integration redirects to the configured Stripe checkout, so collecting card details locally would misrepresent the available provider integration.
- Application copy on cart, account, 404, checkout and consent-category descriptions remains partly fixed. The policy/consent settings change does not make every visible string configurable.
- Existing JSON files remain source snapshots and seed defaults. The Liquid page renderer already reads editable page documents and shell settings from the database. This change did not introduce an exhaustive generic JSON-to-DB migration for every remaining Liquid JSON key.
- No migration was added. Existing settings use JSON columns; new policy and consent keys are additive and only written when a merchant saves settings. This avoids destructive schema operations, but has not been exercised against the live PostgreSQL database.

## Compliance

No product claims, figures, reviews, citations or FDA disclaimer wording were changed. Policy and consent defaults are interface copy only. The existing FDA disclaimer remains in its seeded editable block.
