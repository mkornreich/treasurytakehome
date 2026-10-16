# Label Check: alcohol label verification prototype

Label Check compares a picture of an alcohol beverage label with what the applicant typed into the COLA application, and says in plain language what matches, what doesn't, and what a person should look at. It handles one label at a time or a few hundred at once.

**Deployed prototype:** _add the URL here after deploying (see [Deploying](#deploying))_

- **Fast.** Clean labels come back in about 0.3 s. Angled, glary or sideways phone photos take 1–2 s. Real approved labels from the TTB registry take 2.8 s median and 3.2 s at worst, on a single CPU.
- **Tested on real labels.** On 21 approved labels from TTB's Public COLA Registry, it raised **no false rejections** and **missed nothing**, including every deliberately tampered value. 78% of checks were confirmed automatically; the rest were honestly marked "Check". See [eval/REPORT.md](eval/REPORT.md).
- **Works behind the firewall.** Reading is done by Tesseract OCR on the server itself, and the page loads nothing from outside sites. An optional AI reader (Claude vision) can help with hard photos when the network allows it, but the app never depends on it.
- **Judgment, not just string matching.** "STONE'S THROW" matches "Stone's Throw". "750 mL" matches "75 cl", and "12 FL. OZ." matches "355 mL". A missing accent or a near-miss is sent to a person, not rejected.
- **Strict where the rules are strict.** The government warning must be word-for-word, with "GOVERNMENT WARNING:" in capitals and bold. The tool shows a word-level diff when it isn't.
- **Simple to use.** Large text, one obvious button per step, and results in sentences ("Label shows 40%; the application says 45%"). Click any result to see where it was found on the label.
- **Batch mode.** Drop in a folder of pictures plus a CSV of applications. The results stream in as each label finishes, can be filtered to problems, and can be downloaded as a CSV.
- **Nothing is stored.** Pictures and application data exist in memory for the length of one request.

---

## Quick start

### With Docker (simplest)

```bash
docker build -t label-check .
```

```bash
docker run --rm -p 8000:8000 label-check
```

Open <http://localhost:8000>. Pick a sample label from **"No picture handy? Try a sample"**, or open **Check many labels → Load the sample batch**.

### Without Docker

Needs Python 3.12+, [uv](https://docs.astral.sh/uv/), and the Tesseract binary (`sudo apt install tesseract-ocr` on Debian/Ubuntu, or `brew install tesseract` on macOS).

```bash
uv sync
```

```bash
uv run uvicorn app.main:app --port 8000
```

### Run the tests

```bash
uv run pytest
```

There are 93 tests: unit tests for parsing and rules, regression tests for every failure found on real labels, the AI reader against a fake client, the HTTP API, and end-to-end runs over every sample label with real OCR. The end-to-end runs also assert each label finishes in under 5 seconds.

### Run the evaluation on real labels

```bash
uv run python eval/fetch_cola.py
```

```bash
uv run python eval/run_eval.py --compare eval/results/after.json
```

The first command downloads 21 approved labels from TTB's Public COLA Registry. They are kept out of git, because the artwork belongs to the brand owners. The second scores the app against hand-checked ground truth in [eval/real_labels.csv](eval/real_labels.csv), pinned to one CPU. [eval/REPORT.md](eval/REPORT.md) has the results and what they led to.

---

## Using it

### One label

1. **Add the label picture.** Drag it in or press *Choose picture…*. If the warning is on the back label, add the back picture too: all pictures for one application are read together.
2. **Type what the application says.** Leave a box empty to skip that check. The government warning is always checked.
3. **Press *Check label*.** The verdict comes first ("All 8 checks passed", "1 problem found", "2 items need a closer look"). Below it is one card per check, and the label with the selected item highlighted.

| Result | Meaning |
|---|---|
| ✓ **OK** | The label shows what the application says. |
| ! **Check** | The tool could not be sure: the text was unreadable, a near-match, differs only by accents, or the two readers disagree. A person should look. |
| ✕ **Problem** | The tool read something that conflicts with the application or the rules: a different number, or warning wording that is not word-for-word. |

### Many labels

1. Drag in all the pictures, or *Choose a folder…*.
2. Add the applications spreadsheet (CSV). [`samples/batch_applications.csv`](samples/batch_applications.csv) is an example. Columns:

   | column | example |
   |---|---|
   | `label_file` (required) | `old_tom.jpg`, or `front.jpg;back.jpg` for several pictures |
   | `beverage_type` | `spirits`, `wine` or `beer` |
   | `brand_name` | `OLD TOM DISTILLERY` |
   | `class_type` | `Kentucky Straight Bourbon Whiskey` |
   | `alcohol_content` | `45% Alc./Vol. (90 Proof)` |
   | `net_contents` | `750 mL` |
   | `bottler` | `Old Tom Distillery, Bardstown, KY` |
   | `country_of_origin` | `France` (imports only) |
   | `application_id` (optional) | carried through to the results CSV |

   Common alternative headers (`brand`, `abv`, `filename`, …) and semicolon-separated files from European Excel are accepted. Without a spreadsheet, each picture is still checked for the warning, alcohol content and net contents.
3. Press *Start checking*. Four labels are checked at a time, with progress and a time-left estimate. Filter to *Problems* or *Needs review*, open any row for the full result, and use *Download results (CSV)* for the record.

---

## How it works

```
 picture(s) ──► decode + EXIF rotate ──► OCR round 1 (grayscale) ──► rules ──► all OK? ──► answer
                                             │  sideways? → rotate (Tesseract OSD)          │ no
                                             │  tilted?   → deskew from text-line slopes    ▼
                                             ▼                                   more OCR rounds while time allows:
                                     [optional] AI reader ◄── started only when   • darkest colour channel + local contrast (glare)
                                     (Claude vision, JSON)    OCR can't confirm   • adaptive binarization (uneven light)
                                             │                everything          • contrast-equalized grayscale
                                             └────────────────► rules over everything read ──► answer
```

**1. Reading** ([`app/extraction/`](app/extraction))
- [`ocr.py`](app/extraction/ocr.py) runs Tesseract 5 (LSTM) in sparse-text mode and keeps every word with its position. Positions are mapped back through every rotation and resize ([`imaging.View`](app/imaging.py)), so highlights land on the original picture.
- The pipeline ([`pipeline.py`](app/pipeline.py)) works in rounds and **stops as soon as every check passes**. An easy label costs one ~0.3 s pass. A hard one gets more passes within a 3-second OCR budget, each a different take:
  - an auto-layout pass that finds text boxed in tables;
  - the darkest colour channel with local contrast, which recovers coloured ink washed out by glare;
  - turned 90°/270°, for text running up a side panel;
  - binarized, for uneven light;
  - turned 180°, for keg collars.
- **A second look at the warning.** If the government warning isn't confirmed, its paragraph is cropped out, enlarged and re-read as one block. Sparse-text OCR otherwise tends to splice a neighbouring column or barcode digits into it.
- **Is the heading bold?** OCR doesn't report font weight, so [`typography.py`](app/extraction/typography.py) measures it. Each word is scaled to a fixed letter height and binarized, and a distance transform gives the stroke width. "GOVERNMENT WARNING:" is compared with the regular words after it. On approved labels, bold headings measure 1.2–1.8× as thick, and regular-weight ones about 1.0×. A heading too small to measure is reported as "could not tell".
- [`vision.py`](app/extraction/vision.py) is the optional AI reader. It asks Claude for a **verbatim** transcription (structured JSON) and is told never to correct text, not even the standard warning.

**2. Deciding** ([`app/verification/`](app/verification))

The rules are deterministic. **The AI reads; the rules decide**, so every outcome can be explained and repeated.

| Check | Rule |
|---|---|
| Brand name, class/type | Fuzzy search over everything read, ignoring case, punctuation and spacing. Exact → OK, with a note when only the capitalization differs. Accent-only difference or ≥90 % similar → Check. Not found → Check, because OCR missing a decorative font is not evidence the label is wrong. Only the AI reader's explicit, different reading of the field → Problem. |
| Bottler, country of origin | As above, and the application text may sit inside a longer statement ("Bottled by …", "Product of France"). Name and town are matched separately, so a street address printed between them is fine. State abbreviations are normalized ("Bardstown, KY" = "Bardstown, Kentucky"). An origin statement naming another country ("Produced and bottled in Italy" when the application says France) is reported. |
| Alcohol content | Compared by value: 45 % = "45.0% ABV". A different percentage on the label → Problem. A proof on the label must equal the application's proof, and must be twice the ABV. A number printed without its % sign ("53.5 ALC/VOL") → Check. "100% agave" is not mistaken for the alcohol statement. |
| Net contents | Converted to millilitres, so mL, cl, L, fl oz, pints, quarts and gallons all compare (1 % tolerance for rounded conversions). A different volume → Problem. |
| Government warning: wording | The statement is located by fuzzy alignment, then compared word by word with 27 CFR 16.21. Missing, added or changed words → Problem, with a diff. Things a scanner gets wrong are a Check instead: a one-letter difference inside a word ("rnachinery"), digit-for-letter swaps ("T0"), an unreadable "(1)", a dropped short word, punctuation, or stray marks. Spacing and hyphenation ("ABILITYTO", "wom- en") are ignored. All-caps body text is accepted. |
| Government warning: capitals | "GOVERNMENT WARNING" must be uppercase ("Government Warning" → Problem). A missing colon → Check. The heading is located separately from the body, so a heading on its own line that OCR reads out of order still counts. |
| Government warning: bold | Bold → OK. Not bold → Check, or Problem when both readers agree it isn't bold. |

**Several readers, one answer.** A field is OK if *any* reader (any OCR pass, any picture, the AI) confirmed it, because OCR never invents an exact match. It is a Problem only if a conflict was seen by **two separate readings**, or by the AI reader, and nothing confirmed the application. A conflict seen once ("150 ML" where the label says 750) is a possible misread and needs review. So does a conflict between digits Tesseract often confuses (3/8, 5/6, 1/7…), even when it is read twice. This is what took false rejections on real labels from 24 to zero. The warning has one extra safeguard: if the AI reader and OCR disagree on pass vs. fail, and OCR read the statement clearly, the result becomes **Check**. That catches a model that silently "fixes" a non-compliant warning, which is the failure mode that matters most here.

---

## How each stakeholder concern was addressed

| What we heard | What was built |
|---|---|
| "If we can't get results back in about 5 seconds, nobody's going to use it." (Sarah) | Local OCR with early exit: ~0.3 s for clean labels, 1–2 s for hard photos. The AI reader is only consulted when OCR can't confirm everything, and gets a hard 4.5 s deadline. If it is slow or unreachable, the answer comes from OCR with a notice. Large photos are shrunk in the browser before upload. |
| "Something my mother could figure out." Half the team is over 50. (Sarah, Dave) | 18 px base type, high contrast, numbered steps, one primary button, results as sentences with ✓/!/✕ *and* words (never colour alone), click-to-highlight on the label, a "Look at:" jump list in the verdict, keyboard and screen-reader friendly, works on a phone. |
| Batch uploads of 200–300 labels. (Sarah, Janet) | *Check many labels*: a folder of pictures plus a CSV, four at a time, live progress with a time estimate, filters, and CSV export. The server stays stateless, so nothing is queued or stored on it. |
| Outbound traffic to many domains is blocked. (Marcus) | All reading happens on the server; the page loads no CDN fonts or scripts. The AI reader is off unless configured, and any failure falls back to OCR. In production it could point at a model inside the agency's own Azure boundary (Claude is available through Microsoft Foundry). |
| Not storing anything sensitive; PII considerations. (Marcus) | No database, no disk writes; uploads are processed in memory and discarded. Logs record only outcome and timing, never label or application content. |
| "STONE'S THROW" vs "Stone's Throw" needs judgment. (Dave) | Case and punctuation differences are an OK with a note. Accent-only and near-misses go to a person instead of being rejected. |
| The warning must be exact, "GOVERNMENT WARNING:" in caps and bold. (Jenny) | A dedicated word-level comparison with a visual diff, a separate capitals check, and a measured bold check. |
| Photos at weird angles, bad lighting, glare. (Jenny) | Automatic rotation (sideways photos), deskewing, glare-resistant colour-channel passes, adaptive thresholding, plus plain-language photo-quality notices (blurry / dark / glare / low resolution). |
| Standalone proof of concept, no COLA integration. (Marcus) | A self-contained container with a small JSON API ([below](#api)) that a future COLA integration could call. |

---

## Configuration

All optional, set as environment variables.

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | unset | Enables the AI reader (Claude vision) for hard-to-read labels. |
| `VISION` | `auto` | `auto` (= `fallback` when a key is set, else `off`), `fallback` (ask the AI only when OCR can't confirm everything), `always` (every label), or `off`. |
| `VISION_MODEL` | `claude-opus-5-5` | Model for the AI reader. `claude-haiku-5-5` is faster and cheaper if latency matters more. |
| `VISION_EFFORT` | `low` | Reasoning effort for the AI reader; transcription doesn't need more. |
| `VISION_TIMEOUT_SECONDS` | `4.5` | How long to wait for the AI reader before answering without it. |
| `OCR_BUDGET_SECONDS` | `3.0` | Time allowed for extra OCR passes on hard photos. |
| `OCR_WORKERS` | CPUs available | Parallel Tesseract processes (container CPU limits are respected). |
| `MAX_UPLOAD_MB` | `15` | Per-picture upload limit. |
| `PORT` | `8000` | Port inside the container. |

## API

Interactive docs are served at `/api/docs`.

```bash
curl -s -X POST http://localhost:8000/api/verify \
  -F "images=@samples/labels/03_abv_mismatch.png" \
  -F 'application={"brand_name":"OLD TOM DISTILLERY","alcohol_content":"45% Alc./Vol.","net_contents":"750 mL"}'
```

The response is an `overall` status (`pass` / `review` / `fail`), a `headline`, and one entry per check with `status`, `message`, `expected`, `found` and `boxes`. The boxes are where it was found, as fractions of the picture's width and height. The response also includes `notices` about photo quality or the AI reader, the engines used, and timings. Other endpoints: `GET /api/health`, `GET /api/config`, `GET /api/samples`.

---

## Sample labels

[`samples/labels/`](samples/labels) holds 14 synthetic label images (13 applications) from [`samples/generate_labels.py`](samples/generate_labels.py). Each one pairs an application with a label rendered from slightly different data, to exercise one rule:

| Sample | Expected |
|---|---|
| `01_old_tom_bourbon` (the example from the brief) | ✓ all OK |
| `02_stones_throw_gin` (brand in capitals vs. title case) | ✓ OK, with a capitalization note |
| `03_abv_mismatch` (40 % vs. 45 %) | ✕ alcohol content |
| `04_warning_title_case` ("Government Warning") | ✕ capitals |
| `05_warning_not_bold` | ! bold heading |
| `06_warning_reworded` (phrase removed, "impairs" → "may impair") | ✕ wording, with diff |
| `07_imported_wine` ("Château" vs. "CHATEAU") | ! accents differ |
| `08_craft_beer` (12 FL. OZ. vs. 355 mL) | ✓ OK by unit conversion |
| `09_photo_angle_glare` (perspective, tilt, glare, shading, noise) | ✓ all OK |
| `10_net_contents_mismatch` (700 vs. 750 mL) | ✕ net contents |
| `11_photo_sideways` (rotated 90°) | ✓ all OK |
| `12_front_label` + `12_back_label` (warning only on the back) | ✓ all OK when both pictures are given |
| `13_side_panel` (bottler and warning run up a side strip) | ✓ all OK (read by the rotated passes) |

Regenerate them with `uv run python samples/generate_labels.py`. Real photos and AI-generated label images work the same way; just upload them.

---

## Assumptions

- The tool compares the **label against the application**, not against lab analysis. Values must therefore match exactly (45 % ≠ 45.5 %); the TTB tolerances for actual alcohol content don't apply.
- Fields left blank on the application are not checked. Alcohol content and net contents are still reported if found, and a missing alcohol statement is flagged for spirits.
- The warning text is the statutory one in 27 CFR 16.21. An all-caps body is acceptable; the heading must be capitals and bold.
- When the tool can't read something, the answer is "Check", never a silent pass and never an automatic rejection. The agent always makes the final decision.

## Limitations and trade-offs

- **OCR limits, measured.** On 21 real approved labels, 22% of checks still come back as *Check* although the label is fine ([eval/REPORT.md](eval/REPORT.md)). The causes:
  - script or ornate brand names;
  - net contents in tiny, condensed or light type;
  - warning punctuation and numbering that can't be confirmed at small sizes;
  - accents, which Tesseract's English model drops.

  Text curving around a bottle, embossing and very low-resolution images are also out of reach. The optional AI reader is much better at all of these; it is off by default only because of the firewall constraint.
- **A one-letter typo looks like a misread.** One approved label prints "CONSUMTION". The tool shows the difference and asks for a look, but OCR alone can't tell a typo from its own slip, so it doesn't reject.
- **Caution costs a little on clear-cut mismatches.** A conflicting value read only once becomes *Check* rather than *Problem*. In the tampered-label test, 6 of 21 changed alcohol values were flagged as *Check* instead of *Problem*; none were missed.
- **Bold detection is a measurement, not a certainty.** On its own it can only flag "not bold" for review. Both readers must agree before it becomes a Problem. A heading in a much larger font than the body can look bold to the measurement even if it isn't.
- **Not checked:** minimum type size (1/2/3 mm by container size; a photo has no physical scale), placement, contrast, and "separate and apart" rules. Also not checked: beverage-specific statements such as sulfites, appellation, vintage, age statements, colour additives and "table wine" alternatives, and whether a mention is in the brand position rather than elsewhere on the label.
- **Batch runs in the browser.** Closing the tab stops it. That is a deliberate trade for a stateless server with nothing stored; a production version would add a job queue with retention rules.
- **No sign-in or audit trail.** It's a prototype. Production would sit behind the agency's identity provider and log decisions, not content.
- **English labels only** (the Tesseract `eng` model).
- **The AI reader sends pictures to an external API** when enabled. That needs a policy decision, or a model hosted inside the agency's boundary.

---

## Deploying

The app is one stateless container, so any container host works. 1 vCPU / 1 GB RAM is enough: each label takes ≤1.5 s on one CPU. More CPUs speed up batches.

- **Render:** *New → Blueprint →* select this repository ([`render.yaml`](render.yaml) is included). Optionally add `ANTHROPIC_API_KEY`. Use the Starter plan or larger: the free tier's 0.1 CPU is too slow for the 5-second target.
- **Azure Container Apps** (matches the agency's Azure environment):

  ```bash
  az containerapp up --name label-check --resource-group label-check-rg --location eastus --source . --ingress external --target-port 8000
  ```

- **Any Docker host:** `docker run -p 80:8000 label-check`.

## Project layout

```
app/
  main.py               HTTP API + static files
  pipeline.py           orchestration: OCR rounds with early exit, optional AI reader, rules
  config.py             environment settings
  models.py             request/response models
  imaging.py            decoding, geometry-tracking views, clean-up filters, photo-quality hints
  extraction/
    ocr.py              Tesseract passes, rotation and skew detection
    typography.py       bold-heading measurement
    vision.py           optional Claude vision reader
  verification/
    evidence.py         common shape for anything a reader saw
    text.py             normalization and fuzzy phrase search
    units.py            alcohol/proof and volume parsing
    warning.py          government warning alignment and diff
    rules.py            the checks and how readers' results combine
  static/               index.html, app.css, app.js (no build step, no external assets)
samples/                synthetic labels, batch CSV, generator
eval/                   real-label evaluation: registry fetcher, ground truth, scorer, report
tests/                  unit, API, vision (mocked), real-label regression and end-to-end tests
```

## Tools used

Python 3.12, FastAPI, Tesseract 5 via `pytesseract`, OpenCV and Pillow for image processing, RapidFuzz for fuzzy matching, and the Anthropic Python SDK for the optional AI reader. The front end is plain HTML/CSS/JavaScript. Tests use pytest. The sample labels are rendered with Pillow and "photographed" with OpenCV.
