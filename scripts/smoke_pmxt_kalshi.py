#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from weather_alpha.backtest.kalshi_pmxt import PmxtKalshiHistoricalClient


def parse_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise argparse.ArgumentTypeError("timestamp must include timezone, e.g. 2026-06-10T19:00:00Z")
    return dt.astimezone(timezone.utc)


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test PMXT historical Kalshi L2 reconstruction")
    parser.add_argument("ticker", help="Kalshi market ticker / PMXT Kalshi outcomeId")
    parser.add_argument("--at", required=True, type=parse_utc, help="UTC as-of timestamp")
    parser.add_argument("--outcome", choices=("yes", "no"), default="yes")
    parser.add_argument("--contracts", type=float, default=1.0)
    args = parser.parse_args()

    client = PmxtKalshiHistoricalClient()
    book = client.snapshot(args.ticker, at=args.at, outcome=args.outcome)
    vwap, filled = book.executable_buy(args.contracts)
    print(f"ticker={book.ticker}")
    print(f"outcome={book.outcome}")
    print(f"requested_asof={args.at.isoformat()}")
    print(f"book_timestamp={book.timestamp.isoformat()}")
    print(f"age_seconds={(args.at - book.timestamp).total_seconds():.3f}")
    print(f"bid_levels={len(book.bids)} ask_levels={len(book.asks)}")
    print(f"best_bid={book.best_bid} best_ask={book.best_ask}")
    print(f"buy_contracts={args.contracts} filled={filled} vwap={vwap}")


if __name__ == "__main__":
    main()
