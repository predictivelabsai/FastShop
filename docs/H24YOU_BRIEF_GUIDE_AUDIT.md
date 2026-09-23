# H24YOU: original brief, user guide and implementation audit

Audit date: 21 September 2026. Repository baseline: `b563b1d5ef64887d7fbcf70d76b2d0507a810e26`.

## Verdict

The guide is a useful, substantially accurate **builder and training guide**, not evidence that the original store brief is fully delivered. Phase 1 has broad page/content coverage. Phase 2 has deployed settings, provider-boundary code and a private simulator, but H24YOU is not an operational commerce store. Several requirements still need implementation, provider acceptance or merchant approval.

The original brief's Shopify-only requirement was superseded by the subsequent request for a FastShop Shopify/WordPress-style hybrid, Stripe, US-only destinations, EU fulfilment and classical/chat building. That is an approved architectural change, not an accidental omission. It does **not** waive the functional outcomes of Shopify checkout, subscriptions, accounts, email and analytics.

## Sources and evidence limits

- [Original brief snapshot](H24YOU_SOURCE_BRIEF.md), §§0–11; byte-identical to `data/feature-requests/h24you/h24you_website_build_brief.md` at audit time.
- [User guide](USER_GUIDE.md), 39-page source; page numbers below refer to its exported sequence.
- Source inspection of site rendering, seed content, articles, editor routes, host routing, analytics, commerce and payment boundaries.
- [Recorded production verification](PRODUCTION_GUIDE_VERIFICATION.md): 17 routes at three viewport sizes, private builder/model exercise and simulated commerce. These are earlier recorded results, not browser tests rerun in this audit.
- Fresh read-only HTTP checks of the public tablet page and authenticated H24YOU settings/commerce views. No site edits, purchases, email submissions or deployments were performed for this audit.
- The live tablet page returned 200, `noindex,nofollow`, and “Add to cart — coming in Phase 2”. H24YOU commerce selected `disabled`; readiness reported Stripe test key and webhook signing secret required, and shipping fee not configured. The shared-settings GA4 field was empty.
- Seed article body counts, excluding headings/reference boxes: **655, 641 and 631 words**; all meet 600–900 words. This verifies source drafts, not subsequent merchant edits.

This is a full brief-scope traceability audit, not a penetration test, independent scientific fact-check, legal opinion, fresh accessibility certification or real-provider acceptance run. The 39-page source was reviewed; PDF/PPTX visual parity relies on the existing guide build/tests. Coolify deployment history and CI were not re-audited here. The production-verification skill's evidence distinction is applied: deployment, simulator success and provider acceptance are different claims.

## Highest-priority findings

| Priority | Finding | Evidence and required closure |
|---|---|---|
| Launch blocker | Live commerce is intentionally unavailable | `app/commerce.py` rejects any checkout mode other than sandbox; `app/commerce_routes.py` offers disabled/sandbox only. Live H24YOU is disabled. Complete provider acceptance, then implement and approve a controlled live release. |
| Launch blocker | No complete merchant-facing launch transition was found | Page publication changes draft sites to `preview` in `app/site_routes.py`; GA4 requires `published` in `app/site_analytics.py`. H24YOU is noindex. Add a reviewed launch transition, domain setup/verification and rollback instructions. Host-routing support already exists; do not describe this as wholly absent domain support. |
| Launch blocker | Provider and operational acceptance remains open | Stripe tax/payment/webhooks, real saved-card renewals, real email sign-in/marketing/contact delivery and scheduled processing are not established by the production simulator. Supply merchant configuration and exercise real test-provider journeys before live enablement. |
| High | Payment-method brief is incomplete | Hosted checkout currently requests `payment_method_types: ["card"]` in `app/checkout_payments.py`. PayPal is not implemented; Apple/Google Pay require separate eligibility/device acceptance. Card configuration alone neither proves nor rules out Stripe wallets. |
| High | Tracking is manual, not carrier-synchronized | Merchant-entered events and carrier links exist. Automatic carrier updates requested through the original tracking-app outcome are missing. Select and implement a tracking provider or explicitly approve a manual operating model. |
| High | Analytics does not meet the original full outcome | Consent-gated GA4 public page views exist, but no verified property ingestion or purchase/revenue events; no demonstrated equivalent of the brief's built-in store analytics. Preview status additionally suppresses GA4. Define funnel/revenue reporting, implement events and test consent plus ingestion. |
| High | Acceptance documentation contradicts itself | `PHASE2_ACCEPTANCE_STATUS.md` says “not deployed”, 156 tests and migration 0012; the newer production report records deployment and 212 tests. Guide p37 links to that stale register. Reconcile chronology without marking unresolved provider work complete. |
| Medium | Merchant instructions stop short of an operating runbook | The guide's account/checkout/email screenshots are deliberately simulator-based. Add separate actual sandbox checkout/account journeys, provider setup, job operation, failed-payment recovery and go-live procedures. |
| Medium | Reviews and several visual requirements remain partial | Review sections are labelled placeholders, not a functioning review/photo workflow. Header/social/payment items use text in places where icons were requested. Require a visual acceptance pass and a genuine-review strategy. |

