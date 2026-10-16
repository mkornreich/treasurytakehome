# Evaluation on real approved labels (TTB Public COLA Registry)

The synthetic samples in `samples/` are rendered in plain fonts, and all of them passed long before this
evaluation. Real label artwork is much harder: script and stencil brand names, condensed type, light text on
dark panels, mandatory text running up a side strip, keg collars printed in a circle. This report measures
Label Check (local OCR only, no AI reader) on 21 approved labels from the public registry, before and after
the fixes it prompted.

## Method

**Labels.** I picked 22 COLAs approved June–October 2026 by searching the registry for common product names.
One had no images, which left 21: 9 spirits, 6 wines and 6 beers, 7 of them imports. Together they have 43
label images (front, back, neck and strip panels). [`fetch_cola.py`](fetch_cola.py) downloads them again on
demand. The artwork belongs to the brand owners, so the images are not committed.

**Ground truth.** The registry provides each COLA's brand name, product type, origin and applicant. The
application form has no alcohol-content or net-contents fields, so I viewed every label image and transcribed
the class/type, alcohol content, net contents and bottler statement as printed. All of this is in
[`real_labels.csv`](real_labels.csv).

**Two runs per label** ([`run_eval.py`](run_eval.py)):

1. **Truthful.** The application matches the label, so every check should pass. The exceptions are the
   genuine problems I found by eye on these approved labels (listed in the `expect` column, and below).
2. **Tampered.** The alcohol content is raised by 2 points, the net contents changed to another standard
   size, and the brand replaced with one that isn't on the label. Each change must be flagged.

An outcome counts as *correct* when it matches the ground truth. A *false review* is "Examine" where the
answer was OK. That is safe, but costs the agent a look. A *false problem* is "Problem" where the answer was
OK, the outcome that makes agents stop trusting a tool. *Missed* is OK where something was wrong.

Timing is worst-case: the process is pinned to **one CPU with one OCR worker**.

## Results

| Truthful applications (171 checks) | Before | After |
|---|---|---|
| Correct | 88 (53%) | **134 (78%)** |
| False review (safe, costs a look) | 52 (32%) | 37 (22%) |
| False problem | 24 (15%) | **0** |
| Missed | 1 | **0** |
| Labels with every check right | 0 of 21 | 5 of 21 |

| Tampered applications | Before | After |
|---|---|---|
| Alcohol content changed (21) | 20 Problem, 1 Examine, 0 missed | 15 Problem, 6 Examine, **0 missed** |
| Net contents changed (20) | 14 Problem, 6 Examine, 0 missed | 14 Problem, 6 Examine, **0 missed** |
| Brand replaced (21) | 21 Examine, 0 missed | 21 Examine, **0 missed** |

| Time per label, one CPU | Before | After |
|---|---|---|
| Median | 2.6 s | 3.1 s |
| Worst | 3.4 s | 3.8 s |

(The baseline run had 165 checks rather than 171, because the capitals and bold rows only appear once the
warning has been found.)

The trade-off is deliberate. A conflict seen by only one OCR reading now goes to **Examine** instead of
**Problem**, which took all 24 false rejections to zero. The cost is that six tampered alcohol values that
only one reading picked up are now "Examine" rather than "Problem". They are still flagged, with the conflicting
value in the message, and none got through.

### By check

| Check | Correct before | Correct after |
|---|---|---|
| brand name | 15/21 | 17/21 |
| class type | 15/21 | 17/21 |
| alcohol content | 16/21 | 20/21 |
| net contents | 12/21 | 13/21 |
| bottler | 10/21 | 19/21 |
| country of origin | 7/7 | 7/7 |
| warning text | 2/21 | 10/21 |
| warning caps | 7/16 | 18/19 |
| warning bold | 4/16 | 13/19 |

### By label

