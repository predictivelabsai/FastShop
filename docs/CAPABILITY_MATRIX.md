# H24YOU capability and acceptance matrix

Last updated: 2026-09-23. Baseline: local worktree at Alembic head `20260923_0015`,
full suite **259 passing**. This is a single source of truth for *what state each brief
capability is in*, so the storefront simulator is not mistaken for an open shop. It does
not assert production deployment or real-provider acceptance.

Legend:

- **Available** — works on a published storefront now.
- **Sandbox** — implemented against the Stripe test gateway; blocked from live by design.
- **Manual** — works, but operated by the merchant, not automated.
- **Provider gate** — code is ready; real availability needs provider credentials/eligibility/domain verification.
- **Deferred** — intentionally not built; rationale given.
- **Merchant input** — needs merchant-supplied content/config/approval before launch.

## Phase 1 — design and content

| Brief | Capability | State | Evidence / note |
|---|---|---|---|
| §4.1 | Header, Shop dropdown, responsive menu | Available | `site_ui.py`; mobile is a drop-down panel, not a slide animation |
| §4.1 | Transparent-over-hero header + logo swap on scroll | Available | home page only |
| §4.2 | First-order 10% banner + email capture | Sandbox | capture activates with sandbox commerce; pure-preview shows a disabled placeholder (see Deferred: preview capture) |
| §4.3 | Footer legal/company/FDA + social & payment icons | Available | icons now rendered (`site_ui.py` `payment_methods`/`social_link`) |
| §4.4 | Cookie consent (Accept/Decline/Preferences), consent-gated scripts | Available | `site-analytics.js`; GA4 only, no separate marketing loader |
| §5.1 | Hero video (autoplay/muted/loop/inline, poster, mobile source) | Available | placeholder media pending (Merchant input) |
| §5.1/§7 | Science counters 2,000+/120+/68 + source line + scroll animation | Available | re-check date is a placeholder (Merchant input) |
| §5.1 | Reviews section | Available | model-backed display + moderation; sample shown until real reviews approved |
| §5.1/§5.6 | Latest-3 articles, Learn blog with "Studies referenced" | Available | `site_articles.py`, `site_seed.py` |
| §5.5 | Science library cards, external links, theme filters | Available | |
| §5.7/§5.8 | About (founder + Randma/Lumiorav), Contact form | Available | email delivery is a Provider gate (Postmark); founder attribution to confirm (Merchant input) |
| §5.9 | Legal pages (Terms/Privacy/FAQ/Returns) editable | Available | placeholder text (Merchant input) |
| §2 | Per-page SEO + clean URLs; preview `noindex` | Available | |
| §3 | Lovera headings | Deferred | Quicksand body present; Lovera needs a licensed font file (Merchant input) |

## Phase 2 — commerce (all sandbox-gated)

| Brief | Capability | State | Evidence / note |
|---|---|---|---|
| §6.1 | Cart + slide-out drawer + discount field | Sandbox | `store_checkout_routes.py`, `cart-drawer.js` |
| §6.1 | Guest checkout, optional account | Sandbox | passwordless email login |
| §6.1 | US tax by delivery address, total before payment | Sandbox | Stripe Tax; real registrations/rates are a Provider gate |
| §6.1 | US-only shipping, flat rate, free over $75 (configurable) | Sandbox | threshold in commerce settings |
| §6.1 | Payments: card | Sandbox | `checkout_payments.py` |
| §6.1 | Payments: PayPal | Provider gate | opt-in per store (one-time orders); needs PayPal enabled on the Stripe account |
| §6.1 | Payments: Apple Pay / Google Pay | Provider gate | automatic in Stripe Checkout once the store domain is registered |
| §6.2 | Subscriptions monthly, 10% off, flavour change | Sandbox | tablets-only via per-product allowlist (Merchant input) |
| §6.2 | Real recurring charge (saved card, off-session) | Sandbox | `subscription_renewals.py` against test gateway |
| §6.2 | Renewal scheduling | Available (opt-in) | bundled `app/scheduler.py` (`FASTSHOP_ENABLE_SCHEDULER`) or CLI worker |
| §6.3 | My account: orders, subscription controls (skip/pause/flavour/frequency/payment/address/cancel) | Sandbox | `subscriptions.py`, `subscription_routes.py` |
| §6.3 | Order tracking | Manual | merchant-entered events; automatic carrier feed is a Provider gate (`integrations/carrier.py` seam) |
| §6.1 | First-order 10% stacks with subscription 10% | Sandbox | ~19% combined, largest-remainder allocation |

## Compliance, analytics, placeholders

| Brief | Capability | State | Evidence / note |
|---|---|---|---|
| §8 | Approved benefit statements: central, toggle, asterisk + disclaimer co-located | Available | `compliance.py`, `site_routes.py`; disclaimer also global in footer |
| §8 | Reserved statement slots 4–6 / add-claim workflow | Available | add/remove in settings |
| §8 | Banned-word / disease-claim guardrail | Available | blocks page publish and claim approval (`compliance.py`) |
| §8 | No invented reviews | Available | only approved reviews render |
| §10 | Built-in analytics + GA4 after consent, no Meta pixel | Partial | GA4 `page_view`/`view_item`/`add_to_cart`; `purchase`/`begin_checkout` + per-site revenue dashboard **Deferred** (checkout/account pages are analytics-excluded by design) |
| §9 | Placeholder register | Available | generated at `/admin/sites/{id}/placeholders` |

## Deferred (intentional), with rationale

- **Preview-only email capture** — coupled to sandbox commerce on purpose; enabling it in pure
  preview would store emails with no redeemable code or double-opt-in mail path.
- **GA4 purchase/revenue events + per-site store-analytics dashboard** — checkout/account pages
  deliberately load no analytics (`Referrer-Policy: no-referrer`); revenue reporting should be
  server-side. Open decision for the merchant.
- **Automatic carrier tracking** — pluggable seam with a manual default; a real provider adapter
  needs carrier credentials.

## Launch gates (unchanged)

Live payments are blocked in code (`integrations/stripe_commerce.py`: sandbox keys only,
live responses rejected). Going live still requires: provider credentials + acceptance,
real email/carrier configuration, final legal/ingredient/price/review content, domain
binding, and a reviewed publish transition. See `PHASE2_ACCEPTANCE_STATUS.md` and
`H24YOU_BRIEF_GUIDE_AUDIT.md`.
