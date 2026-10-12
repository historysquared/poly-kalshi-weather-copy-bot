from pathlib import Path

from datetime import datetime, timezone

from scripts.record_kalshi_weather_l2_archive import (
    bbo_payload,
    book_quote,
    health_template,
    load_all_weather_catalog_series_count,
    load_all_weather_catalog_tickers,
    load_ticker_cache,
    market_series,
    ticker_quote,
    update_live_book_state,
    save_ticker_cache,
)
from weather_alpha.live.kalshi_ws import KalshiBook
from weather_alpha.markets.kalshi_weather_series import (
    ALL_DAILY_TEMPERATURE_SERIES, ALL_HIGH_SERIES, LOW_SERIES_BY_CITY, SERIES_BY_CITY,
)


def test_weather_series_registry_is_exact_and_unique():
    assert len(SERIES_BY_CITY) == 24
    assert len(ALL_HIGH_SERIES) == 24
    assert len(set(ALL_HIGH_SERIES)) == 24
    assert "KXHIGHINFLATION" not in ALL_HIGH_SERIES
    assert SERIES_BY_CITY["trenton"] == "KXHIGHTTTN"
    assert SERIES_BY_CITY["louisville"] == "KXHIGHTSDF"
    assert SERIES_BY_CITY["newark"] == "KXHIGHTEWR"
    assert SERIES_BY_CITY["san_diego"] == "KXHIGHTSAN"


def test_daily_temperature_collection_includes_full_verified_low_family():
    assert len(LOW_SERIES_BY_CITY) == 24
    assert len(set(LOW_SERIES_BY_CITY.values())) == 24
    assert LOW_SERIES_BY_CITY["atlanta"] == "KXLOWTATL"
    assert LOW_SERIES_BY_CITY["san_diego"] == "KXLOWTSAN"
    assert LOW_SERIES_BY_CITY["trenton"] == "KXLOWTTTN"
    assert len(ALL_DAILY_TEMPERATURE_SERIES) == 48


def test_market_series_prefers_explicit_series_then_event_prefix():
    assert market_series({"series_ticker": "kxhighny", "event_ticker": "WRONG-26SEP27"}) == "KXHIGHNY"
    assert market_series({"event_ticker": "KXHIGHCHI-26SEP27"}) == "KXHIGHCHI"


def test_ticker_cache_roundtrip(tmp_path: Path):
    path = tmp_path / "tickers.json"
    save_ticker_cache(path, {"B", "A", "A"})
    assert load_ticker_cache(path) == ["A", "B"]


def test_health_template_is_read_only():
    health = health_template(list(ALL_DAILY_TEMPERATURE_SERIES))
    assert health["read_only"] is True
    assert health["live_order_submission"] is False
    assert health["series_count"] == 48


def test_load_all_weather_catalog_tickers(tmp_path: Path):
    path = tmp_path / "catalog.json"
    path.write_text('{"active_series":[{"markets":[{"ticker":"B"},{"ticker":"A"}]},{"markets":[{"ticker":"A"}]}]}')
    assert load_all_weather_catalog_tickers(path) == ["A", "B"]


def test_load_all_weather_catalog_series_count(tmp_path: Path):
    path = tmp_path / "catalog.json"
    path.write_text('{"active_series":[{"ticker":"A","markets":[]},{"ticker":"B","markets":[]},null]}')
    assert load_all_weather_catalog_series_count(path) == 2


def test_ticker_quote_derives_binary_no_side():
    q = ticker_quote(
        {
            "market_ticker": "KXTEST",
            "time": "2026-10-11T17:00:00Z",
            "ts_ms": 1,
            "yes_bid_dollars": "0.3900",
            "yes_ask_dollars": "0.4000",
            "yes_bid_size_fp": "1.00",
            "yes_ask_size_fp": "81.00",
        },
        datetime(2026, 10, 11, 17, 0, tzinfo=timezone.utc),
    )
    assert q is not None
    assert q["yes_bid"] == "0.3900"
    assert q["yes_ask"] == "0.4000"
    assert q["no_bid"] == "0.6000"
    assert q["no_ask"] == "0.6100"
    assert q["no_bid_size"] == "81.00"
    assert q["no_ask_size"] == "1.00"


