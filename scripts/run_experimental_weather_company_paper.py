from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx

from weather_alpha.backtest.causal_settlement import TimedTemperature, lock_gate, surface_state
from weather_alpha.markets.kalshi_weather_resolver import ticker_event_date
from weather_alpha.markets.source_family import detect_settlement_source_family
from weather_alpha.markets.weather_catalog import classify_kalshi_market
from weather_alpha.providers.surface import IemAsosOneMinuteArchive
from weather_alpha.settlement.reconstruction import local_standard_settlement_window
from weather_alpha.settlement.stations import station_clock

BASE = "https://external-api.kalshi.com/trade-api/v2"
SERIES_STATION = {
    "KXHIGHNY": "KNYC",
    "KXHIGHCHI": "KMDW",
    "KXHIGHMIA": "KMIA",
    "KXHIGHLAX": "KLAX",
    "KXHIGHDEN": "KDEN",
}


def D(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    return Decimal(str(value))


def get_json(client: httpx.Client, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    for attempt in range(6):
        r = client.get(BASE + path, params=params)
        if r.status_code != 429 and not 500 <= r.status_code < 600:
            r.raise_for_status()
            payload = r.json()
            return payload if isinstance(payload, dict) else {}
        time.sleep(min(20.0, 2 ** attempt))
    raise RuntimeError(f"retries exhausted: {path}")


def market_price(market: dict[str, Any], key: str) -> Decimal | None:
    value = market.get(key + "_dollars")
    if value in (None, ""):
        value = market.get(key)
        if value not in (None, ""):
            value = Decimal(str(value)) / Decimal("100")
    return D(value)


def kalshi_taker_fee(price: Decimal, contracts: int) -> Decimal:
    raw = Decimal("0.07") * Decimal(contracts) * price * (Decimal("1") - price)
    return (raw * Decimal("100")).to_integral_value(rounding="ROUND_CEILING") / Decimal("100")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"traded_events": {}, "pending": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"traded_events": {}, "pending": []}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


async def fetch_obs(station: str, start: datetime, end: datetime) -> list[TimedTemperature]:
    provider = IemAsosOneMinuteArchive()
    rows = await provider.fetch([station], start, end)
    return [TimedTemperature(r.valid_time, Decimal(str(r.temperature_f))) for r in rows if r.temperature_f is not None]


def contains(shape: str, lower: Decimal | None, upper: Decimal | None, x: Decimal) -> bool:
    if shape == "bucket" and lower is not None and upper is not None:
        return lower <= x <= upper
    if shape == "above" and lower is not None:
        return x >= lower
    if shape == "below" and upper is not None:
        return x <= upper
    return False


def provisional_probability(*, in_bucket: bool, lock_pass: bool, drop_f: Decimal | None, slope: Decimal | None) -> Decimal:
    """Deliberately simple forward-only heuristic, not a calibrated Weather Company model.

    It assigns confidence only after the late-day ASOS lock gate passes. The score
    is intentionally capped and is logged as PROVISIONAL_UNCALIBRATED so that it
    cannot be confused with the validated NWS/CLI model.
    """
    if not lock_pass:
        return Decimal("0.50")
    strength = Decimal("0.84")
    if drop_f is not None:
        strength += min(Decimal("0.06"), max(Decimal("0"), (drop_f - Decimal("1")) * Decimal("0.02")))
    if slope is not None and slope < 0:
        strength += min(Decimal("0.04"), abs(slope) * Decimal("2"))
    strength = min(Decimal("0.94"), max(Decimal("0.50"), strength))
    return strength if in_bucket else Decimal("1") - strength


@dataclass
class ContractView:
    series: str
    station: str
    day: date
    ticker: str
    event_id: str
    shape: str
    lower: Decimal | None
    upper: Decimal | None
    yes_bid: Decimal | None
    yes_ask: Decimal | None
    no_bid: Decimal | None
    no_ask: Decimal | None
    market: dict[str, Any]
    source_family: str


def fetch_contracts(series_ids: list[str]) -> list[ContractView]:
    out: list[ContractView] = []
    with httpx.Client(timeout=25.0, follow_redirects=True, headers={"User-Agent": "weather-alpha-paper/0.1"}) as client:
        for series in series_ids:
            station = SERIES_STATION.get(series)
            if station is None:
                continue
            sp = get_json(client, f"/series/{series}", {"include_product_metadata": "true"})
            series_row = sp.get("series", sp) if isinstance(sp, dict) else {}
            payload = get_json(client, "/markets", {"series_ticker": series, "status": "open", "limit": 1000})
            for listed in payload.get("markets") or []:
                if not isinstance(listed, dict):
                    continue
                ticker = str(listed.get("ticker") or "")
                if not ticker:
                    continue
                detail = get_json(client, f"/markets/{ticker}")
                market = dict(listed)
                if isinstance(detail.get("market"), dict):
                    market.update(detail["market"])
                event_id = str(market.get("event_ticker") or "")
                ep = get_json(client, f"/events/{event_id}") if event_id else {}
                event = ep.get("event", ep) if isinstance(ep, dict) else {}
                source = detect_settlement_source_family(market, event if isinstance(event, dict) else {}, series_row if isinstance(series_row, dict) else {})
                if source.family != "WEATHER_COMPANY":
                    continue
                rec = classify_kalshi_market(market)
                day = ticker_event_date(event_id)
                if day is None or rec.shape is None:
                    continue
                yb, ya = market_price(market, "yes_bid"), market_price(market, "yes_ask")
                nb, na = market_price(market, "no_bid"), market_price(market, "no_ask")
                if ya is None and nb is not None:
                    ya = Decimal("1") - nb
                if na is None and yb is not None:
                    na = Decimal("1") - yb
                out.append(ContractView(
                    series=series, station=station, day=day, ticker=ticker, event_id=event_id,
                    shape=rec.shape.value, lower=D(rec.lower), upper=D(rec.upper),
                    yes_bid=yb, yes_ask=ya, no_bid=nb, no_ask=na,
                    market=market, source_family=source.family,
                ))
    return out


def current_quote(ticker: str) -> dict[str, Any] | None:
    with httpx.Client(timeout=20.0, follow_redirects=True, headers={"User-Agent": "weather-alpha-paper/0.1"}) as client:
        payload = get_json(client, f"/markets/{ticker}")
        market = payload.get("market", payload)
        return market if isinstance(market, dict) else None


def settle_pending(args: argparse.Namespace, state: dict[str, Any]) -> None:
    now = datetime.now(timezone.utc)
    remain = []
    for item in state.get("pending", []):
        due = datetime.fromisoformat(str(item["fill_due"]).replace("Z", "+00:00"))
        if due > now:
            remain.append(item)
            continue
        market = current_quote(item["ticker"])
        if market is None:
            item["fill_status"] = "NO_MARKET_QUOTE"
            append_jsonl(args.fills_output, item)
            continue
        side = item["side"]
        ask = market_price(market, "yes_ask" if side == "YES" else "no_ask")
        if ask is None:
            other_bid = market_price(market, "no_bid" if side == "YES" else "yes_bid")
            ask = None if other_bid is None else Decimal("1") - other_bid
        if ask is None:
            item["fill_status"] = "NO_EXECUTABLE_ASK"
            append_jsonl(args.fills_output, item)
            continue
        contracts = args.contracts
        fee = kalshi_taker_fee(ask, contracts)
        item.update({
            "fill_status": "PAPER_FILLED",
            "fill_time": now.isoformat(),
            "fill_price": str(ask),
            "contracts": contracts,
            "estimated_taker_fee": str(fee),
            "capital_at_risk": str(ask * Decimal(contracts) + fee),
            "fill_model": f"LIVE_QUOTE_AFTER_{args.latency_seconds}S",
            "live_order_submission": False,
        })
        append_jsonl(args.fills_output, item)
        print(f"PAPER_FILL {item['ticker']} {side} price={ask} fee={fee} latency={args.latency_seconds}s", flush=True)
    state["pending"] = remain


def run_cycle(args: argparse.Namespace, state: dict[str, Any]) -> None:
    now = datetime.now(timezone.utc)
    settle_pending(args, state)
    contracts = fetch_contracts([x.strip().upper() for x in args.series.split(",") if x.strip()])
    by_event: dict[str, list[ContractView]] = {}
    for c in contracts:
        by_event.setdefault(c.event_id, []).append(c)

    obs_cache: dict[tuple[str, str], list[TimedTemperature]] = {}
    candidates: list[dict[str, Any]] = []

    for event_id, group in by_event.items():
        c0 = group[0]
        window = local_standard_settlement_window(station_clock(c0.station), c0.day)
        if not (window.start_utc <= now < window.end_utc):
            continue
        key = (c0.station, c0.day.isoformat())
        if key not in obs_cache:
            try:
                obs_cache[key] = asyncio.run(fetch_obs(c0.station, window.start_utc, now))
            except Exception as exc:
                print(f"obs_error station={c0.station} {type(exc).__name__}:{exc}", flush=True)
                obs_cache[key] = []
        state_surface = surface_state(obs_cache[key], as_of=now)
        lock_pass, lock_reasons = lock_gate(
            state_surface,
            min_minutes_since_high=args.min_minutes_since_high,
            min_drop_from_high_f=args.min_drop_from_high_f,
            max_positive_slope_f_per_min=args.max_positive_slope,
        )
        if state_surface.high_so_far_f is None:
            continue

        for c in group:
            in_bucket = contains(c.shape, c.lower, c.upper, state_surface.high_so_far_f)
            p_yes = provisional_probability(
                in_bucket=in_bucket,
                lock_pass=lock_pass,
                drop_f=state_surface.drop_from_high_f,
                slope=state_surface.slope_15m_f_per_min,
            )
            p_no = Decimal("1") - p_yes
            yes_edge = None if c.yes_ask is None else p_yes - c.yes_ask
            no_edge = None if c.no_ask is None else p_no - c.no_ask
            choices = []
            if yes_edge is not None and c.yes_ask is not None:
                choices.append((yes_edge, "YES", c.yes_ask, p_yes))
            if no_edge is not None and c.no_ask is not None:
                choices.append((no_edge, "NO", c.no_ask, p_no))
            if not choices:
                continue
            edge, side, ask, p_side = max(choices, key=lambda x: x[0])
            candidates.append({
                "event_id": event_id,
                "ticker": c.ticker,
                "series": c.series,
                "station": c.station,
                "settlement_date": c.day.isoformat(),
                "source_family": c.source_family,
                "side": side,
                "signal_time": now.isoformat(),
                "signal_yes_bid": None if c.yes_bid is None else str(c.yes_bid),
                "signal_yes_ask": None if c.yes_ask is None else str(c.yes_ask),
                "signal_no_bid": None if c.no_bid is None else str(c.no_bid),
                "signal_no_ask": None if c.no_ask is None else str(c.no_ask),
                "signal_entry_ask": str(ask),
                "provisional_probability_side": str(p_side),
                "provisional_probability_yes": str(p_yes),
                "gross_edge": str(edge),
                "high_so_far_f": str(state_surface.high_so_far_f),
                "latest_temp_f": None if state_surface.latest_temp_f is None else str(state_surface.latest_temp_f),
                "minutes_since_high": None if state_surface.minutes_since_high is None else str(state_surface.minutes_since_high),
                "drop_from_high_f": None if state_surface.drop_from_high_f is None else str(state_surface.drop_from_high_f),
                "slope_15m_f_per_min": None if state_surface.slope_15m_f_per_min is None else str(state_surface.slope_15m_f_per_min),
                "lock_gate_pass": lock_pass,
                "lock_gate_reasons": list(lock_reasons),
                "price_floor": str(args.price_floor),
                "minimum_edge": str(args.minimum_edge),
                "model_status": "PROVISIONAL_UNCALIBRATED_WEATHER_COMPANY_FORWARD_PAPER",
                "live_order_submission": False,
            })

    best_by_event: dict[str, dict[str, Any]] = {}
    for row in candidates:
        cur = best_by_event.get(row["event_id"])
        if cur is None or Decimal(row["gross_edge"]) > Decimal(cur["gross_edge"]):
            best_by_event[row["event_id"]] = row

    for event_id, row in sorted(best_by_event.items()):
        traded = state.setdefault("traded_events", {})
        if event_id in traded:
            continue
        edge = Decimal(row["gross_edge"])
        ask = Decimal(row["signal_entry_ask"])
        if not row["lock_gate_pass"] or edge < args.minimum_edge or ask < args.price_floor:
            continue
        fill_due = now.timestamp() + args.latency_seconds
        row["fill_due"] = datetime.fromtimestamp(fill_due, tz=timezone.utc).isoformat()
        row["paper_status"] = "PENDING_DELAYED_FILL"
        append_jsonl(args.signals_output, row)
        state.setdefault("pending", []).append(dict(row))
        traded[event_id] = {"ticker": row["ticker"], "signal_time": row["signal_time"]}
        print(
            f"PAPER_SIGNAL {row['ticker']} side={row['side']} ask={row['signal_entry_ask']} "
            f"p={row['provisional_probability_side']} edge={row['gross_edge']} high={row['high_so_far_f']} "
            f"fill_due={row['fill_due']}", flush=True,
        )

    print(
        f"cycle={now.isoformat()} weather_company_contracts={len(contracts)} events={len(by_event)} "
        f"pending={len(state.get('pending', []))} traded_events={len(state.get('traded_events', {}))}",
        flush=True,
    )
    save_state(args.state, state)


def main() -> int:
    p = argparse.ArgumentParser(description="Experimental Weather Company forward paper trader; NEVER submits live orders")
    p.add_argument("--series", default=",".join(SERIES_STATION))
    p.add_argument("--signals-output", type=Path, default=Path("/data/weather/live/weather_company_paper_signals.jsonl"))
    p.add_argument("--fills-output", type=Path, default=Path("/data/weather/live/weather_company_paper_fills.jsonl"))
    p.add_argument("--state", type=Path, default=Path("/data/weather/live/weather_company_paper_state.json"))
    p.add_argument("--loop-seconds", type=int, default=60)
    p.add_argument("--latency-seconds", type=int, default=300)
    p.add_argument("--contracts", type=int, default=1)
    p.add_argument("--price-floor", type=Decimal, default=Decimal("0.15"))
    p.add_argument("--minimum-edge", type=Decimal, default=Decimal("0.08"))
    p.add_argument("--min-minutes-since-high", type=Decimal, default=Decimal("60"))
    p.add_argument("--min-drop-from-high-f", type=Decimal, default=Decimal("1.0"))
    p.add_argument("--max-positive-slope", type=Decimal, default=Decimal("0.02"))
    args = p.parse_args()

    if args.loop_seconds < 10:
        raise SystemExit("--loop-seconds must be >=10")
    print("mode=EXPERIMENTAL_WEATHER_COMPANY_PAPER live_order_submission=false", flush=True)
    print("WARNING=model is provisional and uncalibrated; this process cannot submit orders", flush=True)
    state = load_state(args.state)
    while True:
        try:
            run_cycle(args, state)
        except KeyboardInterrupt:
            save_state(args.state, state)
            raise
        except Exception as exc:
            print(f"paper_cycle_error={type(exc).__name__}:{exc}", flush=True)
            save_state(args.state, state)
        time.sleep(args.loop_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
