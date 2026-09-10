from __future__ import annotations

import argparse
import gc
import os
from collections import Counter
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from weather_alpha.backtest.kalshi_pmxt import ReconstructedKalshiBook, parse_kalshi_parquet_row

REQUIRED_COLUMNS = [
    "timestamp_received",
    "timestamp",
    "market_ticker",
    "market_id",
    "event_type",
    "yes_bids",
    "no_bids",
    "price",
    "delta",
    "side",
]

LEVEL_TYPE = pa.struct([
    pa.field("price", pa.string(), nullable=False),
    pa.field("size", pa.string(), nullable=False),
])
OUTPUT_SCHEMA = pa.schema([
    pa.field("contract_id", pa.string(), nullable=False),
    pa.field("market_id", pa.string(), nullable=False),
    pa.field("book_time", pa.string(), nullable=False),
    pa.field("received_time", pa.string(), nullable=False),
    pa.field("exchange_time", pa.string(), nullable=True),
    pa.field("clock_source", pa.string(), nullable=False),
    pa.field("yes_bids", pa.list_(LEVEL_TYPE), nullable=False),
    pa.field("no_bids", pa.list_(LEVEL_TYPE), nullable=False),
    pa.field("source_file", pa.string(), nullable=False),
])


def _levels(levels: tuple[tuple[Any, Any], ...]) -> list[dict[str, str]]:
    # String serialization deliberately preserves native decimal values exactly.
    return [{"price": str(price), "size": str(size)} for price, size in levels]


def _rss_mb() -> float:
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    return 0.0


def _book_dict(levels: tuple[tuple[Decimal, Decimal], ...]) -> dict[Decimal, Decimal]:
    return {price: size for price, size in levels if size > 0}


def _apply_delta(book: dict[Decimal, Decimal], price: Decimal, delta: Decimal) -> None:
    new_size = book.get(price, Decimal("0")) + delta
    if new_size <= 0:
        book.pop(price, None)
    else:
        book[price] = new_size


def _update_state(
    event: Any,
    state: dict[str, tuple[str, dict[Decimal, Decimal], dict[Decimal, Decimal]]],
) -> ReconstructedKalshiBook | None:
    current = state.get(event.market_ticker)
    is_snapshot = "snapshot" in event.event_type.lower()

    if is_snapshot:
        yes = _book_dict(event.yes_bids)
        no = _book_dict(event.no_bids)
    elif (
        current is not None
        and event.price is not None
        and event.delta is not None
        and event.side in {"yes", "no"}
    ):
        _, yes0, no0 = current
        yes, no = dict(yes0), dict(no0)
        _apply_delta(yes if event.side == "yes" else no, event.price, event.delta)
    else:
        return None

    state[event.market_ticker] = (event.market_id, yes, no)
    return ReconstructedKalshiBook(
        event.market_ticker,
        event.market_id,
        event.effective_timestamp,
        event.received_timestamp,
        event.exchange_timestamp,
        tuple(sorted(yes.items(), reverse=True)),
        tuple(sorted(no.items(), reverse=True)),
    )


def _output_row(book: ReconstructedKalshiBook, source_file: str) -> dict[str, Any]:
    return {
        "contract_id": book.market_ticker,
        "market_id": book.market_id,
        "book_time": book.effective_timestamp.isoformat(),
        "received_time": book.received_timestamp.isoformat(),
        "exchange_time": book.exchange_timestamp.isoformat() if book.exchange_timestamp else None,
        "clock_source": "exchange" if book.exchange_timestamp else "received_fallback",
        "yes_bids": _levels(book.yes_bids),
        "no_bids": _levels(book.no_bids),
        "source_file": source_file,
    }


def _rows_to_table(rows: list[dict[str, Any]]) -> pa.Table:
    # Force the same Arrow schema on every flush. In particular, an empty
    # yes_bids/no_bids list must stay list<struct<price:string,size:string>>
    # rather than being inferred as list<null> by Arrow.
    return pa.Table.from_pylist(rows, schema=OUTPUT_SCHEMA)


def _flush_rows(
    writer: pq.ParquetWriter | None,
    rows: list[dict[str, Any]],
    output: Path,
) -> pq.ParquetWriter | None:
    if not rows:
        return writer
    table = _rows_to_table(rows)
    if writer is None:
        writer = pq.ParquetWriter(output, OUTPUT_SCHEMA, compression="zstd")
    writer.write_table(table)
    rows.clear()
    del table
    return writer