def test_book_quote_uses_orderbook_state_for_bbo_and_depth():
    book = KalshiBook("KXTEST")
    book.apply_snapshot(
        {
            "yes_dollars_fp": [["0.39", "2"], ["0.38", "3"]],
            "no_dollars_fp": [["0.60", "5"], ["0.59", "7"]],
            "ts": "2026-10-11T17:00:00Z",
        },
        42,
    )
    q = book_quote(book, datetime(2026, 10, 11, 17, 0, tzinfo=timezone.utc))
    assert q["yes_bid"] == "0.39"
    assert q["yes_ask"] == "0.40"
    assert q["no_bid"] == "0.60"
    assert q["no_ask"] == "0.61"
    assert q["yes_bid_size"] == "2"
    assert q["yes_ask_size"] == "5"
    assert q["sequence"] == 42
    assert q["yes_bid_depth_top5"] == "5"
    assert q["yes_ask_depth_top5"] == "12"


def test_bbo_payload_filters_to_daily_temperature_series():
    now = datetime(2026, 10, 11, 17, 0, tzinfo=timezone.utc)
    payload = bbo_payload(
        {
            "KXHIGHNY-26OCT12-B70.5": {"yes_ask": "0.40"},
            "KXLOWTNYC-26OCT12-B50.5": {"yes_ask": "0.30"},
            "KXGTEMP-27-P0": {"yes_ask": "0.55"},
        },
        generated_at=now,
        health={"current_tickers": 3, "series_count": 2},
    )
    assert payload["quote_count"] == 2
    assert set(payload["quotes"]) == {"KXHIGHNY-26OCT12-B70.5", "KXLOWTNYC-26OCT12-B50.5"}
    assert payload["scope"] == "DAILY_TEMPERATURE_BBO_FROM_ALL_WEATHER_WS"


def test_live_book_state_never_cross_contaminates_market_tickers():
    received = datetime(2026, 10, 11, 17, 0, tzinfo=timezone.utc)
    books = {}
    quotes = {}
    a = {
        "type": "orderbook_snapshot", "seq": 1,
        "msg": {
            "market_ticker": "A",
            "yes_dollars_fp": [["0.20", "2"]],
            "no_dollars_fp": [["0.70", "3"]],
        },
    }
    b = {
        "type": "orderbook_snapshot", "seq": 2,
        "msg": {
            "market_ticker": "B",
            "yes_dollars_fp": [["0.60", "4"]],
            "no_dollars_fp": [["0.30", "5"]],
        },
    }
    assert update_live_book_state(a, received, books, quotes) == "A"
    assert update_live_book_state(b, received, books, quotes) == "B"
    assert set(books) == {"A", "B"}
    assert quotes["A"]["yes_bid"] == "0.20"
    assert quotes["A"]["yes_ask"] == "0.30"
    assert quotes["B"]["yes_bid"] == "0.60"
    assert quotes["B"]["yes_ask"] == "0.70"


def _message(kind, ticker='A', seq=1, sid=1):
    msg = {'market_ticker': ticker}
    if kind == 'orderbook_snapshot':
        msg.update(yes_dollars_fp=[['0.30', '5']], no_dollars_fp=[['0.60', '7']])
    elif kind == 'orderbook_delta':
        msg.update(side='yes', price_dollars='0.30', delta_fp='2')
    return {'type': kind, 'sid': sid, 'seq': seq, 'msg': msg}


def _state():
    return {}, {}, {}, health_template([]), datetime.now(timezone.utc)


def test_sequence_duplicate_and_reorder_do_not_mutate_book():
    from scripts.record_kalshi_weather_l2_archive import apply_sequenced_message as apply
    books, quotes, seqs, health, now = _state()
    apply(_message('orderbook_snapshot'), now, books, quotes, seqs, health)
    apply(_message('orderbook_delta', seq=2), now, books, quotes, seqs, health)
    apply(_message('orderbook_delta', seq=2), now, books, quotes, seqs, health)
    apply(_message('orderbook_delta', seq=1), now, books, quotes, seqs, health)
    assert quotes['A']['yes_bid_size'] == '7'
    assert seqs == {1: 2}
    assert health['duplicates_or_reorders'] == 2


def test_sequence_gap_invalidates_all_markets_on_subscription():
    from scripts.record_kalshi_weather_l2_archive import apply_sequenced_message as apply
    books, quotes, seqs, health, now = _state()
    apply(_message('orderbook_snapshot'), now, books, quotes, seqs, health)
    apply(_message('orderbook_snapshot', ticker='B', seq=2), now, books, quotes, seqs, health)
    assert apply(_message('orderbook_delta', seq=4), now, books, quotes, seqs, health)
    assert not books and not quotes
    assert health['sequence_gaps'] == 1


def test_sequence_is_per_subscription_not_per_market():
    from scripts.record_kalshi_weather_l2_archive import apply_sequenced_message as apply
    books, quotes, seqs, health, now = _state()
    for msg in [_message('orderbook_snapshot'), _message('orderbook_snapshot', ticker='B', seq=2),
                _message('orderbook_delta', seq=3), _message('orderbook_delta', ticker='B', seq=4)]:
        assert not apply(msg, now, books, quotes, seqs, health)
    assert quotes['A']['yes_bid_size'] == quotes['B']['yes_bid_size'] == '7'
    assert health['sequence_gaps'] == 0


