"""End to end over the bundled sample labels, with real OCR (needs the tesseract binary)."""

import asyncio
import shutil

import pytest

from app.config import Settings
from app.models import Application, Status
from app.pipeline import Verifier
from tests.conftest import sample_bytes

pytestmark = pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")

EXPECTED = {
    "01_old_tom_bourbon.png": ("pass", {}),
    "02_stones_throw_gin.png": ("pass", {}),
    "03_abv_mismatch.png": ("fail", {"alcohol_content": "fail"}),
    "04_warning_title_case.png": ("fail", {"warning_caps": "fail"}),
    "05_warning_not_bold.png": ("review", {"warning_bold": "review"}),
    "06_warning_reworded.png": ("fail", {"warning_text": "fail"}),
    "07_imported_wine.png": ("review", {"brand_name": "review", "country_of_origin": "pass"}),
    "08_craft_beer.png": ("pass", {"net_contents": "pass"}),
    "09_photo_angle_glare.jpg": ("pass", {}),
    "10_net_contents_mismatch.png": ("fail", {"net_contents": "fail"}),
    "11_photo_sideways.jpg": ("pass", {}),
    "13_side_panel.png": ("pass", {"warning_text": "pass", "bottler": "pass"}),
}


@pytest.fixture(scope="module")
def verifier():
    return Verifier(Settings(ocr_workers=2))


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_sample_label(name, sample_rows, verifier):
    row = sample_rows[name]
    app = Application(**{k: v for k, v in row.items() if k in Application.model_fields})
    result = asyncio.run(verifier.verify([sample_bytes(name)], app))

    overall, fields = EXPECTED[name]
    by_field = {c.field: c.status.value for c in result.checks}
    assert result.overall.value == overall, [(c.field, c.status.value, c.message) for c in result.checks]
    for field, status in fields.items():
        assert by_field[field] == status, next(c.message for c in result.checks if c.field == field)
    # The stakeholders' hard requirement: answers in about five seconds.
    assert result.elapsed_ms < 5000


def test_front_and_back_images_are_combined(verifier, sample_rows):
    """Brand on the front picture, warning on the back: an application covers both."""
    row = sample_rows["12_front_label.png;12_back_label.png"]
    app = Application(**{k: v for k, v in row.items() if k in Application.model_fields})

    front_only = asyncio.run(verifier.verify([sample_bytes("12_front_label.png")], app))
    assert front_only.overall != Status.PASS  # no warning on the front panel

    both = asyncio.run(verifier.verify([sample_bytes("12_front_label.png"), sample_bytes("12_back_label.png")], app))
    assert both.overall == Status.PASS, [(c.field, c.message) for c in both.checks]
    boxes = {c.field: {b.image for b in c.boxes} for c in both.checks}
    assert boxes["brand_name"] == {0} and boxes["warning_text"] == {1}


def test_no_more_passes_after_the_page_stops_waiting(verifier, sample_rows):
    """The page cancels its request when the browser confirmed every item first: the
    server must not keep a CPU busy for an answer nobody reads."""
    row = sample_rows["03_abv_mismatch.png"]
    app = Application(**{k: v for k, v in row.items() if k in Application.model_fields})
    full = asyncio.run(verifier.verify([sample_bytes("03_abv_mismatch.png")], app))
    assert len(full.engines) > 1  # a label with a problem gets more than one pass

    stopped = asyncio.run(verifier.verify([sample_bytes("03_abv_mismatch.png")], app, cancelled=lambda: True))
    assert stopped.engines == ["ocr:standard"]
