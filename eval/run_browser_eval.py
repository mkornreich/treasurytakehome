"""Score the browser-only path (app/static/local/) on the same real labels as run_eval.py.

    uv run python eval/fetch_cola.py                                  # once: download the label images
    uv run python eval/run_browser_eval.py --save eval/results/browser.json --compare eval/results/after.json

This serves the page's files and the label images on 127.0.0.1, opens
eval/browser_harness.html in headless Chrome, and lets the page check every label
the way it does when the server does not answer: tesseract.js and the JavaScript
rules, nothing sent back but the results. The two runs per label (truthful and
tampered applications) and the scoring are run_eval.py's.

As in run_eval.py, Chrome is pinned to one CPU by default, so timings are worst-case.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

from run_eval import (  # noqa: E402
    application,
    label_files,
    load_cases,
    print_summary,
    score,
    summarize,
    tampered_values,
)

CHROMES = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]


def make_handler(jobs: bytes, inbox: queue.Queue):
    routes = {"/static/": ROOT / "app" / "static", "/labels/": HERE / "real_labels"}

    class Handler(SimpleHTTPRequestHandler):
        def translate_path(self, path: str) -> str:
            path = urlparse(path).path
            if path in ("/", "/harness"):
                return str(HERE / "browser_harness.html")
            for prefix, folder in routes.items():
                if path.startswith(prefix):
                    target = (folder / path[len(prefix):]).resolve()
                    if target.is_relative_to(folder.resolve()):
                        return str(target)
            return str(HERE / "not-found")

        def do_GET(self):
            if urlparse(self.path).path == "/jobs.json":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(jobs)))
                self.end_headers()
                self.wfile.write(jobs)
                return
            super().do_GET()

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            inbox.put((urlparse(self.path).path, json.loads(body or b"{}")))
            self.send_response(204)
            self.end_headers()

        def log_message(self, *args):
            pass

    return Handler


def find_chrome(name: str | None) -> str:
    for candidate in [name] if name else CHROMES:
        if candidate and (path := shutil.which(candidate)):
            return path
    sys.exit("Chrome or Chromium is needed: install it, or pass --chrome /path/to/chrome.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--save", type=Path, help="write per-label results and the summary as JSON")
    parser.add_argument("--compare", type=Path, help="earlier results JSON (for example the server's) to compare with")
    parser.add_argument("--all-cpus", action="store_true", help="don't pin Chrome to a single CPU")
    parser.add_argument("--budget-ms", type=float, help="reading budget per label (default: the page's 3.5 s)")
    parser.add_argument("--chrome", help="Chrome or Chromium executable")
    parser.add_argument("--timeout", type=float, default=1800, help="seconds to wait for the whole run")
    parser.add_argument("--only", help="comma-separated TTB IDs to check (default: all)")
    parser.add_argument("--verbose", action="store_true", help="print where the time went for each label")
    args = parser.parse_args()

    only = set(args.only.split(",")) if args.only else None
    cases = {c["ttb_id"]: c for c in load_cases() if (only is None or c["ttb_id"] in only) and label_files(c) is not None}
    jobs = []
    for case in cases.values():
        files = [f"/labels/{case['ttb_id']}/{name}" for name in case["label_files"].split(";")]
        jobs.append({
            "ttb_id": case["ttb_id"], "files": files,
            "truthful": application(case).model_dump(mode="json"),
            "tampered": application(case, **tampered_values(case)).model_dump(mode="json"),
        })

    inbox: queue.Queue = queue.Queue()
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(json.dumps(jobs).encode(), inbox))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/" + (f"?budget={args.budget_ms}" if args.budget_ms else "")

    command = [find_chrome(args.chrome), "--headless=new", "--no-first-run", "--no-default-browser-check",
               "--disable-extensions", "--disable-background-timer-throttling", "--disable-renderer-backgrounding"]
    if not args.all_cpus and hasattr(os, "sched_getaffinity") and shutil.which("taskset"):
        command = ["taskset", "-c", str(min(os.sched_getaffinity(0)))] + command  # Chrome's processes inherit this
    results, info = [], {}
    with tempfile.TemporaryDirectory() as profile:
        chrome = subprocess.Popen([*command, f"--user-data-dir={profile}", url],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            while True:
                path, data = inbox.get(timeout=args.timeout)
                if path == "/done":
                    info = data
                    break
                if "error" in data:
                    print(f"{data['ttb_id']}: the browser failed: {data['error']}", file=sys.stderr)
                    continue
                case = cases[data["ttb_id"]]
                results.append(score(case, data["truthful"], data["tampered"], tampered_values(case)))
                if args.verbose:
                    for run in ("truthful", "tampered"):
                        print(f"   {run}: {json.dumps(data[run].get('timings_ms'))}")
        except queue.Empty:
            print(f"No answer from the browser in {args.timeout:.0f} s; scoring what arrived.", file=sys.stderr)
        finally:
            chrome.terminate()
            chrome.wait(timeout=30)
            server.shutdown()
    if "error" in info:
        sys.exit(f"The browser could not run the check: {info['error']}")

    summary = summarize(results)
    summary["browser"] = info.get("userAgent", "")
    summary["cpus"] = "all" if args.all_cpus else 1
    if args.compare:
        print_summary(json.loads(args.compare.read_text())["summary"], f"before ({args.compare.name})")
    print_summary(summary, "browser only")
    if args.save:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        args.save.write_text(json.dumps({"summary": summary, "results": results}, indent=1) + "\n")


if __name__ == "__main__":
    main()
