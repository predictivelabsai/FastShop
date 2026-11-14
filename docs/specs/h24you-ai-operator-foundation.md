# H24You AI operator foundation

## Goal

Make the natural-language assistant a useful operator for the whole merchant
workspace, not just the page currently open in the editor. It must be able to
make draft design and content changes across pages, handle site and page SEO,
and prepare confirmation-gated store settings changes.

## Scope for this slice

- Keep existing natural-language design editing across every site page.
- Make the assistant's editor prompts expose design, SEO and store-operation
  work rather than only selected-block edits.
- Add confirmation-gated policy and tax-rate updates, reusing the existing
  merchant validation rules and tenant scoping.
- Keep commercial, customer and live-payment effects behind the existing
  review cards.

## Files expected to change

- `app/store_agent.py`
- `app/store_agent_ops.py`
- `static/editor/editor.js`
- `tests/test_store_agent.py`
- `tests/test_store_agent_ops.py`
- `scripts/verify_editor_agent_browser.py`
- Playwright evidence under `output/playwright/`

## Acceptance checks

- The model receives explicit, bounded tools for policy and tax-rate changes.
- Both new settings operations require merchant confirmation and reject stale,
  cross-site or invalid data.
- The editor presents natural-language starting points for design, SEO, store
  settings and operations.
- Design changes remain draft-only and are restorable.
- Targeted tests, Ruff, JavaScript syntax, Python compilation, disposable
  Alembic validation, browser flow and `git diff --check` pass.

## Out of scope

- Connecting, replacing or exposing payment-provider credentials.
- Publishing a draft without the merchant using the existing Publish control.
- Legal advice or inventing policy text.
- Refunds, fulfilment, customer messaging and prices without the existing
  merchant confirmation card.

## Design references

- No external design references are configured for this repository. The
  existing H24You editor is the visual contract: terse controls, draft-first
  editing and confirmation cards for consequential changes.
