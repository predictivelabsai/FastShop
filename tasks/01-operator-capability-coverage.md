# Task 01: operator capability coverage

builder: codex

Implement the capability index and bounded assistant controls described in
`docs/specs/h24you-operator-coverage-and-settings.md`.

Own `app/store_agent.py`, `app/store_agent_ops.py`, the assistant/review tests and any
operator-only presentation file. Do not edit the Settings directory or platform CSS.

Acceptance: an operator can discover every existing merchant-managed domain; direct
changes remain draft-only where reversible; consequential changes produce durable,
tenant-scoped review cards with exact summaries; protected settings return a clear route;
usage stays observable and output is capped.