| Label | Type | Before | After | Still asks for a look at |
|---|---|---|---|---|
| Belle Isle | spirits | 1 false ✕, 1 missed, 1 false ! | 2 false ! | warning bold, warning text |
| Jettywave | spirits | 1 false ✕, 4 false ! | 2 false ! | class type, warning text |
| Appalachian Spirits | spirits | 2 false ✕, 4 false ! | 2 false ! | net contents, warning bold |
| Juan Lobo | spirits | 1 false ✕, 1 false ! | 2 false ! | net contents, warning text |
| Blue Chair Bay | spirits | 2 false ✕, 3 false ! | 3 false ! | bottler, brand name, warning text |
| Silk Velvet Whiskey | spirits | 2 false ✕, 3 false ! | 4 false ! | brand name, class type, warning bold, warning text |
| Creature Comforts Spirits | spirits | 2 false ✕, 2 false ! | all right | – |
| Moustache | spirits | 1 false ✕, 2 false ! | 1 false ! | net contents |
| Henebery | spirits | 1 false ✕, 4 false ! | 1 false ! | net contents |
| Unbound | wine | 1 false ✕, 2 false ! | all right | – |
| Migliavacca Wine Company | wine | 1 false ! | 1 false ! | class type |
| Romeo | wine | 2 false ✕, 1 false ! | all right | – |
| Somar | wine | 1 false ✕, 4 false ! | 4 false ! | bottler, net contents, warning bold, warning text |
| D2 | wine | 2 false ✕, 4 false ! | 4 false ! | brand name, net contents, warning caps, warning text |
| Essence | wine | 2 false ✕, 3 false ! | 4 false ! | brand name, class type, warning bold, warning text |
| Revolver Brewing | beer | 2 false ! | 2 false ! | net contents, warning text |
| Two Lanterns | beer | 1 false ✕, 2 false ! | 1 false ! | warning text |
| Five Boroughs | beer | 3 false ! | 3 false ! | alcohol content, net contents, warning bold |
| New Belgium | beer | 2 false ✕, 1 false ! | all right | – |
| Lidskoe | beer | 2 false ! | all right | – |
| Candymaker | beer | 3 false ! | 1 false ! | warning text |

## Genuine problems on approved labels

Four labels approved by TTB have something an agent should look at, and the tool flags each one:

