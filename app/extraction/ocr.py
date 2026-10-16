"""Local OCR with Tesseract. No network access needed: works behind the agency firewall."""

from __future__ import annotations

import math
import os
from collections import defaultdict

import numpy as np
import pytesseract
from pytesseract import Output

from app.extraction.typography import WordImage, header_boldness
from app.imaging import View
from app.verification.evidence import Evidence, Token
from app.verification.text import loose

# Tesseract's own threading fights with our worker pool; one thread per call is faster overall.
os.environ.setdefault("OMP_THREAD_LIMIT", "1")

# psm 11 = "sparse text": labels are scattered blocks of text, not a page of paragraphs.
TESSERACT_CONFIG = "--oem 1 --psm 11"
# Keep every word Tesseract returns: on display fonts it often reports single-digit
# confidence for text it read correctly ("ALC.12.5%"). The rules cope with noise.
MIN_WORD_CONFIDENCE = 0


def tesseract_available() -> bool:
    try:
        pytesseract.get_tesseract_version()
        return True
    except (pytesseract.TesseractNotFoundError, OSError):
        return False


def detect_rotation(gray: np.ndarray) -> int:
    """Clockwise rotation (0/90/180/270) that makes the text upright, per Tesseract OSD."""
    try:
        osd = pytesseract.image_to_osd(gray, config="--psm 0", output_type=Output.DICT)
    except pytesseract.TesseractError:
        return 0  # too little text to tell
    return int(osd.get("rotate", 0)) % 360 if float(osd.get("orientation_conf", 0)) > 1.5 else 0


MIN_HEADING_PX = 14  # heading letters smaller than this in the upload: stroke widths are unreliable

# psm 6 = "a single uniform block of text": for re-reading the warning paragraph on its own.
BLOCK_CONFIG = "--oem 1 --psm 6"


def read_text(view: View, name: str, image_index: int = 0, config: str = TESSERACT_CONFIG) -> tuple[Evidence, float]:
    """Run one OCR pass. Returns what was read and the text skew in degrees (clockwise)."""
    gray = view.image
    data = pytesseract.image_to_data(gray, lang="eng", config=config, output_type=Output.DICT)

    tokens: list[Token] = []
    words: list[WordImage] = []
    lines: dict[tuple, list[WordImage]] = defaultdict(list)
    for i, text in enumerate(data["text"]):
        text = text.strip()
        conf = float(data["conf"][i])
        if not text or conf < MIN_WORD_CONFIDENCE:
            continue
        word = WordImage(text, *(int(data[k][i]) for k in ("left", "top", "width", "height")))
        box = view.box_on_original(word.left, word.top, word.width, word.height, image=image_index)
        tokens.append(Token(text=text, box=box, conf=conf, px=(word.left, word.top, word.width, word.height)))
        words.append(word)
        lines[(data["block_num"][i], data["par_num"][i], data["line_num"][i])].append(word)

    evidence = Evidence(source="ocr", name=name, tokens=tokens, image_index=image_index, view=view)
    evidence.header_bold, evidence.header_bold_note = _header_weight(view, words)
    return evidence, _skew_degrees(lines.values(), gray.shape[1])


def _header_weight(view: View, words: list[WordImage]) -> tuple[bool | None, str]:
    for i in range(len(words) - 1):
        if loose(words[i].text) == "government" and loose(words[i + 1].text).startswith("warning"):
            # Judge print size on the upload itself: an enlarged crop only looks big.
            enlarged = float(np.sqrt(abs(np.linalg.det(view.matrix[:2, :2]))))
            if words[i].height / max(enlarged, 1e-6) < MIN_HEADING_PX:
                return None, "The heading is printed too small in this picture to judge whether it is bold."
            return header_boldness(view.image, words[i : i + 2], words[i + 2 : i + 40])
    return None, ""


def _skew_degrees(lines, image_width: int) -> float:
    """Median slope of text lines with several words; positive = text runs downhill."""
    angles, weights = [], []
    for words in lines:
        if len(words) < 3:
            continue
        xs = np.array([w.left + w.width / 2 for w in words], float)
        ys = np.array([w.top + w.height / 2 for w in words], float)
        span = xs.max() - xs.min()
        if span < image_width * 0.15:
            continue
        slope = np.polyfit(xs, ys, 1)[0]
        angles.append(math.degrees(math.atan(slope)))
        weights.append(span)
    if not angles:
        return 0.0
    order = np.argsort(angles)
    cumulative = np.cumsum(np.array(weights)[order])
    return float(np.array(angles)[order][np.searchsorted(cumulative, cumulative[-1] / 2)])
