"""Compare the application with what the readers saw, field by field.

Every check produces one of three outcomes:

* PASS   - the label shows what the application says.
* FAIL   - we read something that conflicts (a different ABV, a reworded warning).
* REVIEW - we could not confirm either way (unreadable, near-miss, readers disagree).

"Not found" is deliberately REVIEW, not FAIL: OCR missing a stylized brand
name is not evidence that the label is wrong, and false rejections are what
made agents abandon the last tool.

With several readers (OCR passes, multiple images, the vision model), a field
passes if any reader confirms it, fails if a conflict was seen and none
confirmed it, and otherwise needs review. A conflict seen by only one OCR pass
(say "150 ML" where the label says 750) is treated as a possible misread and
sent to review; it takes two separate readings, or the AI reader, to fail.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field, replace
from statistics import median

from app.models import Application, BeverageType, Box, CheckResult, Status
from app.verification.evidence import Evidence, merge_boxes, tokens_from_text
from app.verification.text import (
    US_STATES,
    clean,
    expand_states,
    find_phrase,
    loose,
    similarity,
)
from app.verification.units import (
    Mention,
    find_abv_mentions,
    find_proof_mentions,
    find_volume_mentions,
    format_ml,
    parse_alcohol,
    parse_volume,
    same_volume,
)
from app.verification.warning import WarningReading, read_warning

READER_NAMES = {"ocr": "text scanner", "vision": "AI reader"}
_BOLD_PREFERENCE = {"vision:claude": 4, "ocr:warning": 3, "ocr:standard": 2, "ocr:layout": 2, "ocr:rotate": 2}
# Digit pairs Tesseract confuses in display fonts: a "13%" read as "18%" is not evidence of a wrong label.
_CONFUSABLE_DIGITS = [{"3", "8"}, {"5", "6"}, {"6", "8"}, {"0", "8"}, {"1", "7"}, {"5", "9"}]


def _confusable_numbers(a: float, b: float) -> bool:
    x, y = f"{a:g}", f"{b:g}"
    if len(x) != len(y):
        return False
    pairs = [{c, d} for c, d in zip(x, y, strict=True) if c != d]
    return bool(pairs) and all(pair in _CONFUSABLE_DIGITS for pair in pairs)


def _maybe_misread(finding: Finding, wanted: float, seen: list[float], unit: str) -> Finding:
    """Downgrade a numeric conflict to review when it could be a digit misread."""
    if finding.status == Status.FAIL and any(_confusable_numbers(v, wanted) for v in seen):
        return replace(finding, status=Status.REVIEW, conflict=True, message=(
            f"{finding.message} The scanner often reads these digits incorrectly. "
            f"Examine the {unit} on the label."
        ))
    return finding
NEAR_MATCH = 90  # similarity (0-100) for "nearly identical - probably a typo or misread"
WEAK_MATCH = 70


@dataclass
class Finding:
    status: Status
    message: str
    found: str | None = None
    evidence: Evidence | None = None
    boxes: list[Box] = field(default_factory=list)
    score: float = 0.0
    exact: bool = False  # identical text, capitalization included
    prominence: float = 0.0  # letter height; prefer the big brand text over a mention in small print
    key: str = ""  # what conflicted ("abv:40,80"); a FAIL needs two readings sharing a value
    conflict: bool = False  # a reader saw something that contradicts the application or the rules

    def __post_init__(self):
        self.conflict = self.conflict or self.status == Status.FAIL


def _best(group: list[Finding]) -> Finding:
    return max(group, key=lambda f: (f.score, f.exact, f.prominence, len(f.boxes)))


def _corroborated(finding: Finding, fails: list[Finding]) -> bool:
    if finding.evidence is None or finding.evidence.source != "ocr":
        return True
    values = set(finding.key.split(":")[-1].split(","))
    kind = finding.key.split(":")[0]
    return any(
        g.evidence is not finding.evidence and g.key.split(":")[0] == kind and values & set(g.key.split(":")[-1].split(","))
        for g in fails if g is not finding
    )


def _pick(findings: list[Finding]) -> Finding:
    """PASS if any reader confirmed; else FAIL if a conflict was seen twice; else best REVIEW."""
    passes = [f for f in findings if f.status == Status.PASS]
    if passes:
        return _best(passes)
    fails = [f for f in findings if f.status == Status.FAIL]
    if fails:
        confirmed = [f for f in fails if _corroborated(f, fails)]
        if confirmed:
            return _best(confirmed)
        single = _best(fails)
        return replace(single, status=Status.REVIEW, conflict=True, message=(
            f"{single.message} Only one reading of the label shows this. The scanner can make reading errors. "
            "Examine the label."
        ))
    reviews = [f for f in findings if f.status == Status.REVIEW]
    if reviews:
        return _best(reviews)
    raise ValueError("no findings")


def _result(field_name: str, label: str, expected: str | None, finding: Finding) -> CheckResult:
    return CheckResult(
        field=field_name,
        label=label,
        status=finding.status,
        message=finding.message,
        expected=expected,
        found=finding.found,
        source=finding.evidence.source if finding.evidence else None,
        boxes=merge_boxes(finding.boxes),
    )


def _boxes(evidence: Evidence, start: int, end: int) -> list[Box]:
    return [t.box for t in evidence.tokens[start:end] if t.box]


def _span_boxes(evidence: Evidence, mention: Mention, searched_text: str | None = None) -> list[Box]:
    """Boxes of the tokens covering a character span of `evidence.text`.

    Mentions found in a structured field (searched_text != evidence.text) have no location.
    """
    if searched_text is not None and searched_text != evidence.text:
        return []
    boxes, pos = [], 0
    for token in evidence.tokens:
        end = pos + len(token.text)
        if end > mention.start and pos < mention.end and token.box:
            boxes.append(token.box)
        pos = end + 1
    return boxes


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


# --- free-text fields: brand name, class/type, bottler, country ---------------------------------


def _describe_match(expected: str, found: str) -> str:
    if clean(found) == clean(expected):
        return "The label and the application are the same."
    if clean(found).casefold() == clean(expected).casefold():
        return f"The text is the same. Only the capital letters are different. The label shows “{found}”."
    if loose(found) != loose(expected):
        return f"The text is the same. The label uses the full word for an abbreviation: “{found}”."
    return f"The text is the same. Only the punctuation or the spaces are different. The label shows “{found}”."


def _text_finding(expected: str, found: str, score: float, evidence: Evidence, boxes, structured: bool,
                  prominence: float = 0.0) -> Finding:
    if score == 100:
        exact = clean(found) == clean(expected)
        return Finding(Status.PASS, _describe_match(expected, found), found, evidence, boxes, score, exact, prominence)
    accented = _strip_accents(found) != found or _strip_accents(expected) != expected
    if accented and loose(_strip_accents(found)) == loose(_strip_accents(expected)):
        return Finding(
            Status.REVIEW,
            f"The letters are the same, but the accent marks are different. The label shows “{found}”. "
            "Make sure that this difference is permitted.",
            found, evidence, boxes, score,
        )
    if score >= NEAR_MATCH:
        return Finding(
            Status.REVIEW,
            f"The text is almost the same. The label shows “{found}”. Examine the label for a typing error or a reading error.",
            found, evidence, boxes, score,
        )
    if structured and found:
        # A reader that returns fields told us what this field says, and it is different.
        return Finding(Status.FAIL, f"The label shows “{found}”. This is not the same as the application.", found, evidence, boxes, score)
    if score >= WEAK_MATCH:
        return Finding(
            Status.REVIEW,
            f"The tool did not find this text on the label. The nearest text is “{found}”.",
            found, evidence, boxes, score,
        )
    return Finding(
        Status.REVIEW,
        "The tool did not find this text on the label. The font can be decorative, or the text can be hard to read. "
        "Examine the label.",
        None, evidence, [], score,
    )


_CORPORATE = {"llc", "l l c", "inc", "co", "corp", "corporation", "ltd", "company", "lp", "llp", "spa", "s p a", "sa", "s a"}


def address_parts(text: str) -> list[str]:
    """Split a name-and-address into pieces a label may print apart from each other.

    "Appalachian Spirits, LLC, Mechanicsburg, PA" -> ["Appalachian Spirits, LLC", "Mechanicsburg, PA"];
    the label can put a street address between them.
    """
    parts: list[str] = []
    for part in (p.strip() for p in text.split(",")):
        if not part:
            continue
        key = loose(part)
        state = key in US_STATES or key in US_STATES.values() or re.fullmatch(r"[a-z]{2} \d{5}(?: \d{4})?", key)
        if parts and (key in _CORPORATE or state):
            parts[-1] = f"{parts[-1]}, {part}"
        else:
            parts.append(part)
    return parts


def _address_finding(expected: str, ev: Evidence, tokens, structured: bool) -> Finding | None:
    parts = address_parts(expected)
    if len(parts) < 2:
        return None
    matches = [find_phrase(part, tokens, expand_states) for part in parts]
    if not all(matches):
        return None
    score = min(m.score for m in matches)
    found = " … ".join(m.text for m in matches)
    boxes = [] if structured else [b for m in matches for b in _boxes(ev, m.start, m.end)]
    if score == 100:
        return Finding(Status.PASS, f"The label shows the same name and address: “{found}”.", found, ev, boxes, score)
    return _text_finding(expected, found, score, ev, boxes, structured=structured)


def check_text(field_name: str, label: str, expected: str, evidences: list[Evidence],
               address: bool = False, contained: bool = False) -> CheckResult:
    """`contained`: the application text may sit inside a longer statement on the label
    ("Bottled by ...", "Product of France"), so look for it within a reader's field value."""
    transform = expand_states if address else None
    prepare = transform or (lambda text: text)
    findings = []
    for ev in evidences:
        value = ev.fields.get(field_name, "")
        if value:
            found, score = value, similarity(prepare(expected), prepare(value))
            inner = find_phrase(expected, tokens_from_text(value), transform) if contained and score < 100 else None
            if inner and inner.score > score:
                found, score = inner.text, inner.score
            findings.append(_text_finding(expected, found, score, ev, [], structured=True))
            if address and (parts := _address_finding(expected, ev, tokens_from_text(value), structured=True)):
                findings.append(parts)
        match = find_phrase(expected, ev.tokens, transform)
        if match:
            boxes = _boxes(ev, match.start, match.end)
            findings.append(_text_finding(expected, match.text, match.score, ev, boxes, structured=False, prominence=match.height))
        if address and match and match.score < 100 and (parts := _address_finding(expected, ev, ev.tokens, structured=False)):
            findings.append(parts)
    if not findings:
        findings.append(Finding(Status.REVIEW, "The tool cannot read text on the label."))
    return _result(field_name, label, expected, _pick(findings))