| Label | What's wrong | What Label Check says |
|---|---|---|
| Belle Isle bourbon | Prints "53.5 ALC/VOL" with no % sign (TTB's approval note asks for it to be added) | ! "The label shows 53.5 next to “ALC/VOL” but no % sign. The alcohol content must be stated as a percentage." |
| Moustache gin | Warning reads "CONSUMTION" (missing P) and drops the final period | ! Shows the word-level diff "expected “Consumption”, read “CONSUMTION”". A one-letter difference could also be a scanner slip, so it asks a person rather than rejecting. |
| Antica Fratta "Essence" sparkling rosé | Registry origin is France; the label says "Produced and bottled in Italy" | ! "The label says “bottled in Ital[y]”; the application says France." |
| Two Lanterns IPA keg collar | No net contents on the label | ! "Could not find the net contents on the label." |

## What changed, and why

Each fix answers a failure seen on these labels. Each one also has a regression test in `tests/`.

| Failure on real labels | Fix |
|---|---|
| Sparse-text OCR read a neighbouring column or barcode digits into the warning, so the wording "failed" on 11 of 21 labels | When the warning isn't confirmed, its paragraph is located, cropped from the plain grayscale, enlarged and **re-read as one block** (`pipeline.ImageReader.queue_warning_reread`). A reading with the wording intact outweighs readings with text spliced in. |
| A heading set on its own line was read out of order, so the capitals check "failed" on 7 labels | The heading and body are now located separately (`warning.read_warning`). A heading OCR can't find is "Examine", never "Problem". |
| One pass misread a digit ("750 ML" as "150 ML", "80 PROOF" as "0") and the label was failed | **Corroboration**: an OCR-only conflict needs two separate readings that agree, or the AI reader, to become a Problem (`rules._pick`). Digit pairs Tesseract confuses in display fonts (3/8, 5/6, 6/8, 0/8, 1/7, 5/9) go to review even when read twice: Tesseract reads Unbound's "13%" as "18%" on every pass. |
| "ABILITYTO", "wom- en", "T0", "(I)" counted as wording changes | Spacing and hyphenation are ignored. "(1)"/"(2)", one dropped short word, OCR digit-for-letter swaps and low-confidence words count as possible misreads, not rewording. |
| Bold headings measured ~1.0×, because stroke widths were quantized and measured on binarized passes | Stroke width is now the 80th percentile of a precise distance transform at one common scale, measured on unfiltered images. Approved bold headings now measure 1.2–1.8×. Headings too small to measure say so instead of "not bold". |
| Mandatory text running up a side panel, or upside down around a keg collar, was never read | **Rotated passes** (90°, 270°, 180°) and an **auto-layout pass** (Tesseract psm 3, which finds text boxed in tables) join the ladder. They run only while checks remain unconfirmed. |
| Tesseract gave "ALC.12.5%" a confidence of 2 and it was discarded | Every word is kept. The rules (corroboration, misread tolerance) now handle the noise. |
| "GLASTONBURY CT" became "GLASTONBURY Noneconnecticut" when a state code had no comma before it | Bug fixed in `text.expand_states`. |
| Name and address printed with a street in between | Bottler addresses are matched in parts: name, then city and state. |
| "JETTY. WAVE", "FIVE _ BOROUGHS", "7 5 0 m L", "15.5 “¥ o GALLONS", "43% ALC/VOL IL" | Phrase matching forgives spacing and stray specks. Unit parsing closes up letter-spacing, skips specks before the unit, and reads "IL" right after an alcohol statement as 1 L. |
| In the browser, one pass read Belle Isle's "107 PROOF" but not the "53.5 ALC/VOL" beside it, and that matching proof made the alcohol content OK, hiding the missing % sign | A reading that saw only the proof no longer confirms the alcohol content when another reading saw the statement without its % sign (`rules.check_alcohol`, and the same in `rules.js`). A reading that shows the % sign still does. The server never got that reading, so its results did not change. |

## The browser-only path

When the server does not answer, or the page is served as plain static files, the page checks labels by itself:
tesseract.js 7 (the same Tesseract engine and English model, compiled to WebAssembly) and a JavaScript port of
the rules ([`app/static/local/`](../app/static/local)). This section measures that path on the same 21 labels.

**Method.** [`run_browser_eval.py`](run_browser_eval.py) serves the page's files and the label images on
127.0.0.1, opens [`browser_harness.html`](browser_harness.html) in headless Chrome 155, and the page checks every
label with exactly the code it uses when the server is out of reach. The two runs per label, the ground truth
and the scoring are `run_eval.py`'s. As for the server, Chrome is pinned to **one CPU** (with three Tesseract
workers and the page sharing it). A second run used all CPUs, as on an ordinary desktop.

| Truthful applications | Server (one CPU) | Browser (one CPU) | Browser (all CPUs) |
|---|---|---|---|
| Correct | 134 of 171 (78%) | **136 of 171 (80%)** | 136 of 173 (79%) |
| False review (safe, costs a look) | 37 (22%) | 35 (20%) | 37 (21%) |
| False problem | 0 | **0** | 0 |
| Missed | 0 | **0** | 0 |
| Labels with every check right | 5 of 21 | 5 of 21 | 5 of 21 |

| Tampered applications | Server | Browser (one CPU) | Browser (all CPUs) |
|---|---|---|---|
| Alcohol content changed (21) | 15 Problem, 6 Examine | 15 Problem, 6 Examine, **0 missed** | 16 Problem, 5 Examine |
| Net contents changed (20) | 14 Problem, 6 Examine | 12 Problem, 8 Examine, **0 missed** | 12 Problem, 8 Examine |
| Brand replaced (21) | 21 Examine | 21 Examine, **0 missed** | 21 Examine |

| Time per label | Server (one CPU) | Browser (one CPU) | Browser (all CPUs) |
|---|---|---|---|
| Median | 3.1 s | 3.2 s | 3.1 s |
| Worst | 3.8 s | 3.8 s | 3.6 s |

(The browser's all-CPU run has two more checks because on one more label it found the warning, which adds the
capitals and bold rows.)

**By check.**

| Check | Server | Browser (one CPU) | Browser (all CPUs) |
|---|---|---|---|
| brand name | 17/21 | 17/21 | 17/21 |
| class type | 17/21 | 17/21 | 17/21 |
| alcohol content | 20/21 | 20/21 | 19/21 |
| net contents | 13/21 | 15/21 | 15/21 |
| bottler | 19/21 | 19/21 | 19/21 |
| country of origin | 7/7 | 7/7 | 7/7 |
| warning text | 10/21 | 10/21 | 10/21 |
| warning caps | 18/19 | 18/19 | 19/20 |
| warning bold | 13/19 | 13/19 | 13/20 |

