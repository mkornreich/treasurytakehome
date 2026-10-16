"""Generate synthetic test labels (and a batch spreadsheet) for demos and tests.

    uv run python samples/generate_labels.py

Each sample pairs an application (what the applicant typed into COLA) with a
label rendered from slightly different data, to exercise one rule. Some are
also degraded to look like phone photos: perspective, rotation, glare, blur.
"""

from __future__ import annotations

import csv
import random
import textwrap
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).parent / "labels"
FONT_DIR = Path("/usr/share/fonts/truetype")
WARNING = (
    "GOVERNMENT WARNING: (1) According to the Surgeon General, women should not drink "
    "alcoholic beverages during pregnancy because of the risk of birth defects. "
    "(2) Consumption of alcoholic beverages impairs your ability to drive a car or "
    "operate machinery, and may cause health problems."
)


def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    paths = {
        "serif-bold": "dejavu/DejaVuSerif-Bold.ttf",
        "serif": "dejavu/DejaVuSerif.ttf",
        "sans": "liberation/LiberationSans-Regular.ttf",
        "sans-bold": "liberation/LiberationSans-Bold.ttf",
        "condensed-bold": "dejavu/DejaVuSerifCondensed-Bold.ttf",
    }
    return ImageFont.truetype(str(FONT_DIR / paths[name]), size)


@dataclass
class Label:
    brand: str
    class_type: str
    alcohol: str
    net: str
    bottler: str
    origin: str = ""
    warning: str = WARNING
    header_bold: bool = True
    palette: tuple[str, str, str] = ("#f6efe0", "#2b1d0e", "#8a5a2b")  # paper, ink, accent
    brand_font: str = "serif-bold"


@dataclass
class Sample:
    file: str
    application: dict
    label: Label
    photo: str = ""  # "", "angle", "sideways"
    back: str = ""  # if set: `file` is the front panel, `back` the back panel (bottler + warning)
    side: bool = False  # bottler and warning printed up a narrow side strip, as on many cans
    note: str = ""
    expected: str = "pass"
    extra: dict = field(default_factory=dict)


def flow(draw, x0, y, width, runs, line_gap=6, fill="#000"):
    """Word-wrap mixed-font text runs (list of (text, font)) inside a column."""
    words = [(w, f) for text, f in runs for w in text.split()]
    line, line_w = [], 0
    def emit(line, y):
        x = x0
        height = max(f.size for _, f in line)
        for w, f in line:
            draw.text((x, y + height - f.size), w, font=f, fill=fill)
            x += draw.textlength(w + " ", font=f)
        return y + height + line_gap
    for w, f in words:
        ww = draw.textlength(w + " ", font=f)
        if line and line_w + ww > width:
            y = emit(line, y)
            line, line_w = [], 0
        line.append((w, f))
        line_w += ww
    if line:
        y = emit(line, y)
    return y


def centered(draw, y, text, f, width, fill):
    for row in textwrap.wrap(text, width=max(8, int(width / (f.size * 0.55)))):
        tw = draw.textlength(row, font=f)
        draw.text(((width - tw) / 2, y), row, font=f, fill=fill)
        y += f.size + 10
    return y


def fitted(name: str, text: str, size: int, max_width: int) -> ImageFont.FreeTypeFont:
    f = font(name, size)
    while size > 20 and f.getlength(text) > max_width:
        size -= 4
        f = font(name, size)
    return f


