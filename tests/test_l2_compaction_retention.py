import gzip
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pyarrow.parquet as pq

from scripts.compact_kalshi_weather_l2_day import (
    compact_files,
    load_checkpoint,
    sha256_file,
)
from scripts.prune_kalshi_weather_raw import eligible_by_age, verify_manifest_for_prune
from scripts.validate_kalshi_weather_compact_day import deterministic_sample_parquet, replay_samples


def write_raw(path: Path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def ob_row(ts, ticker, typ, seq, msg):
    return {
        "schema_version": 1,
        "collector_version": "test",
        "local_receive_timestamp": ts,
        "message_type": typ,
        "ticker": ticker,
        "sequence": seq,
        "raw": {"type": typ, "seq": seq, "msg": {"market_ticker": ticker, **msg}},
    }


def test_compact_files_preserves_bucketed_depth_trade_and_checkpoint(tmp_path: Path):
    ticker = "KXHIGHNY-26OCT11-B70.5"
    raw = tmp_path / "raw.jsonl.gz"
    rows = [
        ob_row(
            "2026-10-11T00:00:01+00:00", ticker, "orderbook_snapshot", 1,
            {"yes_dollars_fp": [["0.30", "5"]], "no_dollars_fp": [["0.60", "7"]]},
        ),
        ob_row(
            "2026-10-11T00:00:05+00:00", ticker, "orderbook_delta", 2,
            {"side": "yes", "price_dollars": "0.30", "delta_fp": "3"},
        ),
        {
            "local_receive_timestamp": "2026-10-11T00:00:06+00:00",
            "message_type": "trade", "ticker": ticker, "sequence": 3,
            "raw": {"type": "trade", "seq": 3, "msg": {
                "market_ticker": ticker, "trade_id": "T1", "ts_ms": 1,
                "yes_price_dollars": "0.40", "no_price_dollars": "0.60",
                "count_fp": "2", "taker_side": "yes", "taker_book_side": "ask",
            }},
        },
        ob_row(
            "2026-10-11T00:00:12+00:00", ticker, "orderbook_delta", 4,
            {"side": "no", "price_dollars": "0.60", "delta_fp": "-2"},
        ),
    ]
    write_raw(raw, rows)
    snapshots = tmp_path / "replay.parquet"
    trades = tmp_path / "trades.parquet"
    checkpoint = tmp_path / "checkpoint.json.gz"
    stats, snapshot_rows, trade_rows, books = compact_files(
        [raw],
        snapshots_path=snapshots,
        trades_path=trades,
        checkpoint_path=checkpoint,
        checkpoint_in=None,
        bootstrap=True,
        through_date="2026-10-11",
        daily_temperature_seconds=10,
        other_seconds=60,
        batch_size=2,
    )
    assert stats.delta_without_state == 0
    assert stats.crossed_states == 0
    assert snapshot_rows == 2
    assert trade_rows == 1
    assert books == 1

    replay = pq.read_table(snapshots).to_pylist()
    assert [r["sequence"] for r in replay] == [2, 4]
    assert replay[0]["yes_best_bid"] == pytest_approx(0.30)
    assert replay[0]["yes_bid_depth_top5"] == pytest_approx(8.0)
    assert replay[0]["yes_best_ask"] == pytest_approx(0.40)
    assert replay[1]["yes_best_ask"] == pytest_approx(0.40)
    assert replay[1]["yes_ask_depth_top5"] == pytest_approx(5.0)

    trade_data = pq.read_table(trades).to_pylist()
    assert trade_data[0]["trade_id"] == "T1"
    assert trade_data[0]["count"] == pytest_approx(2.0)

    restored = load_checkpoint(checkpoint)
    assert ticker in restored
    assert str(restored[ticker].yes_bids[next(iter(restored[ticker].yes_bids))]) == "8"


def test_checkpoint_allows_next_day_to_start_with_delta(tmp_path: Path):
    ticker = "KXHIGHNY-26OCT11-B70.5"
    day1 = tmp_path / "day1.gz"
    write_raw(day1, [
        ob_row(
            "2026-10-10T23:59:59+00:00", ticker, "orderbook_snapshot", 10,
            {"yes_dollars_fp": [["0.20", "5"]], "no_dollars_fp": [["0.70", "5"]]},
        )
    ])
    cp1 = tmp_path / "cp1.json.gz"
    compact_files(
        [day1],
        snapshots_path=tmp_path / "r1.parquet",
        trades_path=tmp_path / "t1.parquet",
        checkpoint_path=cp1,
        checkpoint_in=None, bootstrap=True, through_date="2026-10-10",
    )

    day2 = tmp_path / "day2.gz"
    write_raw(day2, [
        ob_row(
            "2026-10-11T00:00:01+00:00", ticker, "orderbook_delta", 11,
            {"side": "yes", "price_dollars": "0.20", "delta_fp": "2"},
        )
    ])
    stats, rows, _, _ = compact_files(
        [day2],
        snapshots_path=tmp_path / "r2.parquet",
        trades_path=tmp_path / "t2.parquet",
        checkpoint_path=tmp_path / "cp2.json.gz",
        checkpoint_in=cp1, bootstrap=False, through_date="2026-10-11",
    )
    assert stats.delta_without_state == 0
    assert rows == 1
    replay = pq.read_table(tmp_path / "r2.parquet").to_pylist()
    assert replay[0]["yes_bid_depth_top5"] == pytest_approx(7.0)


def test_independent_replay_sample_matches_compact_row(tmp_path: Path):
    ticker = "KXHIGHNY-26OCT11-B70.5"
    raw = tmp_path / "raw.gz"
    write_raw(raw, [
        ob_row(
            "2026-10-11T00:00:01+00:00", ticker, "orderbook_snapshot", 1,
            {"yes_dollars_fp": [["0.30", "5"]], "no_dollars_fp": [["0.60", "7"]]},
        ),
        ob_row(
            "2026-10-11T00:00:12+00:00", ticker, "orderbook_delta", 2,
            {"side": "yes", "price_dollars": "0.30", "delta_fp": "2"},
        ),
    ])
    replay_path = tmp_path / "replay.parquet"
    trade_path = tmp_path / "trades.parquet"
    checkpoint = tmp_path / "cp.json.gz"
    compact_files(
        [raw],
        snapshots_path=replay_path,
        trades_path=trade_path,
        checkpoint_path=checkpoint,
        checkpoint_in=None, bootstrap=True, through_date="2026-10-11",
    )
    rows = pq.read_table(replay_path).to_pylist()
    manifest = {"source_files": [{"path": str(raw)}], "checkpoint_in": None}
    matched, errors = replay_samples(manifest, rows)
    assert matched == len(rows)
    assert errors == []


def test_retention_requires_whole_day_to_be_older_than_seven_days():
    now = datetime(2026, 10, 11, 17, 0, tzinfo=timezone.utc)
    assert eligible_by_age(date(2026, 10, 3), now=now, retention_days=7)
    assert not eligible_by_age(date(2026, 10, 4), now=now, retention_days=7)


def test_prune_manifest_fails_closed_on_unvalidated_or_changed_raw(tmp_path: Path):
    raw = tmp_path / "raw.gz"
    raw.write_bytes(b"raw")
    output = tmp_path / "replay.parquet"
    output.write_bytes(b"compact")
    manifest = {
        "status": "COMPACTED_UNVALIDATED",
        "retention_eligible": False,
        "complete_day": True,
        "outputs": {"book_replay": {"path": str(output), "sha256": sha256_file(output)}},
        "source_files": [{"path": str(raw), "sha256": sha256_file(raw)}],
    }
    errors = verify_manifest_for_prune(manifest, tmp_path / "manifest.json")
    assert "manifest_not_validated" in errors

    manifest["status"] = "VALIDATED"
    manifest["retention_eligible"] = True
    raw.write_bytes(b"changed")
    errors = verify_manifest_for_prune(manifest, tmp_path / "manifest.json")
    assert any(x.startswith("raw_hash_mismatch:") for x in errors)


def pytest_approx(value):
    import pytest
    return pytest.approx(value, abs=1e-5)


def test_parquet_sampler_reads_requested_rows_without_full_table(tmp_path: Path):
    ticker = "KXHIGHNY-26OCT11-B70.5"
    raw = tmp_path / "sampler.gz"
    rows = [
        ob_row(
            f"2026-10-11T00:00:{i:02d}+00:00", ticker, "orderbook_snapshot" if i == 0 else "orderbook_delta", i + 1,
            {"yes_dollars_fp": [["0.30", "5"]], "no_dollars_fp": [["0.60", "7"]]}
            if i == 0 else {"side": "yes", "price_dollars": "0.30", "delta_fp": "1"},
        )
        for i in range(20)
    ]
    write_raw(raw, rows)
    replay = tmp_path / "sample.parquet"
    compact_files(
        [raw], snapshots_path=replay, trades_path=tmp_path / "trades.parquet",
        checkpoint_path=tmp_path / "cp.json.gz", checkpoint_in=None, bootstrap=True,
        through_date="2026-10-11", daily_temperature_seconds=1, other_seconds=60, batch_size=3,
    )
    sample = deterministic_sample_parquet(replay, 5, 123)
    assert len(sample) == 5
    assert all(row["ticker"] == ticker for row in sample)
    assert len({row["sequence"] for row in sample}) == 5
