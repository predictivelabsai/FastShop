# Shopify connector eval

Result: **10/10 checks passed** (offline).

| Check | Result |
| --- | --- |
| Dry Run Has No Writes | Pass |
| Dry Run Reports All Types | Pass |
| Unmapped Items Warned | Pass |
| Shop Currency Decision Reported | Pass |
| First Apply Created Resources | Pass |
| Shop Money And Half Up Are Exact | Pass |
| Reimport Plans Updates | Pass |
| Reimport Has No Duplicates | Pass |
| Reimport Applied Updates | Pass |
| Api Is Pinned | Pass |

The fixture exercises GraphQL dry-run → apply → re-import, collection and catalog mapping, invalid customer and historic-line warnings, shop-currency selection, exact HALF_UP conversion, and duplicate-free mapped updates.
