"""Offline block-refinement evals for guided and mocked-provider chat paths."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import content
from app import site_builder_services as builder
from app.integrations.site_builder_llm import guided
from app.models import Base, User
from app.site_blocks import add_block, patch_block, remove_block, reorder_blocks
from app.site_refinement import apply_operation

SCENARIOS = (
    ("tweak_hero_heading", "Headline: A more thoughtful welcome", "patch"),
    ("swap_two_sections", "swap two sections", "reorder"),
    ("remove_faq_block", "remove the FAQ block", "remove"),
)


def _workspace(db: Session, owner: User, suffix: str):
    site = content.create_site(db, owner.id, "Refinement eval", "refine-eval-" + suffix + "-" + uuid4().hex[:6])
    page = next(page for page in content.site_pages(db, site) if page.path == "/")
    page.draft_json = add_block(page.draft_json, {
        "id": "details", "type": "text", "heading": "How it works", "body": "A useful second block.",
    })
    page.draft_json = add_block(page.draft_json, {
        "id": "faq", "type": "faq", "heading": "Questions", "items": [{"heading": "When?", "body": "Soon."}],
    })
    db.commit()
    return site, page


def _mocked_response(name: str, context: dict) -> dict:
    page_id = context["page_id"]
    document = copy.deepcopy(context["snapshot"]["pages"][page_id]["document"])
    if name == "tweak_hero_heading":
        hero = next(block for block in document["blocks"] if block["type"] == "hero")
        document = patch_block(document, hero["id"], {"heading": "A more thoughtful welcome"}, locale="en")
    elif name == "swap_two_sections":
        ids = [block["id"] for block in document["blocks"]]
        ids[0], ids[1] = ids[1], ids[0]
        document = reorder_blocks(document, ids)
    else:
        document = remove_block(document, "faq")
    return {"answer": "Mock provider prepared a full-document edit.", "operations": [
        {"op": "document", "page_id": page_id, "document": document},
    ]}


def _preview(db, owner, site, page, name, prompt, mode):
    selected = page.draft_json["blocks"][0]["id"] if name == "tweak_hero_heading" else ""
    turn, _ = builder.begin_turn(db, site.id, owner.id, uuid4().hex, prompt, page.id, site.version, selected)
    response = guided(prompt, turn.context_json) if mode == "guided" else _mocked_response(name, turn.context_json)
    builder.finish_turn(db, site.id, owner.id, turn.id, response, mode)
    db.commit()
    return turn


def _run_scenario(db: Session, owner: User, name: str, prompt: str, expected_op: str, mode: str) -> dict:
    site, page = _workspace(db, owner, mode)
    before = copy.deepcopy(page.draft_json)
    rejected = _preview(db, owner, site, page, name, prompt, mode)
    rejected_ops = rejected.response_json["refinement"]["operations"]
    builder.decide_refinement(db, site.id, owner.id, rejected.id, "reject", "all", site.version)
    db.commit()
    reject_unchanged = page.draft_json == before

    accepted = _preview(db, owner, site, page, name, prompt, mode)
    operations = accepted.response_json["refinement"]["operations"]
    expected_document = copy.deepcopy(before)
    for operation in operations:
        expected_document = apply_operation(expected_document, operation)
    builder.decide_refinement(db, site.id, owner.id, accepted.id, "accept", "all", site.version)
    db.commit()
    exact_apply = page.draft_json == expected_document
    minimal = len(operations) == 1 and operations[0]["op"] == expected_op
    if expected_op == "patch":
        target_ok = operations[0]["block_id"] == before["blocks"][0]["id"]
    elif expected_op == "remove":
        target_ok = operations[0]["block_id"] == "faq"
    else:
        target_ok = operations[0]["after"][:2] == [before["blocks"][1]["id"], before["blocks"][0]["id"]]
    return {
        "name": name,
        "mode": mode,
        "prompt": prompt,
        "expected_op": expected_op,
        "produced_ops": [operation["op"] for operation in operations],
        "affected_blocks": operations[0].get("block_ids") or [operations[0].get("block_id")],
        "correct_target": target_ok,
        "minimal_op_set": minimal,
        "reject_unchanged": reject_unchanged,
        "accept_exact": exact_apply,
        "passed": target_ok and minimal and reject_unchanged and exact_apply and len(rejected_ops) == 1,
    }


def run() -> dict:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    rows = []
    with Session(engine, expire_on_commit=False) as db:
        owner = User(email="refinement-evals@fastshop.example", name="Refinement evals")
        db.add(owner)
        db.flush()
        for mode in ("guided", "mocked-llm"):
            for scenario in SCENARIOS:
                rows.append(_run_scenario(db, owner, *scenario, mode))
    passed = sum(row["passed"] for row in rows)
    return {
        "suite": "site_refinement",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "total": len(rows),
        "passed": passed,
        "failed": len(rows) - passed,
        "cases": rows,
    }


def write_results(summary: dict, out_dir: str = "output/evals") -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "site_refinement_results.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Site refinement evals",
        "",
        f"Generated: {summary['generated_at']}",
        f"**{summary['passed']}/{summary['total']} passed**",
        "",
        "| Scenario | Path | Ops | Target | Minimal | Reject safe | Exact accept | Pass |",
        "|---|---|---|---|---|---|---|---|",
    ]
    def mark(value):
        return "yes" if value else "NO"

    for row in summary["cases"]:
        lines.append(f"| {row['name']} | {row['mode']} | {row['produced_ops']} | "
            f"{mark(row['correct_target'])} | {mark(row['minimal_op_set'])} | "
            f"{mark(row['reject_unchanged'])} | {mark(row['accept_exact'])} | "
            f"{'PASS' if row['passed'] else 'FAIL'} |")
    (out / "site_refinement_results.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def main():
    summary = run()
    out = write_results(summary)
    print(json.dumps({"passed": summary["passed"], "total": summary["total"], "results": str(out)}, indent=2))


if __name__ == "__main__":
    main()