## Full requirement matrix

“Covered” means the guide addresses the requirement, not that production acceptance is complete. “Partial” means missing detail or incomplete implementation. “Pending” distinguishes legitimate merchant placeholders and unverified acceptance from missing code.

| Brief | Requirement | Guide coverage | Implementation / acceptance finding |
|---|---|---|---|
| §0 | Separate design/content and commerce approvals | Covered: pp16, 36–37 | Clear separation and private demo. Actual merchant sign-off is a human gate, not established by passing tests. |
| §0 | Ask about conflicting instructions | Covered: original phase prompt p16 | Shopify/FastShop change is explained; remaining merchant facts must not be guessed. |
| §1 | Brand, contact and centrally editable legal details | Covered: pp4, 8, 28, 38 | Shared settings exist; legal address remains a permitted placeholder. |
| §1 | US-only, English, USD; EU shipping origin | Covered: pp4, 28, 31 | US/EU configuration exists; actual warehouse and operating rates need merchant confirmation. |
| §1 | Two products and three tablet flavours | Covered: pp4, 10, 20 | Seed/catalog match; final bottle USD price and tablet pack details pending. |
| §1 | Warm, curious, nonclinical voice and founder/partner | Covered: pp17–18, 25 | Draft content exists; portrait/bio/source approval remains required. |
| §2 | Shopify OS2, native platform rather than custom build | Explicit replacement: pp4, 16 | Approved FastShop architecture. Do not claim Shopify-native equivalence. |
| §2 | No-code section/page/product editing | Covered: pp8–11, 15–26 | Classical editor and constrained model operations exist; operational catalog edits are not draft-only. |
| §2 | Mobile, iPad and desktop | Covered: pp11, 36 | Recorded production checks cover all three sizes, one H1 and overflow. Not an exhaustive device/browser test. |
| §2 | Compressed/lazy media and loading performance | Partial: pp11, 19, 36 | Media/rendering support exists. Final-asset payload and measured performance budget acceptance still needed. |
| §2 | Contrast, alt text, keyboard/focus, video pause | Covered: pp11, 36 | Controls and rendering support present. Existing no-overflow checks are not a complete accessibility audit. |
| §2 | Clean routes and per-page SEO | Covered: pp6, 9, 36 | Requested paths exist under the sample prefix. Production custom-domain/indexable launch needs verification. |
| §3 | Light editorial design; avoid purple/AI clichés | Covered: p18 | H24YOU-specific styling exists. Generic Bold preset is purple; no H24YOU guardrail prevents later conflicting design choices. |
| §3 | Supplied light/dark SVG logos | Partial: pp8, 11 | Assets/header support exist; guide lacks an explicit two-logo/scroll-state acceptance checklist. |
| §3 | Lovera headings, Quicksand body, readable scale | Partial: pp18, 38 | Quicksand and documented Georgia fallback are present. Lovera pending licensed asset is allowed by the brief. Generic presets can change fonts. |
| §3 | Sensory/lifestyle imagery and purposeful motion | Partial: pp11, 19–20 | Placeholder concepts, video support and counter motion exist; final dissolving/first-sip realism and animation direction need visual approval. |
| §3 | Raspberry/pineapple accents, bubble transitions, cart delight | Partial: pp18–20 | No complete requirement-specific acceptance evidence. Do not infer these details passed from route smoke tests. |
| §3 | Curiosity hooks, research links, original headlines | Covered: pp19–24 | Seed pages connect Science/Learn; editorial review still needed. |
| §3/5.1 | Strong reviews and future customer photos | Partial: pp36, 38 | Renderer shows labelled sample section; no genuine review collection/moderation/photo integration. Placeholder is allowed in Phase 1, not proof of a live review feature. |
| §4.1 | Header menu, two-category dropdown, responsive menu | Partial: pp8, 11, 36 | Navigation/dropdown code exists. Exact hover/touch, slide-in and scroll-state behavior needs dedicated acceptance. |
| §4.1 | Account/cart icons and item count | Partial: pp30, 33 | Account/Bag controls exist; text-based presentation is not literal icon compliance. Live preview purchasing is closed. |
| §4.2 | Delayed, dismissible 10% offer; consent/privacy | Covered: pp34, 36 | Delay/dismissal and opt-in code exist. Current H24YOU public offer is preview-only, not subscriber acquisition. |
| §4.2 | Store subscriber and send usable first-order code | Covered with substitution: p34 | FastShop email replaces Shopify Email. Real confirmation, delivery and redemption acceptance pending. |
| §4.3 | Legal links, company block, FDA disclaimer | Covered: pp8, 25, 38 | Shared footer and legal routes exist; final legal content pending. |
| §4.3 | Instagram/Facebook and payment icons | Partial: pp8, 31, 38 | Social links and planned payment-method text exist, not the requested complete icon treatment. Do not display unaccepted methods as available. |
| §4.4 | Accept/Decline/Preferences; consent before scripts | Covered: p13 | Consent implementation exists; guide screenshot shows Learn, not cookie/preferences UI. GA4 is additionally gated by published status. |
| §5.1 | Autoplay muted inline looping hero, poster/mobile fallback | Covered: pp11, 19 | Renderer supports these attributes and separate mobile video. Placeholder media must be replaced/approved and payload checked. |
| §5.1 | Products, counters, approved claims, latest three articles | Covered: pp19, 21, 36 | Seed blocks and latest-published article selection implemented. Unapproved claims are deliberately withheld. |
| §5.2 | Editorial shop/categories, images and price | Covered: pp6, 10 | Routes exist. Bottle price remains visibly pending, as permitted during design. |
| §5.3 | Tablet gallery, flavours, purchase options, $29.95 | Covered: pp10, 20, 32 | Preview structure/catalog exist; add-to-cart disabled in current H24YOU. Price is provisional. |
| §5.3 | How-to/ingredients/details accordions; no invented facts | Covered: pp10, 20, 38 | Review placeholders present. Merchant must supply approved product facts before launch. |
| §5.3 | Dissolving/drinking clips, ritual copy, reviews, shipping | Partial: pp11, 20, 38 | Draft/placeholder treatment exists; no real-product media or genuine-review acceptance. Shipping threshold remains provisional. |
| §5.4 | Source-adapted Hydroxy Go, FAQ, branded mock-up | Covered: pp10, 20, 38 | Draft bottle content exists; specs, image authenticity and USD price need approval. Source rights/facts not independently reverified here. |
| §5.5 | Research cards, title/source/year, external links/themes | Covered: pp12, 21 | Science library, filters and references exist; reference metadata/link correctness needs final editorial check. |
| §5.5 | Independent database, uncertainty, disclaimer | Covered: pp12, 21 | Seed sections implement this treatment. No independent scientific validation implied. |
| §5.6 | Three 600–900-word articles with reference boxes | Covered: pp12, 22–24 | Source body counts 655/641/631; provided reference lists present. Drafts still require merchant review. |
| §5.6 | Author label and automatic latest-post selection | Covered: pp9, 12, 22–24 | Team author and draft label rendered; latest-published article selection implemented. |
| §5.7 | Founder story, Andres/Lumiorav, photos | Covered: pp25, 38 | Draft About page exists; portraits and biographical approval pending. |
| §5.8 | Contact form delivers to info@h24you.com | Covered: pp13, 25 | Form/storage/provider boundary present. A stored message is not proof of destination delivery; real delivery not accepted. |
| §5.9 | Four editable legal/help pages | Covered: pp6, 25, 38 | All pages present as drafts/placeholders; US-facing final wording remains merchant-supplied. |
| §6.1 | Cart/drawer quantity and code; guest checkout | Partial: pp30–31 | Implemented sandbox paths; guide demonstrates simulation rather than full actual-provider customer journey. |
| §6.1 | State/ZIP tax and final total before payment | Covered: p31 | Stripe Tax boundary uses delivery address, not a blanket state percentage. Actual address/rate/registration cases remain unverified. |
| §6.1 | US zone, flat shipping, free threshold | Covered: pp28–31 | Configuration exists; live H24YOU readiness says fee unconfigured. EU company address must not silently become warehouse address. |
| §6.1 | Card/PayPal/Apple Pay/Google Pay | Partial and explicit caveat: p31 | PayPal missing; wallets/provider/device acceptance pending; card test end-to-end still outstanding. |
| §6.1 | First-order 10% stacks with subscription 10% | Covered: pp31, 34 | Sequential discount model gives 19% before cent rounding. Provider-backed redemption needs acceptance. |
| §6.2 | Monthly tablet-only subscriptions, future flavours | Covered: p32 | Eligibility and subscription code exist; live H24YOU disabled. Real renewals and tablet-only configuration must be proven. |
| §6.3 | Customer login, history and tracking | Covered with caveat: p33 | Site-scoped account code and manual tracking exist; real email/customer/provider journey not accepted. |
| §6.3 | Skip/pause/flavour/frequency/payment/address/cancel | Covered: p32 | Simulator exercises controls; provider-backed payment update, future renewal and notices need separate acceptance. |
| §7 | Exact 2,000+/120+/68 counters, source/date, recheck | Covered: pp12, 21, 38 | Correct supplied figures seeded; confirmation date and current validity remain merchant review items. |
| §8 | Neutral wording, no invented reviews/medical promises | Covered: pp12, 21–24, 36, 38 | Draft wording and placeholder controls support this; not a guarantee against later manual/model edits. |
| §8 | Central claims, approval toggles, same-view disclaimer | Covered: pp8, 12, 21 | Shared claim editing/approval and rendering exist. Verify withdrawal through publication and all views before launch. |
| §8 | Reserved statement slots 4–6 | Omitted from guide | Seed has two benefit claims; settings edit existing entries. No explicit no-code reserved-slot/add-claim workflow found. |
| §9 | Styled, labelled placeholders and complete register | Partial: pp11, 38 | Category-level register exists; not a complete per-file/per-page asset manifest with replacement owner/status. |
| §10 | Built-in analytics, GA4 after consent, no Meta pixel | Partial: p13 | GA4 page-view boundary exists; full commerce reporting and event acceptance missing. No Meta addition requested. |
| §11 | Working store, customisation docs, completion checklist | Partial: pp7–39 | Builder/training deliverables exist; a working live commerce store does not. Checklist needs missing launch/provider operating steps. |

