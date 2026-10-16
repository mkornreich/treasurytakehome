"""Government Health Warning Statement check (27 CFR 16.21 / 16.22).

The statement must be word-for-word, and "GOVERNMENT WARNING" must be in
capital letters (and bold). The heading and the body are checked separately:
OCR often reads a heading set on its own line out of order, and that is not a
wording problem.

OCR can misread a letter or two, so differences are graded: a missing,
added or different word is a real problem; a one-letter difference inside a
word, punctuation, or barcode digits that strayed into the reading are
flagged for a person to compare. Spacing is ignored entirely ("ABILITYTO",
"wom- en"), since it never changes the wording.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz

from app.models import Box
from app.verification.evidence import Evidence, Token, tokens_from_text
from app.verification.text import clean, loose

CANONICAL_WARNING = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women should not drink "
    "alcoholic beverages during pregnancy because of the risk of birth defects. "
    "(2) Consumption of alcoholic beverages impairs your ability to drive a car or "
    "operate machinery, and may cause health problems."
)
HEADER_WORDS = ["GOVERNMENT", "WARNING:"]
_CANON_WORDS = CANONICAL_WARNING.split()
_CANON_KEYS = [loose(w) for w in _CANON_WORDS]
_BODY_WORDS = _CANON_WORDS[2:]
_BODY_KEYS = _CANON_KEYS[2:]
VOCABULARY = set(_CANON_KEYS)

MINOR_WORD_SIMILARITY = 75  # "rnachinery" vs "machinery" -> probably a misread, not a rewrite
LOW_CONFIDENCE = 75  # OCR's own confidence below this: treat a difference as a possible misread
NUMERALS = {"1", "2"}  # "(1)" and "(2)": tiny, often misread, and never the substance of a rewording
_CONFUSABLE = str.maketrans({"0": "o", "1": "l", "i": "l", "t": "l", "|": "l", "!": "l", "5": "s", "$": "s"})


def _confusable(text: str) -> str:
    return text.translate(_CONFUSABLE).replace("rn", "m").replace("vv", "w")


@dataclass
class WarningReading:
    evidence: Evidence
    located: bool
    score: float = 0.0  # 0-100: how much of the statement was read
    tokens: list[Token] = field(default_factory=list)
    header_tokens: list[Token] = field(default_factory=list)
    header_found: str = ""  # the heading as read, e.g. "Government Warning:"; "" if not seen
    header_caps: bool = False
    header_colon: bool = False
    major: list[str] = field(default_factory=list)  # wording problems
    minor: list[str] = field(default_factory=list)  # probably reading errors / punctuation
    marks: set[str] = field(default_factory=set)  # which required words the problems touch
    diff: list[dict] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(t.text for t in self.tokens)

    @property
    def boxes(self) -> list[Box]:
        return [t.box for t in self.tokens if t.box]

    @property
    def header_boxes(self) -> list[Box]:
        return [t.box for t in self.header_tokens if t.box]

    @property
    def wording_ok(self) -> bool:
        return self.located and not self.major

    @property
    def exact(self) -> bool:
        return self.wording_ok and not self.minor


def _locate(tokens: list[Token], needle_keys: list[str]) -> tuple[int, int, float]:
    """Return the token range that best aligns with the given (normalized) words."""
    keys = [loose(t.text) for t in tokens]
    starts, pos, parts = [], 0, []
    for key in keys:
        starts.append(pos)
        parts.append(key)
        pos += len(key) + 1
    haystack = " ".join(parts)
    needle = " ".join(needle_keys)
    if not haystack.strip():
        return 0, 0, 0.0
    if len(haystack) <= len(needle):
        return 0, len(tokens), fuzz.ratio(haystack, needle)
    alignment = fuzz.partial_ratio_alignment(needle, haystack)
    if alignment is None:
        return 0, 0, 0.0
    begin = max(i for i, s in enumerate(starts) if s <= alignment.dest_start)
    end = max(i for i, s in enumerate(starts) if s < alignment.dest_end) + 1
    return begin, end, alignment.score


def _find_header(tokens: list[Token], near: int) -> int | None:
    """Index of the "GOVERNMENT" of a "GOVERNMENT WARNING" pair, closest to token `near`."""
    hits = [
        i for i in range(len(tokens) - 1)
        if fuzz.ratio(loose(tokens[i].text), "government") >= 80 and loose(tokens[i + 1].text).startswith("warn")
    ]
    return min(hits, key=lambda i: abs(i - near)) if hits else None


def _is_noise(words: list[str]) -> bool:
    """Barcode digits, specks and symbols: nothing that reads as a word."""
    return all(len(re.sub(r"[^a-z]", "", loose(w))) < 3 for w in words)


def _misread(expected_keys: list[str], seen_keys: list[str], confs: list[float]) -> bool:
    """Is this difference more likely the scanner's mistake than the label's?"""
    if expected_keys and set(expected_keys) <= NUMERALS:
        return True
    if _confusable("".join(expected_keys)) == _confusable("".join(seen_keys)):
        return True
    return bool(confs) and sum(confs) / len(confs) < LOW_CONFIDENCE


def _compare(found: list[str], confs: list[float] | None = None) -> tuple[list[str], list[str], set[str], list[dict]]:
    confs = confs or [100.0] * len(found)
    found_keys = [loose(w) for w in found]
    matcher = difflib.SequenceMatcher(None, _BODY_KEYS, found_keys, autojunk=False)
    major, minor, marks, diff = [], [], set(), []
    for op, a0, a1, b0, b1 in matcher.get_opcodes():
        expected, seen = _BODY_WORDS[a0:a1], found[b0:b1]
        joined_expected = "".join(_BODY_KEYS[a0:a1]).replace(" ", "")
        joined_seen = "".join(found_keys[b0:b1]).replace(" ", "")
        if op == "equal" or (op == "replace" and joined_expected == joined_seen):
            diff.append({"op": "equal", "text": " ".join(expected)})
            if op == "equal":
                for want, got in zip(expected, seen, strict=True):
                    if clean(want).strip(".,:;").lower() == clean(got).strip(".,:;").lower() and clean(want).lower() != clean(got).lower():
                        minor.append(f"punctuation “{want}” read as “{got}”")
        elif op == "delete":
            diff.append({"op": "missing", "text": " ".join(expected)})
            if set(_BODY_KEYS[a0:a1]) <= NUMERALS or (a1 - a0 == 1 and len(_BODY_KEYS[a0]) <= 3):
                # "(1)", or one short word such as "or": OCR drops these at line ends.
                minor.append(f"“{' '.join(expected)}” not read")
            else:
                major.append(f"missing: “{' '.join(expected)}”")
                marks.update(f"w{i}" for i in range(a0, a1))
        elif op == "insert":
            text = " ".join(seen)
            diff.append({"op": "extra", "text": text})
            if _is_noise(seen) or _misread([], found_keys[b0:b1], confs[b0:b1]):
                minor.append(f"extra marks “{text}”")
            else:
                major.append(f"extra text: “{text}”")
                marks.add(f"i{a0}")
        else:  # replace
            diff.append({"op": "changed", "expected": " ".join(expected), "found": " ".join(seen)})
            ratio = fuzz.ratio(joined_expected, joined_seen)
            message = f"“{' '.join(expected)}” read as “{' '.join(seen)}”"
            close = ratio >= MINOR_WORD_SIMILARITY and abs((a1 - a0) - (b1 - b0)) <= 1
            if close or _misread(_BODY_KEYS[a0:a1], found_keys[b0:b1], confs[b0:b1]):
                minor.append(message)
            else:
                major.append(message)
                marks.update(f"w{i}" for i in range(a0, a1))
    return major, minor, marks, diff


def read_warning(evidence: Evidence) -> WarningReading:
    tokens = evidence.tokens
    if evidence.warning_text:
        tokens = tokens_from_text(evidence.warning_text)
    if not tokens:
        return WarningReading(evidence, located=False)

    # Find the body first, then the heading nearest to it: a heading set on its own
    # line often comes out of OCR separated from the body by other text.
    begin, end, score = _locate(tokens, _BODY_KEYS)
    if score < 60:
        return WarningReading(evidence, located=False, score=score)
    # Stray text inside the statement shifts the alignment window; grow it back over
    # neighbouring tokens that are still the statement's first or last words.
    def is_word(i: int, keys: list[str]) -> bool:
        key = loose(tokens[i].text)
        return bool(key) and any(key == k or (len(k) > 3 and fuzz.ratio(key, k) >= 85) for k in keys)

    while begin > 0 and is_word(begin - 1, _BODY_KEYS[:8]) and not loose(tokens[begin - 1].text).startswith("warn"):
        begin -= 1
    while end < len(tokens) and is_word(end, _BODY_KEYS[-8:]):
        end += 1
    span = list(range(begin, end))
    # The aligned span can run a few tokens past the final word; trim at "problems".
    for k in range(len(span) - 1, -1, -1):
        if loose(tokens[span[k]].text).startswith("problem"):
            span = span[: k + 1]
            break
    head = _find_header(tokens, begin)
    header_idx = {head, head + 1} if head is not None else set()
    if head is not None and head in span:
        span = [i for i in span if i > head + 1]  # anything before the heading is not the statement
    body = [i for i in span if i not in header_idx]
    span = sorted(header_idx | set(body))

    words = [clean(tokens[i].text) for i in body]
    major, minor, marks, diff = _compare(words, [tokens[i].conf for i in body])
    header = [clean(tokens[i].text) for i in sorted(header_idx)]
    return WarningReading(
        evidence,
        located=True,
        score=score,
        tokens=[tokens[i] for i in span],
        header_tokens=[tokens[i] for i in sorted(header_idx)],
        header_found=" ".join(header),
        header_caps=bool(header) and all(w.rstrip(":").isupper() for w in header),
        header_colon=bool(header) and (
            header[-1].endswith(":") or (head + 2 < len(tokens) and tokens[head + 2].text.startswith(":"))
        ),
        major=major,
        minor=minor,
        marks=marks,
        diff=[{"op": "equal" if header else "missing", "text": "GOVERNMENT WARNING:"}] + diff,
    )