def main() -> int:
    p = argparse.ArgumentParser(description="Stream-filter bulk PMXT hourly archives to a compact EXACT-weather replay parquet")
    p.add_argument("--catalog", type=Path, default=Path("/data/weather/normalized/markets/kalshi_weather_catalog_bulk_resolved.parquet"))
    p.add_argument("--pmxt-dir", type=Path, default=Path("/data/weather/raw/kalshi/pmxt_orderbooks_bulk"))
    p.add_argument("--output", type=Path, default=Path("/data/weather/normalized/orderbooks/kalshi_weather_pmxt_bulk.parquet"))
    p.add_argument("--batch-size", type=int, default=4096)
    p.add_argument("--write-rows", type=int, default=5000, help="flush output after this many reconstructed snapshots")
    args = p.parse_args()

    catalog = pq.read_table(args.catalog, columns=["contract_id", "status"]).to_pylist()
    tickers = {str(r.get("contract_id") or "") for r in catalog if r.get("status") == "EXACT" and r.get("contract_id")}
    ticker_values = pa.array(sorted(tickers), type=pa.string())
    files = sorted(args.pmxt_dir.glob("kalshi_orderbook_*.parquet"))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp_output = args.output.with_suffix(args.output.suffix + ".partial")
    if tmp_output.exists():
        tmp_output.unlink()

    counts: Counter[str] = Counter()
    covered: set[str] = set()
    state: dict[str, tuple[str, dict[Decimal, Decimal], dict[Decimal, Decimal]]] = {}
    pending: list[dict[str, Any]] = []
    writer: pq.ParquetWriter | None = None

    try:
        for file_index, path in enumerate(files, 1):
            pf = pq.ParquetFile(path)
            missing = set(REQUIRED_COLUMNS) - set(pf.schema_arrow.names)
            if missing:
                counts["schema_errors"] += 1
                print(f"schema_error file={path.name} missing={sorted(missing)}", flush=True)
                continue

            # PMXT hourly archives are chronological. Keep book state across record
            # batches and files so delta rows remain correct without loading/sorting an
            # entire hour in RAM.
            for batch in pf.iter_batches(columns=REQUIRED_COLUMNS, batch_size=args.batch_size):
                ticker_col = batch.column(batch.schema.get_field_index("market_ticker"))
                mask = pc.is_in(ticker_col, value_set=ticker_values)
                filtered = batch.filter(mask)
                counts["raw_rows_scanned"] += batch.num_rows
                if filtered.num_rows == 0:
                    del filtered, mask, ticker_col, batch
                    continue

                counts["raw_relevant_events"] += filtered.num_rows
                # Conversion to Python happens only after Arrow-level ticker filtering.
                # This is the key memory bound: no full PMXT batch/file becomes Python objects.
                names = filtered.schema.names
                columns = [filtered.column(i).to_pylist() for i in range(filtered.num_columns)]
                events = [parse_kalshi_parquet_row(dict(zip(names, values))) for values in zip(*columns)]
                events.sort(key=lambda e: (e.effective_timestamp, e.received_timestamp))

                for event in events:
                    book = _update_state(event, state)
                    if book is None:
                        counts["unapplied_events"] += 1
                        continue
                    pending.append(_output_row(book, path.name))
                    covered.add(book.market_ticker)
                    counts["snapshots"] += 1
                    if len(pending) >= args.write_rows:
                        writer = _flush_rows(writer, pending, tmp_output)

                del events, columns, filtered, mask, ticker_col, batch

            if pending:
                writer = _flush_rows(writer, pending, tmp_output)
            del pf
            gc.collect()

            if file_index % 10 == 0 or file_index == len(files):
                print(
                    f"files={file_index}/{len(files)} "
                    f"raw_scanned={counts['raw_rows_scanned']} "
                    f"raw_relevant={counts['raw_relevant_events']} "
                    f"snapshots={counts['snapshots']} "
                    f"covered_contracts={len(covered)} rss_mb={_rss_mb():.1f}",
                    flush=True,
                )

        if pending:
            writer = _flush_rows(writer, pending, tmp_output)
        if writer is not None:
            writer.close()
            writer = None
            os.replace(tmp_output, args.output)
        else:
            empty_arrays = [pa.array([], type=field.type) for field in OUTPUT_SCHEMA]
            empty_table = pa.Table.from_arrays(empty_arrays, schema=OUTPUT_SCHEMA)
            pq.write_table(empty_table, args.output, compression="zstd")
            if tmp_output.exists():
                tmp_output.unlink()

    except BaseException:
        if writer is not None:
            writer.close()
        print(f"interrupted partial_output={tmp_output} rss_mb={_rss_mb():.1f}", flush=True)
        raise

    print(
        f"files_scanned={len(files)} exact_contracts={len(tickers)} covered_contracts={len(covered)} "
        f"relevant_raw_events={counts['raw_relevant_events']} compact_snapshots={counts['snapshots']} "
        f"schema_errors={counts['schema_errors']} final_rss_mb={_rss_mb():.1f}",
        flush=True,
    )
    print(f"output={args.output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
