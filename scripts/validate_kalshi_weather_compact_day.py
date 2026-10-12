#!/usr/bin/env python3
"""Independently validate a compact Kalshi weather replay against raw L2.

Validation samples compact book rows and reconstructs those exact ticker/sequence
states from the recorded checkpoint + raw stream in one independent pass.
Only a complete day with zero structural errors can be marked VALIDATED.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import random
from datetime import datetime
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from scripts.compact_kalshi_weather_l2_day import (
    as_float,
    crossed,
    load_checkpoint,
    parse_dt,
    sha256_file,
    write_manifest,
)
from weather_alpha.live.kalshi_ws import KalshiBook

COMPARE_FIELDS = (
    "yes_best_bid", "yes_best_ask", "no_best_bid", "no_best_ask",
    "yes_spread", "no_spread",
    "yes_bid_depth_top5", "yes_ask_depth_top5",
    "no_bid_depth_top5", "no_ask_depth_top5",
    "yes_buy_vwap_1", "yes_buy_filled_1",
    "yes_buy_vwap_5", "yes_buy_filled_5",
    "yes_buy_vwap_25", "yes_buy_filled_25",
    "yes_buy_vwap_100", "yes_buy_filled_100",
    "no_buy_vwap_1", "no_buy_filled_1",
    "no_buy_vwap_5", "no_buy_filled_5",
    "no_buy_vwap_25", "no_buy_filled_25",
    "no_buy_vwap_100", "no_buy_filled_100",
)


def close(a: Any, b: Any, tol: float = 1e-9) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(float(a) - float(b)) <= tol


def reconstructed_values(book: KalshiBook) -> dict[str, float | None]:
    s = book.summary(depth_levels=5)
    out = {key: as_float(s.get(key)) for key in COMPARE_FIELDS if key in s}
    for outcome in ("yes", "no"):
        for size in (25, 100):
            vwap, filled = book.executable_buy_vwap(outcome.upper(), size)
            out[f"{outcome}_buy_vwap_{size}"] = as_float(vwap)
            out[f"{outcome}_buy_filled_{size}"] = float(filled)
    return out


def deterministic_sample(table: pa.Table, sample_size: int, seed: int) -> list[dict[str, Any]]:
    count = table.num_rows
    if count <= sample_size:
        return table.to_pylist()
    rng = random.Random(seed)
    indexes = sorted(rng.sample(range(count), sample_size))
    return table.take(pa.array(indexes, type=pa.int64())).to_pylist()


def deterministic_sample_parquet(path: Path, sample_size: int, seed: int) -> list[dict[str, Any]]:
    """Sample rows without materializing the entire replay table in memory."""
    pf = pq.ParquetFile(path)
    total = pf.metadata.num_rows
    if total == 0:
        return []
    count = min(max(1, sample_size), total)
    rng = random.Random(seed)
    global_indexes = sorted(rng.sample(range(total), count))
    columns = ["ticker", "sequence", *COMPARE_FIELDS]
    out: list[dict[str, Any]] = []
    cursor = 0
    pos = 0
    for rg in range(pf.num_row_groups):
        n = pf.metadata.row_group(rg).num_rows
        end = cursor + n
        local: list[int] = []
        while pos < len(global_indexes) and global_indexes[pos] < end:
            if global_indexes[pos] >= cursor:
                local.append(global_indexes[pos] - cursor)
            pos += 1
        if local:
            table = pf.read_row_group(rg, columns=columns)
            out.extend(table.take(pa.array(local, type=pa.int64())).to_pylist())
        cursor = end
        if pos >= len(global_indexes):
            break
    return out


def verify_output_hashes(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for name, meta in (manifest.get("outputs") or {}).items():
        path = Path(meta["path"])
        if not path.exists():
            errors.append(f"missing_output:{name}:{path}")
            continue
        if path.stat().st_size != int(meta["bytes"]):
            errors.append(f"size_mismatch:{name}")
        if sha256_file(path) != meta["sha256"]:
            errors.append(f"hash_mismatch:{name}")
    return errors


def verify_source_hashes(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for meta in manifest.get("source_files") or []:
        path = Path(meta["path"])
        if not path.exists():
            errors.append(f"missing_raw:{path}")
            continue
        if path.stat().st_size != int(meta["bytes"]):
            errors.append(f"raw_size_mismatch:{path}")
        if sha256_file(path) != meta["sha256"]:
            errors.append(f"raw_hash_mismatch:{path}")
    cp = manifest.get("checkpoint_in")
    if cp:
        path = Path(cp["path"])
        if not path.exists():
            errors.append(f"missing_checkpoint:{path}")
        elif sha256_file(path) != cp["sha256"]:
            errors.append("checkpoint_hash_mismatch")
    return errors


def replay_samples(manifest: dict[str, Any], samples: list[dict[str, Any]]) -> tuple[int, list[str]]:
    targets: dict[tuple[str, int], list[dict[str, Any]]] = {}
    errors: list[str] = []
    for row in samples:
        seq = row.get("sequence")
        if seq is None:
            errors.append(f"sample_missing_sequence:{row.get('ticker')}")
            continue
        targets.setdefault((str(row["ticker"]), int(seq)), []).append(row)

    cp = manifest.get("checkpoint_in")
    wanted_tickers = {ticker for ticker, _ in targets}
    all_checkpoint_books = load_checkpoint(Path(cp["path"])) if cp else {}
    books = {ticker: book for ticker, book in all_checkpoint_books.items() if ticker in wanted_tickers}
    matched: set[tuple[str, int]] = set()

    for meta in manifest.get("source_files") or []:
        path = Path(meta["path"])
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            for line in fh:
                try:
                    raw_row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                typ = str(raw_row.get("message_type") or "")
                if typ not in {"orderbook_snapshot", "orderbook_delta"}:
                    continue
                ticker = str(raw_row.get("ticker") or "")
                if not ticker or ticker not in wanted_tickers:
                    continue
                data = raw_row.get("raw") if isinstance(raw_row.get("raw"), dict) else {}
                msg = data.get("msg") if isinstance(data.get("msg"), dict) else {}
                if typ == "orderbook_snapshot":
                    book = books.setdefault(ticker, KalshiBook(ticker))
                    book.apply_snapshot(msg, raw_row.get("sequence"))
                else:
                    book = books.get(ticker)
                    if book is None:
                        continue
                    book.apply_delta(msg, raw_row.get("sequence"))
                seq = book.sequence
                if seq is None:
                    continue
                key = (ticker, int(seq))
                expected_rows = targets.get(key)
                if not expected_rows:
                    continue
                actual = reconstructed_values(book)
                for expected in expected_rows:
                    for field in COMPARE_FIELDS:
                        if not close(expected.get(field), actual.get(field)):
                            errors.append(
                                f"sample_mismatch:{ticker}:{seq}:{field}:"
                                f"expected={expected.get(field)}:actual={actual.get(field)}"
                            )
                            break
                matched.add(key)

    missing = set(targets) - matched
    errors.extend(f"sample_not_replayed:{ticker}:{seq}" for ticker, seq in sorted(missing))
    return len(matched), errors


def main() -> int:
    p = argparse.ArgumentParser(description="Validate compact Kalshi weather replay against raw L2")
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--sample-size", type=int, default=200)
    p.add_argument("--seed", type=int, default=20261011)
    p.add_argument("--mark-validated", action="store_true", help="Update manifest only when all checks pass")
    args = p.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    errors: list[str] = []
    if not manifest.get("complete_day"):
        errors.append("not_complete_day")
    if int(manifest.get("delta_without_state") or 0) != 0:
        errors.append(f"delta_without_state:{manifest.get('delta_without_state')}")
    if int(manifest.get("crossed_states") or 0) != 0:
        errors.append(f"crossed_states:{manifest.get('crossed_states')}")
    if int(manifest.get("malformed_rows") or 0) != 0:
        errors.append(f"malformed_rows:{manifest.get('malformed_rows')}")
    errors.extend(verify_output_hashes(manifest))
    errors.extend(verify_source_hashes(manifest))

    replay_path = Path(manifest["outputs"]["book_replay"]["path"])
    replay_rows = pq.read_metadata(replay_path).num_rows
    if replay_rows != int(manifest.get("snapshot_rows") or -1):
        errors.append(f"snapshot_row_count:{replay_rows}!={manifest.get('snapshot_rows')}")
    trade_path = Path(manifest["outputs"]["trades"]["path"])
    trade_rows = pq.read_metadata(trade_path).num_rows
    if trade_rows != int(manifest.get("trade_rows") or -1):
        errors.append(f"trade_row_count:{trade_rows}!={manifest.get('trade_rows')}")

    samples = deterministic_sample_parquet(replay_path, max(1, args.sample_size), args.seed)
    matched, replay_errors = replay_samples(manifest, samples)
    errors.extend(replay_errors)

    validation = {
        "validated_at": datetime.now().astimezone().isoformat(),
        "sample_size_requested": args.sample_size,
        "sample_rows": len(samples),
        "sample_keys_matched": matched,
        "error_count": len(errors),
        "errors": errors[:100],
    }
    passed = not errors
    if passed and args.mark_validated:
        manifest["status"] = "VALIDATED"
        manifest["retention_eligible"] = True
        manifest["validation"] = validation
        write_manifest(args.manifest, manifest)
    elif args.mark_validated:
        manifest["status"] = "VALIDATION_FAILED"
        manifest["retention_eligible"] = False
        manifest["validation"] = validation
        write_manifest(args.manifest, manifest)

    print(json.dumps({"passed": passed, **validation, "manifest": str(args.manifest)}, sort_keys=True))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
