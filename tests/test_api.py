import json
import re
import shutil

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.conftest import sample_bytes


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_index_and_config(client):
    assert "Label Check" in client.get("/").text
    assert client.get("/api/config").json()["vision"] == "off"


def test_page_links_its_own_css_and_js_by_content(client):
    """A browser holding an old app.js must not run it under a new page."""
    page = client.get("/")
    assert page.headers["cache-control"] == "no-cache"
    for name in ("app.css", "app.js"):
        match = re.search(rf'"/static/{name}\?v=([0-9a-f]{{12}})"', page.text)
        assert match, name
        assert client.get(f"/static/{name}?v={match.group(1)}").status_code == 200


def test_robots_blocks_all_crawlers(client):
    response = client.get("/robots.txt")
    assert response.status_code == 200
    assert response.text == "User-agent: *\nDisallow: /\n"


def test_samples_listing(client):
    samples = client.get("/api/samples").json()
    assert len(samples) >= 10
    assert client.get(samples[0]["files"][0]["url"]).status_code == 200
    assert any(len(s["files"]) == 2 for s in samples)  # a front + back application


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
def test_verify_sample(client):
    application = {"brand_name": "OLD TOM DISTILLERY", "alcohol_content": "45%", "net_contents": "750 mL"}
    response = client.post(
        "/api/verify",
        files=[("images", ("label.png", sample_bytes("01_old_tom_bourbon.png"), "image/png"))],
        data={"application": json.dumps(application)},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["overall"] == "pass"
    brand = next(c for c in body["checks"] if c["field"] == "brand_name")
    assert brand["boxes"] and 0 <= brand["boxes"][0]["x"] <= 1


def test_not_an_image_is_a_friendly_422(client):
    response = client.post("/api/verify", files=[("images", ("notes.txt", b"hello", "text/plain"))])
    assert response.status_code == 422
    assert "picture" in response.json()["detail"]


def test_bad_application_json(client):
    response = client.post(
        "/api/verify",
        files=[("images", ("label.png", sample_bytes("01_old_tom_bourbon.png"), "image/png"))],
        data={"application": "{not json"},
    )
    assert response.status_code == 422


def test_too_many_images(client):
    files = [("images", (f"{i}.png", b"x", "image/png")) for i in range(7)]
    assert client.post("/api/verify", files=files).status_code == 422


def _browser_readings(name: str) -> dict:
    """What the page's text scanner sends: the server's own first pass, as words and fractions."""
    from app.extraction.ocr import read_text
    from app.imaging import View, decode, to_gray

    evidence, _ = read_text(View.of(to_gray(decode(sample_bytes(name)))).fit_for_ocr(), "ocr:standard")
    words = [{"text": t.text, "conf": t.conf, "x": t.box.x, "y": t.box.y, "w": t.box.w, "h": t.box.h} for t in evidence.tokens]
    return {"image": 0, "words": words, "heading_weight": 1.4}


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
def test_verify_readings_checks_words_without_a_picture(client):
    application = {"brand_name": "OLD TOM DISTILLERY", "alcohol_content": "45% Alc./Vol. (90 Proof)", "net_contents": "750 mL"}
    response = client.post("/api/verify-readings", json={
        "application": application, "readings": [_browser_readings("01_old_tom_bourbon.png")], "ocr_ms": 400,
    })
    assert response.status_code == 200
    body = response.json()
    assert body["overall"] == "pass"
    assert body["engines"] == ["ocr:browser"]
    assert body["timings_ms"]["browser_ocr"] == 400
    bold = next(c for c in body["checks"] if c["field"] == "warning_bold")
    assert "1.40 times as thick" in bold["message"]


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="tesseract not installed")
def test_verify_readings_reports_a_heading_too_small_to_measure(client):
    reading = {**_browser_readings("01_old_tom_bourbon.png"), "heading_weight": None, "heading_too_small": "picture"}
    body = client.post("/api/verify-readings", json={"readings": [reading]}).json()
    bold = next(c for c in body["checks"] if c["field"] == "warning_bold")
    assert bold["status"] == "review" and "did not measure" in bold["message"]


def test_verify_readings_rejects_bad_input(client):
    assert client.post("/api/verify-readings", json={"readings": []}).status_code == 422
    assert client.post("/api/verify-readings", json={"readings": [{"image": 9, "words": []}]}).status_code == 422
    word = {"text": "x", "conf": 90, "x": 1.5, "y": 0, "w": 0.1, "h": 0.1}  # outside the picture
    assert client.post("/api/verify-readings", json={"readings": [{"words": [word]}]}).status_code == 422


def test_browser_scanner_files_are_served_and_cached(client):
    response = client.get("/static/vendor/tesseract-7.0.0/eng.traineddata.gz")
    assert response.status_code == 200 and len(response.content) > 1_000_000
    assert "immutable" in response.headers["cache-control"]
    # A browser checking its copy gets 304; that answer must keep the long cache too.
    again = client.get("/static/vendor/tesseract-7.0.0/eng.traineddata.gz", headers={"If-None-Match": response.headers["etag"]})
    assert again.status_code == 304 and "immutable" in again.headers["cache-control"]
    # The page's own files are checked on every load, so an update never mixes old and new files.
    assert client.get("/static/app.css").headers["cache-control"] == "no-cache"
    assert client.get("/static/local/rules.js").status_code == 200
    assert client.get("/api/config").json()["browser_ocr"] is True
