"""Rules over synthetic readings, so each behavior is pinned down without OCR."""

from app.models import Application, Status
from app.verification.evidence import Evidence, tokens_from_text
from app.verification.rules import (
    check_country,
    check_text,
    check_warning,
    overall,
    verify,
)
from app.verification.warning import CANONICAL_WARNING, read_warning

LABEL = (
    "OLD TOM DISTILLERY Kentucky Straight Bourbon Whiskey 45% Alc./Vol. (90 Proof) 750 mL "
    "Distilled and Bottled by Old Tom Distillery, Bardstown, Kentucky " + CANONICAL_WARNING
)
APP = Application(
    brand_name="OLD TOM DISTILLERY",
    class_type="Kentucky Straight Bourbon Whiskey",
    alcohol_content="45% Alc./Vol. (90 Proof)",
    net_contents="750 mL",
    bottler="Old Tom Distillery, Bardstown, KY",
)


def ocr(text, bold=True, name="ocr:test"):
    return Evidence("ocr", name, tokens_from_text(text), header_bold=bold)


def ocr_twice(text, **kw):
    """Two OCR passes that read the same thing: enough to corroborate a problem."""
    return [ocr(text, name="ocr:standard", **kw), ocr(text, name="ocr:enhanced", **kw)]


def vision(text, warning=None, bold=True, **fields):
    return Evidence("vision", "vision:test", tokens_from_text(text), fields=fields,
                    warning_text=warning, header_bold=bold)


def statuses(checks):
    return {c.field: c.status for c in checks}


def test_matching_label_passes_everything():
    checks = verify(APP, [ocr(LABEL)])
    assert set(statuses(checks).values()) == {Status.PASS}
    assert overall(checks)[0] == Status.PASS


def test_brand_capitalization_difference_is_a_match_with_a_note():
    app = APP.model_copy(update={"brand_name": "Old Tom Distillery"})
    result = check_text("brand_name", "Brand name", app.brand_name, [ocr(LABEL.replace("Distilled and Bottled by Old Tom Distillery, ", ""))])
    assert result.status == Status.PASS
    assert "capital letters" in result.message


def test_brand_prefers_prominent_exact_text_over_small_print():
    result = check_text("brand_name", "Brand name", "OLD TOM DISTILLERY", [ocr(LABEL)])
    assert result.found == "OLD TOM DISTILLERY"


def test_unreadable_brand_is_review_not_fail():
    result = check_text("brand_name", "Brand name", "STONE'S THROW", [ocr(LABEL)])
    assert result.status == Status.REVIEW


def test_vision_field_that_conflicts_is_a_fail():
    result = check_text("brand_name", "Brand name", "STONE'S THROW", [vision("OLD TOM", brand_name="OLD TOM DISTILLERY")])
    assert result.status == Status.FAIL


def test_vision_statement_containing_the_value_matches():
    app = Application(country_of_origin="France", bottler="Old Tom Distillery, Bardstown, KY")
    ev = vision("", country_of_origin="Product of France",
                bottler="Distilled and Bottled by Old Tom Distillery, Bardstown, Kentucky")
    checks = {c.field: c for c in verify(app, [ev])}
    assert checks["country_of_origin"].status == Status.PASS
    assert checks["bottler"].status == Status.PASS
    assert checks["bottler"].found == "Old Tom Distillery, Bardstown, Kentucky"


def test_accent_only_difference_needs_review():
    result = check_text("brand_name", "Brand name", "Château Belle Rive", [ocr("CHATEAU BELLE RIVE")])
    assert result.status == Status.REVIEW
    assert "accent" in result.message


def test_abv_mismatch_fails_with_both_values():
    checks = verify(APP, ocr_twice(LABEL.replace("45% Alc./Vol. (90 Proof)", "40% Alc./Vol. (80 Proof)")))
    abv = next(c for c in checks if c.field == "alcohol_content")
    assert abv.status == Status.FAIL
    assert "40%" in abv.message and "45%" in abv.message


def test_proof_mismatch_fails():
    checks = verify(APP, ocr_twice(LABEL.replace("(90 Proof)", "(80 Proof)")))
    abv = next(c for c in checks if c.field == "alcohol_content")
    assert abv.status == Status.FAIL  # application says 90 proof, label says 80


def test_proof_not_twice_abv_needs_review():
    app = APP.model_copy(update={"alcohol_content": "45%"})
    checks = verify(app, [ocr(LABEL.replace("(90 Proof)", "(80 Proof)"))])
    abv = next(c for c in checks if c.field == "alcohol_content")
    assert abv.status == Status.REVIEW


def test_net_contents_compared_across_units():
    app = APP.model_copy(update={"net_contents": "75 cl"})
    checks = verify(app, [ocr(LABEL)])
    assert statuses(checks)["net_contents"] == Status.PASS


def test_net_contents_mismatch_fails():
    checks = verify(APP, ocr_twice(LABEL.replace("750 mL", "700 mL")))
    assert statuses(checks)["net_contents"] == Status.FAIL


