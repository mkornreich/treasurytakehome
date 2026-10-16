"""Readings made by the text scanner in the user's browser (tesseract.js, app/static/browser-ocr.js).

The browser runs the same Tesseract engine and English model as the server, on the
same grayscale picture, and sends only what it read: words, their positions and
confidences, and how thick the "GOVERNMENT WARNING" strokes are. The picture itself
stays in the browser. The rules then treat each reading like one server OCR pass.
"""

from __future__ import annotations

from app.extraction.typography import SMALL_IN_PICTURE, TOO_SMALL, judge_weight
from app.models import Box, Reading
from app.verification.evidence import Evidence, Token


def evidence_from_reading(reading: Reading) -> Evidence:
    tokens = [
        Token(text=word.text, conf=word.conf, box=Box(image=reading.image, x=word.x, y=word.y, w=word.w, h=word.h))
        for word in reading.words
        if word.text.strip()
    ]
    evidence = Evidence(source="ocr", name="ocr:browser", tokens=tokens, image_index=reading.image)
    if reading.heading_too_small:
        note = SMALL_IN_PICTURE if reading.heading_too_small == "picture" else TOO_SMALL
        evidence.header_bold, evidence.header_bold_note = None, note
    elif reading.heading_weight is not None:
        evidence.header_bold, evidence.header_bold_note = judge_weight(reading.heading_weight)
    return evidence
