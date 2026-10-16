"""Optional AI reader: Claude transcribes the label; our rules still make every decision.

The model only reads. It is told to transcribe verbatim and never to "fix" text,
because a reader that silently corrects a reworded warning would hide exactly
the problems agents look for. Its reading is cross-checked against local OCR
(see rules._combine_readers).
"""

from __future__ import annotations

import asyncio
import base64
import io
import json

import anthropic
import numpy as np
from PIL import Image
from pydantic import BaseModel, ValidationError

from app.config import Settings
from app.verification.evidence import Evidence, tokens_from_text

MAX_SIDE = 2000  # enough for fine print; smaller requests return faster

SYSTEM_PROMPT = """You transcribe alcohol beverage labels for a federal label-compliance review.

Report exactly what is printed. The review exists to catch labels whose wording,
capitalization or numbers differ from what is required, so never correct, complete,
or normalize text from memory - not even the standard Government Health Warning.
Preserve capitalization, punctuation, spelling and line content as printed. If part
of the text is unreadable, write [illegible] in its place. Use an empty string for
anything that does not appear on the label."""

FIELD_GUIDE = """Transcribe these items from the label image(s):
- brand_name: the brand name.
- class_type: the class/type designation, e.g. "Kentucky Straight Bourbon Whiskey".
- alcohol_content: the alcohol statement, e.g. "45% Alc./Vol. (90 Proof)".
- net_contents: e.g. "750 mL".
- bottler: the bottler / producer / importer name-and-address statement.
- country_of_origin: the country-of-origin statement, e.g. "Product of France".
- government_warning: the full health warning statement, verbatim, starting at its heading.
- warning_heading_bold: "yes" if the heading words GOVERNMENT WARNING are in bold type
  (visibly heavier than the rest of the statement), "no" if they are not, "unclear" if you cannot tell.
- other_text: all other text on the label."""

FIELDS = ["brand_name", "class_type", "alcohol_content", "net_contents", "bottler", "country_of_origin"]
SCHEMA = {
    "type": "object",
    "properties": {
        **{name: {"type": "string"} for name in FIELDS},
        "government_warning": {"type": "string"},
        "warning_heading_bold": {"type": "string", "enum": ["yes", "no", "unclear"]},
        "other_text": {"type": "string"},
    },
    "required": [*FIELDS, "government_warning", "warning_heading_bold", "other_text"],
    "additionalProperties": False,
}


class VisionLabel(BaseModel):
    brand_name: str
    class_type: str
    alcohol_content: str
    net_contents: str
    bottler: str
    country_of_origin: str
    government_warning: str
    warning_heading_bold: str
    other_text: str


class VisionUnavailable(Exception):
    """The AI reader could not produce a reading; the caller falls back to OCR alone."""


def _jpeg_base64(rgb: np.ndarray) -> str:
    img = Image.fromarray(rgb)
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    buffer = io.BytesIO()
    img.save(buffer, format="JPEG", quality=90)
    return base64.standard_b64encode(buffer.getvalue()).decode()


class VisionReader:
    def __init__(self, settings: Settings, client: anthropic.AsyncAnthropic | None = None):
        self.model = settings.vision_model
        self.effort = settings.vision_effort
        # No retries: a retry would blow the ~5 second budget; OCR covers us instead.
        self.client = client or anthropic.AsyncAnthropic(timeout=settings.vision_timeout_s, max_retries=0)
        self._slots = asyncio.Semaphore(settings.vision_max_concurrency)

    @property
    def name(self) -> str:
        return f"vision:{self.model}"

    async def read(self, images: list[np.ndarray]) -> Evidence:
        content: list[dict] = []
        for i, rgb in enumerate(images):
            if len(images) > 1:
                content.append({"type": "text", "text": f"Label image {i + 1} of {len(images)}:"})
            data = await asyncio.to_thread(_jpeg_base64, rgb)
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}})
        content.append({"type": "text", "text": FIELD_GUIDE})

        params = dict(
            model=self.model,
            max_tokens=8000,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
            output_config={"effort": self.effort, "format": {"type": "json_schema", "schema": SCHEMA}},
        )
        if not self.model.startswith("claude-haiku"):
            # Retry on Anthropic's recommended model if a safety classifier declines.
            params.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")

        async with self._slots:
            try:
                response = await self.client.beta.messages.create(**params)
            except anthropic.APITimeoutError as exc:
                raise VisionUnavailable("The AI reader took too long to respond.") from exc
            except anthropic.APIConnectionError as exc:
                raise VisionUnavailable("The AI reader could not be reached (the network may block it).") from exc
            except anthropic.AuthenticationError as exc:
                raise VisionUnavailable("The AI reader's API key was rejected.") from exc
            except anthropic.RateLimitError as exc:
                raise VisionUnavailable("The AI reader is busy (rate limited).") from exc
            except anthropic.APIStatusError as exc:
                raise VisionUnavailable(f"The AI reader returned an error ({exc.status_code}).") from exc

        if response.stop_reason in ("refusal", "max_tokens"):
            raise VisionUnavailable("The AI reader did not return a usable reading.")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            label = VisionLabel.model_validate(json.loads(text))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise VisionUnavailable("The AI reader returned an unreadable answer.") from exc
        return to_evidence(label, self.name)


def to_evidence(label: VisionLabel, name: str) -> Evidence:
    fields = {f: getattr(label, f).strip() for f in FIELDS}
    everything = [*fields.values(), label.government_warning, label.other_text]
    bold = {"yes": True, "no": False}.get(label.warning_heading_bold)
    note = {True: "The AI reader sees it as bold.", False: "The AI reader sees it as regular weight."}.get(bold, "")
    return Evidence(
        source="vision",
        name=name,
        tokens=tokens_from_text(" ".join(t for t in everything if t)),
        fields={k: v for k, v in fields.items() if v},
        warning_text=label.government_warning.strip() or None,
        header_bold=bold,
        header_bold_note=note,
    )
