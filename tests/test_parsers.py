from hubcore.parsers.base import (
    GenericParser,
    compute_flag,
    parse_date,
    parse_reference,
    parse_value,
)


def test_parse_reference_range_dot():
    low, high, text = parse_reference("10.0 - 20.0")
    assert low == 10.0
    assert high == 20.0


def test_parse_reference_range_no_spaces():
    low, high, text = parse_reference("10-20")
    assert low == 10.0
    assert high == 20.0


def test_parse_reference_range_comma():
    low, high, text = parse_reference("10,0-20,0")
    assert low == 10.0
    assert high == 20.0


def test_parse_reference_less_than():
    low, high, text = parse_reference("<5.0")
    assert low is None
    assert high == 5.0


def test_parse_reference_less_than_spaced():
    low, high, text = parse_reference("< 5")
    assert low is None
    assert high == 5.0


def test_parse_reference_greater_than():
    low, high, text = parse_reference(">1.2")
    assert low == 1.2
    assert high is None


def test_parse_reference_do_comma():
    low, high, text = parse_reference("до 5,0")
    assert low is None
    assert high == 5.0


def test_parse_reference_text_only():
    low, high, text = parse_reference("отрицательно")
    assert low is None
    assert high is None
    assert text == "отрицательно"


def test_parse_value_comma_decimal():
    assert parse_value("12,3") == 12.3


def test_parse_value_plain():
    assert parse_value("7") == 7.0


def test_parse_value_invalid():
    assert parse_value("абв") is None


def test_parse_date_dmy():
    assert parse_date("Дата взятия: 05.03.2024") == "2024-03-05"


def test_parse_date_iso():
    assert parse_date("2024-03-05") == "2024-03-05"


def test_compute_flag_low():
    assert compute_flag(5.0, 10.0, 20.0) == "low"


def test_compute_flag_high():
    assert compute_flag(25.0, 10.0, 20.0) == "high"


def test_compute_flag_normal():
    assert compute_flag(15.0, 10.0, 20.0) == "normal"


def test_compute_flag_unknown_no_value():
    assert compute_flag(None, 10.0, 20.0) == "unknown"


def test_compute_flag_unknown_no_refs():
    assert compute_flag(15.0, None, None) == "unknown"


def test_generic_parser_extracts_table_rows():
    text = (
        "Наименование        Результат   Единицы   Референс\n"
        "Гемоглобин           140         г/л       130-160\n"
        "Глюкоза              5.5         ммоль/л   3.9-6.1\n"
    )
    parser = GenericParser()
    assert parser.can_parse(text) is True
    doc = parser.parse(text)
    names = [r.raw_name for r in doc.results]
    assert any("Гемоглобин" in n for n in names)
    assert any("Глюкоза" in n for n in names)
    hgb = next(r for r in doc.results if "Гемоглобин" in r.raw_name)
    assert hgb.value_num == 140.0
    assert hgb.ref_low == 130.0
    assert hgb.ref_high == 160.0
