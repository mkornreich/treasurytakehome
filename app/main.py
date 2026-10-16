"""HTTP API and static UI.

    uv run uvicorn app.main:app --reload

Nothing is stored: images and application data live in memory for the
duration of one request.
"""

from __future__ import annotations

import csv
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from app.config import Settings
from app.extraction.ocr import tesseract_available
from app.extraction.vision import VisionReader
from app.imaging import ImageError
from app.models import Application, BrowserReadings, VerificationResult
from app.pipeline import Verifier

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
SAMPLES = ROOT / "samples"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("label-verifier")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = Settings.from_env()
    vision = VisionReader(settings) if settings.vision_enabled else None
    app.state.settings = settings
    app.state.verifier = Verifier(settings, vision)
    log.info(
        "ready: tesseract=%s vision=%s model=%s ocr_workers=%d",
        tesseract_available(), settings.vision_mode, settings.vision_model, settings.ocr_workers,
    )
    yield
    app.state.verifier.pool.shutdown(wait=False, cancel_futures=True)


app = FastAPI(title="Label Verifier", lifespan=lifespan, docs_url="/api/docs", redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
app.mount("/samples", StaticFiles(directory=SAMPLES), name="samples")


@app.middleware("http")
async def cache_static_files(request: Request, call_next):
    """The browser text scanner (~6 MB) lives in a versioned folder: let browsers keep it.
    The page's own files must be checked on every load, or a browser can mix an old
    stylesheet with new code after an update."""
    response = await call_next(request)
    path = request.url.path
    if path.startswith("/static/vendor/"):
        if response.status_code in (200, 304):  # browsers copy the headers of a 304 into their cache
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.exception_handler(ImageError)
async def image_error(request: Request, exc: ImageError):
    return JSONResponse(
        status_code=422,
        content={"detail": f"{exc} Upload a JPG, PNG, WEBP, or TIFF picture of the label."},
    )


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/robots.txt", include_in_schema=False)
async def robots():
    """A prototype: keep every crawler out of the whole site."""
    return PlainTextResponse("User-agent: *\nDisallow: /\n")


@app.get("/api/health")
async def health():
    return {"status": "ok", "ocr": tesseract_available()}


@app.get("/api/config")
async def config(request: Request):
    settings: Settings = request.app.state.settings
    return {
        "vision": settings.vision_mode,
        "vision_model": settings.vision_model if settings.vision_enabled else None,
        "max_images": settings.max_images,
        "max_upload_mb": settings.max_upload_mb,
        "browser_ocr": settings.browser_ocr,
    }


@app.get("/api/samples")
async def samples():
    """The bundled demo labels, with the application data that goes with each."""
    with open(SAMPLES / "batch_applications.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    samples = []
    for row in rows:
        names = [name.strip() for name in row["label_file"].split(";") if name.strip()]
        samples.append({
            "id": row["label_file"],
            "files": [{"name": name, "url": f"/samples/labels/{name}"} for name in names],
            "note": row.get("notes", ""),
            "application": {k: row.get(k, "") for k in Application.model_fields},
        })
    return samples


@app.post("/api/verify", response_model=VerificationResult)
async def verify(
    request: Request,
    images: Annotated[list[UploadFile], File(description="Label image(s): front, back, neck...")],
    application: Annotated[str, Form(description="Application fields as JSON")] = "{}",
):
    settings: Settings = request.app.state.settings
    try:
        app_data = Application.model_validate(json.loads(application or "{}"))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise HTTPException(422, "The tool cannot read the application data.") from exc

    if not images:
        raise HTTPException(422, "Add a picture of the label.")
    if len(images) > settings.max_images:
        raise HTTPException(422, f"Upload no more than {settings.max_images} pictures for each application.")

    limit = settings.max_upload_mb * 1024 * 1024
    uploads = []
    for upload in images:
        data = await upload.read(limit + 1)
        if len(data) > limit:
            raise HTTPException(413, f"The file “{upload.filename}” is larger than {settings.max_upload_mb} MB. Use a smaller file.")
        if not data:
            raise HTTPException(422, f"The file “{upload.filename}” is empty.")
        uploads.append(data)

    result = await request.app.state.verifier.verify(uploads, app_data)
    log.info("verified %d image(s): %s in %d ms via %s", len(uploads), result.overall.value, result.elapsed_ms, ",".join(result.engines))
    return result


@app.post("/api/verify-readings", response_model=VerificationResult)
async def verify_readings(request: Request, readings: BrowserReadings):
    """Check what the text scanner in the browser read. The page sends no picture.

    The page uses this answer when every check is OK; otherwise it sends the
    pictures to /api/verify, which reads them again with more effort.
    """
    settings: Settings = request.app.state.settings
    if len({r.image for r in readings.readings}) > settings.max_images or any(r.image >= settings.max_images for r in readings.readings):
        raise HTTPException(422, f"Upload no more than {settings.max_images} pictures for each application.")
    result = request.app.state.verifier.verify_readings(readings)
    log.info("verified %d browser reading(s): %s in %d ms", len(readings.readings), result.overall.value, result.elapsed_ms)
    return result