def test_conflict_seen_by_one_reading_only_is_review():
    """One pass misreads 750 as 150, the others can't read it: maybe a misread, not a rejection."""
    misread = ocr(LABEL.replace("750 mL", "150 mL"), name="ocr:standard")
    blurry = ocr(LABEL.replace("750 mL", ""), name="ocr:enhanced")
    net = next(c for c in verify(APP, [misread, blurry]) if c.field == "net_contents")
    assert net.status == Status.REVIEW
    assert "Examine the" in net.message


def test_blank_application_fields_are_skipped_but_label_values_reported():
    checks = verify(Application(), [ocr(LABEL)])
    fields = statuses(checks)
    assert "brand_name" not in fields
    assert fields["alcohol_content"] == Status.PASS
    assert fields["net_contents"] == Status.PASS


def test_any_reader_confirming_wins():
    misread = ocr(LABEL.replace("45%", "46%"))
    good = ocr(LABEL)
    assert statuses(verify(APP, [misread, good]))["alcohol_content"] == Status.PASS


# --- government warning --------------------------------------------------------------------


def warning_checks(text, **kw):
    return {c.field: c for c in check_warning(ocr_twice(text, **kw))}


def test_exact_warning_passes_all_three_checks():
    checks = warning_checks(LABEL)
    assert [c.status for c in checks.values()] == [Status.PASS] * 3


def test_title_case_heading_fails_capitals():
    checks = warning_checks(LABEL.replace("GOVERNMENT WARNING", "Government Warning"))
    assert checks["warning_caps"].status == Status.FAIL
    assert checks["warning_text"].status == Status.PASS


def test_missing_phrase_fails_wording_with_diff():
    checks = warning_checks(LABEL.replace(" because of the risk of birth defects", ""))
    wording = checks["warning_text"]
    assert wording.status == Status.FAIL
    assert any(d["op"] == "missing" for d in wording.diff)


def test_one_letter_ocr_slip_is_review_not_fail():
    checks = warning_checks(LABEL.replace("machinery", "rnachinery"))
    assert checks["warning_text"].status == Status.REVIEW


def test_all_caps_body_is_accepted():
    checks = warning_checks(LABEL.replace(CANONICAL_WARNING, CANONICAL_WARNING.upper()))
    assert checks["warning_text"].status == Status.PASS


def test_heading_not_bold_needs_review():
    checks = warning_checks(LABEL, bold=False)
    assert checks["warning_bold"].status == Status.REVIEW


def test_missing_warning_on_legible_label_fails():
    text = " ".join(["Plenty of clearly printed label text"] * 6)
    checks = check_warning([ocr(text, name="a"), ocr(text, name="b")])
    assert checks[0].status == Status.FAIL
    assert len(checks) == 1


def test_wording_problem_seen_once_needs_review():
    checks = {c.field: c for c in check_warning([
        ocr(LABEL.replace(" because of the risk of birth defects", ""), name="a"),
        ocr(LABEL.replace("because of the risk of birth defects", "because of the risk of birth def3cts"), name="b"),
    ])}
    assert checks["warning_text"].status == Status.REVIEW


def test_heading_read_out_of_order_still_counts():
    """A heading on its own line can come out of OCR after other text; that's not a wording problem."""
    body = CANONICAL_WARNING.replace("GOVERNMENT WARNING: ", "")
    checks = warning_checks("GOVERNMENT WARNING: DISTILLED BY OLD TOM " + body)
    assert checks["warning_text"].status == Status.PASS
    assert checks["warning_caps"].status == Status.PASS


def test_glued_and_hyphenated_words_are_not_wording_problems():
    text = LABEL.replace("ability to drive a car", "abilityto drivea car").replace("women", "wom- en")
    assert warning_checks(text)["warning_text"].status == Status.PASS


def test_barcode_digits_inside_warning_are_not_a_wording_problem():
    text = LABEL.replace("operate machinery,", "operate 8 56890 00620 machinery,")
    assert warning_checks(text)["warning_text"].status == Status.REVIEW


def test_missing_warning_on_illegible_label_needs_review():
    checks = check_warning([ocr("OLD TOM")])
    assert checks[0].status == Status.REVIEW


def test_readers_disagreeing_on_warning_need_review():
    """A model that silently 'corrects' a title-case heading must not hide it."""
    bad_label = LABEL.replace("GOVERNMENT WARNING", "Government Warning")
    checks = {c.field: c for c in check_warning([ocr(bad_label), vision("", warning=CANONICAL_WARNING)])}
    assert checks["warning_caps"].status == Status.REVIEW
    assert "do not agree" in checks["warning_caps"].message


def test_vision_reading_wins_when_ocr_could_not_read_warning():
    checks = {c.field: c for c in check_warning([ocr("OLD TOM blurry"), vision("", warning=CANONICAL_WARNING)])}
    assert checks["warning_text"].status == Status.PASS
    assert checks["warning_text"].source == "vision"


