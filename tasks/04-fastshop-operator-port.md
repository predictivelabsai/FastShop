# Task 04: FastShop operator port

builder: codex

Port the reusable assistant capability coverage from H24You into the upstream
FastShop repository at `C:\Users\Joosep\FastShop`. Keep the work on a feature
branch, never `main` or `master`.

Carry across the merchant-facing capability index, protected-action guidance,
review-gated behaviour and bounded, observable model output that apply to the
generic platform. Do not copy H24You storefront copy, visual-editor details or
client-specific policy wording.

Acceptance: reusable merchant-managed domains are discoverable; direct changes
remain draft-only where reversible; consequential changes stay durable and
review-gated; protected actions state their route; tests cover the port; the
feature branch is pushed after verification. Do not merge or deploy FastShop.
