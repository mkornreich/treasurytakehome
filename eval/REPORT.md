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

`eval/results/baseline.json` and `eval/results/after.json` hold per-label, per-check results for the two runs
in this report.
