from hubcore.analytes import (
    match_analyte,
    normalize_marker_name,
    normalize_unit,
)


def test_normalize_marker_name_basic():
    assert normalize_marker_name("Гемоглобин (HGB)") == "гемоглобинhgb"
    assert normalize_marker_name("  ттг  ") == "ттг"


def test_normalize_marker_name_yo_and_punctuation():
    assert normalize_marker_name("Ёж-тест.значение") == normalize_marker_name("еж тест значение")


def test_match_analyte_cyrillic_and_latin():
    assert match_analyte("Гемоглобин") == "hemoglobin"
    assert match_analyte("HGB") == "hemoglobin"
    assert match_analyte("hgb") == "hemoglobin"
    assert match_analyte("ТТГ") == "tsh"
    assert match_analyte("TSH") == "tsh"


def test_match_analyte_unknown_returns_none():
    # бессмысленный набор символов не должен ложно попадать на маркер
    assert match_analyte("qwqwqwqwqwqwqwqw") is None


def test_match_analyte_vet_shared_alias():
    assert match_analyte("ALT (вет)") == "alt"


def test_normalize_unit_glucose_mgdl_to_mmoll():
    value, unit = normalize_unit(90.0, "мг/дл", "glucose")
    assert unit == "ммоль/л"
    assert abs(value - 4.995) < 0.01


def test_normalize_unit_no_conversion_needed():
    value, unit = normalize_unit(5.0, "ммоль/л", "glucose")
    assert value == 5.0
    assert unit == "ммоль/л"


def test_normalize_unit_unknown_conversion_passthrough():
    value, unit = normalize_unit(42.0, "странная_единица", "hemoglobin")
    assert value == 42.0
    assert unit == "странная_единица"


def test_normalize_unit_vitamin_d_nmol_to_ngml():
    value, unit = normalize_unit(50.0, "нмоль/л", "vitamin_d")
    assert unit == "нг/мл"
    assert abs(value - 20.03) < 0.1
