from __future__ import annotations

import argparse
import json
import time
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import httpx

BASE = "https://external-api.kalshi.com/trade-api/v2"


def D(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict):
                rows.append(obj)
    return rows


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def fill_key(row: dict[str, Any]) -> str:
    return f"{row.get('ticker','')}|{row.get('fill_time','')}|{row.get('side','')}|{row.get('fill_price','')}"


def get_market(client: httpx.Client, ticker: str) -> dict[str, Any] | None:
    for attempt in range(6):
        try:
            r = client.get(f"{BASE}/markets/{ticker}")
        except httpx.HTTPError:
            if attempt == 5:
                return None
            time.sleep(min(20.0, 2 ** attempt))
            continue
        if r.status_code == 404:
            return None
        if r.status_code == 429 or 500 <= r.status_code < 600:
            if attempt == 5:
                return None
            time.sleep(min(20.0, 2 ** attempt))
            continue
        r.raise_for_status()
        payload = r.json()
        market = payload.get("market", payload) if isinstance(payload, dict) else None
        return market if isinstance(market, dict) else None
    return None


def normalized_result(market: dict[str, Any]) -> str | None:
    for key in ("result", "settlement_result", "outcome"):
        value = market.get(key)
        if value is None:
            continue
        text = str(value).strip().lower()
        if text in {"yes", "y", "1", "true"}:
            return "YES"
        if text in {"no", "n", "0", "false"}:
            return "NO"
    return None


def score_fill(fill: dict[str, Any], market: dict[str, Any]) -> dict[str, Any] | None:
    result = normalized_result(market)
    if result is None:
        return None

    side = str(fill.get("side") or "").upper()
    contracts = int(fill.get("contracts") or 0)
    price = D(fill.get("fill_price"))
    fee = D(fill.get("estimated_taker_fee")) or Decimal("0")
    if side not in {"YES", "NO"} or contracts <= 0 or price is None:
        return None

    premium = price * Decimal(contracts)
    payout = Decimal(contracts) if side == result else Decimal("0")
    pnl = payout - premium - fee
    capital = D(fill.get("capital_at_risk")) or (premium + fee)
    roi = None if capital == 0 else pnl / capital

    return {
        **fill,
        "score_status": "SETTLED_SCORED",
        "market_status": market.get("status"),
        "market_result": result,
        "won": side == result,
        "gross_payout": str(payout),
        "premium_paid": str(premium),
        "fee_paid": str(fee),
        "net_pnl": str(pnl),
        "roi": None if roi is None else str(roi),
        "settlement_ts": market.get("settlement_ts") or market.get("settled_time") or market.get("close_time"),
        "live_order_submission": False,
    }


def run_once(args: argparse.Namespace) -> tuple[int, int, int]:
    fills = [r for r in read_jsonl(args.fills) if r.get("fill_status") == "PAPER_FILLED"]
    scored = read_jsonl(args.output)
    scored_keys = {str(r.get("fill_key") or fill_key(r)) for r in scored}

    new_scores = 0
    unresolved = 0
    checked = 0
    with httpx.Client(timeout=20.0, follow_redirects=True, headers={"User-Agent": "weather-alpha-paper-settlement/0.1"}) as client:
        for fill in fills:
            key = fill_key(fill)
            if key in scored_keys:
                continue
            ticker = str(fill.get("ticker") or "")
            if not ticker:
                continue
            checked += 1
            market = get_market(client, ticker)
            if market is None:
                unresolved += 1
                continue
            row = score_fill(fill, market)
            if row is None:
                unresolved += 1
                continue
            row["fill_key"] = key
            append_jsonl(args.output, row)
            scored_keys.add(key)
            new_scores += 1
            print(
                f"PAPER_SETTLED ticker={ticker} side={row['side']} result={row['market_result']} "
                f"won={row['won']} fill={row['fill_price']} pnl={row['net_pnl']} roi={row['roi']}",
                flush=True,
            )
    return checked, new_scores, unresolved


def print_summary(output: Path) -> None:
    rows = [r for r in read_jsonl(output) if r.get("score_status") == "SETTLED_SCORED"]
    if not rows:
        print("summary settled=0", flush=True)
        return
    pnl = sum((D(r.get("net_pnl")) or Decimal("0") for r in rows), Decimal("0"))
    capital = sum((D(r.get("capital_at_risk")) or Decimal("0") for r in rows), Decimal("0"))
    wins = sum(1 for r in rows if r.get("won") is True)
    losses = len(rows) - wins
    roi = None if capital == 0 else pnl / capital
    print(
        f"summary settled={len(rows)} wins={wins} losses={losses} win_rate={wins/len(rows):.4f} "
        f"net_pnl={pnl} capital_at_risk={capital} roi={roi}",
        flush=True,
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Automatically score settled Weather Company paper fills.")
    p.add_argument("--fills", type=Path, default=Path("/data/weather/live/weather_company_paper_fills.jsonl"))
    p.add_argument("--output", type=Path, default=Path("/data/weather/live/weather_company_paper_settled.jsonl"))
    p.add_argument("--loop-seconds", type=int, default=300)
    p.add_argument("--once", action="store_true")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    print("mode=PAPER_SETTLEMENT_SCORER live_order_submission=false", flush=True)
    while True:
        checked, new_scores, unresolved = run_once(args)
        print(f"settlement_cycle checked={checked} new_scores={new_scores} unresolved={unresolved}", flush=True)
        print_summary(args.output)
        if args.once:
            return 0
        time.sleep(max(30, args.loop_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
