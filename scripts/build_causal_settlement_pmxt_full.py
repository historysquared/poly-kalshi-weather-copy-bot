from __future__ import annotations

import argparse
import gc
import heapq
import json
import os
from bisect import bisect_right
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from weather_alpha.backtest.causal_settlement import TimedTemperature, empirical_settlement_posterior, lock_gate, surface_state
from weather_alpha.settlement.reconstruction import local_standard_settlement_window
from weather_alpha.settlement.stations import station_clock

REPLAY_COLUMNS = ["contract_id", "book_time", "yes_bids", "no_bids", "clock_source"]
OUTPUT_SCHEMA = pa.schema([
    pa.field("status", pa.string()),
    pa.field("contract_id", pa.string()),
    pa.field("station", pa.string()),
    pa.field("settlement_date", pa.string()),
    pa.field("book_time", pa.string()),
    pa.field("minutes_to_window_end", pa.string()),
    pa.field("shape", pa.string()),
    pa.field("lower", pa.string()),
    pa.field("upper", pa.string()),
    pa.field("high_so_far_f", pa.string()),
    pa.field("latest_temp_f", pa.string()),
    pa.field("minutes_since_high", pa.string()),
    pa.field("drop_from_high_f", pa.string()),
    pa.field("slope_15m_f_per_min", pa.string()),
    pa.field("lock_gate_pass", pa.bool_()),
    pa.field("lock_gate_reasons", pa.list_(pa.string())),
    pa.field("prior_basis_samples", pa.int64()),
    pa.field("posterior_yes_probability", pa.string()),
    pa.field("yes_best_ask", pa.string()),
    pa.field("no_best_ask", pa.string()),
    pa.field("gross_yes_edge", pa.string()),
    pa.field("gross_no_edge", pa.string()),
    pa.field("candidate_side", pa.string()),
    pa.field("candidate_gross_edge", pa.string()),
    pa.field("candidate_price", pa.string()),
    pa.field("clock_source", pa.string()),
    pa.field("causal_label", pa.string()),
    pa.field("fee_warning", pa.string()),
])