def test_read_warning_locates_statement_inside_other_text():
    reading = read_warning(ocr(LABEL + " Bottle deposit 5c"))
    assert reading.exact and reading.header_caps and reading.header_colon


# --- lessons from real labels in the TTB registry (see eval/REPORT.md) ----------------------


def test_abv_without_percent_sign_needs_review():
    """Belle Isle: "53.5 ALC/VOL" - TTB's approval asked for the % sign to be added."""
    app = APP.model_copy(update={"alcohol_content": "53.5% Alc./Vol. (107 Proof)"})
    label = LABEL.replace("45% Alc./Vol. (90 Proof)", "53.5 ALC/VOL 107 PROOF")
    abv = next(c for c in verify(app, ocr_twice(label)) if c.field == "alcohol_content")
    assert abv.status == Status.REVIEW
    assert "% sign" in abv.message


def test_a_reading_of_only_the_proof_does_not_hide_a_missing_percent_sign():
    """Belle Isle in the browser: one pass read "107 PROOF" but not the "53.5 ALC/VOL" next to it.
    A matching proof says nothing about the % sign, so the label still needs a look."""
    app = APP.model_copy(update={"alcohol_content": "53.5% Alc./Vol. (107 Proof)"})
    bare = LABEL.replace("45% Alc./Vol. (90 Proof)", "53.5 ALC/VOL 107 PROOF")
    proof_only = LABEL.replace("45% Alc./Vol. (90 Proof)", "107 PROOF")
    abv = next(c for c in verify(app, [ocr(bare), ocr(proof_only, name="ocr:enhanced")]) if c.field == "alcohol_content")
    assert abv.status == Status.REVIEW
    assert "% sign" in abv.message
    # A reading that does show the % sign still confirms it: the other pass just missed it.
    with_percent = LABEL.replace("45% Alc./Vol. (90 Proof)", "53.5% ALC/VOL 107 PROOF")
    abv = next(c for c in verify(app, [ocr(bare), ocr(with_percent, name="ocr:layout")]) if c.field == "alcohol_content")
    assert abv.status == Status.PASS


def test_confusable_digits_are_review_even_when_read_twice():
    """Unbound: Tesseract reads a "13%" in Arial as "18%" on every pass."""
    app = APP.model_copy(update={"alcohol_content": "13% Alc./Vol."})
    label = LABEL.replace("45% Alc./Vol. (90 Proof)", "ALC. 18% BY VOL")
    abv = next(c for c in verify(app, ocr_twice(label)) if c.field == "alcohol_content")
    assert abv.status == Status.REVIEW
    assert "reads these digits incorrectly" in abv.message


def test_address_parts_may_be_separated_on_the_label():
    """Appalachian Spirits: the street address sits between the name and the town."""
    app = Application(bottler="Appalachian Spirits, LLC, Mechanicsburg, PA")
    label = "BOTTLED BY APPALACHIAN SPIRITS, LLC. 6462 CARLISLE PIKE · MECHANICSBURG, PENNSYLVANIA " + CANONICAL_WARNING
    bottler = next(c for c in verify(app, [ocr(label)]) if c.field == "bottler")
    assert bottler.status == Status.PASS


def test_clean_reading_outweighs_one_with_spliced_text():
    """Two Lanterns: one pass splices the next column ("6.8% alc./vol") into the warning."""
    spliced = LABEL.replace("drive a car", "drive 76 IBUs 6.8% alc./vol a car")
    readings = [ocr(spliced, name="ocr:standard"), ocr(spliced, name="ocr:layout"),
                ocr(LABEL.replace("machinery", "rnachinery"), name="ocr:warning")]
    wording = next(c for c in check_warning(readings) if c.field == "warning_text")
    assert wording.status == Status.REVIEW


def test_unreadable_numerals_are_not_a_wording_problem():
    assert warning_checks(LABEL.replace("(1) ", "[\\) "))["warning_text"].status == Status.REVIEW


def test_real_typo_in_warning_is_still_flagged():
    """Moustache: an approved label that prints "CONSUMTION"."""
    checks = warning_checks(LABEL.replace("Consumption", "CONSUMTION"))
    assert checks["warning_text"].status in (Status.REVIEW, Status.FAIL)


def test_origin_statement_naming_another_country_fails():
    """Essence: the registry says France; the label says "Produced and bottled in Italy"."""
    label = LABEL + " CONTAINS SULFITES Produced and bottled in Italy by ANTICA CANTINA FRATTA FRANCIACORTA"
    country = check_country("France", ocr_twice(label))
    assert country.status == Status.FAIL
    assert "Italy" in country.message


def test_origin_statement_in_another_language_matches():
    assert check_country("Mexico", ocr_twice("HECHO EN MEXICO - BOTTLED AT ORIGIN")).status == Status.PASS
