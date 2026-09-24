"""Ground-truth evals for the conversational ("lovable-style") site builder chat flow.

Each case sends a natural-language prompt through the *real* builder pipeline
(begin_turn -> respond -> finish_turn) against a fresh in-memory site, then scores the
produced operations/proposals and the resulting draft against a ground-truth expectation.

Deterministic on the guided path (no XAI_API_KEY). Pass ``live=True`` to score the
configured model instead (requires a key; non-deterministic — effect checks still apply).
Results are written to ``output/evals/`` as JSON and Markdown.

Run:  python -m evals.site_builder_chat            # guided
      python -m evals.site_builder_chat --live     # configured model
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import content
from app import site_builder_services as builder
from app.config import settings
from app.integrations.site_builder_llm import respond
from app.models import Base, User

HERO_HEADLINE = "A calmer cup, every morning"


@dataclass(frozen=True)
class Case:
    name: str
    intent: str
    prompt: str
    expect_ops: list[str]
    expect_proposals: list[str]
    check: Callable[[dict, dict], bool]  # (settings_json, home_document) -> effect ok
    select_hero: bool = False


CASES: list[Case] = [
    Case("design_preset_warm", "Apply a design language from a one-word request",
         "warm", ["brief", "theme"], [],
         lambda s, d: bool(s.get("design")) and s.get("builder_brief", {}).get("design_language") == "warm"),
    Case("onboarding_business", "Record the business brief from a conversational answer",
         "business: Organic loose-leaf tea", ["brief"], [],
         lambda s, d: s.get("builder_brief", {}).get("business", "").startswith("Organic loose-leaf tea")),
    Case("onboarding_audience", "Record the audience brief",
         "audience: Home tea drinkers", ["brief"], [],
         lambda s, d: s.get("builder_brief", {}).get("audience") == "Home tea drinkers"),
    Case("edit_hero_headline", "Edit the selected hero heading from chat",
         f"Headline: {HERO_HEADLINE}", ["section"], [],
         lambda s, d: any(sec.get("heading") == HERO_HEADLINE for sec in d["sections"] if sec["type"] == "hero"),
         select_hero=True),
    Case("shipping_needs_approval", "A commerce change is a proposal, never auto-applied",
         "shipping: 1000", [], ["merchant"],
         lambda s, d: not s.get("builder_brief") and not s.get("design")),
    Case("discussion_no_change", "A question produces no draft change",
         "hello", [], [],
         lambda s, d: not s.get("builder_brief") and not s.get("design")),
]


def _run_case(db: Session, owner: User, case: Case, live: bool) -> dict:
    slug = "eval-" + uuid4().hex[:10]
    site = content.create_site(db, owner.id, "Eval " + case.name, slug)
    db.commit()
    home = next(p for p in content.site_pages(db, site) if p.path == "/")
    section_id = ""
    if case.select_hero:
        section_id = next(s["id"] for s in home.draft_json["sections"] if s["type"] == "hero")
    db.refresh(site)
    command_id = uuid4().hex
    turn, _ = builder.begin_turn(db, site.id, owner.id, command_id, case.prompt, home.id, site.version, section_id)
    turn_id, context = turn.id, turn.context_json
    db.commit()

    response, provider = respond(case.prompt, context, [])
    builder.finish_turn(db, site.id, owner.id, turn_id, response, provider)
    db.commit()

    db.refresh(site)
    home_after = next(p for p in content.site_pages(db, site) if p.path == "/")
    produced_ops = sorted(op.get("op", "") for op in response.get("operations", []))
    produced_proposals = sorted(p.get("kind", "") for p in response.get("proposals", []))
    effect_ok = bool(case.check(site.settings_json, home_after.draft_json))

    ops_ok = produced_ops == sorted(case.expect_ops)
    proposals_ok = produced_proposals == sorted(case.expect_proposals)
    # On the guided path the mode is deterministic; on live runs we score by effect/ops only.
    mode_ok = provider == "guided" if not live else True
    passed = ops_ok and proposals_ok and effect_ok and mode_ok
    return {
        "name": case.name, "intent": case.intent, "prompt": case.prompt, "provider": provider,
        "expected_ops": sorted(case.expect_ops), "produced_ops": produced_ops,
        "expected_proposals": sorted(case.expect_proposals), "produced_proposals": produced_proposals,
        "ops_ok": ops_ok, "proposals_ok": proposals_ok, "effect_ok": effect_ok, "mode_ok": mode_ok,
        "answer": response.get("answer", "")[:200], "passed": passed,
    }


def run(*, live: bool = False) -> dict:
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    rows = []
    with Session(engine, expire_on_commit=False) as db:
        owner = User(email="evals@fastshop.example", name="Evals")
        db.add(owner)
        db.flush()
        for case in CASES:
            rows.append(_run_case(db, owner, case, live))
    passed = sum(r["passed"] for r in rows)
    return {
        "suite": "site_builder_chat",
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "mode": "live" if live else "guided",
        "model": settings.model_name if live else "guided-presets",
        "total": len(rows), "passed": passed, "failed": len(rows) - passed,
        "cases": rows,
    }


def write_results(summary: dict, out_dir: str = "output/evals") -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / "site_builder_chat_results.json").write_text(json.dumps(summary, indent=2))
    lines = [
        f"# Site builder chat evals — {summary['mode']}",
        "",
        f"Generated: {summary['generated_at']} · model: `{summary['model']}`",
        f"**{summary['passed']}/{summary['total']} passed**",
        "",
        "| Case | Intent | Prompt | Expected ops | Produced ops | Proposals | Effect | Pass |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in summary["cases"]:
        lines.append(
            f"| {r['name']} | {r['intent']} | `{r['prompt']}` | {r['expected_ops']} | "
            f"{r['produced_ops']} | {r['produced_proposals'] or '—'} | "
            f"{'ok' if r['effect_ok'] else 'FAIL'} | {'✅' if r['passed'] else '❌'} |"
        )
    (out / "site_builder_chat_results.md").write_text("\n".join(lines) + "\n")
    return out


def main() -> None:
    import sys
    live = "--live" in sys.argv[1:]
    summary = run(live=live)
    out = write_results(summary)
    print(json.dumps({"mode": summary["mode"], "passed": summary["passed"], "total": summary["total"],
                      "results": str(out)}, indent=2))


if __name__ == "__main__":
    main()
