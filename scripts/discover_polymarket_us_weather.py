from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from weather_alpha.markets.polymarket_us import PolymarketUSClient


def main() -> int:
    ap = argparse.ArgumentParser(description="Discover live Polymarket US temperature markets via official API")
    ap.add_argument("--include-closed", action="store_true")
    ap.add_argument("--output", type=Path, default=Path("/data/weather/normalized/markets/polymarket_us_weather_catalog.parquet"))
    args = ap.parse_args()

    client = PolymarketUSClient()
    rows = client.discover_weather(include_closed=args.include_closed)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    payload = []
    for r in rows:
        payload.append({
            "venue": "polymarket_us",
            "event_id": r.event_id,
            "event_slug": r.event_slug,
            "market_id": r.market_id,
            "market_slug": r.market_slug,
            "title": r.event_title,
            "outcome_label": r.outcome_label,
            "station": r.station,
            "settlement_date": r.settlement_date.isoformat() if r.settlement_date else None,
            "weather_event_id": r.weather_event_id,
            "shape": r.shape.value,
            "lower_f": r.lower_f,
            "upper_f": r.upper_f,
            "volume": r.volume,
            "liquidity": r.liquidity,
            "active": r.active,
            "closed": r.closed,
        })

    table = pa.Table.from_pylist(payload) if payload else pa.table({"market_slug": pa.array([], type=pa.string())})
    pq.write_table(table, args.output)

    print(f"markets={len(payload)}")
    print(f"events={len(set(x['event_slug'] for x in payload))}")
    print(f"stations={dict(Counter(x['station'] for x in payload))}")
    print(f"dated={sum(x['settlement_date'] is not None for x in payload)}")
    print(f"output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
