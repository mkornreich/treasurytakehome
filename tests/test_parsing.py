import pytest

from app.verification.evidence import Token
from app.verification.text import expand_states, find_phrase, loose
from app.verification.units import (
    find_abv_mentions,
    find_proof_mentions,
    find_volume_mentions,
    parse_alcohol,
    parse_volume,
    same_volume,
)


@pytest.mark.parametrize(
    "text, abv, proof",
    [
        ("45% Alc./Vol. (90 Proof)", 45.0, 90.0),
        ("45", 45.0, None),
        ("90 Proof", 45.0, 90.0),
        ("12.5% ABV", 12.5, None),
        ("13,5 % vol", 13.5, None),
    ],
)
def test_parse_alcohol(text, abv, proof):
    parsed = parse_alcohol(text)
    assert (parsed.abv, parsed.proof) == (abv, proof)


def test_abv_mentions_prefer_alcohol_context_and_ignore_100_percent():
    mentions = find_abv_mentions("Made from 100% agave. Aged 12% longer. 40% ALC/VOL")
    assert [m.value for m in mentions] == [40.0]


def test_abv_mentions_repair_ocr_letter_o():
    assert [m.value for m in find_abv_mentions("4O% Alc./Vol.")] == [40.0]


def test_proof_mentions():
    assert [m.value for m in find_proof_mentions("45% ALC/VOL (90 PROOF)")] == [90.0]


@pytest.mark.parametrize(
    "text, ml",
    [
        ("750 mL", 750), ("750ML", 750), ("75 cl", 750), ("0,75 l", 750), ("1.75L", 1750),
        ("1 Liter", 1000), ("12 FL. OZ.", 354.882), ("75O mL", 750), ("NET CONTENTS 750ML.", 750),
    ],
)
def test_parse_volume(text, ml):
    assert parse_volume(text) == pytest.approx(ml, rel=1e-3)


def test_volume_with_specks_between_number_and_unit():
    assert [m.value for m in find_volume_mentions("15.5 “¥ o GALLONS")] == [pytest.approx(58673.9, rel=1e-3)]


def test_one_litre_read_as_il_after_alcohol_statement():
    assert [m.value for m in find_volume_mentions("86 PROOF 43% ALC/VOL IL")] == [1000.0]
    assert find_volume_mentions("Springfield, IL") == []


def test_letter_spaced_volume_and_bare_abv():
    assert [m.value for m in find_volume_mentions("signature 7 5 0 m L")] == [750.0]
    bare = find_abv_mentions("STRAIGHT BOURBON WHISKEY 53.5 ALC/VOL 750ML")
    assert [(m.value, m.percent) for m in bare] == [(53.5, False)]


def test_same_volume_tolerates_rounded_unit_conversions():
    assert same_volume(parse_volume("12 fl oz"), parse_volume("355 mL"))
    assert not same_volume(parse_volume("700 mL"), parse_volume("750 mL"))


def test_loose_ignores_case_and_punctuation():
    assert loose("STONE'S THROW") == loose("Stone’s Throw") == "stones throw"


def test_expand_states_without_comma():
    assert expand_states("GLASTONBURY CT") == "GLASTONBURY connecticut"


def test_expand_states_only_expands_ambiguous_codes_after_comma():
    assert expand_states("Portland, OR").endswith("oregon")
    assert expand_states("this or that") == "this or that"
    assert "kentucky" in expand_states("Bardstown, KY 40004")


def tokens(text):
    return [Token(w) for w in text.split()]


def test_find_phrase_exact_and_fuzzy():
    words = tokens("OLD TOM DISTILLERY Kentucky Straight Bourbon Whiskey 45% ALC/VOL")
    assert find_phrase("Old Tom Distillery", words).score == 100
    fuzzy = find_phrase("Kentucky Straight Bourbon Whisky", words)
    assert 90 <= fuzzy.score < 100
    assert fuzzy.text == "Kentucky Straight Bourbon Whiskey"


def test_find_phrase_trims_trailing_punctuation():
    match = find_phrase("Old Tom Distillery", tokens("Bottled by Old Tom Distillery, Bardstown"))
    assert match.text == "Old Tom Distillery"
