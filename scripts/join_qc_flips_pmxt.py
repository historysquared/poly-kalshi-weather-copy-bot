from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from weather_alpha.backtest.kalshi_pmxt import iter_market_snapshots, read_kalshi_parquet
from weather_alpha.backtest.settlement_money import best_bid_ask, executable_buy, terminal_buy_economics


def D(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return Decimal(text)


def _table(rows: list[dict[str, Any]]) -> pa.Table:
    return pa.Table.from_pylist(rows) if rows else pa.table({"contract_id": pa.array([], type=pa.string())})


def main() -> int:
    p = argparse.ArgumentParser(description="Join QC-clean settlement disagreements to historical executable Kalshi PMXT books")
    p.add_argument("--flips", type=Path, default=Path("/data/weather/results/settlement_reconstruction/qc_actual_contract_flips.parquet"))
    p.add_argument("--pmxt-dir", type=Path, default=Path("/data/weather/raw/kalshi/pmxt_orderbooks"))
    p.add_argument("--output", type=Path, default=Path("/data/weather/results/settlement_reconstruction/qc_pmxt_money_overlay.parquet"))
    p.add_argument("--only-boundary-flips", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--pass-only", action=argparse.BooleanOptionalAction, default=True)
    p.add_argument("--fee-coefficient", type=Decimal, default=None, help="Optional verified Kalshi taker fee coefficient. Omit to report gross-only economics.")
    p.add_argument("--depths", default="1,5,25", help="Comma-separated executable buy sizes")
    args = p.parse_args()

    flip_rows = pq.read_table(args.flips).to_pylist()
    selected: list[dict[str, Any]] = []
    for row in flip_rows:
        if args.pass_only and not row.get("pass_only_eligible"):
            continue
        if args.only_boundary_flips and row.get("boundary_flip") is not True:
            continue
        selected.append(row)

    tickers = {str(r.get("contract_id") or "") for r in selected if r.get("contract_id")}
    flip_by_ticker = {str(r["contract_id"]): r for r in selected}
    depths = [Decimal(x.strip()) for x in str(args.depths).split(",") if x.strip()]
    if not depths:
        raise SystemExit("no depths supplied")

    files = sorted(args.pmxt_dir.glob("kalshi_orderbook_*.parquet"))
    if not files:
        raise SystemExit(f"no PMXT parquet files found in {args.pmxt_dir}")

    snapshots_by_ticker: dict[str, list[Any]] = defaultdict(list)
    files_scanned = 0
    relevant_events = 0
    for path in files:
        events = [e for e in read_kalshi_parquet(path) if e.market_ticker in tickers]
        files_scanned += 1
        relevant_events += len(events)
        if not events:
            continue
        for book in iter_market_snapshots(events):
            snapshots_by_ticker[book.market_ticker].append(book)

    rows: list[dict[str, Any]] = []
    coverage = Counter()
    for ticker in sorted(tickers):
        flip = flip_by_ticker[ticker]
        books = sorted(snapshots_by_ticker.get(ticker, []), key=lambda b: b.effective_timestamp)
        if not books:
            coverage["NO_PMXT_BOOK"] += 1
            continue
        coverage["HAS_PMXT_BOOK"] += 1
        official_yes = bool(flip.get("official_winner"))
        for book in books:
            yes_bid, yes_ask = best_bid_ask(book, "yes")
            no_bid, no_ask = best_bid_ask(book, "no")
            base = {
                "contract_id": ticker,
                "station": flip.get("station"),
                "settlement_date": flip.get("settlement_date"),
                "shape": flip.get("shape"),
                "lower": flip.get("lower"),
                "upper": flip.get("upper"),
                "qc_status": flip.get("qc_status"),
                "basis_f": flip.get("basis_f"),
                "official_cli_high_f": flip.get("official_cli_high_f"),
                "asos_1min_high_f": flip.get("asos_1min_high_f"),
                "official_yes_winner": official_yes,
                "asos_yes_winner": bool(flip.get("asos_winner")),
                "boundary_flip": bool(flip.get("boundary_flip")),
                "book_time": book.effective_timestamp.isoformat(),
                "received_time": book.received_timestamp.isoformat(),
                "exchange_time": book.exchange_timestamp.isoformat() if book.exchange_timestamp else None,
                "clock_source": "exchange" if book.exchange_timestamp else "received_fallback",
                "yes_best_bid": None if yes_bid is None else str(yes_bid),
                "yes_best_ask": None if yes_ask is None else str(yes_ask),
                "no_best_bid": None if no_bid is None else str(no_bid),
                "no_best_ask": None if no_ask is None else str(no_ask),
                "yes_spread": None if yes_bid is None or yes_ask is None else str(yes_ask - yes_bid),
                "no_spread": None if no_bid is None or no_ask is None else str(no_ask - no_bid),
                "fee_coefficient": None if args.fee_coefficient is None else str(args.fee_coefficient),
                "economics_label": "EX_POST_HINDSIGHT_SETTLEMENT_LABEL_NOT_A_CAUSAL_STRATEGY",
            }
            for depth in depths:
                y = executable_buy(book, "yes", depth)
                n = executable_buy(book, "no", depth)
                ye = terminal_buy_economics(book, outcome="yes", contracts=depth, official_yes_winner=official_yes, fee_coefficient=args.fee_coefficient)
                ne = terminal_buy_economics(book, outcome="no", contracts=depth, official_yes_winner=official_yes, fee_coefficient=args.fee_coefficient)
                suffix = str(depth).replace(".", "p")
                base.update({
                    f"yes_vwap_{suffix}": None if y.vwap is None else str(y.vwap),
                    f"yes_filled_{suffix}": str(y.filled),
                    f"yes_complete_{suffix}": y.complete,
                    f"no_vwap_{suffix}": None if n.vwap is None else str(n.vwap),
                    f"no_filled_{suffix}": str(n.filled),
                    f"no_complete_{suffix}": n.complete,
                    f"hindsight_yes_gross_pnl_{suffix}": None if ye.gross_pnl is None else str(ye.gross_pnl),
                    f"hindsight_no_gross_pnl_{suffix}": None if ne.gross_pnl is None else str(ne.gross_pnl),
                    f"hindsight_yes_fee_{suffix}": None if ye.fee is None else str(ye.fee),
                    f"hindsight_no_fee_{suffix}": None if ne.fee is None else str(ne.fee),
                    f"hindsight_yes_net_pnl_{suffix}": None if ye.net_pnl is None else str(ye.net_pnl),
                    f"hindsight_no_net_pnl_{suffix}": None if ne.net_pnl is None else str(ne.net_pnl),
                })
            rows.append(base)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(_table(rows), args.output)

    print(f"selected_contracts={len(tickers)} files_scanned={files_scanned} relevant_raw_events={relevant_events}")
    print(f"coverage={dict(coverage)}")
    print(f"snapshot_rows={len(rows)}")
    if args.fee_coefficient is None:
        print("fee_status=UNSPECIFIED gross_only=true")
    else:
        print(f"fee_status=USER_SUPPLIED coefficient={args.fee_coefficient}")
    print("warning=HINDSIGHT_SETTLEMENT_LABELS_ONLY_NOT_CAUSAL_ALPHA")
    print(f"output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
