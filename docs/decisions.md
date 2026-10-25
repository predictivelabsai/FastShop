# Decisions

- 2026-10-25: P3 extends the existing durable order commands and checkout stock path. Returns/refunds retain line-level stock provenance; manual draft payments have no hosted pay link. Revision 20261025_0031_orders_customers chains from P2 independently of P4. Verification: docs/specs/admin-v2-p3-verification.md.
- 2026-10-26: Merchant entry points now converge on the membership-scoped v2 store shell. Retain account billing, owned onboarding and operator-only routes; preserve provider persistence and CSRF checks. Verification: docs/specs/admin-v2-entry-points-verification.md.