# --- alcohol content -------------------------------------------------------------------------


def _alcohol_text(ev: Evidence) -> str:
    return ev.fields.get("alcohol_content") or ev.text


def check_alcohol(app: Application, evidences: list[Evidence]) -> CheckResult | None:
    label = "Alcohol content"
    if not app.alcohol_content:
        return _alcohol_presence(app, evidences)

    wanted = parse_alcohol(app.alcohol_content)
    if wanted.abv is None:
        return CheckResult(
            field="alcohol_content", label=label, status=Status.REVIEW, expected=app.alcohol_content,
            message="The tool cannot read the alcohol content on the application. Type it as in this example: “45% Alc./Vol.”.",
        )

    findings = []
    no_percent = with_percent = False
    proof_only: set[int] = set()  # ids of findings from readings that saw a proof but no ABV statement
    for ev in evidences:
        text = _alcohol_text(ev)
        abvs = find_abv_mentions(text)
        proofs = find_proof_mentions(text)

        def boxes(m: Mention, ev=ev, text=text) -> list[Box]:
            return _span_boxes(ev, m, text)

        matching = sorted((m for m in abvs if abs(m.value - wanted.abv) < 0.05), key=lambda m: not m.percent)
        if matching:
            m = matching[0]
            no_percent |= not m.percent
            with_percent |= m.percent
            message, status, key = f"The label shows the same value: {m.value:g}% alcohol by volume.", Status.PASS, ""
            if not m.percent:
                message, status = (f"The label shows {m.value:g} next to “ALC/VOL”, but it has no % sign. "
                                   "The alcohol content must be a percentage."), Status.REVIEW
            elif wanted.proof is not None and proofs:
                if not any(abs(p.value - wanted.proof) < 0.05 for p in proofs):
                    shown = ", ".join(f"{p.value:g}" for p in proofs)
                    message, status = (f"The ABV is the same, but the label shows {shown} proof. "
                                       f"The application shows {wanted.proof:g} proof."), Status.FAIL
                    key = "proof:" + ",".join(f"{p.value:g}" for p in proofs)
            elif proofs and not any(abs(p.value - 2 * m.value) < 0.05 for p in proofs):
                message, status = f"The ABV is the same, but the proof on the label ({proofs[0].value:g}) is not two times the ABV.", Status.REVIEW
            findings.append(Finding(status, message, m.text, ev, boxes(m) + sum((boxes(p) for p in proofs), []), 100,
                                    key=key))
        elif abvs:
            shown = ", ".join(f"{m.value:g}%" for m in abvs)
            findings.append(_maybe_misread(Finding(
                Status.FAIL,
                f"The label shows {shown}. The application shows {wanted.abv:g}%.",
                abvs[0].text, ev, sum((boxes(m) for m in abvs), []), 50,
                key="abv:" + ",".join(sorted(f"{m.value:g}" for m in abvs)),
            ), wanted.abv, [m.value for m in abvs], "alcohol content"))
        elif proofs:
            p = proofs[0]
            if wanted.proof is not None and abs(p.value - wanted.proof) < 0.05 or abs(p.value / 2 - wanted.abv) < 0.05:
                findings.append(Finding(Status.PASS, f"The label shows the same value: {p.value:g} proof ({p.value / 2:g}% ABV).", p.text, ev, boxes(p), 90))
                proof_only.add(id(findings[-1]))
            else:
                findings.append(Finding(Status.FAIL, f"The label shows {p.value:g} proof. The application shows {wanted.describe()}.",
                                        p.text, ev, boxes(p), 50, key=f"proof:{p.value:g}"))
    if no_percent and not with_percent:
        # A reading that saw only the proof cannot show the % sign that another reading found missing.
        findings = [f for f in findings if id(f) not in proof_only]
    if not findings:
        findings.append(Finding(Status.REVIEW, "The tool did not find an alcohol content statement on the label."))
    return _result("alcohol_content", label, app.alcohol_content, _pick(findings))


