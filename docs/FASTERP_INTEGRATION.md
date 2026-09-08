# FastERP integration contract

FastShop owns storefront publication, channel prices, carts, checkout, payment
events, and the customer order experience. FastERP owns accounting, invoices,
purchasing, and authoritative warehouse movements.

Confirmed orders are written to FastShop's transactional outbox in the same
transaction as checkout. The connector sends them to
`POST /api/v1/commerce/orders` with a bearer token, `Idempotency-Key`, and
`X-FastERP-Company`. Retries must return the original FastERP resource rather
than create a duplicate.

FastERP should expose company-scoped item and availability feeds, an idempotent
commerce-order command, and signed order/invoice/fulfilment change events.
External IDs and versions are stored in FastShop mappings. Neither application
writes directly into the other's PostgreSQL schema.