def render(label: Label, size=(1200, 1500), panel: str = "full") -> Image.Image:
    paper, ink, accent = label.palette
    img = Image.new("RGB", size, paper)
    d = ImageDraw.Draw(img)
    w, h = size
    d.rectangle([24, 24, w - 24, h - 24], outline=accent, width=8)
    d.rectangle([44, 44, w - 44, h - 44], outline=accent, width=2)

    y = 110
    if panel == "back":
        y = centered(d, y + 40, label.bottler, font("sans", 38), w, ink) + 20
        if label.origin:
            centered(d, y, label.origin, font("sans", 38), w, ink)
        body, head = font("sans", 36), font("sans-bold" if label.header_bold else "sans", 36)
        header, _, rest = label.warning.partition(":")
        flow(d, 110, h // 2 - 120, w - 220, [(header + ":", head), (rest, body)], line_gap=12, fill=ink)
        return img
    y = centered(d, y, label.brand, fitted(label.brand_font, label.brand, 92, w - 200), w, ink) + 20
    d.line([(200, y), (w - 200, y)], fill=accent, width=4)
    y = centered(d, y + 40, label.class_type, font("serif", 54), w, accent) + 50
    y = centered(d, y, label.alcohol, font("sans-bold", 46), w, ink) + 10
    y = centered(d, y, label.net, font("sans-bold", 46), w, ink) + 40
    if panel == "front":
        cy = (y + h) / 2
        d.ellipse([w / 2 - 160, cy - 160, w / 2 + 160, cy + 160], outline=accent, width=6)
        centered(d, cy - 22, "EST. 1891", font("serif-bold", 40), w, accent)
        return img
    y = centered(d, y, label.bottler, font("sans", 34), w, ink) + 10
    if label.origin:
        y = centered(d, y, label.origin, font("sans", 34), w, ink)

    # A simple emblem fills the middle, as artwork does on real labels.
    cy = (y + h - 420) / 2
    d.ellipse([w / 2 - 120, cy - 120, w / 2 + 120, cy + 120], outline=accent, width=5)
    d.ellipse([w / 2 - 100, cy - 100, w / 2 + 100, cy + 100], outline=accent, width=2)
    centered(d, cy - 18, "EST. 1891", font("serif-bold", 32), w, accent)

    body = font("sans", 31)
    head = font("sans-bold" if label.header_bold else "sans", 31)
    header, _, rest = label.warning.partition(":")
    runs = [(header + ":", head), (rest, body)] if rest else [(label.warning, body)]
    flow(d, 110, h - 420, w - 220, runs, line_gap=10, fill=ink)
    return img


def with_side_strip(label: Label) -> Image.Image:
    """Front panel plus a strip whose text runs bottom-to-top along the right edge."""
    front = render(label, panel="front")
    paper, ink, _ = label.palette
    strip = Image.new("RGB", (front.height, 250), paper)
    d = ImageDraw.Draw(strip)
    d.text((40, 20), label.bottler, font=font("sans", 26), fill=ink)
    body = font("sans", 24)
    head = font("sans-bold" if label.header_bold else "sans", 24)
    header, _, rest = label.warning.partition(":")
    flow(d, 40, 70, front.height - 80, [(header + ":", head), (rest, body)], line_gap=6, fill=ink)
    strip = strip.rotate(90, expand=True)  # counter-clockwise: reads from the bottom up
    out = Image.new("RGB", (front.width + strip.width, front.height), paper)
    out.paste(front, (0, 0))
    out.paste(strip, (front.width, 0))
    return out


def photograph(img: Image.Image, style: str, seed: int = 7) -> Image.Image:
    """Make a clean render look like a hand-held photo of a bottle."""
    rng = random.Random(seed)
    src = cv2.cvtColor(np.asarray(img), cv2.COLOR_RGB2BGR)
    h, w = src.shape[:2]
    canvas_w, canvas_h = int(w * 1.35), int(h * 1.25)
    background = np.full((canvas_h, canvas_w, 3), (54, 60, 66), np.uint8)
    noise = np.random.default_rng(seed).normal(0, 9, background.shape)
    background = np.clip(background + noise, 0, 255).astype(np.uint8)

    if style == "sideways":
        src = cv2.rotate(src, cv2.ROTATE_90_COUNTERCLOCKWISE)
        h, w = src.shape[:2]
        canvas_w, canvas_h = int(w * 1.2), int(h * 1.2)
        background = cv2.resize(background, (canvas_w, canvas_h))
        ox, oy = (canvas_w - w) // 2, (canvas_h - h) // 2
        background[oy : oy + h, ox : ox + w] = src
        out = background
    else:
        # Perspective: the label leans back and to the side.
        pts = np.float32([[0, 0], [w, 0], [w, h], [0, h]])
        jitter = lambda: rng.uniform(0.03, 0.07)
        dst = np.float32([
            [canvas_w * 0.12, canvas_h * jitter()],
            [canvas_w * (0.88 - 0.04), canvas_h * (0.04 + jitter())],
            [canvas_w * 0.9, canvas_h * (0.95 - jitter() / 2)],
            [canvas_w * 0.1, canvas_h * 0.95],
        ])
        matrix = cv2.getPerspectiveTransform(pts, dst)
        warped = cv2.warpPerspective(src, matrix, (canvas_w, canvas_h))
        mask = cv2.warpPerspective(np.full((h, w), 255, np.uint8), matrix, (canvas_w, canvas_h))
        out = np.where(mask[..., None] > 0, warped, background)
        # Rotate slightly, as if the phone was tilted.
        rot = cv2.getRotationMatrix2D((canvas_w / 2, canvas_h / 2), rng.uniform(-4, 4), 1.0)
        out = cv2.warpAffine(out, rot, (canvas_w, canvas_h), borderMode=cv2.BORDER_REPLICATE)
        # Glare: a soft bright highlight across the upper part of the bottle.
        yy, xx = np.mgrid[0:canvas_h, 0:canvas_w]
        cx, cy = canvas_w * 0.68, canvas_h * 0.3
        glare = np.exp(-(((xx - cx) / (canvas_w * 0.16)) ** 2 + ((yy - cy) / (canvas_h * 0.22)) ** 2))
        out = np.clip(out + glare[..., None] * 120, 0, 255).astype(np.uint8)
        # Uneven lighting: darker toward the bottom-left.
        shade = 1 - 0.25 * (yy / canvas_h) * (1 - xx / canvas_w)
        out = np.clip(out * shade[..., None], 0, 255).astype(np.uint8)
        out = cv2.GaussianBlur(out, (3, 3), 0.8)

    sensor = np.random.default_rng(seed + 1).normal(0, 6, out.shape)
    out = np.clip(out + sensor, 0, 255).astype(np.uint8)
    return Image.fromarray(cv2.cvtColor(out, cv2.COLOR_BGR2RGB))


def application(**fields) -> dict:
    base = {
        "beverage_type": "spirits", "brand_name": "", "class_type": "", "alcohol_content": "",
        "net_contents": "", "bottler": "", "country_of_origin": "",
    }
    base.update(fields)
    return base


OLD_TOM_APP = application(
    brand_name="OLD TOM DISTILLERY",
    class_type="Kentucky Straight Bourbon Whiskey",
    alcohol_content="45% Alc./Vol. (90 Proof)",
    net_contents="750 mL",
    bottler="Old Tom Distillery, Bardstown, KY",
)
OLD_TOM_LABEL = Label(
    brand="OLD TOM DISTILLERY",
    class_type="Kentucky Straight Bourbon Whiskey",
    alcohol="45% Alc./Vol. (90 Proof)",
    net="750 mL",
    bottler="Distilled and Bottled by Old Tom Distillery, Bardstown, Kentucky",
)

SAMPLES = [
    Sample("01_old_tom_bourbon.png", OLD_TOM_APP, OLD_TOM_LABEL,
           note="All data on the label is the same as the application. This is the example from the brief."),
    Sample(
        "02_stones_throw_gin.png",
        application(brand_name="Stone's Throw", class_type="London Dry Gin",
                    alcohol_content="41.2% ABV", net_contents="1 L",
                    bottler="Stone's Throw Spirits Co., Portland, OR"),
        Label(brand="STONE'S THROW", class_type="London Dry Gin", alcohol="41.2% ALC/VOL",
              net="1 LITER", bottler="Bottled by Stone's Throw Spirits Co., Portland, Oregon",
              palette=("#e8f0ee", "#14332e", "#2f6f62")),
        note="The brand on the label is in capital letters. The application uses capital letters only at the start of words.",
    ),
    Sample(
        "03_abv_mismatch.png",
        OLD_TOM_APP | {"brand_name": "OLD TOM DISTILLERY"},
        Label(**{**OLD_TOM_LABEL.__dict__, "alcohol": "40% Alc./Vol. (80 Proof)"}),
        note="The label shows 40% alcohol. The application shows 45%.", expected="fail",
    ),
    Sample(
        "04_warning_title_case.png",
        application(brand_name="RIVERBEND", class_type="Straight Rye Whisky",
                    alcohol_content="50% Alc./Vol.", net_contents="750 mL",
                    bottler="Riverbend Distilling, Louisville, KY"),
        Label(brand="RIVERBEND", class_type="Straight Rye Whisky", alcohol="50% Alc./Vol.",
              net="750 mL", bottler="Bottled by Riverbend Distilling, Louisville, Kentucky",
              warning=WARNING.replace("GOVERNMENT WARNING", "Government Warning"),
              palette=("#f3ece6", "#3a1212", "#7d2020")),
        note="The warning heading is “Government Warning”. It is not in capital letters.", expected="fail",
    ),
    Sample(
        "05_warning_not_bold.png",
        application(brand_name="HARBOR LIGHT", class_type="Spiced Rum", alcohol_content="35% Alc./Vol.",
                    net_contents="750 mL", bottler="Harbor Light Rum Co., Key West, FL"),
        Label(brand="HARBOR LIGHT", class_type="Spiced Rum", alcohol="35% Alc./Vol. (70 Proof)",
              net="750 mL", bottler="Bottled by Harbor Light Rum Co., Key West, Florida",
              header_bold=False, palette=("#eef3f8", "#0f2236", "#245a8a")),
        note="The heading “GOVERNMENT WARNING:” is not in bold type.", expected="review",
    ),
    Sample(
        "06_warning_reworded.png",
        application(brand_name="SILVER CREEK", class_type="Vodka", alcohol_content="40% Alc./Vol.",
                    net_contents="1.75 L", bottler="Silver Creek Spirits, Boise, ID"),
        Label(brand="SILVER CREEK", class_type="Vodka", alcohol="40% Alc./Vol. (80 Proof)",
              net="1.75 L", bottler="Distilled by Silver Creek Spirits, Boise, Idaho",
              warning=WARNING.replace(" because of the risk of birth defects", "")
                             .replace("impairs your ability", "may impair your ability"),
              palette=("#f2f2f2", "#1b1b1b", "#6b6b6b")),
        note="The warning text is changed. A phrase is removed, and the word “impairs” is different.", expected="fail",
    ),
    Sample(
        "07_imported_wine.png",
        application(beverage_type="wine", brand_name="Château Belle Rive", class_type="Bordeaux Red Wine",
                    alcohol_content="13.5% Alc./Vol.", net_contents="750 mL",
                    bottler="Imported by Vineyard Imports LLC, New York, NY",
                    country_of_origin="France"),
        Label(brand="CHATEAU BELLE RIVE", class_type="Bordeaux Red Wine", alcohol="13.5% Alc./Vol.",
              net="750 ML", bottler="Imported by Vineyard Imports LLC, New York, NY",
              origin="Product of France", palette=("#f7f1f3", "#3b0d1d", "#7a1f3d")),
        note="Imported wine. The application shows “Château” with an accent. The label shows “CHATEAU”.",
        expected="review",
    ),
    Sample(
        "08_craft_beer.png",
        application(beverage_type="beer", brand_name="Hoppy Trails", class_type="India Pale Ale",
                    alcohol_content="6.8% ABV", net_contents="355 mL",
                    bottler="Hoppy Trails Brewing Co., Bend, OR"),
        Label(brand="HOPPY TRAILS", class_type="India Pale Ale", alcohol="6.8% ALC. BY VOL.",
              net="12 FL. OZ.", bottler="Brewed and Canned by Hoppy Trails Brewing Co., Bend, Oregon",
              palette=("#fdf6e3", "#233d1f", "#4f7d2a"), brand_font="condensed-bold"),
        note="The label gives the net contents in fluid ounces. The application gives them in milliliters.",
    ),
    Sample("09_photo_angle_glare.jpg", OLD_TOM_APP, OLD_TOM_LABEL, photo="angle",
           note="A photograph at an angle, with glare and dark areas."),
    Sample(
        "10_net_contents_mismatch.png",
        application(brand_name="NORTHERN PEAK", class_type="Blended Scotch Whisky",
                    alcohol_content="43% Alc./Vol.", net_contents="750 mL",
                    bottler="Imported by Highland Trade Co., Boston, MA",
                    country_of_origin="Scotland"),
        Label(brand="NORTHERN PEAK", class_type="Blended Scotch Whisky", alcohol="43% Alc./Vol.",
              net="700 mL", bottler="Imported by Highland Trade Co., Boston, MA",
              origin="Product of Scotland", palette=("#eef1ea", "#1f2a1a", "#55693d")),
        note="The label shows 700 mL. The application shows 750 mL.", expected="fail",
    ),
    Sample("11_photo_sideways.jpg", OLD_TOM_APP, OLD_TOM_LABEL, photo="sideways",
           note="A photograph that is turned 90 degrees."),
    Sample(
        "12_front_label.png",
        application(beverage_type="wine", brand_name="WILLOW BEND", class_type="Pinot Noir",
                    alcohol_content="13.9% Alc./Vol.", net_contents="750 mL",
                    bottler="Willow Bend Cellars, Dundee, OR"),
        Label(brand="WILLOW BEND", class_type="Pinot Noir", alcohol="13.9% Alc./Vol.", net="750 mL",
              bottler="Produced and Bottled by Willow Bend Cellars, Dundee, Oregon",
              palette=("#f4f1ea", "#2a2140", "#5b4a8a")),
        back="12_back_label.png",
        note="Two pictures. The brand is on the front label, and the warning is on the back label.",
    ),
    Sample(
        "13_side_panel.png",
        application(beverage_type="beer", brand_name="COPPER KETTLE", class_type="Amber Ale",
                    alcohol_content="5.5% ABV", net_contents="16 fl oz",
                    bottler="Copper Kettle Brewing, Asheville, NC"),
        Label(brand="COPPER KETTLE", class_type="Amber Ale", alcohol="5.5% ALC/VOL", net="16 FL OZ",
              bottler="Brewed and canned by Copper Kettle Brewing, Asheville, North Carolina",
              palette=("#fbf3e6", "#3a2210", "#a4521c")),
        side=True,
        note="The bottler and the warning go up a narrow side panel, as on many cans.",
    ),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for sample in SAMPLES:
        panels = [(sample.file, "front" if sample.back else "full")]
        if sample.back:
            panels.append((sample.back, "back"))
        for file, panel in panels:
            img = with_side_strip(sample.label) if sample.side else render(sample.label, panel=panel)
            if sample.photo:
                img = photograph(img, sample.photo)
            path = OUT / file
            if path.suffix == ".jpg":
                img.save(path, quality=88)
            else:
                img.save(path, optimize=True)
        label_file = ";".join(f for f, _ in panels)
        rows.append({"label_file": label_file, **sample.application, "notes": sample.note})
        print(f"wrote {path.relative_to(OUT.parent)}  ({sample.note})")

    with open(OUT.parent / "batch_applications.csv", "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print("wrote batch_applications.csv")


if __name__ == "__main__":
    main()