**Where they differ.** 14 of the 21 labels give the same result on every check. On the other seven, each path is
sometimes the one that reads a borderline item:

| Label | Browser better | Browser worse |
|---|---|---|
| Appalachian Spirits | net contents read | – |
| Somar | net contents read; heading measured as bold (1.21×) | – |
| Five Boroughs | alcohol content and net contents read | – |
| Blue Chair Bay | – | "1.75 L" not read; the tampered net contents is "Examine", not "Problem" |
| D2 | – | "13%" not read |
| Henebery | – | heading measured on another reading at 1.00×, so "not bold: examine" (the server measured 1.26×) |
| Candymaker | – | the tampered net contents was seen by one reading only, so "Examine", not "Problem" |

All of these are "Examine" results; no difference is a wrong "OK" or a wrong "Problem". The causes:

- **tesseract.js and native Tesseract do not read identically.** They share the model, but the WebAssembly build
  uses different arithmetic, so small or condensed text ("1.75 L", "13%") comes out differently. The same effect
  makes the browser read other labels better.
- **The browser's clean-ups are ports, not the OpenCV originals.** Rotation and resampling match OpenCV's bicubic
  interpolation, and the glare filter differs from OpenCV's by 0.3 grey levels or less on average. The bilateral smoothing before
  binarization is left out, because it is too slow in JavaScript.
- **Sideways pictures are found by reading both quarter turns**, not by Tesseract's orientation detection, which
  would cost another 9 MB download. That costs time on labels with a vertical strip, so fewer passes fit in the
  budget.
- **One CPU is shared by the page and three Tesseract workers.** The budget is the same wall-clock 3.5 s as on the
  server, so a slow computer gets fewer passes, and more "Examine". It does not take longer.

The evaluation found one real fault, in the rules both paths share: a reading that saw only the proof could hide
a missing % sign (Belle Isle). It is fixed in both, with a regression test and a parity case (see the table above).

## What is still hard (the 22% false reviews)

All remaining errors are the safe kind: **Examine** where the label is fine.

- **Display fonts for the brand** (Blue Chair Bay's script, Silk Velvet's ornate letters, the superscript in D²,
  Essence's script): Tesseract can't read them, so the brand check asks for a look. This is where the optional
  AI reader would earn its keep.
- **Tiny or condensed type**: net contents on 8 labels (for example a 750 ML set inside a box, a light-gold
  "12 FL OZ" on a can), and the warning on low-resolution or vertical strips.
- **Warning punctuation and numbering**: "(1)", commas and periods are often unclear at small sizes. When every
  word matches but the punctuation can't be confirmed, the tool still asks for a look rather than assume.
- **Accents** (Rosé vs ROSE): Tesseract's English model drops accents, so it can't tell a real difference from
  its own limitation.
- **Bold on small, light-on-dark or vertical headings**: reported as "could not tell".

Next steps, in order of expected value:

1. Turn on the AI reader (`VISION=fallback`) where policy allows. It is consulted only for labels like these,
   and its readings are cross-checked against OCR.
2. Ask for higher-resolution images in the COLA submission. Several registry images are under 800 px.
3. Train or fine-tune Tesseract on label typefaces.

## Reproducing

```bash
uv run python eval/fetch_cola.py
```

```bash
uv run python eval/run_eval.py --save eval/results/after.json --compare eval/results/baseline.json
```

The browser-only path needs Chrome or Chromium installed. Add `--all-cpus` for the second run; `--only` checks
chosen TTB IDs, and `--verbose` prints where the time went.

```bash
uv run python eval/run_browser_eval.py --save eval/results/browser.json --compare eval/results/after.json
```

`eval/results/baseline.json` and `eval/results/after.json` hold per-label, per-check results for the server runs
in this report; `browser.json` and `browser-all-cpus.json` hold the browser-only runs. Timings vary a little from
run to run: a repeat of the server run on the same day differed on one check (Five Boroughs' net contents was
read within the budget).
