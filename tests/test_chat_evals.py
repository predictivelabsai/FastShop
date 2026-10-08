"""Ground-truth evals for the conversational site-builder chat flow (guided path)."""

from evals.site_builder_chat import CASES, run, write_results
from evals.site_refinement import run as run_refinement
from evals.site_refinement import write_results as write_refinement_results


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


def test_refinement_evals_cover_guided_and_mocked_provider(tmp_path):
    summary = run_refinement()
    assert summary["total"] == 6 and summary["passed"] == 6
    assert {case["mode"] for case in summary["cases"]} == {"guided", "mocked-llm"}
    out = write_refinement_results(summary, str(tmp_path))
    assert (out / "site_refinement_results.json").exists()
    assert (out / "site_refinement_results.md").exists()
