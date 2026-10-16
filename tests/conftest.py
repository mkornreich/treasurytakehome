import csv
import os
from pathlib import Path

import pytest

# Never let tests reach a real model API, whatever the developer's shell has set.
for name in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"):
    os.environ.pop(name, None)
os.environ["VISION"] = "off"

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


@pytest.fixture(scope="session")
def sample_rows():
    with open(SAMPLES / "batch_applications.csv", newline="", encoding="utf-8") as fh:
        return {row["label_file"]: row for row in csv.DictReader(fh)}


def sample_bytes(name: str) -> bytes:
    return (SAMPLES / "labels" / name).read_bytes()
