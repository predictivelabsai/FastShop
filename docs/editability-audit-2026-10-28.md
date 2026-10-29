# Liquid storefront editability audit

Rule: no merchant-visible storefront text, image or link may need code to change. Merchants have
the admin, the visual editor and the chat assistant only.

## How content is stored

- Page sections and blocks live in `SitePage.draft_json` / `published_json` and are edited in the
  visual editor (schema in `app/site_block_schema.py`). This already covered home, shop, product,
  editorial, science and support pages, including the FDA disclaimer text inside the benefits and
  editorial disclaimer blocks.
- The shared shell (offer bar, header navigation, footer, cookie banner) lives in
  `Site.settings_json` under `liquid_shell`, `liquid_consent`, `footer` and `email`. Before this
  change the visual editor showed the header and footer as fixed "Theme" rows with no fields, and
  `liquid_shell` had no editing screen anywhere. That is where the reported FDA footer disclaimer
  came from.
- `app/site_liquid_*_content.json` are seed snapshots only. Renderers read the per-site values;
  JSON defaults fill paths that are absent and never overwrite a merchant edit.

## What changed

Generic mechanism (`app/site_editable_settings.py`): a theme pack registers a list of fields
whose ids are dotted paths into the site settings. The editor document API returns
`shell_schema` and `shell_settings`; `PUT /editor/api/shell-settings` validates and saves only the
declared paths as a draft (site version guarded, change recorded in history); publishing a page
in the editor copies the declared paths to the published settings, as design settings already were.

Visual editor: the "Store header", "Store footer" and new "Cookie banner" rows are now selectable
and open their fields in the settings panel (text, long text, links with the link picker, and
repeatable link lists). Save, autosave and Publish include them.

Liquid fields (`app/site_liquid_shell.py`), previously fixed in code or reachable only by
database edits:

| Area | Now editable |
| --- | --- |
| Offer bar | Text, button label, form text, email placeholder, consent text, privacy link label and URL, signup button, thank-you message |
| Header navigation | Shop menu label and links, second menu label and links, About label and link, mobile help links |
| Accessibility labels | Skip link, dismiss offer, account, bag, open and close menu |
| Footer | Newsletter heading and text, footer links, help heading and links (with anchors), company, address, contact email, social links, payment marks, FDA footer disclaimer, cookie preferences button, copyright line |
| Cookie banner | Heading, message, accept/decline/save buttons, policy link label and URL, category labels |

The FDA disclaimer default is byte-identical to the approved wording. The banner always renders
for visitors without a stored choice; when the privacy policy is empty only the link is omitted.
The Settings > General form now merges its consent fields instead of replacing the whole object.

## Remaining gaps

- Checkout: handled in a separate change (Shopify-style one-page checkout, pending a decision on
  the payment step).
- Cart, account, order and 404 screens: interface labels are still application copy.
- Generic (non-Liquid) storefront theme in `app/site_ui.py`: its footer copyright, "Cookie
  preferences" button and banner text are still fixed strings. It can adopt the same mechanism by
  registering a shell module.
- The chat assistant cannot yet edit shell fields; its operations cover pages and theme design.
- Live verification: tested with unit and route tests on SQLite, not yet against the live
  PostgreSQL data. No schema migration is needed (JSON columns, additive keys).
