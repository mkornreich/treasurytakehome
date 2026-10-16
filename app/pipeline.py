"""One verification, end to end: decode -> read (OCR passes, optional AI reader) -> rules.

OCR runs in rounds. Each round gives every image one more pass with a
different clean-up (straightened, contrast-equalized, turned sideways for
vertical side-panel text, binarized). After each round the rules run on
everything read so far; we stop as soon as every check passes or the time
budget is spent. Clean labels finish after one ~0.3 s pass; hard photos get
the extra effort. If the government warning isn't confirmed, its paragraph is
cropped out, enlarged and re-read as a single block of text.
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from statistics import median
from time import perf_counter

import numpy as np

from app.config import Settings
from app.extraction.browser import evidence_from_reading
from app.extraction.ocr import BLOCK_CONFIG, detect_rotation, read_text
from app.extraction.vision import VisionReader, VisionUnavailable
from app.imaging import (
    View,
    binarize,
    darkest_channel,
    decode,
    enhance,
    quality_notices,
    to_gray,
)
from app.models import (
    Application,
    BrowserReadings,
    CheckResult,
    Status,
    VerificationResult,
)
from app.verification.evidence import Evidence
from app.verification.rules import overall, verify
from app.verification.text import loose
from app.verification.warning import VOCABULARY, read_warning

log = logging.getLogger(__name__)

MIN_UPRIGHT_WORDS = 12  # fewer words than this on the first pass -> the photo may be sideways
MIN_SKEW_DEGREES = 0.8
WARNING_LINE_PX = 36  # re-read the warning with its lines enlarged to about this height
# Most often useful first. "layout" segments the page differently (it finds text boxed in
# tables that sparse-text mode skips); "rotate-90"/"rotate-270" read text running up or down
# a side panel; "rotate-180" reads text printed upside down around keg collars.
PLAN = ["standard", "layout", "enhanced", "rotate-90", "rotate-270", "binarized", "enhanced-gray", "rotate-180"]
LAYOUT_CONFIG = "--oem 1 --psm 3"


class ImageReader:
    """The OCR passes for one label image."""

    def __init__(self, rgb: np.ndarray, gray: np.ndarray, index: int):
        self.index = index
        self.rgb = rgb
        self.gray = gray
        self.view = View.of(gray).fit_for_ocr()
        self.plan: list = list(PLAN)
        self.urgent: list[str] = []  # passes on a straightened view: they run before anything else
        self.evidences: list[Evidence] = []
        self.rotated = self.deskewed = False
        self.full_pass_s = 0.0
        self.reread: set[int] = set()

    @property
    def has_more(self) -> bool:
        return bool(self.urgent or self.plan)

    @property
    def next_cost(self) -> float:
        """Rough time the next step will take (a warning re-read covers a small crop)."""
        step = (self.urgent or self.plan or [None])[0]
        return self.full_pass_s * (0.35 if isinstance(step, View) else 1.0)

    def run_next(self) -> None:
        start = perf_counter()
        step = self.urgent.pop(0) if self.urgent else self.plan.pop(0)
        if isinstance(step, View):  # a cropped warning paragraph
            evidence, _ = read_text(step, "ocr:warning", self.index, config=BLOCK_CONFIG)
            self.evidences.append(evidence)
            return
        config = LAYOUT_CONFIG if step == "layout" else None
        if step in ("standard", "layout"):
            view = self.view
        elif step.startswith("rotate-"):
            view = self.view.rotate(int(step.split("-")[1]))
        elif step == "enhanced-gray":
            view = self.view.map(enhance)
        else:
            color = self.view.reproject(darkest_channel(self.rgb))
            view = color.map(enhance if step == "enhanced" else binarize)
        evidence, skew = read_text(view, f"ocr:{step}", self.index, **({"config": config} if config else {}))
        self.evidences.append(evidence)
        self.full_pass_s = max(self.full_pass_s, perf_counter() - start)
        if step == "standard":
            self._straighten(evidence, skew)

    def _straighten(self, evidence: Evidence, skew: float) -> None:
        """Queue another standard pass if the photo is sideways or tilted."""
        if not self.rotated and evidence.word_count < MIN_UPRIGHT_WORDS:
            self.rotated = True
            rotation = detect_rotation(self.view.image)
            if rotation:
                self.view = self.view.rotate(rotation)
                self.urgent.append("standard")
                return
        if not self.deskewed and abs(skew) > MIN_SKEW_DEGREES:
            self.deskewed = True
            self.view = self.view.rotate(-skew)
            # A tilted picture is a photo, where glare and uneven light are likely: run the
            # contrast-equalized pass next, on the straightened view, rather than repeating the
            # plain one. On a slow server that saves the pass photos need most.
            if "enhanced" in self.plan:
                self.plan.remove("enhanced")
            self.urgent.append("enhanced")

    def queue_warning_reread(self) -> None:
        """Crop the best reading of the warning paragraph and queue it to be read on its own.

        Sparse-text OCR reads a label's scattered blocks in an arbitrary order, so a
        neighbouring column or a barcode can end up spliced into the warning. Read
        as one enlarged block, the paragraph comes out in order.
        """
        readings = [read_warning(e) for e in self.evidences if e.name != "ocr:warning" and e.view is not None]
        readings = [r for r in readings if r.located and not r.exact and id(r.evidence) not in self.reread]
        if not readings:
            return
        reading = max(readings, key=lambda r: r.score)
        self.reread.add(id(reading.evidence))
        # Long words only: "the", "of" and "a" also turn up in text next to the warning.
        boxes = [t.px for t in reading.tokens if t.px and len(loose(t.text)) >= 5 and loose(t.text) in VOCABULARY]
        if len(boxes) < 4:
            return
        line = median(h for _, _, _, h in boxes)
        left = min(x for x, _, _, _ in boxes) - line
        top = min(y for _, y, _, _ in boxes) - 1.5 * line
        right = max(x + w for x, _, w, _ in boxes) + line
        bottom = max(y + h for _, y, _, h in boxes) + 1.5 * line
        scale = float(np.clip(WARNING_LINE_PX / max(line, 1), 1.0, 4.0))
        plain = reading.evidence.view.reproject(self.gray)  # same geometry, without that pass's filter
        crop = plain.crop(int(left), int(top), int(right), int(bottom), scale=scale)
        self.plan.insert(0, crop)


def _all_pass(checks: list[CheckResult] | None) -> bool:
    return bool(checks) and all(c.status == Status.PASS for c in checks)


class Verifier:
    def __init__(self, settings: Settings, vision: VisionReader | None = None):
        self.settings = settings
        self.vision = vision if settings.vision_enabled else None
        self.pool = ThreadPoolExecutor(max_workers=settings.ocr_workers, thread_name_prefix="ocr")

    def verify_readings(self, readings: BrowserReadings) -> VerificationResult:
        """Apply the rules to what the browser's text scanner read. No picture is involved."""
        started = perf_counter()
        evidences = [evidence_from_reading(r) for r in readings.readings]
        checks = verify(readings.application, evidences)
        status, headline = overall(checks)
        rules_s = perf_counter() - started
        return VerificationResult(
            overall=status,
            headline=headline,
            checks=checks,
            engines=["ocr:browser"],
            elapsed_ms=readings.ocr_ms + round(rules_s * 1000),
            timings_ms={"browser_ocr": readings.ocr_ms, "rules": round(rules_s * 1000)},
        )

    async def verify(self, uploads: list[bytes], app: Application) -> VerificationResult:
        """Raises imaging.ImageError if an upload is not a readable picture."""
        started = perf_counter()
        loop = asyncio.get_running_loop()
        run = lambda fn, *args: loop.run_in_executor(self.pool, fn, *args)

        rgbs = await asyncio.gather(*(run(decode, data) for data in uploads))
        grays = await asyncio.gather(*(run(to_gray, rgb) for rgb in rgbs))
        notices = []
        for i, found in enumerate(await asyncio.gather(*(run(quality_notices, gray) for gray in grays))):
            prefix = f"Image {i + 1}: " if len(grays) > 1 else ""
            notices += [prefix + n for n in found]
        timings = {"decode": perf_counter() - started}

        vision_task = vision_started = None
        if self.vision and self.settings.vision_mode == "always":
            vision_task, vision_started = asyncio.create_task(self.vision.read(rgbs)), perf_counter()

        readers = [ImageReader(rgb, gray, i) for i, (rgb, gray) in enumerate(zip(rgbs, grays, strict=True))]
        deadline = started + self.settings.ocr_budget_s
        checks: list[CheckResult] | None = None
        ocr_started = perf_counter()
        while pending := [r for r in readers if r.has_more]:
            # Straightening passes always run: without them a sideways photo yields no reading at all.
            over_budget = perf_counter() + max(r.next_cost for r in pending) > deadline
            if checks is not None and over_budget and not any(r.urgent for r in pending):
                break
            await asyncio.gather(*(run(r.run_next) for r in pending))
            evidences = [e for r in readers for e in r.evidences]
            checks = verify(app, evidences)
            if _all_pass(checks):
                break
            if any(c.field.startswith("warning") and c.status != Status.PASS for c in checks):
                for reader in readers:
                    reader.queue_warning_reread()
            if self.vision and vision_task is None:
                # Local OCR could not confirm everything: ask the AI reader while OCR keeps trying.
                vision_task, vision_started = asyncio.create_task(self.vision.read(rgbs)), perf_counter()
        timings["ocr"] = perf_counter() - ocr_started
        engines = sorted({e.name for e in evidences})

        if vision_task is not None:
            if _all_pass(checks) and self.settings.vision_mode != "always":
                vision_task.cancel()
            else:
                remaining = vision_started + self.settings.vision_timeout_s - perf_counter()
                try:
                    reading = await asyncio.wait_for(vision_task, timeout=max(0.1, remaining))
                    evidences.append(reading)
                    checks = verify(app, evidences)
                    engines.append(reading.name)
                except (TimeoutError, VisionUnavailable) as exc:
                    message = str(exc) or "The AI reader did not reply in time."
                    notices.append(f"{message} The results come only from the text scanner on this server.")
                    log.warning("vision reader unavailable: %s", message)
                except Exception:  # never let the optional reader break a verification
                    log.exception("vision reader failed")
                    notices.append("The AI reader did not operate correctly. The results come only from the text scanner on this server.")
                timings["vision"] = perf_counter() - vision_started

        status, headline = overall(checks)
        timings["total"] = perf_counter() - started
        return VerificationResult(
            overall=status,
            headline=headline,
            checks=checks,
            notices=notices,
            engines=engines,
            elapsed_ms=round(timings["total"] * 1000),
            timings_ms={k: round(v * 1000) for k, v in timings.items()},
        )
