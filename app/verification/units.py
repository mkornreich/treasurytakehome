"""Parsing of alcohol content and net contents, so values compare by meaning, not spelling.

"45% Alc./Vol. (90 Proof)" == "45.0% ABV"; "750 mL" == "75 cl" == "0.75 L".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_NUMBER = r"(\d{1,4}(?:[.,]\d{1,3})?)"
_PERCENT_RE = re.compile(_NUMBER + r"\s*%")
_PROOF_RE = re.compile(_NUMBER + r"\s*°?\s*proof\b", re.IGNORECASE)
_ALCOHOL_CONTEXT_RE = re.compile(r"alc|vol|abv|alcohol|proof", re.IGNORECASE)
# "53.5 ALC/VOL": an alcohol statement missing its % sign (TTB asks for it to be added).
_BARE_ABV_RE = re.compile(_NUMBER + r"\s*(?=alc\b|alc[./]|abv\b)", re.IGNORECASE)
_SPACED_RE = re.compile(r"(?<!\S)(?:\S ){2,}\S(?!\S)")

# Longest spellings first so "fl oz" wins over "oz" and "liters" over "l".
_UNITS = [
    (r"millilit(?:er|re)s?", 1.0),
    (r"m\s?l", 1.0),
    (r"centilit(?:er|re)s?", 10.0),
    (r"c\s?l", 10.0),
    (r"lit(?:er|re)s?", 1000.0),
    (r"l", 1000.0),
    (r"fl\.?\s*oz\.?", 29.5735),
    (r"fluid\s+ounces?", 29.5735),
    (r"oz\.?", 29.5735),
    (r"pints?", 473.176),
    (r"pt\.?", 473.176),
    (r"quarts?", 946.353),
    (r"qt\.?", 946.353),
    (r"gallons?", 3785.41),
    (r"gal\.?", 3785.41),
]
_VOLUME_RE = re.compile(
    # Up to two specks may sit between number and unit ("15.5 “¥ o GALLONS").
    _NUMBER + r"(?:\s+[^\d\s]{1,2}(?=\s)){0,2}\s*(" + "|".join(u for u, _ in _UNITS) + r")(?![a-z])", re.IGNORECASE
)
# "43% ALC/VOL IL": a 1-litre size right after the alcohol statement, its "1" read as "I".
_ONE_LITRE_RE = re.compile(r"((?:vol|proof|%)\.?[^\w\s]{0,2}\s+)[Il](\s?l(?:iters?|itres?)?\b)", re.IGNORECASE)
_UNIT_PATTERNS = [(re.compile(rf"^{u}$", re.IGNORECASE), ml) for u, ml in _UNITS]


def to_float(raw: str) -> float:
    """'0,75' -> 0.75 (decimal comma); '1,000' -> 1000 (thousands separator)."""
    if "," in raw:
        whole, frac = raw.split(",", 1)
        raw = whole + frac if len(frac) == 3 else f"{whole}.{frac}"
    return float(raw)


def fix_ocr_digits(text: str) -> str:
    """Repair common OCR confusions inside numbers: 75O mL -> 750 mL, 4S% stays as-is."""

    def repair(match: re.Match) -> str:
        token = match.group(0)
        if not any(c.isdigit() for c in token):
            return token
        return token.translate(str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1"}))

    return re.sub(r"(?<![A-Za-z])[\dOolI][\dOolI.,]*(?=\s*(?:%|[a-zA-Z]{1,6}\b|$|\s))", repair, text)


@dataclass
class Alcohol:
    abv: float | None = None  # percent alcohol by volume
    proof: float | None = None

    def describe(self) -> str:
        parts = []
        if self.abv is not None:
            parts.append(f"{self.abv:g}% alcohol by volume")
        if self.proof is not None:
            parts.append(f"{self.proof:g} proof")
        return ", ".join(parts)


def parse_alcohol(text: str) -> Alcohol:
    """Parse an application value such as '45% Alc./Vol. (90 Proof)' or just '45'."""
    result = Alcohol()
    if m := _PERCENT_RE.search(text):
        result.abv = to_float(m.group(1))
    if m := _PROOF_RE.search(text):
        result.proof = to_float(m.group(1))
    if result.abv is None and result.proof is None:
        if m := re.fullmatch(r"\s*" + _NUMBER + r"\s*", text):
            result.abv = to_float(m.group(1))
    if result.abv is None and result.proof is not None:
        result.abv = result.proof / 2
    return result


@dataclass
class Mention:
    value: float
    text: str
    start: int  # character span in the text that was searched
    end: int
    percent: bool = True  # False for "53.5 ALC/VOL" printed without a % sign


def _prepare(text: str) -> tuple[str, list[int]]:
    """Repair OCR digit slips and close up letter-spaced text ("7 5 0 m L" -> "750mL").

    Returns the cleaned text and, for each of its characters, the index of the
    same character in the original, so spans can still be located on the label.
    """
    text = _ONE_LITRE_RE.sub(r"\g<1>1\2", fix_ocr_digits(text))  # same length: offsets still line up
    out, index, pos = [], [], 0
    for m in _SPACED_RE.finditer(text):
        for i in range(pos, m.end()):
            if i < m.start() or text[i] != " ":
                out.append(text[i])
                index.append(i)
        pos = m.end()
    out.extend(text[pos:])
    index.extend(range(pos, len(text)))
    index.append(len(text))
    return "".join(out), index


def _mention(value: float, m: re.Match, index: list[int], percent: bool = True) -> Mention:
    return Mention(value, m.group(0).strip(), index[m.start()], index[m.end() - 1] + 1, percent)


def find_abv_mentions(text: str) -> list[Mention]:
    """Percentages on the label that look like an alcohol statement.

    Percentages right next to words like "alc", "vol" or "ABV" win; otherwise any
    plausible percentage counts ("100% agave" is excluded by the < 100 cap).
    A number directly followed by "ALC" but no % sign is reported too, flagged.
    """
    text, index = _prepare(text)
    mentions, contextual = [], []
    for m in _PERCENT_RE.finditer(text):
        value = to_float(m.group(1))
        if not 0 < value < 100:
            continue
        mention = _mention(value, m, index)
        mentions.append(mention)
        before, after = text[max(0, m.start() - 15) : m.start()], text[m.end() : m.end() + 12]
        if _ALCOHOL_CONTEXT_RE.search(before) or _ALCOHOL_CONTEXT_RE.search(after):
            contextual.append(mention)
    for m in _BARE_ABV_RE.finditer(text):
        value = to_float(m.group(1))
        if 0 < value < 100:
            contextual.append(_mention(value, m, index, percent=False))
    return contextual or mentions


def find_proof_mentions(text: str) -> list[Mention]:
    text, index = _prepare(text)
    return [_mention(to_float(m.group(1)), m, index) for m in _PROOF_RE.finditer(text)]


def _unit_to_ml(unit: str) -> float | None:
    unit = re.sub(r"\s+", " ", unit.strip())
    for pattern, ml in _UNIT_PATTERNS:
        if pattern.match(unit):
            return ml
    return None


def parse_volume(text: str) -> float | None:
    """Parse '750 mL', '1.75L', '12 FL. OZ.' into millilitres."""
    m = _VOLUME_RE.search(fix_ocr_digits(text))
    if not m:
        return None
    factor = _unit_to_ml(m.group(2))
    return to_float(m.group(1)) * factor if factor else None


def find_volume_mentions(text: str) -> list[Mention]:
    text, index = _prepare(text)
    found = []
    for m in _VOLUME_RE.finditer(text):
        factor = _unit_to_ml(m.group(2))
        if factor:
            found.append(_mention(to_float(m.group(1)) * factor, m, index))
    return found


def same_volume(a_ml: float, b_ml: float) -> bool:
    """Equal within 1% - covers '12 fl oz' printed as '355 mL'."""
    return abs(a_ml - b_ml) <= max(a_ml, b_ml) * 0.01


def format_ml(ml: float) -> str:
    return f"{ml / 1000:g} L" if ml >= 1000 else f"{ml:g} mL"
