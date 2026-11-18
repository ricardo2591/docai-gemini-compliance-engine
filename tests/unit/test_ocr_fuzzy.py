"""Unit tests for OCR lookalike normalization, fuzzy ID matching, and name similarity."""

from compliance_engine.ocr.fuzzy_match import (
    edit_distance,
    extract_candidate_worker_ids,
    id_in_text_fuzzy,
    map_lookalikes_to_digits,
    match_by_keywords,
    name_similarity,
    normalize_name,
)


def test_map_lookalikes_to_digits() -> None:
    assert map_lookalikes_to_digits("9O123l56I") == "901231561"
    assert map_lookalikes_to_digits(None) == ""
    assert map_lookalikes_to_digits("SBZ") == "582"


def test_normalize_name_and_similarity() -> None:
    a = normalize_name("MarÍa   José-Gómez\nPérez")
    b = normalize_name("maria jose gomez perez")
    assert a == ["maria", "jose", "gomez", "perez"]
    assert name_similarity(a, b) == 1.0
    assert name_similarity([], a) == 0.0


def test_edit_distance_and_fuzzy_id_matching() -> None:
    assert edit_distance("901234561", "901234562") == 1
    assert id_in_text_fuzzy("901234561", "Worker CC 9O123456l confirmed")
    # Distance-1 tolerance on noisy OCR token
    assert id_in_text_fuzzy("901234561", "CC 901234591 verified")
    assert not id_in_text_fuzzy("901234561", "")
    assert not id_in_text_fuzzy(None, "CC 901234561")


def test_extract_candidate_worker_ids_filters_tax_ids_and_currency() -> None:
    text = (
        "NIT: 900123451 | Planilla 889911223 | Valor $ 15000000 | "
        "CC: 901234561 Jane Doe | Worker ID: 9O1234562 Robert Smith"
    )
    ids = extract_candidate_worker_ids(text)
    assert "901234561" in ids
    assert "901234562" in ids
    assert "900123451" not in ids
    assert "15000000" not in ids


def test_match_by_keywords() -> None:
    assert match_by_keywords(["planilla", "pila"], ["alturas"], "doc.pdf", "Pago PILA mes julio")
    assert not match_by_keywords(["planilla"], ["alturas"], "curso_alturas.pdf", "Planilla referencia")
