"""What a reader (OCR pass or vision model) saw on a label, in one common shape.

The rules never care which engine produced the text: they search `tokens`
(and, when a reader returns structured fields, `fields`) for the values on
the application.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models import Box


@dataclass
class Token:
    text: str
    box: Box | None = None  # on the original upload, as fractions
    conf: float = 100.0
    px: tuple[int, int, int, int] | None = None  # (left, top, width, height) in the pixels of `Evidence.view`


@dataclass
class Evidence:
    source: str  # engine family shown to users: "ocr" or "vision"
    name: str  # specific pass, for diagnostics: "ocr:standard", "ocr:enhanced", ...
    tokens: list[Token]
    image_index: int = 0
    fields: dict[str, str] = field(default_factory=dict)
    warning_text: str | None = None  # verbatim warning transcription, when a reader provides one
    header_bold: bool | None = None  # is "GOVERNMENT WARNING:" bold? None = could not tell
    header_bold_note: str = ""
    view: object | None = field(default=None, repr=False, compare=False)  # imaging.View the OCR pass read

    @property
    def text(self) -> str:
        return " ".join(t.text for t in self.tokens)

    @property
    def word_count(self) -> int:
        return sum(1 for t in self.tokens if any(c.isalnum() for c in t.text))


def tokens_from_text(text: str) -> list[Token]:
    return [Token(part) for part in text.split()]


def merge_boxes(boxes: list[Box]) -> list[Box]:
    """Collapse word boxes on the same text line (of the same image) into one box per line."""
    lines: list[list[Box]] = []
    for box in boxes:
        for line in lines:
            ref = line[-1]
            same_row = abs((box.y + box.h / 2) - (ref.y + ref.h / 2)) < max(box.h, ref.h) * 0.6
            if ref.image == box.image and same_row:
                line.append(box)
                break
        else:
            lines.append([box])
    merged = []
    for line in lines:
        x0 = min(b.x for b in line)
        y0 = min(b.y for b in line)
        x1 = max(b.x + b.w for b in line)
        y1 = max(b.y + b.h for b in line)
        merged.append(Box(image=line[0].image, x=x0, y=y0, w=x1 - x0, h=y1 - y0))
    return merged