def _alcohol_presence(app: Application, evidences: list[Evidence]) -> CheckResult | None:
    for ev in evidences:
        mentions = find_abv_mentions(_alcohol_text(ev))
        if mentions:
            m = mentions[0]
            boxes = _span_boxes(ev, m, _alcohol_text(ev))
            return _result("alcohol_content", "Alcohol content", None, Finding(
                Status.PASS, f"The label shows {m.value:g}% alcohol by volume. The application does not give a value, so the tool did not compare it.",
                m.text, ev, boxes,
            ))
    if app.beverage_type == BeverageType.SPIRITS:
        return _result("alcohol_content", "Alcohol content", None, Finding(
            Status.REVIEW, "The tool did not find the alcohol content on the label. Distilled spirits must show it.",
        ))
    return None  # optional for most beer and wine


# --- net contents ----------------------------------------------------------------------------


def check_net_contents(app: Application, evidences: list[Evidence]) -> CheckResult:
    label = "Net contents"
    wanted = parse_volume(app.net_contents) if app.net_contents else None
    if app.net_contents and wanted is None:
        return CheckResult(
            field="net_contents", label=label, status=Status.REVIEW, expected=app.net_contents,
            message="The tool cannot read the net contents on the application. Type it as in this example: “750 mL”.",
        )

    findings = []
    for ev in evidences:
        text = ev.fields.get("net_contents") or ev.text
        mentions = find_volume_mentions(text)
        if not mentions:
            continue

        def boxes(m: Mention, ev=ev, text=text) -> list[Box]:
            return _span_boxes(ev, m, text)
        if wanted is None:
            m = mentions[0]
            findings.append(Finding(Status.PASS, f"The label shows {m.text}. The application does not give a value, so the tool did not compare it.",
                                    m.text, ev, boxes(m), 100))
            continue
        matching = [m for m in mentions if same_volume(m.value, wanted)]
        if matching:
            m = matching[0]
            same_units = abs(m.value - wanted) < 0.01
            note = "" if same_units else f" {m.text} is the same as {format_ml(wanted)}."
            findings.append(Finding(Status.PASS, f"The label shows the same value: {m.text}.{note}", m.text, ev, boxes(m), 100))
        else:
            shown = ", ".join(m.text for m in mentions)
            findings.append(_maybe_misread(Finding(
                Status.FAIL, f"The label shows {shown}. The application shows {app.net_contents}.",
                mentions[0].text, ev, sum((boxes(m) for m in mentions), []), 50,
                key="ml:" + ",".join(sorted(f"{m.value:.0f}" for m in mentions)),
            ), wanted, [round(m.value, 2) for m in mentions], "net contents"))
    if not findings:
        findings.append(Finding(Status.REVIEW, "The tool did not find the net contents on the label, for example “750 mL”."))
    return _result("net_contents", label, app.net_contents or None, _pick(findings))


