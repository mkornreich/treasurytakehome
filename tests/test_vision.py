"""The optional AI reader, exercised against a fake Anthropic client (no network)."""

import asyncio
import json
import shutil
from types import SimpleNamespace

import anthropic
import httpx2
import numpy as np
import pytest

from app.config import Settings
from app.extraction.vision import VisionReader, VisionUnavailable
from app.models import Application, Status
from app.pipeline import Verifier
from app.verification.warning import CANONICAL_WARNING
from tests.conftest import sample_bytes

needs_tesseract = pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")


def reading(**overrides):
    payload = {
        "brand_name": "OLD TOM DISTILLERY",
        "class_type": "Kentucky Straight Bourbon Whiskey",
        "alcohol_content": "45% Alc./Vol. (90 Proof)",
        "net_contents": "750 mL",
        "bottler": "Distilled and Bottled by Old Tom Distillery, Bardstown, Kentucky",
        "country_of_origin": "",
        "government_warning": CANONICAL_WARNING,
        "warning_heading_bold": "yes",
        "other_text": "EST. 1891",
    }
    payload.update(overrides)
    return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text=json.dumps(payload))])


class FakeMessages:
    def __init__(self, response=None, error=None, delay=0.0):
        self.response, self.error, self.delay, self.calls = response, error, delay, []

    async def create(self, **params):
        self.calls.append(params)
        await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        return self.response


def fake_client(**kw):
    messages = FakeMessages(**kw)
    return SimpleNamespace(beta=SimpleNamespace(messages=messages)), messages


IMAGE = np.full((40, 40, 3), 255, np.uint8)


def test_reader_sends_images_with_schema_and_fallbacks():
    client, messages = fake_client(response=reading())
    evidence = asyncio.run(VisionReader(Settings(vision_mode="always"), client).read([IMAGE, IMAGE]))
    params = messages.calls[0]
    assert params["model"] == "claude-opus-5-5"
    assert params["output_config"]["format"]["type"] == "json_schema"
    assert params["fallbacks"] == "default" and params["betas"] == ["server-side-fallback-2026-07-01"]
    assert sum(block["type"] == "image" for block in params["messages"][0]["content"]) == 2
    assert evidence.source == "vision"
    assert evidence.fields["brand_name"] == "OLD TOM DISTILLERY"
    assert evidence.header_bold is True
    assert "country_of_origin" not in evidence.fields  # empty strings are "not on the label"


def test_haiku_requests_skip_server_fallbacks():
    client, messages = fake_client(response=reading())
    asyncio.run(VisionReader(Settings(vision_mode="always", vision_model="claude-haiku-5-5"), client).read([IMAGE]))
    assert "fallbacks" not in messages.calls[0]


@pytest.mark.parametrize(
    "error, phrase",
    [
        (anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com")), "could not be reached"),
        (anthropic.APITimeoutError(request=httpx2.Request("POST", "https://api.anthropic.com")), "too long"),
    ],
)
def test_network_errors_become_vision_unavailable(error, phrase):
    client, _ = fake_client(error=error)
    with pytest.raises(VisionUnavailable, match=phrase):
        asyncio.run(VisionReader(Settings(vision_mode="always"), client).read([IMAGE]))


def test_refusal_is_unavailable():
    client, _ = fake_client(response=SimpleNamespace(stop_reason="refusal", content=[]))
    with pytest.raises(VisionUnavailable):
        asyncio.run(VisionReader(Settings(vision_mode="always"), client).read([IMAGE]))


# --- in the pipeline ----------------------------------------------------------------------


def verifier(mode, **client_kw):
    settings = Settings(vision_mode=mode, vision_timeout_s=1.0, ocr_workers=2)
    client, messages = fake_client(**client_kw)
    return Verifier(settings, VisionReader(settings, client)), messages


OLD_TOM = Application(
    brand_name="OLD TOM DISTILLERY", class_type="Kentucky Straight Bourbon Whiskey",
    alcohol_content="45% Alc./Vol. (90 Proof)", net_contents="750 mL",
)


@needs_tesseract
def test_fallback_mode_skips_ai_when_ocr_confirms_everything():
    v, messages = verifier("fallback", response=reading())
    result = asyncio.run(v.verify([sample_bytes("01_old_tom_bourbon.png")], OLD_TOM))
    assert result.overall == Status.PASS
    assert not any(e.startswith("vision") for e in result.engines)


@needs_tesseract
def test_fallback_mode_consults_ai_when_ocr_is_unsure():
    app = Application(brand_name="HARBOR LIGHT", net_contents="750 mL")
    v, messages = verifier("fallback", response=reading(brand_name="HARBOR LIGHT", warning_heading_bold="no"))
    result = asyncio.run(v.verify([sample_bytes("05_warning_not_bold.png")], app))
    assert messages.calls, "the AI reader should have been asked"
    assert any(e.startswith("vision") for e in result.engines)
    bold = next(c for c in result.checks if c.field == "warning_bold")
    assert bold.status == Status.FAIL  # both readers agree the heading is not bold


@needs_tesseract
def test_ai_outage_falls_back_to_ocr_with_a_notice():
    app = Application(brand_name="HARBOR LIGHT")
    error = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.anthropic.com"))
    v, _ = verifier("always", error=error)
    result = asyncio.run(v.verify([sample_bytes("05_warning_not_bold.png")], app))
    assert any("could not be reached" in n for n in result.notices)
    assert result.engines and all(e.startswith("ocr") for e in result.engines)


@needs_tesseract
def test_slow_ai_reader_does_not_hold_up_the_answer():
    v, _ = verifier("always", response=reading(), delay=5)
    result = asyncio.run(v.verify([sample_bytes("03_abv_mismatch.png")], OLD_TOM))
    assert result.elapsed_ms < 3000
    assert any("too long" in n for n in result.notices)


@needs_tesseract
def test_ai_that_auto_corrects_the_warning_is_caught_by_ocr():
    """Label says 'Government Warning'; a model that 'fixes' it to capitals must not pass it."""
    app = Application(brand_name="RIVERBEND")
    v, _ = verifier("always", response=reading(brand_name="RIVERBEND"))
    result = asyncio.run(v.verify([sample_bytes("04_warning_title_case.png")], app))
    caps = next(c for c in result.checks if c.field == "warning_caps")
    assert caps.status == Status.REVIEW
    assert "disagree" in caps.message
