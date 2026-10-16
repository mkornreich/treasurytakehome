# Label Check: alcohol label verification prototype

Label Check compares a picture of an alcohol beverage label with what the applicant typed into the COLA application, and says in plain language what matches, what doesn't, and what a person should look at. It handles one label at a time or a few hundred at once.

**Deployed prototype:** <https://labelcheck.mkornreich.me> (also at <https://label-check-fz5a.onrender.com>)

- **Fast.** The agent's browser reads the label first, so a clean label or a sideways photo comes back in about 0.5 s however slow the server is. Labels that need a closer look are read again on the server: on one CPU core, 1–2 s for angled or glary photos, and 3.1 s median and 3.8 s at worst for real approved labels from the TTB registry ([details](#where-the-work-is-done-browser-and-server)).
- **Tested on real labels.** On 21 approved labels from TTB's Public COLA Registry, it raised **no false rejections** and **missed nothing**, including every deliberately tampered value. 78% of checks were confirmed automatically; the rest were honestly marked "Examine". The browser-only path does as well (80%, also with no false rejections and nothing missed). See [eval/REPORT.md](eval/REPORT.md).
- **Works behind the firewall, and without the server.** Reading is done by Tesseract OCR: in the browser (tesseract.js, served by this app) and on the server. The page loads nothing from outside sites. If the server does not answer, or the page is served as plain static files, the browser does the whole check, with the same rules, tested to give the same answers. An optional AI reader (Claude vision) can help with hard photos when the network allows it, but the app never depends on it.
- **Judgment, not just string matching.** "STONE'S THROW" matches "Stone's Throw". "750 mL" matches "75 cl", and "12 FL. OZ." matches "355 mL". A missing accent or a near-miss is sent to a person, not rejected.
- **Strict where the rules are strict.** The government warning must be word-for-word, with "GOVERNMENT WARNING:" in capitals and bold. The tool shows a word-level diff when it isn't.
- **Plain, controlled language.** All text on the page, including every result message, follows ASD-STE100 Simplified Technical English: short sentences, approved verbs, one word for one meaning. It was checked with the [SimpleEnglish](https://github.com/AminBlg/SimpleEnglish) STE linter; no tool can guarantee full compliance.
- **Simple to use, and accessible.** Large text, one obvious button per step, and results in sentences ("Label shows 40%; the application says 45%"). *Show on the label* highlights where each item was found; click any picture to see it larger. It meets WCAG 2.2 AA in automated and manual checks ([details](#accessibility-wcag-22-aa)).
- **Batch mode.** Drop in a folder of pictures plus a CSV of applications. The results stream in as each label finishes, can be filtered to problems, and can be downloaded as a CSV.
- **Nothing is stored.** Pictures and application data exist in memory for the length of one request. When the browser confirms every item, the picture never leaves the agent's computer.

**Contents:** [Setup and run](#quick-start) · [Using it](#using-it) · [Approach](#approach-how-it-works) · [Browser and server](#where-the-work-is-done-browser-and-server) · [Stakeholder concerns](#how-each-stakeholder-concern-was-addressed) · [Accessibility](#accessibility-wcag-22-aa) · [Assumptions](#assumptions) · [Limitations and trade-offs](#limitations-and-trade-offs) · [Deploying](#deploying) · [Tools used](#tools-used)

---

## Quick start

### With Docker (simplest)

```bash
docker build -t label-check .
```

```bash
docker run --rm -p 8000:8000 label-check
```

Open <http://localhost:8000>. Pick a sample label from **"If you do not have a picture, use a sample"**, or open **Check many labels → Use the sample labels**.

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

There are 101 tests: unit tests for parsing and rules, regression tests for every failure found on real labels, the AI reader against a fake client, the HTTP API, end-to-end runs over every sample label with real OCR (each must finish in under 5 seconds), and parity tests that run the browser's JavaScript rules and image measurements against the Python ones. The parity tests need [Node.js](https://nodejs.org/) 18 or later and are skipped without it.

### Run the evaluation on real labels

```bash
uv run python eval/fetch_cola.py
```

```bash
uv run python eval/run_eval.py --compare eval/results/after.json
```

The first command downloads 21 approved labels from TTB's Public COLA Registry. They are kept out of git, because the artwork belongs to the brand owners. The second scores the app against hand-checked ground truth in [eval/real_labels.csv](eval/real_labels.csv), pinned to one CPU. [eval/REPORT.md](eval/REPORT.md) has the results and what they led to.

To score the browser-only path the same way (in headless Chrome, which must be installed):

```bash
uv run python eval/run_browser_eval.py --compare eval/results/after.json
```

---

## Using it

### One label

1. **Add the label picture.** Drag it in or press *Choose picture…*. If the warning is on the back label, add the back picture too: all pictures for one application are read together.
2. **Type what the application says.** Leave a box empty to skip that check. The government warning is always checked.
3. **Press *Check label*.** The verdict comes first ("All 8 checks are OK", "1 problem found", "2 items to examine"). Below it is one card per check, and the label with the selected item highlighted. *Show on the label* on a card highlights that item; a click on any picture shows it larger.

| Result | Meaning |
|---|---|
| ✓ **OK** | The label shows what the application says. |
| ! **Examine** | The tool could not be sure: the text was unreadable, a near-match, differs only by accents, or the two readers disagree. A person should look. |
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
3. Press *Check N labels*. Four labels are checked at a time (when the browser does all the work, one per browser worker, up to three), with progress and a time-left estimate. Filter to *Problems* or *Examine*, open any row for the full result, and use *Download results (CSV)* for the record.

---

## Approach: how it works

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
- The pipeline ([`pipeline.py`](app/pipeline.py)) works in rounds and **stops as soon as every check passes**. An easy label costs one ~0.3 s pass. A hard one gets more passes within a 3.5-second OCR budget, each a different take:
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
| Brand name, class/type | Fuzzy search over everything read, ignoring case, punctuation and spacing. Exact → OK, with a note when only the capitalization differs. Accent-only difference or ≥90 % similar → Examine. Not found → Examine, because OCR missing a decorative font is not evidence the label is wrong. Only the AI reader's explicit, different reading of the field → Problem. |
| Bottler, country of origin | As above, and the application text may sit inside a longer statement ("Bottled by …", "Product of France"). Name and town are matched separately, so a street address printed between them is fine. State abbreviations are normalized ("Bardstown, KY" = "Bardstown, Kentucky"). An origin statement naming another country ("Produced and bottled in Italy" when the application says France) is reported. |
| Alcohol content | Compared by value: 45 % = "45.0% ABV". A different percentage on the label → Problem. A proof on the label must equal the application's proof, and must be twice the ABV. A number printed without its % sign ("53.5 ALC/VOL") → Examine. "100% agave" is not mistaken for the alcohol statement. |
| Net contents | Converted to millilitres, so mL, cl, L, fl oz, pints, quarts and gallons all compare (1 % tolerance for rounded conversions). A different volume → Problem. |
| Government warning: wording | The statement is located by fuzzy alignment, then compared word by word with 27 CFR 16.21. Missing, added or changed words → Problem, with a diff. Things a scanner gets wrong are an Examine result instead: a one-letter difference inside a word ("rnachinery"), digit-for-letter swaps ("T0"), an unreadable "(1)", a dropped short word, punctuation, or stray marks. Spacing and hyphenation ("ABILITYTO", "wom- en") are ignored. All-caps body text is accepted. |
| Government warning: capitals | "GOVERNMENT WARNING" must be uppercase ("Government Warning" → Problem). A missing colon → Examine. The heading is located separately from the body, so a heading on its own line that OCR reads out of order still counts. |
| Government warning: bold | Bold → OK. Not bold → Examine, or Problem when both readers agree it isn't bold. |

**Several readers, one answer.** A field is OK if *any* reader (any OCR pass, any picture, the AI) confirmed it, because OCR never invents an exact match. It is a Problem only if a conflict was seen by **two separate readings**, or by the AI reader, and nothing confirmed the application. A conflict seen once ("150 ML" where the label says 750) is a possible misread and needs review. So does a conflict between digits Tesseract often confuses (3/8, 5/6, 1/7…), even when it is read twice. This is what took false rejections on real labels from 24 to zero. The warning has one extra safeguard: if the AI reader and OCR disagree on pass vs. fail, and OCR read the statement clearly, the result becomes **Examine**. That catches a model that silently "fixes" a non-compliant warning, which is the failure mode that matters most here.

---

## Where the work is done: browser and server

The same Tesseract engine and English model run in two places: natively on the server, and in the agent's browser as [tesseract.js](https://github.com/naptha/tesseract.js) 7 (WebAssembly). The browser files are served by this app from [`app/static/vendor/`](app/static/vendor/tesseract-7.0.0/README.md); nothing comes from another site. The English model is the tessdata_fast 4.1.0 file that the server's `tesseract-ocr-eng` package installs.

```
                        ┌── all OK ──► answer (only words and positions were sent; the picture stayed in the browser)
 browser reads once ──► server rules ┤
 (upright if sideways)  /api/verify-readings
                        └── else ────► server reads the pictures again with every pass (/api/verify) ──► answer

 server does not answer, or plain static files ──► browser runs every pass and the rules itself ──► answer
```

| Situation | What happens |
|---|---|
| Normal | The browser reads each picture once, turned upright first if it is sideways. It sends the words, their positions and the measured heading weight to `POST /api/verify-readings`, and the server applies the rules. If every check is OK, that is the answer. Otherwise the page sends the pictures to `POST /api/verify`, and the server reads them again with all of its passes. |
| The server does not answer, or the page is plain static files | The browser does everything: the same rounds of passes (straightened, glare channel, layout, rotations, binarized, a second look at the warning), the same 3.5 s budget, and the same rules. The result says "The server did not answer. The browser examined the label." The page tries the server again after a minute. |
| The browser cannot run it (no WebAssembly SIMD, or the files are blocked) | The page sends the pictures to the server, as before. |

**The same answers in both places.** [`app/static/local/`](app/static/local) ports the rules (`rules.py`, `warning.py`, `units.py`, `text.py`) and the matching functions they use: RapidFuzz's `ratio` and `partial_ratio_alignment`, ported from the exact C++ the server runs, and `difflib.SequenceMatcher`. It also ports the image work (`imaging.py`, and the bold measurement in `typography.py`). [`tests/test_local_parity.py`](tests/test_local_parity.py) runs both versions on more than 650 cases and requires identical results: status, message, values, highlight boxes and warning diff. The cases come from every reading pass the server makes on every sample, with the real applications, altered applications, and doctored readings. A second test compares the bold measurement and the photo notices on the samples.

**Measured** on the 13 samples (Chrome on a Ryzen AI 9 HX 370 laptop, October 2026):

| | Clean labels, sideways and side-panel photos | Glare photo | Labels with problems |
|---|---|---|---|
| Normal (browser reads, server decides) | 0.4–0.7 s | 1.7 s (read again on the server) | 2.4–3.5 s (read again on the server) |
| Browser only | 0.4–0.6 s | 1.6 s | 2.6–3.6 s |
| Server only, on the deployed half-CPU instance | 1.3–5.4 s | 4.8 s | 2.8–3.9 s |

Every result was the same in all three. A browser-only batch of all 13 samples took 16 s, again with identical results. In a batch nobody waits for one label, so the browser runs every pass a label needs instead of stopping at 3.5 s.

---

## How each stakeholder concern was addressed

| What we heard | What was built |
|---|---|
| "If we can't get results back in about 5 seconds, nobody's going to use it." (Sarah) | The browser reads the label first: about 0.5 s for a clean label or a sideways photo, whatever the server's load. Labels that need more work get local OCR rounds with early exit, inside a 3.5 s budget. The AI reader is only consulted when OCR can't confirm everything, and gets a hard 4.5 s deadline. If it is slow or unreachable, the answer comes from OCR with a notice. Large photos are shrunk in the browser before upload. |
| "Something my mother could figure out." Half the team is over 50. (Sarah, Dave) | 18 px base type, high contrast, numbered steps, one primary button, results as sentences with ✓/!/✕ *and* words (never colour alone), *Show on the label* buttons, pictures that open larger with one click, full file names, an "Examine:" jump list in the verdict, and plain controlled English. WCAG 2.2 AA ([below](#accessibility-wcag-22-aa)); works on a phone. |
| Batch uploads of 200–300 labels. (Sarah, Janet) | *Check many labels*: a folder of pictures plus a CSV, four at a time, live progress with a time estimate, filters, and CSV export. The server stays stateless, so nothing is queued or stored on it. |
| Outbound traffic to many domains is blocked. (Marcus) | All reading happens in the browser and on this server; the page loads no CDN fonts, scripts or models. If the server itself is out of reach, the browser does the whole check. The AI reader is off unless configured, and any failure falls back to OCR. In production it could point at a model inside the agency's own Azure boundary (Claude is available through Microsoft Foundry). |
| Not storing anything sensitive; PII considerations. (Marcus) | No database, no disk writes; uploads are processed in memory and discarded. When the browser confirms every item, the picture never leaves the agent's computer. Logs record only outcome and timing, never label or application content. The browser keeps a copy of the scanner and the English model (no label data) so it need not download them again. |
| "STONE'S THROW" vs "Stone's Throw" needs judgment. (Dave) | Case and punctuation differences are an OK with a note. Accent-only and near-misses go to a person instead of being rejected. |
| The warning must be exact, "GOVERNMENT WARNING:" in caps and bold. (Jenny) | A dedicated word-level comparison with a visual diff, a separate capitals check, and a measured bold check. |
| Photos at weird angles, bad lighting, glare. (Jenny) | Automatic rotation (sideways photos), deskewing, glare-resistant colour-channel passes, adaptive thresholding, plus plain-language photo-quality notices (blurry / dark / glare / low resolution). |
| Standalone proof of concept, no COLA integration. (Marcus) | A self-contained container with a small JSON API ([below](#api)) that a future COLA integration could call. |

---

## Accessibility (WCAG 2.2 AA)

The target is [WCAG 2.2](https://www.w3.org/WAI/standards-guidelines/wcag/) Level AA. That also covers WCAG 2.0 AA, which Section 508 requires of federal agencies.

- **Automated:** [axe-core](https://github.com/dequelabs/axe-core) 4.10, with the WCAG 2.0, 2.1 and 2.2 A and AA rules plus best practices, finds no violations. It ran on every state of the page: the empty form, a result, the enlarged picture, batch ready, batch results with a row open, and help.
- **Measured by hand:**
  - text contrast is at least 4.5:1, placeholders included (1.4.3);
  - control borders and the focus ring are at least 3:1 against their background (1.4.11);
  - nothing scrolls sideways at 320 px wide (1.4.10);
  - no text is cut off with WCAG's text-spacing values (1.4.12);
  - every control is at least 24 × 24 px (2.5.8).
- **Keyboard:** everything works without a mouse, with a dark focus ring. Each result card has a *Show on the label* toggle button, and the card itself is no longer a button that holds other controls. The picture viewer is a modal dialog with *Full size* and *Close* buttons, Esc closes it, and focus goes back to where it was.
- **Screen readers:**
  - one `h1`, a heading for each section, and landmarks;
  - labelled controls;
  - short status messages: "The tool is examining the label.", and batch progress at each quarter rather than after every label;
  - focus moves to the result heading when a check finishes, and errors are announced as alerts.
- **Not done:** sessions with people who use screen readers or magnifiers. Automated tools find only part of the WCAG problems, so a production version needs that.

## Configuration

All optional, set as environment variables.

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | unset | Enables the AI reader (Claude vision) for hard-to-read labels. |
| `VISION` | `auto` | `auto` (= `fallback` when a key is set, else `off`), `fallback` (ask the AI only when OCR can't confirm everything), `always` (every label), or `off`. |
| `VISION_MODEL` | `claude-opus-5-5` | Model for the AI reader. `claude-haiku-5-5` is faster and cheaper if latency matters more. |
| `VISION_EFFORT` | `low` | Reasoning effort for the AI reader; transcription doesn't need more. |
| `VISION_TIMEOUT_SECONDS` | `4.5` | How long to wait for the AI reader before answering without it. |
| `OCR_BUDGET_SECONDS` | `3.5` | Time allowed for extra OCR passes on hard photos. |
| `OCR_WORKERS` | CPUs available | Parallel Tesseract processes (container CPU limits are respected). |
| `MAX_UPLOAD_MB` | `15` | Per-picture upload limit. |
| `BROWSER_OCR` | `on` | `off` makes the page send every picture to the server instead of reading it in the browser first. |
| `PORT` | `8000` | Port inside the container. |

To compare the paths, add `?mode=local` to the page address (everything in the browser) or `?mode=server` (every picture to the server).

## API

Interactive docs are served at `/api/docs`.

```bash
curl -s -X POST http://localhost:8000/api/verify \
  -F "images=@samples/labels/03_abv_mismatch.png" \
  -F 'application={"brand_name":"OLD TOM DISTILLERY","alcohol_content":"45% Alc./Vol.","net_contents":"750 mL"}'
```

The response is an `overall` status (`pass` / `review` / `fail`), a `headline`, and one entry per check with `status`, `message`, `expected`, `found` and `boxes`. The boxes are where it was found, as fractions of the picture's width and height. The response also includes `notices` about photo quality or the AI reader, the engines used, and timings.

`POST /api/verify-readings` applies the same rules to text that was already read, with no picture. The browser uses it, and another OCR system could too:

```json
{
  "application": {"brand_name": "OLD TOM DISTILLERY", "net_contents": "750 mL"},
  "readings": [{"image": 0, "words": [{"text": "OLD", "conf": 96, "x": 0.10, "y": 0.08, "w": 0.15, "h": 0.04}],
                "heading_weight": 1.43, "heading_too_small": ""}],
  "ocr_ms": 350
}
```

`heading_weight` is the stroke width of "GOVERNMENT WARNING" divided by that of the warning text, as [`typography.py`](app/extraction/typography.py) measures it. Other endpoints: `GET /api/health`, `GET /api/config`, `GET /api/samples`, and `GET /robots.txt`, which asks every crawler to stay out.

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
- When the tool can't read something, the answer is "Examine", never a silent pass and never an automatic rejection. The agent always makes the final decision.

## Limitations and trade-offs

- **OCR limits, measured.** On 21 real approved labels, 22% of checks still come back as *Examine* although the label is fine ([eval/REPORT.md](eval/REPORT.md)). The causes:
  - script or ornate brand names;
  - net contents in tiny, condensed or light type;
  - warning punctuation and numbering that can't be confirmed at small sizes;
  - accents, which Tesseract's English model drops.

  Text curving around a bottle, embossing and very low-resolution images are also out of reach. The optional AI reader is much better at all of these; it is off by default only because of the firewall constraint.
- **A one-letter typo looks like a misread.** One approved label prints "CONSUMTION". The tool shows the difference and asks for a look, but OCR alone can't tell a typo from its own slip, so it doesn't reject.
- **Caution costs a little on clear-cut mismatches.** A conflicting value read only once becomes *Examine* rather than *Problem*. In the tampered-label test, 6 of 21 changed alcohol values were flagged as *Examine* instead of *Problem*; none were missed.
- **Bold detection is a measurement, not a certainty.** On its own it can only flag "not bold" for review. Both readers must agree before it becomes a Problem. A heading in a much larger font than the body can look bold to the measurement even if it isn't.
- **Not checked:** minimum type size (1/2/3 mm by container size; a photo has no physical scale), placement, contrast, and "separate and apart" rules. Also not checked: beverage-specific statements such as sulfites, appellation, vintage, age statements, colour additives and "table wine" alternatives, and whether a mention is in the brand position rather than elsewhere on the label.
- **Batch runs in the browser.** Closing the tab stops it. That is a deliberate trade for a stateless server with nothing stored; a production version would add a job queue with retention rules.
- **No sign-in or audit trail.** It's a prototype. Production would sit behind the agency's identity provider and log decisions, not content.
- **English labels only** (the Tesseract `eng` model).
- **The browser and the server read slightly differently.** tesseract.js and native Tesseract use the same model but different arithmetic, so they can disagree on borderline text. On the 21 real labels the browser-only path is as safe as the server (no false problems, nothing missed, 80% of checks confirmed against 78%), but 7 labels differ on a borderline item in one direction or the other ([eval/REPORT.md](eval/REPORT.md#the-browser-only-path)).
- **The browser-only path skips two server steps.** To find which way a sideways photo is turned, it reads both quarter turns instead of using Tesseract's orientation detection (an extra 9 MB download). Before binarizing, it does not apply the server's bilateral smoothing, which is too slow in JavaScript.
- **The first visit downloads about 6 MB** for the browser scanner; browsers keep it for a year. A browser without WebAssembly SIMD uses the server instead. When the browser does all the work, the picture clean-up runs on the page's main thread, so the page can be slow to respond during a large batch.
- **The AI reader sends pictures to an external API** when enabled. That needs a policy decision, or a model hosted inside the agency's boundary.

---

## Deploying

The app is one stateless container, so any container host works. 1 vCPU / 1 GB RAM is enough: each label takes ≤1.5 s on one CPU. More CPUs speed up batches. Because the browser reads first, clean labels and sideways photos do not depend on the server's speed.

- **Render:** *New → Blueprint →* select this repository ([`render.yaml`](render.yaml) is included). It uses Render's default 0.5 CPU / 512 MB instance. Measured there on the sample labels (October 2026), from upload to answer: about 1.3 s for a clean label, 3–4 s for a label with problems (the tool reads it more times before it reports), and 4.8–5.4 s for an angled or sideways photo. That is at the edge of the 5-second target. A 1 CPU instance is about twice as fast, and the free instance is too slow. To enable the AI reader, add `ANTHROPIC_API_KEY` under the service's *Environment* settings.
- **Azure Container Apps** (matches the agency's Azure environment):

  ```bash
  az containerapp up --name label-check --resource-group label-check-rg --location eastus --source . --ingress external --target-port 8000
  ```

- **Any Docker host:** `docker run -p 80:8000 label-check`.
- **Static files only, no server code:** the page also works from any static web host, with every check done in the browser. Serve this folder at the root of a site:

  ```bash
  mkdir -p site/static && cp -r app/static/. site/static/ && cp app/static/index.html site/ && cp -r samples site/
  ```

  The page finds no API, reads the sample list from `samples/batch_applications.csv`, and does all of the work in the browser.

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
    browser.py          readings sent by the text scanner in the browser
  verification/
    evidence.py         common shape for anything a reader saw
    text.py             normalization and fuzzy phrase search
    units.py            alcohol/proof and volume parsing
    warning.py          government warning alignment and diff
    rules.py            the checks and how readers' results combine
  static/               index.html, app.css, app.js (no build step, no external assets)
    local/              the check in the browser: engine.js (tesseract.js passes), imaging.js,
                        and the rules ported from verification/ (rules, warning, units, text, fuzz)
    vendor/             tesseract.js 7.0.0 and the English model, with their sources and checksums
samples/                synthetic labels, batch CSV, generator
eval/                   real-label evaluation: registry fetcher, ground truth, scorers (server, and
                        the browser-only path in headless Chrome), report
tests/                  unit, API, vision (mocked), real-label regression, end-to-end and browser/server parity tests
  js/                   Node runners for the parity tests
```

## Tools used

Python 3.12, FastAPI, Tesseract 5 via `pytesseract`, OpenCV and Pillow for image processing, RapidFuzz for fuzzy matching, and the Anthropic Python SDK for the optional AI reader. The front end is plain HTML/CSS/JavaScript, with [tesseract.js](https://github.com/naptha/tesseract.js) 7.0.0 and the [tessdata_fast](https://github.com/tesseract-ocr/tessdata_fast) English model for reading in the browser. Tests use pytest, and Node.js for the parity tests. The sample labels are rendered with Pillow and "photographed" with OpenCV. Accessibility was checked with axe-core.