# --- government warning ----------------------------------------------------------------------


def _legible(ev: Evidence) -> bool:
    confidences = [t.conf for t in ev.tokens]
    return ev.source == "vision" or (ev.word_count >= 20 and bool(confidences) and median(confidences) >= 75)


def _absent_warning(readings: list[WarningReading]) -> Finding:
    """No reading located the statement: missing, or just unreadable?"""
    evidences = [r.evidence for r in readings]
    partial = any(loose(t.text) in {"government", "surgeon", "pregnancy", "machinery"} for ev in evidences for t in ev.tokens)
    per_image: dict[int, int] = {}
    for ev in evidences:
        per_image[ev.image_index] = per_image.get(ev.image_index, 0) + _legible(ev)
    vision = any(ev.source == "vision" for ev in evidences)
    # Missing only if every picture was read clearly, twice (or by the AI reader).
    if not partial and (vision or (per_image and min(per_image.values()) >= 2)):
        return Finding(Status.FAIL, "The tool did not find the government warning on the label.", evidence=evidences[0])
    return Finding(Status.REVIEW, "The tool cannot read the government warning. The warning is not on the label, "
                   "or it is too small or not clear.",
                   evidence=evidences[0])


def _sentences(items: list[str]) -> str:
    """Differences as short sentences: "Missing: “(1)”. “impairs” read as “may impair”."."""
    return " ".join(f"{item[0].upper()}{item[1:]}" + ("" if item.endswith((".", ".”")) else ".") for item in items)


