"""The rules in the browser (app/static/local/rules.js) must give the same answers as the server.

When the server cannot be reached, the page checks labels with that JavaScript
port. Every case from tests/parity_cases.py runs through both, and every field of
every check must be the same: status, message, values, highlight boxes, warning diff.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from app.models import Application
from app.verification.rules import overall, verify
from tests.parity_cases import all_cases, evidence_from_json

ROOT = Path(__file__).resolve().parent.parent
pytestmark = [
    pytest.mark.skipif(shutil.which("node") is None, reason="node not installed"),
    pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed"),
]


def _differences(python, js) -> list[str]:
    found = []
    status, headline = overall(python)
    if (status.value, headline) != (js["overall"], js["headline"]):
        found.append(f"overall: {status.value} {headline!r} != {js['overall']} {js['headline']!r}")
    if [c.field for c in python] != [c["field"] for c in js["checks"]]:
        return [*found, "different checks"]
    for check, other in zip((c.model_dump(mode="json") for c in python), js["checks"], strict=True):
        for key in ("label", "status", "message", "expected", "found", "source", "diff"):
            if check[key] != other[key]:
                found.append(f"{check['field']}.{key}: {check[key]!r} != {other[key]!r}")
        boxes = [[b["image"], b["x"], b["y"], b["w"], b["h"]] for b in check["boxes"]]
        js_boxes = [[b["image"], b["x"], b["y"], b["w"], b["h"]] for b in other["boxes"]]
        if len(boxes) != len(js_boxes) or any(
            abs(a - b) > 1e-9 for x, y in zip(boxes, js_boxes, strict=True) for a, b in zip(x, y, strict=True)
        ):
            found.append(f"{check['field']}.boxes differ")
    return found


def test_browser_rules_match_server_rules(tmp_path):
    cases = all_cases()
    (tmp_path / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
    subprocess.run(
        ["node", str(ROOT / "tests/js/run_rules.mjs"), str(tmp_path / "cases.json"), str(tmp_path / "js.json")],
        check=True, timeout=300,
    )
    js_results = json.loads((tmp_path / "js.json").read_text(encoding="utf-8"))
    assert len(js_results) == len(cases)

    mismatches = []
    for case, js in zip(cases, js_results, strict=True):
        python = verify(Application.model_validate(case["application"]), [evidence_from_json(e) for e in case["evidences"]])
        mismatches += [f"{case['name']}: {d}" for d in _differences(python, js)]
    assert not mismatches, "\n".join(mismatches[:20])
    statuses = {c["status"] for js in js_results for c in js["checks"]}
    assert statuses == {"pass", "review", "fail"}  # the cases cover every outcome


def test_browser_bold_and_photo_checks_match_server(tmp_path):
    """The heading-weight measurement and the photo notices, in imaging.js, against the Python originals."""
    import pytesseract
    from pytesseract import Output

    from app.extraction.ocr import TESSERACT_CONFIG, _header_weight
    from app.extraction.typography import WordImage
    from app.imaging import View, decode, quality_notices, to_gray

    names = ["01_old_tom_bourbon.png", "04_warning_title_case.png", "05_warning_not_bold.png",
             "09_photo_angle_glare.jpg", "12_back_label.png", "13_side_panel.png"]
    cases, expected = [], []
    for i, name in enumerate(names):
        gray = to_gray(decode((ROOT / "samples/labels" / name).read_bytes()))
        view = View.of(gray).fit_for_ocr()
        data = pytesseract.image_to_data(view.image, lang="eng", config=TESSERACT_CONFIG, output_type=Output.DICT)
        words = [WordImage(t.strip(), *(int(data[k][j]) for k in ("left", "top", "width", "height")))
                 for j, t in enumerate(data["text"]) if t.strip()]
        view.image.tofile(tmp_path / f"{i}.gray")
        gray.tofile(tmp_path / f"{i}.orig")
        cases.append({"id": i, "width": view.image.shape[1], "height": view.image.shape[0],
                      "orig_width": gray.shape[1], "orig_height": gray.shape[0], "words": [w.__dict__ for w in words]})
        expected.append((name, _header_weight(view, words), quality_notices(gray)))
    (tmp_path / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
    subprocess.run(["node", str(ROOT / "tests/js/run_imaging.mjs"), str(tmp_path)], check=True, timeout=120)
    js_results = json.loads((tmp_path / "js.json").read_text(encoding="utf-8"))

    measured = 0
    for (name, (bold, note), notices), js in zip(expected, js_results, strict=True):
        assert js["bold"] == bold, name
        assert js["notices"] == notices, name
        ratio = re.search(r"(\d+\.\d\d) times", note)
        if ratio:  # the server gives the ratio to two decimals in its note
            measured += 1
            assert abs(float(ratio.group(1)) - js["ratio"]) < 0.05, (name, ratio.group(1), js["ratio"])
        else:
            assert js["note"] == note, name
    assert measured >= 4  # bold and not-bold headings were both measured
