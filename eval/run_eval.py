"""Score Label Check against real, approved labels from TTB's Public COLA Registry.

    uv run python eval/fetch_cola.py                       # once: download the label images
    uv run python eval/run_eval.py --save eval/results/after.json
    uv run python eval/run_eval.py --compare eval/results/baseline.json

Two runs per label:

* truthful: the application matches the label (transcribed by hand into
  eval/real_labels.csv). Every check should pass, except the few genuine
  problems noted in the `expect` column.
* tampered: alcohol content, net contents and brand are changed on the
  application. The tool should flag each one.

By default the process is pinned to one CPU with one OCR worker, the smallest
deployment, so timings are worst-case.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.models import Application  # noqa: E402
from app.pipeline import Verifier  # noqa: E402
from app.verification.units import parse_alcohol, parse_volume  # noqa: E402

HERE = Path(__file__).resolve().parent
FIELDS = ["brand_name", "class_type", "alcohol_content", "net_contents", "bottler", "country_of_origin"]
TAMPER_BRAND = "HARBOR LIGHT"


def load_cases() -> list[dict]:
    with open(HERE / "real_labels.csv", newline="", encoding="utf-8") as fh:
        cases = list(csv.DictReader(fh))
    for case in cases:
        case["expect_map"] = {}
        for part in filter(None, case["expect"].split(";")):
            field, allowed = part.split("=")
            case["expect_map"][field] = set(allowed.split("|"))
    return cases


def application(case: dict, **overrides) -> Application:
    data = {f: case[f] for f in FIELDS}
    data["beverage_type"] = case["beverage_type"]
    data.update(overrides)
    return Application(**data)


def tampered_values(case: dict) -> dict:
    out = {"brand_name": TAMPER_BRAND}
    alcohol = parse_alcohol(case["alcohol_content"])
    if alcohol.abv is not None:
        new = round(alcohol.abv + 2, 1)
        out["alcohol_content"] = f"{new:g}% Alc./Vol." + (f" ({2 * new:g} Proof)" if alcohol.proof else "")
    ml = parse_volume(case["net_contents"]) if case["net_contents"] else None
    if ml:
        out["net_contents"] = "1 L" if abs(ml - 750) < 1 else ("7.75 gallons" if ml > 10000 else "750 mL")
    return out


def classify(status: str, allowed: set[str]) -> str:
    if status in allowed:
        return "correct"
    if "pass" in allowed:
        return "false review" if status == "review" else "false problem"
    return "missed" if status == "pass" else "correct"


def label_files(case: dict) -> list[Path] | None:
    """The label images of one case, or None (with a note) when they have not been downloaded."""
    folder = HERE / "real_labels" / case["ttb_id"]
    files = [folder / name for name in case["label_files"].split(";")]
    if not all(f.exists() for f in files):
        print(f"skip {case['ttb_id']}: images missing (run eval/fetch_cola.py)", file=sys.stderr)
        return None
    return files


def score(case: dict, truthful: dict, tampered: dict, changes: dict) -> dict:
    """Grade one label's two runs. `truthful` and `tampered` are verification results as JSON
    (from the server pipeline, or from the browser in run_browser_eval.py)."""
    checks = []
    for c in truthful["checks"]:
        allowed = case["expect_map"].get(c["field"], {"pass"})
        checks.append({"field": c["field"], "status": c["status"], "message": c["message"],
                       "found": c["found"], "outcome": classify(c["status"], allowed)})
    caught = {}
    for field in changes:
        status = next((c["status"] for c in tampered["checks"] if c["field"] == field), "missing")
        caught[field] = {"pass": "missed", "review": "flagged", "fail": "caught"}.get(status, status)
    result = {
        "ttb_id": case["ttb_id"], "brand": case["brand_name"], "type": case["beverage_type"],
        "overall": truthful["overall"], "elapsed_ms": truthful["elapsed_ms"],
        "tampered_elapsed_ms": tampered["elapsed_ms"], "engines": truthful["engines"],
        "checks": checks, "tampered": caught,
    }
    bad = [f"{c['field']}:{c['status']}" for c in checks if c["outcome"] != "correct"]
    print(f"{case['ttb_id']} {case['brand_name'][:22]:22} {truthful['elapsed_ms']:5} ms  "
          f"{'ok' if not bad else ' '.join(bad)}  | tampered: {caught}", flush=True)
    return result


async def run(cases: list[dict], verifier: Verifier) -> list[dict]:
    results = []
    for case in cases:
        files = label_files(case)
        if files is None:
            continue
        uploads = [f.read_bytes() for f in files]
        changes = tampered_values(case)
        truthful = await verifier.verify(uploads, application(case))
        tampered = await verifier.verify(uploads, application(case, **changes))
        results.append(score(case, truthful.model_dump(mode="json"), tampered.model_dump(mode="json"), changes))
    return results


def summarize(results: list[dict]) -> dict:
    outcomes = Counter(c["outcome"] for r in results for c in r["checks"])
    by_field: dict[str, Counter] = {}
    for r in results:
        for c in r["checks"]:
            by_field.setdefault(c["field"], Counter())[c["outcome"]] += 1
    tamper: dict[str, Counter] = {}
    for r in results:
        for field, verdict in r["tampered"].items():
            tamper.setdefault(field, Counter())[verdict] += 1
    times = [r["elapsed_ms"] for r in results] + [r["tampered_elapsed_ms"] for r in results]
    return {
        "labels": len(results),
        "checks": sum(outcomes.values()),
        "outcomes": dict(outcomes),
        "labels_fully_correct": sum(all(c["outcome"] == "correct" for c in r["checks"]) for r in results),
        "by_field": {f: dict(c) for f, c in by_field.items()},
        "tampered": {f: dict(c) for f, c in tamper.items()},
        "median_ms": statistics.median(times) if times else 0,
        "max_ms": max(times) if times else 0,
    }


def pct(n: int, d: int) -> str:
    return f"{100 * n / d:.0f}%" if d else "-"


def print_summary(summary: dict, title: str) -> None:
    o, n = summary["outcomes"], summary["checks"]
    print(f"\n== {title}: {summary['labels']} labels, {n} checks")
    for key in ("correct", "false review", "false problem", "missed"):
        print(f"   {key:14} {o.get(key, 0):4}  {pct(o.get(key, 0), n)}")
    print(f"   labels with every check right: {summary['labels_fully_correct']}/{summary['labels']}")
    for field, counts in summary["tampered"].items():
        total = sum(counts.values())
        print(f"   tampered {field:16} caught {counts.get('caught', 0)}, flagged {counts.get('flagged', 0)}, missed {counts.get('missed', 0)} of {total}")
    print(f"   time per label: median {summary['median_ms']:.0f} ms, max {summary['max_ms']} ms")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--save", type=Path, help="write per-label results and the summary as JSON")
    parser.add_argument("--compare", type=Path, help="earlier results JSON to compare with")
    parser.add_argument("--all-cpus", action="store_true", help="don't pin to a single CPU")
    args = parser.parse_args()

    workers = os.cpu_count() or 1
    if not args.all_cpus and hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(0, {min(os.sched_getaffinity(0))})  # Tesseract child processes inherit this
        workers = 1
    os.environ["VISION"] = os.environ.get("VISION", "off")
    settings = Settings.from_env()
    verifier = Verifier(Settings(**{**settings.__dict__, "ocr_workers": workers}))

    results = asyncio.run(run(load_cases(), verifier))
    summary = summarize(results)
    if args.compare:
        print_summary(json.loads(args.compare.read_text())["summary"], f"before ({args.compare.name})")
    print_summary(summary, "this run")
    if args.save:
        args.save.parent.mkdir(parents=True, exist_ok=True)
        args.save.write_text(json.dumps({"summary": summary, "results": results}, indent=1) + "\n")


if __name__ == "__main__":
    main()
