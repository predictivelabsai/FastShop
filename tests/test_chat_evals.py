"""Ground-truth evals for the conversational site-builder chat flow (guided path)."""

from evals.site_builder_chat import CASES, run, write_results


def test_guided_chat_flow_matches_ground_truth():
    summary = run(live=False)
    assert summary["total"] == len(CASES)
    failures = [c["name"] for c in summary["cases"] if not c["passed"]]
    assert not failures, f"chat eval failures: {failures}"
    assert all(c["provider"] == "guided" for c in summary["cases"])


def test_results_are_writable(tmp_path):
    out = write_results(run(live=False), str(tmp_path))
    assert (out / "site_builder_chat_results.json").exists()
    assert (out / "site_builder_chat_results.md").exists()
