# Phase 2B — block-level refinement diffs

## Scope and outcome

Chat copy and block-structure requests now produce a reviewable block diff instead
of replacing or immediately saving a page document. The builder shows every
proposed operation with a human-readable before/after summary, affected block type
and stable block ID. A merchant can accept or reject each operation, or accept all
remaining operations. Rejection never writes page content. Acceptance uses the
existing block primitives and normal page-save boundary, so page revisions,
builder change sets and undo remain coherent.

Menus, site/page-level structure, themes, brand/brief settings, merchant proposals
and publication keep their established behavior and authorization paths. The chat
provider still cannot publish content.

## Operation model

`app/site_refinement.py` owns the pure operation model. A stored preview contains a
bounded list of at most 12 canonical operations:

- `patch`: page ID, block ID/type, and changed `before`/`after` field maps.
- `add`: page ID, new block ID/type, insertion index, `before: null`, and the full
  validated block in `after`.
- `remove`: page ID, block ID/type, prior index and full block in `before`, with
  `after: null`.
- `reorder`: page ID, affected block IDs and the relative `before`/`after` orders.

Every operation also receives an opaque operation ID, description and decision
status. Field deletion is represented by an `after` value of `null`; the existing
`patch_block` primitive now treats that as removal of an optional field. Adds still
respect the five-type chat allowlist (`hero`, `text`, `split`, `products`,
`articles`). Normal block validation retains the 60-block, 200-entry, field, URL
and gallery limits, while chat copy retains its 8,000-character cap.

Provider `patch`, `add`, `remove` and `reorder` commands are applied to a detached
copy of the request snapshot and structurally diffed back against the original.
Legacy `section`/`add_section` responses remain accepted at this normalization
boundary. A provider can also return a full canonical page through the `document`
operation; it goes through the same structural diff, so direct and whole-document
paths converge on the same minimal representation. Stable IDs identify common,
added and removed blocks. Common blocks with a changed type are rejected. Default-
locale merging preserves other translations even when a full-document provider
returns a plain string.

## Preview, decisions and persistence

`finish_turn` partitions chat commands before applying anything:

- Block `patch`, `add`, `remove`, `reorder` and full-document inputs become a
  pending refinement preview.
- `brief`, `navigation`, `theme`, `brand` and `create_page` remain immediate, as in
  the previous builder. `create_page` is the page-structure command; block
  structure is intentionally reviewable. Merchant/catalog operations remain the
  separate explicit proposal-review flow. Publish is never a chat operation.

Pending operations do not change `SitePage.draft_json`, create a `SiteRevision`,
or create a `SiteChangeSet`. They are stored only as preview metadata in the
already-existing, tenant-scoped `SiteBuilderTurn.response_json`; no new table or
migration was needed. This lets redirects, refreshes and multiple app requests keep
the review UI consistent. Previews expire after 30 minutes. Lazy cleanup marks
expired operations inert when the user sends another builder turn or tries to act
on a preview.

The decision route is CSRF-protected and re-reads the turn under site, tenant and
user ownership. Reject marks only preview metadata. Accept rechecks each operation's
before-state, applies it with `patch_block`, `add_block`, `remove_block` and/or
`reorder_blocks`, validates media ownership and the canonical document, and runs
the existing compliance scanner on the resulting page. Banned claims therefore
fail before any transaction commits. Accepted operations are batched once per page,
saved through `content.save_page`, recorded as a `chat-refinement` change set and
remain eligible for the existing latest-revision undo.

Relative reorder operations cover only common block IDs. Adds and removals therefore
remain independently decidable: rejecting a removal does not make an accepted
reorder invalid, and rejecting an add does not introduce a phantom ID into the
page order.

## Eval design and results

`evals/site_refinement.py` runs three required scenarios twice, entirely offline:

- Tweak the hero heading.
- Swap the first two sections.
- Remove the FAQ block.

The guided path emits direct block operations. The mocked-provider path emits full
page documents to exercise structural derivation. Each case asserts the targeted
block(s), a one-operation minimal diff, unchanged content after reject, and an
accepted document equal to applying exactly the stored operation set.

Result on 2026-10-08: **6/6 refinement runs passed**. The existing offline chat
suite, updated to accept its heading preview, also passed **6/6**. Reports are in
`output/evals/site_refinement_results.{json,md}` and
`output/evals/site_builder_chat_results.{json,md}`.

## Verification

Verified on 2026-10-08:

- Ruff passed for the full repository.
- Compile checks passed for `app`, `tests`, `evals`, `migrations` and `scripts`.
- Full isolated SQLite suite: **396 tests passed**, with the existing Starlette/
  AnyIO `BlockingPortal` deprecation warning.
- The existing 50 normalized storefront render baselines passed unchanged as part
  of the full suite.
- Offline evals: existing chat **6/6** and refinement **6/6**.
- Empty SQLite upgraded through `20261008_0020`; `alembic check` reported no new
  upgrade operations. No migration was added.
- Browser command: `uv run python scripts/verify_site_browser.py --base
  http://127.0.0.1:51436 --merchant-only --refinement --output
  output/playwright/phase2b-refinement`. It reported **3 checks, zero failures**.
  The guided heading preview remained unchanged after reject and matched exactly
  after accept. Desktop and mobile captures were visually inspected.
- The interface detector's only finding was the workspace's existing layout-
  property transition; the unnecessary transition was removed.
- `git diff --check HEAD` passed.

## Follow-ups

- Add co-editing locks or block-level optimistic versions for simultaneous editors.
- Batch related operations across multi-turn refinement conversations.
- Add richer field-level highlighting and optional live visual diff rendering in
  the preview canvas.

## Files changed

- Diff and application: `app/site_refinement.py`, `app/site_builder_services.py`,
  `app/site_blocks.py`, `app/integrations/site_builder_llm.py`.
- Review UI: `app/site_builder_routes.py`, `static/site-workspace.css`,
  `static/site-workspace.js`.
- Tests and evals: `tests/test_site_refinement.py`, `tests/test_site_builder.py`,
  `tests/test_site_builder_routes.py`, `tests/test_chat_evals.py`,
  `evals/site_refinement.py`, `evals/site_builder_chat.py`.
- Browser verification: `scripts/verify_site_browser.py`,
  `output/playwright/phase2b-refinement/`.

No commerce/payment behavior, published content, permission checks or database
schema changed. No commit was created.
