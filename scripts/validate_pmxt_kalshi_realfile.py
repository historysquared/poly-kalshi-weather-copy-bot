from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

from weather_alpha.backtest.kalshi_pmxt import (
    iter_market_snapshots,
    read_kalshi_parquet,
    reconstruct_outcome_book,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate/replay one real PMXT Kalshi hourly Parquet file")
    parser.add_argument("path", type=Path)
    parser.add_argument("--ticker", default=None, help="Optional ticker to focus reconstruction")
    args = parser.parse_args()

    events = list(read_kalshi_parquet(args.path))
    types = Counter(e.event_type for e in events)
    sides = Counter(e.side for e in events)
    exchange_ts = sum(e.exchange_timestamp is not None for e in events)
    received_fallback = len(events) - exchange_ts
    tickers = Counter(e.market_ticker for e in events)

    print(f"rows={len(events)}")
    print(f"markets={len(tickers)}")
    print(f"exchange_timestamp_present={exchange_ts}")
    print(f"received_timestamp_fallback={received_fallback}")
    print("event_types=" + repr(dict(types.most_common())))
    print("sides=" + repr(dict(sides.most_common())))
    print("top_markets=" + repr(tickers.most_common(20)))

    target = args.ticker or (tickers.most_common(1)[0][0] if tickers else None)
    if target is None:
        print("no rows to reconstruct")
        return 2

    books = list(iter_market_snapshots(events, ticker=target))
    print(f"replay_ticker={target}")
    print(f"reconstructed_books={len(books)}")
    if not books:
        print("no reconstructed book for ticker")
        return 3

    last = books[-1]
    yes = reconstruct_outcome_book(last, "yes")
    no = reconstruct_outcome_book(last, "no")
    print(f"last_effective_timestamp={last.effective_timestamp.isoformat()}")
    print(f"last_received_timestamp={last.received_timestamp.isoformat()}")
    print(f"last_exchange_timestamp={last.exchange_timestamp.isoformat() if last.exchange_timestamp else None}")
    print(f"yes_best_bid={yes.best_bid} yes_best_ask={yes.best_ask}")
    print(f"no_best_bid={no.best_bid} no_best_ask={no.best_ask}")
    print(f"yes_levels={len(yes.bids)}/{len(yes.asks)} no_levels={len(no.bids)}/{len(no.asks)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