## Later requirements, outside the original brief

| Addition | Audit result |
|---|---|
| Classical plus prompt/chat building | Guide pp7–26 covers both and switching/undo. Configured production model has one recorded successful draft operation, not acceptance of every sample prompt or arbitrary site generation. |
| Original brief prompts | The original brief is specifications, not a pre-existing extensive prompt library. Guide quotes the phase instruction and labels other prompts as adapted examples. That distinction is appropriate. |
| Synthetic merchant data, manually replaceable | Guide pp28–30 explains review and isolation. Do not promote simulator totals, fake cards or synthetic customer records to real commerce. |
| Consistent FastShop branding | Merchant shell uses shared platform components; H24YOU retains its own storefront brand. This is sensible platform/store separation, not a requirement to replace the merchant's logo with FastShop. Full CSS/HTML brand regression across every error/account screen remains unverified. |
| Shopify/WordPress-style self-service product | Existing section editing, products, blog and chat provide a foundation. Missing no-code launch/domain/video-hosting instructions and operational commerce acceptance prevent an end-to-end self-service claim. |

## Specific guide corrections and additions

1. Add a prominent capability table: **available now / simulated / provider sandbox / not implemented / merchant approval required**. Preserve the current honest local-screenshot labels.
2. Reconcile `PHASE2_ACCEPTANCE_STATUS.md` and historical Phase 1 delivery notes with the dated production report. Keep historical findings labelled by date rather than silently overwriting evidence.
3. Add a real launch chapter: domain binding and DNS/TLS ownership, publish versus preview, indexing/GA4 eligibility, approval gates, controlled live activation and rollback. Some steps first need product implementation.
4. Add actual Stripe sandbox, customer account and subscription walkthroughs, including failed payment, authentication recovery, address changes, cancellation and renewal verification. Clearly separate them from the private demo.
5. Link provider setup and worker operation instructions directly beside each relevant workflow; identify who configures jobs and who checks delivery/renewal failures.
6. Replace p13's Learn screenshot with contact Inbox and cookie Preferences examples, then document consent/GA4 verification, not just entering an ID.
7. Explain video replacement: the current media upload accepts images; editor video/mobile-video URL fields exist. Document approved hosting, URLs, mobile version, poster and size checks; do not imply the image uploader uploads video.
8. Add an explicit H24YOU design checklist: logo variants, font fallback, no-purple constraint, flavours, menu states, icons, research motion and final media. Explain that generic presets may violate these brand choices.
9. Expand the placeholder register into an asset/content inventory with page, field/file, owner and approval state. Include reviews, statement slots, social destinations, source-date confirmation and outstanding bottle price.
10. Add task-level acceptance for every adapted prompt. One successful tagline operation does not prove whole-page layout, complete article creation or full-brief generation.

## Recommended closure order

1. **Documentation truth:** reconcile status records and publish a capability/acceptance matrix. This prevents merchants treating the simulator as an open shop.
2. **Finish Phase 1 acceptance:** merchant approves copy/media/placeholders, design details and accessibility/performance; record remaining supplied inputs explicitly.
3. **Merchant configuration and real sandbox acceptance:** confirm EU origin, prices/rates, tax configuration and email; exercise actual Stripe, webhooks, customer login, discount redemption and recurring jobs with test-provider evidence.
4. **Close functional scope:** PayPal decision/implementation, wallet acceptance, tracking automation or approved scope change, review workflow and commerce analytics.
5. **Implement guarded launch:** verified domain, status/indexing transition, live credentials boundaries, monitoring and rollback; then demonstrate the real customer journey before authorising live trading.

No missing feature was fixed and no guide, production configuration or customer data was changed by this audit. This report is a local audit artifact, not a new release or merchant approval.
