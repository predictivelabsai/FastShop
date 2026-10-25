"""Run the existing browser journeys on disposable local stores without environment files."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "output/playwright/admin-v2/polish/legacy"
JOURNEYS = (
    "analytics", "builder", "customer_accounts", "customer", "liquid", "liquid_m3",
    "liquid_storefront", "site", "store_checkout", "store_checkout_subscription",
    "subscription", "us_commerce",
)
SELF_HOSTED = {
    "analytics": 5043, "builder": 5044, "customer_accounts": 5039,
    "store_checkout": 5041, "store_checkout_subscription": 5041, "subscription": 5042,
}


def observe_browser(journey: str, evidence: Path):
    """Observe every context, including secondary pages and iframe requests."""
    from playwright.sync_api import Browser

    errors, expected, downloads = [], [], set()
    original = Browser.new_context
    original_page = Browser.new_page
    observed_contexts = set()
    expected_responses = {
        "customer_accounts": {(404, "/sites/h24you/account")},
        "customer": {(404, "/sites/h24you/account")},
        "liquid_storefront": {(404, "/sites/h24you-liquid/missing-page"),
                              (400, "/sites/h24you-liquid/cart/update"),
                              (404, "/sites/h24you-liquid/checkout/missing"),
                              (404, "/sites/h24you-liquid/account"),
                              (404, "/sites/h24you-liquid/account/subscriptions")},
    }.get(journey, set())

    def response(response):
        if "attachment" in response.headers.get("content-disposition", ""):
            downloads.add(response.url)
        if response.status < 400:
            return
        path = urlsplit(response.url).path
        record = f"HTTP {response.status}: {response.request.method} {path}"
        (expected if (response.status, path) in expected_responses else errors).append(record)

    def request_failed(request):
        if request.failure == "net::ERR_ABORTED" and request.url in downloads:
            expected.append(f"Completed attachment navigation: {urlsplit(request.url).path}")
            return
        errors.append(f"Request failed: {request.method} {urlsplit(request.url).path}: {request.failure}")

    def console(message):
        if message.type != "error":
            return
        path = urlsplit(message.location.get("url", "")).path
        record = f"Console: {path}: {message.text}"
        if any(path == allowed_path and f"{status}" in message.text
               for status, allowed_path in expected_responses) and message.text.startswith("Failed to load resource:"):
            expected.append(record)
        else:
            errors.append(record)

    def page_opened(page):
        page.on("pageerror", lambda error: errors.append(f"Uncaught: {error}"))
        page.on("console", console)

    def observe_context(context):
        if context in observed_contexts:
            return context
        observed_contexts.add(context)
        context.on("page", page_opened)
        context.on("response", response)
        context.on("requestfailed", request_failed)
        return context

    def new_context(browser, **kwargs):
        return observe_context(original(browser, **kwargs))

    def new_page(browser, **kwargs):
        page = original_page(browser, **kwargs)
        observe_context(page.context)
        page_opened(page)
        return page

    Browser.new_context = new_context
    Browser.new_page = new_page

    def finish():
        Browser.new_context = original
        Browser.new_page = original_page
        report = {"unexpected_errors": sorted(set(errors)),
                  "expected_negative_checks": sorted(set(expected))}
        (evidence / "browser-observations.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8",
        )
        assert not report["unexpected_errors"], "\n".join(report["unexpected_errors"])

    return finish


def worker(journey: str) -> None:
    from verify_admin_v2_browser import isolate

    module_name = "store_checkout" if journey == "store_checkout_subscription" else journey
    with tempfile.TemporaryDirectory(prefix="fastshop-legacy-browser-") as data_dir:
        port = SELF_HOSTED.get(journey)
        if not port:
            with socket.socket() as connection:
                connection.bind(("127.0.0.1", 0))
                port = connection.getsockname()[1]
        # The storefront verifier checks explicit relative URLs against its original port.
        if journey == "liquid_storefront":
            port = 5025
        isolate(data_dir, port)
        original_mkdtemp = tempfile.mkdtemp

        def child_data(*args, **kwargs):
            kwargs.setdefault("dir", data_dir)
            return original_mkdtemp(*args, **kwargs)

        tempfile.mkdtemp = child_data
        module = importlib.import_module(f"verify_{module_name}_browser")
        evidence = OUTPUT / journey
        evidence.mkdir(parents=True, exist_ok=True)
        finish_observing = observe_browser(journey, evidence)

        def evidence_path(*args):
            path = Path(*args)
            prefix = Path("output/playwright")
            if not path.is_absolute() and path.is_relative_to(prefix):
                return evidence / path.relative_to(prefix)
            return path

        evidence_path.cwd = Path.cwd
        module.Path = evidence_path
        if journey == "liquid_storefront":
            module.OUT = evidence
        sys.argv = [str(ROOT / "scripts" / f"verify_{module_name}_browser.py")]
        if journey in {"liquid", "liquid_m3", "site", "us_commerce"}:
            sys.argv += ["--base", f"http://127.0.0.1:{port}"]
        if journey in {"liquid", "liquid_m3", "site"}:
            sys.argv += ["--output", str(evidence)]
        if journey == "store_checkout_subscription":
            sys.argv += ["--subscription"]
        server = thread = None
        try:
            if journey not in SELF_HOSTED:
                import uvicorn

                from app.main import app

                server = uvicorn.Server(uvicorn.Config(
                    app, host="127.0.0.1", port=port, log_level="error", access_log=False,
                ))
                thread = threading.Thread(target=server.run, daemon=True)
                thread.start()
                deadline = time.monotonic() + 30
                while not server.started:
                    if not thread.is_alive() or time.monotonic() > deadline:
                        raise RuntimeError("Disposable legacy verifier server failed to start")
                    thread.join(.05)
            try:
                module.main()
            finally:
                finish_observing()
        finally:
            if server:
                server.should_exit = True
                thread.join(timeout=10)
            if "app.db" in sys.modules:
                sys.modules["app.db"].engine.dispose()
            tempfile.mkdtemp = original_mkdtemp


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", choices=JOURNEYS)
    parser.add_argument("--only", nargs="+", choices=JOURNEYS)
    args = parser.parse_args()
    if args.worker:
        worker(args.worker)
        return
    OUTPUT.mkdir(parents=True, exist_ok=True)
    results_path = OUTPUT / "results.json"
    results = json.loads(results_path.read_text(encoding="utf-8")) if args.only and results_path.exists() else []
    for journey in args.only or JOURNEYS:
        started = time.monotonic()
        process = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--worker", journey],
            cwd=ROOT, env=os.environ | {"PYTHONUTF8": "1", "PYTHON_DOTENV_DISABLED": "1"},
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        result = {"journey": journey, "status": "passed" if process.returncode == 0 else "failed",
                  "seconds": round(time.monotonic() - started, 2),
                  "stdout": process.stdout, "stderr": process.stderr}
        results = [previous for previous in results if previous["journey"] != journey] + [result]
        results_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(json.dumps({key: result[key] for key in ("journey", "status", "seconds")}), flush=True)
        if process.returncode:
            print(process.stdout + process.stderr, flush=True)
    if any(result["status"] == "failed" for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
