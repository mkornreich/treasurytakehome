"""Inputs for comparing the browser rules (app/static/local/rules.js) with the Python rules.

The OCR readings come from the server pipeline itself: every pass it can make on
each sample label, without stopping early. Each case pairs some of those readings
with an application, the original from samples/batch_applications.csv or one with
a changed value, so that the "Problem" and "Examine" paths run as well as "OK".
Synthetic readings add what OCR of the samples never produces: AI-reader
fields, a doctored warning, readings that agree on a conflict.

    uv run python tests/parity_cases.py cases.json   # write the cases for tests/js/run_rules.mjs
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.imaging import decode, to_gray
from app.models import Application
from app.pipeline import ImageReader
from app.verification.evidence import Evidence, Token, tokens_from_text
from app.verification.warning import CANONICAL_WARNING

SAMPLES = ROOT / "samples"
VARIANTS = {
    "alcohol": {"alcohol_content": "12.5% Alc./Vol."},
    "proof": {"alcohol_content": "45% Alc./Vol. (80 Proof)"},
    "net": {"net_contents": "1.75 L"},
    "brand": {"brand_name": "SUNSET RIDGE"},
    "brand_near": {"brand_name": "OLD TOMS DISTILERY"},
    "class": {"class_type": "Vodka"},
    "bottler": {"bottler": "Acme Spirits, Austin, TX"},
    "country": {"country_of_origin": "Italy"},
    "blank_wine": {"beverage_type": "wine", "brand_name": "", "class_type": "", "alcohol_content": "", "bottler": ""},
    "blank_spirits": {"brand_name": "", "class_type": "", "alcohol_content": "", "net_contents": "", "bottler": ""},
}


def all_passes(files: list[bytes]) -> list[Evidence]:
    """Every OCR pass the pipeline can make on these pictures, warning re-reads included."""
    readers = []
    for i, data in enumerate(files):
        rgb = decode(data)
        readers.append(ImageReader(rgb, to_gray(rgb), i))
    first = True
    while any(r.has_more for r in readers):
        for reader in readers:
            if reader.has_more:
                reader.run_next()
        if first:
            for reader in readers:
                reader.queue_warning_reread()
            first = False
    return [e for r in readers for e in r.evidences]


def evidence_json(ev: Evidence) -> dict:
    return {
        "source": ev.source, "name": ev.name, "image_index": ev.image_index, "fields": ev.fields,
        "warning_text": ev.warning_text, "header_bold": ev.header_bold, "header_bold_note": ev.header_bold_note,
        "tokens": [{"text": t.text, "conf": t.conf, "box": t.box.model_dump() if t.box else None} for t in ev.tokens],
    }


def evidence_from_json(data: dict) -> Evidence:
    from app.models import Box

    tokens = [Token(t["text"], Box(**t["box"]) if t["box"] else None, t["conf"]) for t in data["tokens"]]
    return Evidence(data["source"], data["name"], tokens, data["image_index"], data["fields"], data["warning_text"],
                    data["header_bold"], data["header_bold_note"])


def sample_cases() -> list[dict]:
    cases = []
    with open(SAMPLES / "batch_applications.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for row in rows:
        files = [(SAMPLES / "labels" / name).read_bytes() for name in row["label_file"].split(";")]
        evidences = [evidence_json(e) for e in all_passes(files)]
        app = {k: row[k] for k in Application.model_fields}
        name = row["label_file"]
        for n in range(1, len(evidences) + 1):
            cases.append({"name": f"{name} first {n}", "application": app, "evidences": evidences[:n]})
        for n, ev in enumerate(evidences):
            cases.append({"name": f"{name} pass {n}", "application": app, "evidences": [ev]})
        for variant, changes in VARIANTS.items():
            changed = {**app, **changes}
            cases.append({"name": f"{name} {variant} all", "application": changed, "evidences": evidences})
            cases.append({"name": f"{name} {variant} first", "application": changed, "evidences": evidences[:1]})
    return cases


LABEL = (
    "OLD TOM DISTILLERY Kentucky Straight Bourbon Whiskey 45% Alc./Vol. (90 Proof) 750 mL "
    "Distilled and Bottled by Old Tom Distillery, Bardstown, Kentucky Product of USA " + CANONICAL_WARNING
)
APP = {
    "beverage_type": "spirits", "brand_name": "OLD TOM DISTILLERY", "class_type": "Kentucky Straight Bourbon Whiskey",
    "alcohol_content": "45% Alc./Vol. (90 Proof)", "net_contents": "750 mL", "bottler": "Old Tom Distillery, Bardstown, KY",
    "country_of_origin": "",
}


def _ocr(text, bold=True, name="ocr:standard", image=0):
    return evidence_json(Evidence("ocr", name, tokens_from_text(text), image, header_bold=bold))


def _vision(text, warning=None, bold=True, **fields):
    return evidence_json(Evidence("vision", "vision:claude", tokens_from_text(text), fields=fields,
                                  warning_text=warning, header_bold=bold))


def synthetic_cases() -> list[dict]:
    reworded = LABEL.replace("impairs", "may impair").replace("of birth defects", "")
    title = LABEL.replace("GOVERNMENT WARNING:", "Government Warning:")
    texts = {
        "clean": LABEL, "reworded": reworded, "title": title, "no_colon": LABEL.replace("WARNING:", "WARNING"),
        "no_warning": LABEL.split("GOVERNMENT")[0], "abv40": LABEL.replace("45%", "40%"), "abv48": LABEL.replace("45%", "48%"),
        "proof80": LABEL.replace("90 Proof", "80 Proof"), "net700": LABEL.replace("750 mL", "700 mL"),
        "bare_abv": LABEL.replace("45% Alc./Vol.", "45 ALC/VOL"), "fl_oz": LABEL.replace("750 mL", "25.4 FL. OZ."),
        "spaced": LABEL.replace("750 mL", "7 5 0 m L"), "misread": LABEL.replace("machinery", "rnachinery").replace("(1)", "(l)"),
        "italy": LABEL.replace("Product of USA", "Produced and bottled in Italy"),
        "accent": LABEL.replace("OLD TOM", "ÓLD TÖM"), "case": LABEL.replace("OLD TOM DISTILLERY", "Old Tom Distillery"),
    }
    cases = []
    for name, text in texts.items():
        apps = {"app": APP, "usa": {**APP, "country_of_origin": "USA"}, "france": {**APP, "country_of_origin": "France"}}
        for app_name, app in apps.items():
            cases.append({"name": f"synthetic {name} {app_name} once", "application": app, "evidences": [_ocr(text)]})
            cases.append({"name": f"synthetic {name} {app_name} twice", "application": app,
                          "evidences": [_ocr(text), _ocr(text, name="ocr:enhanced")]})
        cases.append({"name": f"synthetic {name} not bold twice", "application": APP,
                      "evidences": [_ocr(text, bold=False), _ocr(text, bold=False, name="ocr:layout")]})
        cases.append({"name": f"synthetic {name} with clean", "application": APP, "evidences": [_ocr(text), _ocr(LABEL, name="ocr:enhanced")]})
        cases.append({"name": f"synthetic {name} vision", "application": APP, "evidences": [
            _ocr(LABEL), _vision(text, warning=None, bold=False, brand_name="OLD TOM", alcohol_content="45% Alc./Vol."),
        ]})
        warning = text[text.find("GOVERNMENT") if "GOVERNMENT" in text else text.find("Government"):]
        cases.append({"name": f"synthetic {name} vision fields", "application": {**APP, "country_of_origin": "France"}, "evidences": [
            _vision(text, warning=warning or None, bold=True, brand_name="OLD TOM DISTILLERY RESERVE",
                    class_type="Kentucky Bourbon", bottler="Bottled by Old Tom Distillery, Bardstown, Kentucky",
                    country_of_origin="Product of France", net_contents="750 mL", alcohol_content="45% ALC/VOL"),
        ]})
    cases.append({"name": "synthetic not bold, both readers", "application": APP,
                  "evidences": [_ocr(LABEL, bold=False), _vision(LABEL, warning=CANONICAL_WARNING, bold=False)]})
    cases.append({"name": "synthetic other brand, AI reader", "application": APP,
                  "evidences": [_ocr(LABEL), _vision(LABEL, brand_name="SUNSET RIDGE", class_type="Vodka")]})
    belle = {**APP, "alcohol_content": "53.5% Alc./Vol. (107 Proof)"}
    bare = LABEL.replace("45% Alc./Vol. (90 Proof)", "53.5 ALC/VOL 107 PROOF")
    for other in ("107 PROOF", "53.5% ALC/VOL 107 PROOF", "53.5 ALC/VOL"):
        cases.append({"name": f"synthetic no % sign, then {other}", "application": belle,
                      "evidences": [_ocr(bare), _ocr(LABEL.replace("45% Alc./Vol. (90 Proof)", other), name="ocr:enhanced")]})
    cases.append({"name": "synthetic empty", "application": APP, "evidences": []})
    cases.append({"name": "synthetic two pictures", "application": APP,
                  "evidences": [_ocr(LABEL.split("GOVERNMENT")[0]), _ocr(CANONICAL_WARNING, image=1)]})
    return cases


def all_cases() -> list[dict]:
    return sample_cases() + synthetic_cases()


if __name__ == "__main__":
    Path(sys.argv[1]).write_text(json.dumps(all_cases()), encoding="utf-8")
