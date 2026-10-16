"""Request/response models shared by the API, the pipeline and the rules."""

from __future__ import annotations

from enum import Enum

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
