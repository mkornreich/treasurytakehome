"""Is "GOVERNMENT WARNING:" printed in bold? Estimated from stroke thickness.

OCR returns text, not font weight, so we measure it. Every word is scaled by
the same factor (chosen so the heading is ~48 px tall, enough for thin strokes
to span several pixels), binarized, and run through a distance transform. The
80th percentile of the distances over the ink is half the width of the
word's main strokes. Bold type has clearly thicker strokes than the
regular-weight words that follow the heading.

Measured on approved labels from the TTB registry, bold headings come out
1.2-1.8x as thick as the body text, and regular-weight headings ~1.0x.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

BOLD_RATIO = 1.20  # heading strokes this much thicker than body strokes -> bold
REGULAR_RATIO = 1.08  # at or below this -> same weight as the body text
MIN_LETTER_PX = 10  # smaller than this and stroke measurements are mostly noise
REFERENCE_HEIGHT = 48


@dataclass
class WordImage:
    text: str
    left: int
    top: int
    width: int
    height: int


def stroke_width(gray: np.ndarray, word: WordImage, scale: float) -> float | None:
    """Main stroke width of a word, in pixels of the original image."""
    crop = gray[word.top : word.top + word.height, word.left : word.left + word.width]
    if crop.size == 0 or word.height < MIN_LETTER_PX:
        return None
    crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    _, binary = cv2.threshold(crop, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    dark = binary == 0
    ink = (dark if dark.mean() < 0.5 else ~dark).astype(np.uint8)  # ink is the minority color
    dist = cv2.distanceTransform(ink, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
    values = dist[ink > 0]
    if values.size < 20:
        return None
    return 2.0 * float(np.percentile(values, 80)) / scale


def header_boldness(
    gray: np.ndarray, header: list[WordImage], body: list[WordImage]
) -> tuple[bool | None, str]:
    """Compare stroke weight of the heading words with the warning text after them."""
    heights = [h.height for h in header if h.height >= MIN_LETTER_PX]
    if not heights:
        return None, "The text is too small to measure the type weight of the heading."
    scale = min(6.0, REFERENCE_HEIGHT / float(np.median(heights)))
    header_widths = [w for w in (stroke_width(gray, h, scale) for h in header) if w]
    body_words = [b for b in body if sum(c.isalpha() for c in b.text) >= 3]
    body_widths = [w for w in (stroke_width(gray, b, scale) for b in body_words) if w]
    if not header_widths or len(body_widths) < 3:
        return None, "The text is too small to measure the type weight of the heading."
    ratio = float(np.median(header_widths) / np.median(body_widths))
    if ratio >= BOLD_RATIO:
        return True, f"The heading letters are {ratio:.2f} times as thick as the warning text."
    if ratio <= REGULAR_RATIO:
        return False, f"The heading letters and the warning text are almost the same thickness ({ratio:.2f} times)."
    return None, f"The heading letters are only a little thicker than the warning text ({ratio:.2f} times)."