def D(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip()
    return None if not text else Decimal(text)


def _dt(value: Any) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _load_asos_month(raw_dir: Path, station: str, year: int, month: int) -> list[TimedTemperature]:
    path = raw_dir / f"{station}_{year}_{month:02d}.jsonl"
    if not path.exists():
        return []
    out: list[TimedTemperature] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if str(row.get("station") or "").upper() != station:
                continue
            temp = D(row.get("temperature_f"))
            if temp is None or not row.get("valid_time"):
                continue
            out.append(TimedTemperature(_dt(row["valid_time"]), temp))
    return sorted(out, key=lambda x: x.valid_time)


def _best_bid(levels: Any) -> Decimal | None:
    prices = [D(x.get("price")) for x in (levels or [])]
    prices = [x for x in prices if x is not None]
    return max(prices) if prices else None


def _rss_mb() -> float:
    try:
        with open("/proc/self/status", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024.0
    except OSError:
        pass
    return 0.0


def _month_keys(start: datetime, end: datetime) -> list[tuple[int, int]]:
    y, m = start.year, start.month
    end_key = (end.year, end.month)
    out = []
    while (y, m) <= end_key:
        out.append((y, m))
        if m == 12:
            y, m = y + 1, 1
        else:
            m += 1
    return out


def _flush(writer: pq.ParquetWriter, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    writer.write_table(pa.Table.from_pylist(rows, schema=OUTPUT_SCHEMA))
    rows.clear()


def main() -> int:
    p = argparse.ArgumentParser(description="Stream-evaluate causal settlement alpha over compact weather-only PMXT windows")
    p.add_argument("--replay", type=Path, default=Path("/data/weather/normalized/orderbooks/kalshi_weather_pmxt_bulk.parquet"))
    p.add_argument("--history", type=Path, default=Path("/data/weather/results/settlement_reconstruction/cli_asos_history.parquet"))
    p.add_argument("--catalog", type=Path, default=Path("/data/weather/normalized/markets/kalshi_weather_catalog_bulk_resolved.parquet"))
    p.add_argument("--asos-raw-dir", type=Path, default=Path("/data/weather/raw/asos/history_months"))
    p.add_argument("--output", type=Path, default=Path("/data/weather/results/settlement_reconstruction/causal_settlement_pmxt_bulk.parquet"))
    p.add_argument("--minimum-prior-samples", type=int, default=20)
    p.add_argument("--min-minutes-since-high", type=Decimal, default=Decimal("60"))
    p.add_argument("--min-drop-from-high-f", type=Decimal, default=Decimal("1.0"))
    p.add_argument("--max-positive-slope", type=Decimal, default=Decimal("0.02"))
    p.add_argument("--minimum-edge", type=Decimal, default=Decimal("0.05"))
    p.add_argument("--batch-size", type=int, default=10000)
    p.add_argument("--write-rows", type=int, default=5000)
    p.add_argument("--write-all", action="store_true", help="persist all evaluated snapshots; default writes CANDIDATE rows only")
    args = p.parse_args()

    history = pq.read_table(args.history).to_pylist()
    catalog_rows = [r for r in pq.read_table(args.catalog).to_pylist() if r.get("status") == "EXACT"]
    catalog = {str(r.get("contract_id") or ""): r for r in catalog_rows}

    prior_by_station: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in history:
        if row.get("qc_status") == "PASS" and D(row.get("cli_minus_asos_high_f")) is not None:
            prior_by_station[str(row.get("station") or "").upper()].append(row)
    for station in prior_by_station:
        prior_by_station[station].sort(key=lambda r: str(r.get("settlement_date") or "")[:10])

    asos_month_cache: dict[tuple[str, int, int], list[TimedTemperature]] = {}
    event_obs_cache: dict[tuple[str, str], tuple[list[TimedTemperature], list[datetime]]] = {}
    prior_basis_cache: dict[tuple[str, str], list[Decimal]] = {}
    window_cache: dict[tuple[str, str], Any] = {}
    status: Counter[str] = Counter()
    pending: list[dict[str, Any]] = []
    candidate_count = 0
    evaluated = 0
    replay_snapshots = 0
    top_candidates: list[tuple[Decimal, int, tuple[str, str, str, str, str]]] = []
    top_seq = 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp_output = args.output.with_suffix(args.output.suffix + ".partial")
    if tmp_output.exists():
        tmp_output.unlink()
    writer = pq.ParquetWriter(tmp_output, OUTPUT_SCHEMA, compression="zstd")

    pf = pq.ParquetFile(args.replay)
    missing = set(REPLAY_COLUMNS) - set(pf.schema_arrow.names)
    if missing:
        writer.close()
        tmp_output.unlink(missing_ok=True)
        raise ValueError(f"replay missing required columns: {sorted(missing)}")

    try:
        for batch_index, batch in enumerate(pf.iter_batches(columns=REPLAY_COLUMNS, batch_size=args.batch_size), 1):
            replay_snapshots += batch.num_rows
            for snap in batch.to_pylist():
                ticker = str(snap.get("contract_id") or "")
                contract = catalog.get(ticker)
                if contract is None:
                    status["MISSING_CONTRACT"] += 1
                    continue
                station = str(contract.get("station") or "").upper()
                day_text = str(contract.get("settlement_date") or "")[:10]
                if not station or not day_text:
                    status["MISSING_SETTLEMENT_METADATA"] += 1
                    continue
                day = date.fromisoformat(day_text)
                book_time = _dt(snap.get("book_time"))

                event_key = (station, day_text)
                window = window_cache.get(event_key)
                if window is None:
                    window = local_standard_settlement_window(station_clock(station), day)
                    window_cache[event_key] = window
                if not (window.start_utc <= book_time < window.end_utc):
                    status["OUTSIDE_SETTLEMENT_WINDOW"] += 1
                    continue

                obs_entry = event_obs_cache.get(event_key)
                if obs_entry is None:
                    all_obs: list[TimedTemperature] = []
                    for year, month in _month_keys(window.start_utc, window.end_utc):
                        key = (station, year, month)
                        if key not in asos_month_cache:
                            asos_month_cache[key] = _load_asos_month(args.asos_raw_dir, station, year, month)
                        all_obs.extend(asos_month_cache[key])
                    day_obs = sorted(
                        (o for o in all_obs if window.start_utc <= o.valid_time < window.end_utc),
                        key=lambda o: o.valid_time,
                    )
                    obs_entry = (day_obs, [o.valid_time for o in day_obs])
                    event_obs_cache[event_key] = obs_entry
                day_obs, day_times = obs_entry
                obs_end = bisect_right(day_times, book_time)
                event_obs = day_obs[:obs_end]
                state = surface_state(event_obs, as_of=book_time)
                locked, reasons = lock_gate(
                    state,
                    min_minutes_since_high=args.min_minutes_since_high,
                    min_drop_from_high_f=args.min_drop_from_high_f,
                    max_positive_slope_f_per_min=args.max_positive_slope,
                )

                prior_basis = prior_basis_cache.get(event_key)
                if prior_basis is None:
                    prior_basis = [
                        basis
                        for r in prior_by_station.get(station, [])
                        if str(r.get("settlement_date") or "")[:10] < day_text
                        for basis in [D(r.get("cli_minus_asos_high_f"))]
                        if basis is not None
                    ]
                    prior_basis_cache[event_key] = prior_basis

                posterior = None
                if state.high_so_far_f is not None:
                    posterior = empirical_settlement_posterior(
                        high_so_far_f=state.high_so_far_f,
                        prior_basis_samples_f=prior_basis,
                        shape=str(contract.get("shape") or ""),
                        lower=D(contract.get("lower")),
                        upper=D(contract.get("upper")),
                        minimum_samples=args.minimum_prior_samples,
                    )

                p_yes = None if posterior is None else posterior.probability_yes
                yes_bid = _best_bid(snap.get("yes_bids"))
                no_bid = _best_bid(snap.get("no_bids"))
                yes_ask = None if no_bid is None else Decimal("1") - no_bid
                no_ask = None if yes_bid is None else Decimal("1") - yes_bid
                yes_edge = None if p_yes is None or yes_ask is None else p_yes - yes_ask
                no_prob = None if p_yes is None else Decimal("1") - p_yes
                no_edge = None if no_prob is None or no_ask is None else no_prob - no_ask

                chosen_side = None
                chosen_edge = None
                chosen_price = None
                if locked and p_yes is not None:
                    cands = []
                    if yes_edge is not None:
                        cands.append((yes_edge, "YES", yes_ask))
                    if no_edge is not None:
                        cands.append((no_edge, "NO", no_ask))
                    if cands:
                        best = max(cands, key=lambda x: x[0])
                        if best[0] >= args.minimum_edge:
                            chosen_edge, chosen_side, chosen_price = best

                if p_yes is None:
                    label = "INSUFFICIENT_PRIOR"
                elif not locked:
                    label = "LOCK_GATE_FAIL"
                elif chosen_side is None:
                    label = "NO_EDGE"
                else:
                    label = "CANDIDATE"
                status[label] += 1
                evaluated += 1

                row = {
                    "status": label,
                    "contract_id": ticker,
                    "station": station,
                    "settlement_date": day_text,
                    "book_time": book_time.isoformat(),
                    "minutes_to_window_end": str(Decimal(str((window.end_utc - book_time).total_seconds())) / Decimal("60")),
                    "shape": str(contract.get("shape") or ""),
                    "lower": None if contract.get("lower") is None else str(contract.get("lower")),
                    "upper": None if contract.get("upper") is None else str(contract.get("upper")),
                    "high_so_far_f": None if state.high_so_far_f is None else str(state.high_so_far_f),
                    "latest_temp_f": None if state.latest_temp_f is None else str(state.latest_temp_f),
                    "minutes_since_high": None if state.minutes_since_high is None else str(state.minutes_since_high),
                    "drop_from_high_f": None if state.drop_from_high_f is None else str(state.drop_from_high_f),
                    "slope_15m_f_per_min": None if state.slope_15m_f_per_min is None else str(state.slope_15m_f_per_min),
                    "lock_gate_pass": locked,
                    "lock_gate_reasons": list(reasons),
                    "prior_basis_samples": len(prior_basis),
                    "posterior_yes_probability": None if p_yes is None else str(p_yes),
                    "yes_best_ask": None if yes_ask is None else str(yes_ask),
                    "no_best_ask": None if no_ask is None else str(no_ask),
                    "gross_yes_edge": None if yes_edge is None else str(yes_edge),
                    "gross_no_edge": None if no_edge is None else str(no_edge),
                    "candidate_side": chosen_side,
                    "candidate_gross_edge": None if chosen_edge is None else str(chosen_edge),
                    "candidate_price": None if chosen_price is None else str(chosen_price),
                    "clock_source": None if snap.get("clock_source") is None else str(snap.get("clock_source")),
                    "causal_label": "STRICT_PRIOR_PASS_BASIS_AND_ASOS_AT_OR_BEFORE_BOOK_TIME",
                    "fee_warning": "GROSS_ONLY_FEES_NOT_DEDUCTED",
                }

                if label == "CANDIDATE":
                    candidate_count += 1
                    top_seq += 1
                    assert chosen_edge is not None
                    summary = (ticker, row["book_time"], str(chosen_side), str(chosen_price), str(chosen_edge))
                    item = (chosen_edge, top_seq, summary)
                    if len(top_candidates) < 20:
                        heapq.heappush(top_candidates, item)
                    elif chosen_edge > top_candidates[0][0]:
                        heapq.heapreplace(top_candidates, item)

                if args.write_all or label == "CANDIDATE":
                    pending.append(row)
                    if len(pending) >= args.write_rows:
                        _flush(writer, pending)

            del batch
            if batch_index % 100 == 0:
                _flush(writer, pending)
                gc.collect()
                print(
                    f"batches={batch_index} replay_snapshots={replay_snapshots} evaluated={evaluated} "
                    f"candidates={candidate_count} status_counts={dict(status)} rss_mb={_rss_mb():.1f}",
                    flush=True,
                )

        _flush(writer, pending)
        writer.close()
        writer = None
        os.replace(tmp_output, args.output)
    except BaseException:
        if writer is not None:
            writer.close()
        print(f"interrupted partial_output={tmp_output} replay_snapshots={replay_snapshots} rss_mb={_rss_mb():.1f}", flush=True)
        raise

    print(f"replay_snapshots={replay_snapshots} evaluated_snapshots={evaluated}")
    print(f"status_counts={dict(status)} candidates={candidate_count}")
    if top_candidates:
        best = [item[2] for item in sorted(top_candidates, key=lambda x: x[0], reverse=True)]
        print("top_candidates=" + repr(best))
    print("causality=STRICTLY_PRIOR_SETTLEMENT_BASIS_PLUS_ASOS_AT_OR_BEFORE_BOOK_TIME")
    print("fee_status=NOT_DEDUCTED_GROSS_ONLY")
    print(f"output_mode={'ALL_EVALUATED' if args.write_all else 'CANDIDATES_ONLY'}")
    print(f"output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
