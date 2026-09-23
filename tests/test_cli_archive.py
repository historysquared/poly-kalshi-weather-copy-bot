from datetime import date

from weather_alpha.settlement.cli_archive import parse_iem_cli_payload


def test_parse_iem_cli_payload_and_missing_trace_values():
    rows = parse_iem_cli_payload("KORD", {"results": [
        {"valid": "2026-06-11", "high": 86, "low": 67, "precip": "T", "snow": "M", "high_time": "0352 PM"},
        {"valid": "2026-06-12", "high": "M", "low": "65", "precip": "0.03"},
    ]})
    assert len(rows) == 2
    assert rows[0].valid_date == date(2026, 6, 11)
    assert rows[0].high_f == 86.0
    assert rows[0].precip_in == 0.0
    assert rows[0].snow_in is None
    assert rows[1].high_f is None
    assert rows[1].low_f == 65.0


def test_parser_accepts_data_wrapper_and_case_insensitive_keys():
    rows = parse_iem_cli_payload("KNYC", {"data": [{"DATE": "Jun11,26", "MAXIMUM": "91", "MINIMUM": "73"}]})
    assert rows[0].valid_date == date(2026, 6, 11)
    assert rows[0].high_f == 91.0
    assert rows[0].low_f == 73.0
