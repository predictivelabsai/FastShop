# Product

<!-- impeccable:product-schema 1 -->

The facts in this record are inferred from the operator-approved
`docs/FASTSHOP_PRODUCT_PLAN.md`, `AGENTS.md`, and the Phase 5a implementation brief.

## Platform

web

## Users

FastShop serves merchants launching a new online business and established merchants
moving a store and its content from Shopify, WooCommerce, WordPress, or catalog feeds.
They need to produce, review, publish, and operate a checkout-capable storefront without
assembling separate site-builder, CMS, migration, and commerce products.

## Product Purpose

FastShop turns a short business brief into a structured draft storefront, then gives the
merchant a visual builder, full content-management tools, migration paths, and guarded
commerce operations to take that store live. Success means a merchant can move from an
idea or existing catalog to a reviewed, publishable store while retaining control over
every generated change and every go-live decision.

## Positioning

FastShop combines brief-to-site generation with a block-based CMS, additive migration,
and tenant-scoped commerce in one reviewable workflow. Generation creates drafts rather
than publishing autonomously; provider integrations remain sandbox-first until explicit
acceptance and reviewed publication checks pass.

## Operating Context

Merchants work through a server-rendered browser application. They can generate a draft
site, edit blocks and menus, manage media and articles, preview and publish changes,
import existing store data through dry-run plans, bind a domain, enable commerce, and
manage orders, refunds, and revenue reporting. Operators retain responsibility for live
provider credential acceptance and production readiness.

## Capabilities and Constraints

- Python 3.12+ FastHTML application with distinct legacy storefront, tenant site-builder,
  versioned API, and public SaaS marketing surfaces.
- Tenant-scoped owned data, integer minor currency units, idempotent checkout commands,
  stable primary-key inventory locking, and no cross-schema writes.
- Static public marketing content makes no model calls, performs no analytics, and has
  no tenant-data dependency.
- Provider integrations are sandbox-first. Live credentials and commerce require a
  separate reviewed acceptance gate.
- Phase 5a provides a signup placeholder only; self-serve signup, onboarding, plans,
  quotas, and SaaS billing remain later Phase 5 work.
- The final public hostname is an open launch decision. The surface must work on the
  configured platform hostname and at `/marketing/` during review.

## Brand Commitments

The product name is FastShop. Public copy should be direct, specific, and honest: no
fabricated customers or testimonials, no unsupported superlatives, no medical claims,
and no promises beyond shipped capabilities. The marketing page may take inspiration
from the clarity and confidence of Shopify and Lovable without copying either product.

## Evidence on Hand

The repository contains implementation documentation and automated coverage for the
Phase 1 CMS, Phase 2 site generation and refinement, Phase 3 migration connectors, and
Phase 4 publication, live-credential, checkout, order-management, refund, and revenue
workflows. There are no approved customer logos, testimonials, benchmark claims, or
third-party screenshots; future public surfaces must not invent them.

## Product Principles

- Generated work stays reviewable: drafts, diffs, previews, and explicit publication.
- Commerce earns trust through sandbox-first testing and explicit go-live gates.
- Migration is previewed, additive, idempotent, and scoped to the owning tenant.
- Content and commerce belong in one coherent merchant workflow.
- Public claims must be traceable to shipped repository behavior.

## Accessibility & Inclusion

Public and merchant interfaces must be semantic, keyboard accessible, readable at
mobile and desktop widths, free of horizontal overflow, and usable with reduced motion.