def _source_wording(readings: list[WarningReading]) -> tuple[Finding, WarningReading]:
    """One reader family's verdict on the wording, from all of its readings (passes, pictures)."""
    located = [r for r in readings if r.located]
    if not located:
        return _absent_warning(readings), max(readings, key=lambda r: r.score)
    exact = [r for r in located if r.exact]
    if exact:
        r = max(exact, key=lambda r: r.score)
        return Finding(Status.PASS, "The warning is word-for-word the same as the required statement.", r.text, r.evidence, r.boxes, r.score), r
    best = _best_reading(located)
    clean_reads = [r for r in located if not r.major and r.score >= 85]
    if best.major and clean_reads:
        # Some reading shows the wording intact apart from possible misreads, so the
        # differences elsewhere are more likely the scanner's (text from a neighbouring
        # column spliced in, say) than the label's.
        best = max(clean_reads, key=lambda r: r.score)
    if best.major:
        message = f"The text is different from the required statement. {_sentences(best.major[:3])}"
        confirmed = best.evidence.source != "ocr" or any(
            other.evidence is not best.evidence and other.marks & best.marks for other in located
        )
        if confirmed:
            return Finding(Status.FAIL, message, best.text, best.evidence, best.boxes, best.score), best
        return Finding(Status.REVIEW, f"{message} Only one reading shows this. The scanner can make reading errors. "
                       "Compare the text on the label.",
                       best.text, best.evidence, best.boxes, best.score, conflict=True), best
    return Finding(Status.REVIEW, f"The text is almost the same as the required statement. Compare these items. "
                   f"{_sentences(best.minor[:3])} The difference can be a reading error.",
                   best.text, best.evidence, best.boxes, best.score), best