def test_independent_subscription_sequences_do_not_false_gap():
    from scripts.record_kalshi_weather_l2_archive import apply_sequenced_message as apply
    books, quotes, seqs, health, now = _state()
    apply(_message('orderbook_snapshot', seq=100), now, books, quotes, seqs, health)
    assert not apply({'type': 'ticker', 'sid': 2, 'seq': 1, 'msg': {'market_ticker': 'A'}}, now, books, quotes, seqs, health)
    assert not apply(_message('orderbook_delta', seq=101), now, books, quotes, seqs, health)
    assert seqs == {1: 101, 2: 1}


def test_delta_and_ticker_cannot_initialize_book():
    from scripts.record_kalshi_weather_l2_archive import apply_sequenced_message as apply
    books, quotes, seqs, health, now = _state()
    apply(_message('orderbook_delta'), now, books, quotes, seqs, health)
    apply({'type': 'ticker', 'sid': 2, 'seq': 1, 'msg': {'market_ticker': 'A', 'yes_ask_dollars': '0.4'}}, now, books, quotes, seqs, health)
    assert not books and not quotes
    apply(_message('orderbook_snapshot', seq=2), now, books, quotes, seqs, health)
    apply(_message('orderbook_delta', seq=3), now, books, quotes, seqs, health)
    assert quotes['A']['yes_bid_size'] == '7'


def test_missing_book_sequence_invalidates_quotes():
    from scripts.record_kalshi_weather_l2_archive import apply_sequenced_message as apply
    books, quotes, seqs, health, now = _state()
    apply(_message('orderbook_snapshot'), now, books, quotes, seqs, health)
    msg = _message('orderbook_delta', seq=2)
    del msg['sid']
    assert apply(msg, now, books, quotes, seqs, health)
    assert not quotes and not books


def test_reset_requires_snapshot_in_new_generation():
    from scripts.record_kalshi_weather_l2_archive import apply_sequenced_message as apply, reset_live_books
    books, quotes, seqs, health, now = _state()
    apply(_message('orderbook_snapshot'), now, books, quotes, seqs, health)
    reset_live_books(books, quotes)
    new_seqs = {}
    apply(_message('orderbook_delta'), now, books, quotes, new_seqs, health)
    assert not quotes
    apply(_message('orderbook_snapshot', seq=2), now, books, quotes, new_seqs, health)
    assert quotes['A']['yes_bid_size'] == '5'


def test_connection_archives_gap_before_resync_and_clears_old_books(tmp_path, monkeypatch):
    import asyncio
    import json
    from types import SimpleNamespace
    import pytest
    import scripts.record_kalshi_weather_l2_archive as collector
    messages = [_message('orderbook_snapshot'), _message('orderbook_delta', seq=2),
                _message('orderbook_delta', seq=2), _message('orderbook_delta', seq=4)]
    class Socket:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def send(self, data): pass
        async def recv(self): return json.dumps(messages.pop(0))
    class Writer:
        def __init__(self): self.rows = []
        def write(self, received, row): self.rows.append(row)
    monkeypatch.setattr(collector, 'load_all_weather_catalog_tickers', lambda p: ['A'])
    monkeypatch.setattr(collector, 'load_all_weather_catalog_series_count', lambda p: 1)
    monkeypatch.setattr(collector, 'websocket_auth_headers', lambda **kw: {})
    monkeypatch.setattr(collector.websockets, 'connect', lambda *a, **kw: Socket())
    args = SimpleNamespace(all_weather_catalog=tmp_path/'catalog', ticker_cache=tmp_path/'cache',
        key_id=None, private_key_path=None, ws_url='unused', refresh_seconds=300,
        health_interval=2, bbo_interval=5, bbo_output=tmp_path/'bbo', health_output=tmp_path/'health')
    books={'old': KalshiBook('old')}; quotes={'old': {'yes_ask': '0.01'}}
    writer=Writer();health=health_template([])
    with pytest.raises(RuntimeError, match='BOOK_SEQUENCE_INVALID'):
        asyncio.run(collector.run_connection(args, [], writer, health, books, quotes, 1))
    assert [row['sequence'] for row in writer.rows] == [1,2,2,4]
    assert all(row['generation'] == 1 for row in writer.rows)
    assert not books and not quotes
    assert json.loads(args.bbo_output.read_text())['quote_count'] == 0
    assert health['duplicates_or_reorders'] == 1
    assert health['sequence_gaps'] == 1
