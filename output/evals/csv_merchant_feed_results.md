# CSV catalog and Merchant Center feed eval

Result: **10/10 checks passed** (offline).

| Check | Result |
| --- | --- |
| Preview Has No Catalog Writes | Pass |
| Bom Crlf And Quoted Comma Parsed | Pass |
| Decimal Rounding Is Exact | Pass |
| Per Row Status Is Complete | Pass |
| First Apply Created Catalog | Pass |
| Stock And Row Mappings Applied | Pass |
| Reimport Plans Updates | Pass |
| Reimport Has No Duplicates | Pass |
| Merchant Feed Is Well Formed And Complete | Pass |
| Validation Report Names Invalid Product | Pass |

The fixture exercises UTF-8 BOM + CRLF parsing, quoted commas, exact Decimal rounding, immutable normalized-row planning, first apply, idempotent re-import, stock, RSS 2.0 generation, and the downloadable validation report.
