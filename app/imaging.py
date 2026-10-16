"""Image decoding, clean-up and photo-quality hints."""

from __future__ import annotations

import io
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

from app.models import Box

Image.MAX_IMAGE_PIXELS = 60_000_000  # refuse decompression bombs early

OCR_MAX_SIDE = 2400  # larger images slow Tesseract without improving accuracy
OCR_MIN_SIDE = 1400  # small images are upscaled so fine print is legible


class ImageError(ValueError):
    """The upload could not be read as a picture."""


def decode(data: bytes) -> np.ndarray:
    """Bytes -> RGB array, honoring the EXIF orientation that phone cameras set."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            img = ImageOps.exif_transpose(img)
            return np.asarray(img.convert("RGB"))
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as exc:
        raise ImageError("This file could not be opened as a picture.") from exc


def to_gray(rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)


def darkest_channel(rgb: np.ndarray) -> np.ndarray:
    """Per-pixel minimum of R, G, B. Colored ink (gold, brown, red) washed out by glare
    loses contrast in ordinary grayscale but stays dark in at least one channel."""
    return np.ascontiguousarray(rgb.min(axis=2))


@dataclass
class View:
    """A processed copy of an upload, plus the transform back to the original.

    `matrix` (3x3) maps original pixel coordinates to this view's pixels, so
    word boxes found on a rotated/rescaled view can be drawn on the original.
    """

    image: np.ndarray
    matrix: np.ndarray
    original_size: tuple[int, int]  # (width, height)

    @classmethod
    def of(cls, gray: np.ndarray) -> View:
        h, w = gray.shape[:2]
        return cls(gray, np.eye(3), (w, h))

    def _derive(self, image: np.ndarray, step: np.ndarray) -> View:
        return View(image, step @ self.matrix, self.original_size)

    def map(self, fn) -> View:
        """Apply a pixel-wise filter that does not move anything."""
        return View(fn(self.image), self.matrix, self.original_size)

    def reproject(self, source: np.ndarray) -> View:
        """Another rendering of the original upload, with this view's geometry applied."""
        h, w = self.image.shape[:2]
        warped = cv2.warpAffine(source, self.matrix[:2], (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)
        return View(warped, self.matrix, self.original_size)

    def fit_for_ocr(self) -> View:
        h, w = self.image.shape[:2]
        long_side = max(h, w)
        if long_side > OCR_MAX_SIDE:
            scale, interp = OCR_MAX_SIDE / long_side, cv2.INTER_AREA
        elif long_side < OCR_MIN_SIDE:
            scale, interp = min(OCR_MIN_SIDE / long_side, 3.0), cv2.INTER_CUBIC
        else:
            return self
        size = (round(w * scale), round(h * scale))
        resized = cv2.resize(self.image, size, interpolation=interp)
        return self._derive(resized, np.diag([size[0] / w, size[1] / h, 1.0]))

    def rotate(self, degrees: float) -> View:
        """Rotate clockwise by `degrees`, growing the canvas so nothing is cropped."""
        if abs(degrees) < 0.01:
            return self
        h, w = self.image.shape[:2]
        m = cv2.getRotationMatrix2D((w / 2, h / 2), -degrees, 1.0)
        cos, sin = abs(m[0, 0]), abs(m[0, 1])
        new_w, new_h = int(h * sin + w * cos + 0.5), int(h * cos + w * sin + 0.5)
        m[0, 2] += new_w / 2 - w / 2
        m[1, 2] += new_h / 2 - h / 2
        border = int(np.median(np.concatenate([self.image[0], self.image[-1]])))
        rotated = cv2.warpAffine(
            self.image, m, (new_w, new_h), flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_CONSTANT, borderValue=border,
        )
        return self._derive(rotated, np.vstack([m, [0, 0, 1]]))

    def crop(self, left: int, top: int, right: int, bottom: int, scale: float = 1.0, pad: int = 12) -> View:
        """A region of this view, optionally enlarged, with a plain margin around it."""
        h, w = self.image.shape[:2]
        left, top, right, bottom = max(0, left), max(0, top), min(w, right), min(h, bottom)
        region = self.image[top:bottom, left:right]
        if scale != 1.0:
            region = cv2.resize(region, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        border = int(np.median(region)) if region.size else 255
        region = cv2.copyMakeBorder(region, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=border)
        step = np.array([[scale, 0, pad - left * scale], [0, scale, pad - top * scale], [0, 0, 1.0]])
        return self._derive(region, step)

    def box_on_original(self, left: float, top: float, width: float, height: float, image: int = 0) -> Box:
        inverse = np.linalg.inv(self.matrix)
        corners = np.array(
            [[left, top, 1], [left + width, top, 1], [left, top + height, 1], [left + width, top + height, 1]]
        ).T
        xs, ys, _ = inverse @ corners
        ow, oh = self.original_size
        x0, x1 = max(0.0, xs.min() / ow), min(1.0, xs.max() / ow)
        y0, y1 = max(0.0, ys.min() / oh), min(1.0, ys.max() / oh)
        return Box(image=image, x=x0, y=y0, w=max(0.0, x1 - x0), h=max(0.0, y1 - y0))


def enhance(gray: np.ndarray) -> np.ndarray:
    """Even out glare and shadows (local contrast equalization), then sharpen."""
    flat = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(gray)
    blurred = cv2.GaussianBlur(flat, (0, 0), 2)
    return cv2.addWeighted(flat, 1.6, blurred, -0.6, 0)


def binarize(gray: np.ndarray) -> np.ndarray:
    """Adaptive threshold: helps where lighting changes across a curved bottle."""
    smooth = cv2.bilateralFilter(gray, 7, 50, 50)
    return cv2.adaptiveThreshold(smooth, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 15)


def quality_notices(gray: np.ndarray) -> list[str]:
    """Plain-language warnings about the photo itself, shown next to the results."""
    notices = []
    h, w = gray.shape[:2]
    if max(h, w) < 700:
        notices.append("The picture is small (low resolution), so fine print may not be readable.")
    sample = gray
    if max(h, w) > 1200:
        scale = 1200 / max(h, w)
        sample = cv2.resize(gray, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    if cv2.Laplacian(sample, cv2.CV_64F).var() < 40:
        notices.append("The picture looks blurry. A sharper photo will give more reliable results.")
    mean = float(sample.mean())
    if mean < 50:
        notices.append("The picture is very dark.")
    glare = float((sample >= 250).mean())
    if glare > 0.12 and mean < 200:
        notices.append("Part of the label looks washed out by glare or reflections.")
    return notices
