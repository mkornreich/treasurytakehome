"""Text normalization and fuzzy phrase search over what a reader saw."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from rapidfuzz import fuzz

from app.verification.evidence import Token

_PUNCT_MAP = str.maketrans(
    {
        "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'", "`": "'", "´": "'",
        "“": '"', "”": '"', "„": '"', "″": '"',
        "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-",
        " ": " ", "­": "",
    }
)

US_STATES = {
    "al": "alabama", "ak": "alaska", "az": "arizona", "ar": "arkansas", "ca": "california",
    "co": "colorado", "ct": "connecticut", "de": "delaware", "dc": "district of columbia",
    "fl": "florida", "ga": "georgia", "hi": "hawaii", "id": "idaho", "il": "illinois",
    "in": "indiana", "ia": "iowa", "ks": "kansas", "ky": "kentucky", "la": "louisiana",
    "me": "maine", "md": "maryland", "ma": "massachusetts", "mi": "michigan", "mn": "minnesota",
    "ms": "mississippi", "mo": "missouri", "mt": "montana", "ne": "nebraska", "nv": "nevada",
    "nh": "new hampshire", "nj": "new jersey", "nm": "new mexico", "ny": "new york",
    "nc": "north carolina", "nd": "north dakota", "oh": "ohio", "ok": "oklahoma", "or": "oregon",
    "pa": "pennsylvania", "ri": "rhode island", "sc": "south carolina", "sd": "south dakota",
    "tn": "tennessee", "tx": "texas", "ut": "utah", "vt": "vermont", "va": "virginia",
    "wa": "washington", "wv": "west virginia", "wi": "wisconsin", "wy": "wyoming",
}
# Two-letter codes that are also common English words are only expanded after a comma
# ("Portland, OR"), never on their own.
_AMBIGUOUS_STATES = {"in", "me", "or", "oh", "ok", "hi", "pa", "la", "ma", "de", "id"}


def clean(text: str) -> str:
    """Unicode-normalize quotes/dashes and collapse whitespace. Case is preserved."""
    text = unicodedata.normalize("NFKC", text).translate(_PUNCT_MAP)
    return re.sub(r"\s+", " ", text).strip()


def loose(text: str) -> str:
    """Comparison form that ignores case, punctuation and spacing differences.

    "STONE'S THROW" and "Stone's Throw" both become "stones throw".
    """
    text = clean(text).casefold().replace("&", " and ")
    text = text.replace("'", "")
    text = re.sub(r"[^\w%]+|_", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def expand_states(text: str) -> str:
    """Spell out US state abbreviations so "Bardstown, KY" matches "Bardstown, Kentucky"."""

    def replace(match: re.Match) -> str:
        code = match.group(2).lower()
        if code in _AMBIGUOUS_STATES and not match.group(1):
            return match.group(0)
        return f"{match.group(1) or ''}{US_STATES[code]}"

    pattern = r"(,\s*)?\b(" + "|".join(US_STATES) + r")\b\.?"
    return re.sub(pattern, replace, clean(text), flags=re.IGNORECASE)


@dataclass
class PhraseMatch:
    score: float  # 0-100 similarity of the loose forms
    text: str  # what the label says, as read
    start: int  # token index range [start, end) in the searched tokens
    end: int
    height: float = 0.0  # mean letter height of the match: brand names are the big text


def similarity(a: str, b: str) -> float:
    return fuzz.ratio(loose(a), loose(b))


def _window_score(words: list[str], target: str, target_words: set[str]) -> float:
    """Similarity of a window of (already normalized) words to the target.

    Also forgives two common OCR slips: words split or glued differently
    ("JETTY WAVE" for "JETTYWAVE"), and stray specks read as one- or two-letter
    junk inside the phrase ("FIVE _ BOROUGHS").
    """
    joined = " ".join(words)
    score = fuzz.ratio(joined, target)
    if score < 100:
        score = max(score, fuzz.ratio(joined.replace(" ", ""), target.replace(" ", "")))
    if score < 100:
        kept = [w for w in words if w in target_words or not _is_speck(w)]
        if 0 < len(kept) < len(words):
            score = max(score, fuzz.ratio(" ".join(kept), target))
    return score


def _is_speck(word: str) -> bool:
    """One or two characters that are not a plain word: "4s", "_", "x"."""
    return len(word) == 1 or (len(word) == 2 and not word.isalpha())


def find_phrase(expected: str, tokens: list[Token], transform=None) -> PhraseMatch | None:
    """Find the run of tokens that best matches `expected`, tolerating OCR noise.

    Slides windows of roughly the expected word count over the token stream and
    scores each with a normalized Levenshtein ratio. `transform` (applied to
    the expected text and to each whole window) can canonicalize spellings,
    e.g. state abbreviations.
    """
    prepare = (lambda s: loose(transform(s))) if transform else loose
    target = prepare(expected)
    if not target:
        return None
    target_words = set(target.split())
    n_words = len(expected.split())
    idx = [i for i, t in enumerate(tokens) if loose(t.text)]
    if not idx:
        return None

    best: PhraseMatch | None = None
    best_key: tuple = (-1.0,)
    for size in range(max(1, n_words - 1), n_words + 3):
        for k in range(0, len(idx) - size + 1):
            window = [tokens[i] for i in idx[k : k + size]]
            raw = " ".join(t.text for t in window).strip(" ,.;:")
            prepared = prepare(raw)
            score = _window_score(prepared.split(), target, target_words)
            if score < best_key[0]:
                continue
            heights = [t.box.h for t in window if t.box]
            height = sum(heights) / len(heights) if heights else 0.0
            # Ties: same word count, then exact capitalization, then the most prominent text.
            key = (score, -abs(size - n_words), clean(raw) == clean(expected), height)
            if key > best_key:
                best_key = key
                best = PhraseMatch(score=score, text=raw, start=idx[k], end=idx[k + size - 1] + 1, height=height)
    return best