def _source_capitals(readings: list[WarningReading]) -> Finding:
    located = [r for r in readings if r.located]
    with_header = [r for r in located if r.header_found]
    capitals = [r for r in with_header if r.header_caps]
    if capitals:
        r = max(capitals, key=lambda r: (r.header_colon, r.score))
        if r.header_colon:
            return Finding(Status.PASS, "“GOVERNMENT WARNING:” is in capital letters.", r.header_found, r.evidence, r.header_boxes, r.score)
        return Finding(Status.REVIEW, "The heading is in capital letters, but the tool did not find the colon after “GOVERNMENT WARNING”.",
                       r.header_found, r.evidence, r.header_boxes, r.score)
    if with_header:
        r = max(with_header, key=lambda r: r.score)
        message = f"The label shows “{r.header_found}”. The heading must be in capital letters: “GOVERNMENT WARNING:”."
        seen_by = {id(x.evidence) for x in with_header}
        if r.evidence.source != "ocr" or len(seen_by) >= 2:
            return Finding(Status.FAIL, message, r.header_found, r.evidence, r.header_boxes, r.score)
        return Finding(Status.REVIEW, f"{message} Only one reading shows this. Examine the label.",
                       r.header_found, r.evidence, r.header_boxes, r.score, conflict=True)
    r = max(located, key=lambda r: r.score)
    if r.evidence.source != "ocr":
        return Finding(Status.FAIL, "The statement must start with “GOVERNMENT WARNING:”.", None, r.evidence, [], r.score)
    return Finding(Status.REVIEW, "The tool did not find the heading “GOVERNMENT WARNING:”. Make sure that the label has this heading.",
                   None, r.evidence, r.boxes[:2], r.score)


def _precedence(findings: list[Finding]) -> Finding:
    for status in (Status.PASS, Status.FAIL, Status.REVIEW):
        group = [f for f in findings if f.status == status]
        if group:
            return _best(group)
    raise ValueError("no findings")


def _combine_readers(per_reader: dict[str, Finding]) -> Finding:
    """Two different engines that disagree (one pass, one fail) -> a person decides,
    unless the failing engine clearly could not read the statement."""
    findings = list(per_reader.values())
    passing = next((f for f in findings if f.status == Status.PASS), None)
    failing = next((f for f in findings if f.status != Status.PASS and f.conflict), None)
    if passing and failing:
        if failing.score >= 90:
            return Finding(
                Status.REVIEW,
                f"The {READER_NAMES.get(failing.evidence.source)} and the {READER_NAMES.get(passing.evidence.source)} "
                f"do not agree. The {READER_NAMES.get(failing.evidence.source)} found this: {failing.message} "
                "Compare the text on the label.",
                failing.found, failing.evidence, failing.boxes or passing.boxes, failing.score,
            )
        return passing
    return _precedence(findings)


def _best_reading(readings: list[WarningReading]) -> WarningReading:
    return max(readings, key=lambda r: (r.located, r.score, -len(r.major), -len(r.minor)))


def check_warning(evidences: list[Evidence]) -> list[CheckResult]:
    if not evidences:
        return [CheckResult(field="warning_text", label="Government warning", status=Status.REVIEW,
                            message="The tool cannot read text on the label.")]
    by_source: dict[str, list[WarningReading]] = {}
    for ev in evidences:
        by_source.setdefault(ev.source, []).append(read_warning(ev))

    verdicts = {source: _source_wording(readings) for source, readings in by_source.items()}
    wording = _combine_readers({s: v[0] for s, v in verdicts.items()})
    results = [_result("warning_text", "Government warning: text", None, wording)]
    results[0].diff = next((r.diff for _, r in verdicts.values() if r.evidence is wording.evidence and r.located), None)
    located = {s: [r for r in readings if r.located] for s, readings in by_source.items()}
    located = {s: rs for s, rs in located.items() if rs}
    if not located:
        results[0].label = "Government warning"
        return results

    capitals = _combine_readers({s: _source_capitals(rs) for s, rs in located.items()})
    results.append(_result("warning_caps", "Government warning: capital letters", "GOVERNMENT WARNING:", capitals))
    results.append(_bold(located))
    return results


