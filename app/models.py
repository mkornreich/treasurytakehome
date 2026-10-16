"""Request/response models shared by the API, the pipeline and the rules."""

from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class BeverageType(str, Enum):
    SPIRITS = "spirits"
    WINE = "wine"
    BEER = "beer"


class Application(BaseModel):
    """What the applicant typed into the COLA form. Blank fields are not checked."""

    beverage_type: BeverageType = BeverageType.SPIRITS
    brand_name: str = ""
    class_type: str = ""
    alcohol_content: str = ""
    net_contents: str = ""
    bottler: str = Field(default="", description="Name and address of bottler/producer/importer")
    country_of_origin: str = ""

    @field_validator("*", mode="before")
    @classmethod
    def _strip(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("beverage_type", mode="before")
    @classmethod
    def _beverage_alias(cls, value):
        if isinstance(value, str):
            aliases = {
                "": "spirits", "spirit": "spirits", "distilled spirits": "spirits",
                "liquor": "spirits", "malt beverage": "beer", "malt": "beer", "ale": "beer",
            }
            value = value.strip().lower()
            return aliases.get(value, value)
        return value


class Word(BaseModel):
    """One word that the text scanner in the browser read, located on the uploaded picture."""

    text: str = Field(max_length=200)
    conf: float = Field(ge=-1, le=100)
    x: float = Field(ge=0, le=1)  # fractions of the picture's width and height
    y: float = Field(ge=0, le=1)
    w: float = Field(ge=0, le=1)
    h: float = Field(ge=0, le=1)


class Reading(BaseModel):
    """One text-scanner pass over one picture, made in the browser."""

    image: int = Field(default=0, ge=0)  # which picture of the application
    rotation: int = 0  # degrees the picture was turned before it was read (for diagnostics)
    words: list[Word] = Field(default_factory=list, max_length=5000)
    # Stroke width of "GOVERNMENT WARNING" divided by that of the warning text after it,
    # measured as typography.header_boldness does. None: heading not seen, or not measured.
    heading_weight: float | None = Field(default=None, ge=0, le=20)
    # Why a heading that was seen has no weight: its letters are small in the upload
    # ("picture"), or too small to measure after enlarging ("text").
    heading_too_small: Literal["", "picture", "text"] = ""


class BrowserReadings(BaseModel):
    application: Application = Field(default_factory=Application)
    readings: list[Reading] = Field(min_length=1, max_length=40)
    ocr_ms: int = Field(default=0, ge=0)  # time the browser spent reading


class Status(str, Enum):
    PASS = "pass"      # the label matches the application / the requirement is met
    REVIEW = "review"  # inconclusive: a person should look (unreadable, near-miss, disagreement)
    FAIL = "fail"      # we read something on the label that conflicts with the requirement


class Box(BaseModel):
    """A region of a label image, as fractions (0-1) of image width/height."""

    image: int = 0  # which uploaded image (applications can have front and back labels)
    x: float
    y: float
    w: float
    h: float


class CheckResult(BaseModel):
    field: str
    label: str
    status: Status
    message: str
    expected: str | None = None
    found: str | None = None
    source: str | None = None
    boxes: list[Box] = []
    diff: list[dict] | None = None


class VerificationResult(BaseModel):
    overall: Status
    headline: str
    checks: list[CheckResult]
    notices: list[str] = []
    engines: list[str] = []
    elapsed_ms: int
    timings_ms: dict[str, int] = {}
