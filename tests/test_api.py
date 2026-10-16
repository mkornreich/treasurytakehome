import json
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