def _bold(located: dict[str, list[WarningReading]]) -> CheckResult:
    label = "Government warning: bold type"
    votes: dict[str, tuple[bool, str]] = {}
    boxes: list[Box] = []
    for source, readings in located.items():
        # Stroke widths are measured best on unfiltered images: the enlarged warning re-read,
        # then the plain passes. Binarizing or sharpening changes how thick strokes look.
        measured = [r for r in readings if r.evidence.header_bold is not None]
        if measured:
            r = max(measured, key=lambda r: (_BOLD_PREFERENCE.get(r.evidence.name.split("-")[0], 0), r.score))
            votes[source] = (r.evidence.header_bold, r.evidence.header_bold_note)
        boxes = boxes or merge_boxes(_best_reading(readings).header_boxes)
    if not votes:
        return CheckResult(field="warning_bold", label=label, status=Status.REVIEW, boxes=boxes,
                           message="The tool did not measure the type weight of “GOVERNMENT WARNING:”. "
                                   "Examine the label to make sure that the heading is in bold type.")
    says_bold = [v for v, _ in votes.values()]
    notes = " ".join(note for _, note in votes.values() if note)
    if all(says_bold):
        return CheckResult(field="warning_bold", label=label, status=Status.PASS, boxes=boxes,
                           message=f"“GOVERNMENT WARNING:” is in bold type. {notes}".strip())
    status = Status.FAIL if len(votes) > 1 and not any(says_bold) else Status.REVIEW
    return CheckResult(
        field="warning_bold", label=label, status=status, boxes=boxes,
        message=f"The tool measured “GOVERNMENT WARNING:” as not bold. Bold type is required. {notes} Examine the label.".strip(),
    )


# --- country of origin ------------------------------------------------------------------------

_ORIGIN_RE = re.compile(
    r"\b(product of|produce of|produced (?:and bottled )?in|bottled in|made in|hecho en|wine of|"
    r"imported from|produit de|prodotto in|producto de)\s+((?:the\s+)?[a-z][a-z]+(?:\s[a-z]+)?)",
    re.IGNORECASE,
)


def check_country(expected: str, evidences: list[Evidence]) -> CheckResult:
    """Look for the country itself, and for origin statements ("Product of Italy") naming another."""
    result = check_text("country_of_origin", "Country of origin", expected, evidences, contained=True)
    if result.status == Status.PASS:
        return result
    findings = []
    for ev in evidences:
        for m in _ORIGIN_RE.finditer(ev.text):
            stated = m.group(2)
            statement = f"{m.group(1)} {stated}"
            match = find_phrase(expected, tokens_from_text(stated))
            if match and match.score >= 90:
                findings.append(Finding(Status.PASS, f"The label shows the same country: “{statement}”.", statement, ev, [], 100))
            else:
                findings.append(Finding(Status.FAIL, f"The label shows “{statement}”. The application shows {expected}.",
                                        statement, ev, [], 80, key=f"origin:{loose(stated)}"))
    if not findings:
        return result
    finding = _pick(findings)
    return _result("country_of_origin", "Country of origin", expected, finding)


# --- all checks ------------------------------------------------------------------------------


def verify(app: Application, evidences: list[Evidence]) -> list[CheckResult]:
    checks: list[CheckResult] = []
    if app.brand_name:
        checks.append(check_text("brand_name", "Brand name", app.brand_name, evidences))
    if app.class_type:
        checks.append(check_text("class_type", "Class or type", app.class_type, evidences))
    if (alcohol := check_alcohol(app, evidences)) is not None:
        checks.append(alcohol)
    checks.append(check_net_contents(app, evidences))
    if app.bottler:
        checks.append(check_text("bottler", "Name and address of the bottler", app.bottler, evidences,
                                 address=True, contained=True))
    if app.country_of_origin:
        checks.append(check_country(app.country_of_origin, evidences))
    checks.extend(check_warning(evidences))
    return checks


def overall(checks: list[CheckResult]) -> tuple[Status, str]:
    failed = sum(c.status == Status.FAIL for c in checks)
    review = sum(c.status == Status.REVIEW for c in checks)
    plural = lambda n, word: f"{n} {word}{'' if n == 1 else 's'}"
    if failed:
        tail = f", {plural(review, 'item')} to examine" if review else ""
        return Status.FAIL, f"{plural(failed, 'problem')} found{tail}"
    if review:
        return Status.REVIEW, f"{plural(review, 'item')} to examine"
    return Status.PASS, f"All {len(checks)} checks are OK"
