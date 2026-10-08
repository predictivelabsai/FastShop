# WooCommerce connector eval

Result: **9/9 checks passed** (offline).

| Check | Result |
| --- | --- |
| Dry Run Has No Writes | Pass |
| Dry Run Reports All Types | Pass |
| Unmapped Items Warned | Pass |
| First Apply Created Resources | Pass |
| Reimport Plans Updates | Pass |
| Reimport Has No Duplicates | Pass |
| Reimport Applied Updates | Pass |
| Export Is Review Only | Pass |
| Export Rounds Prices Exactly | Pass |

The fixture exercises dry-run → apply → re-import, unmapped warnings, idempotent mappings, HALF_UP price conversion, and the review-only export bundle.
