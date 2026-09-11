from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pyarrow.parquet as pq

from weather_alpha.backtest.causal_settlement import TimedTemperature, empirical_settlement_posterior, lock_gate, surface_state
from weather_alpha.markets.kalshi_weather_resolver import enrich_catalog_record, resolve_weather_rules
from weather_alpha.markets.weather_catalog import classify_kalshi_market
from weather_alpha.providers.surface import IemAsosOneMinuteArchive
from weather_alpha.settlement.reconstruction import local_standard_settlement_window
from weather_alpha.settlement.stations import station_clock

BASE = "https://external-api.kalshi.com/trade-api/v2"
DEFAULT_SERIES = ("KXHIGHNY", "KXHIGHCHI", "KXHIGHMIA", "KXHIGHLAX", "KXHIGHDEN")


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


def load_prior_basis(path: Path) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if not path.exists():
        return out
    for row in pq.read_table(path).to_pylist():
        if row.get("qc_status") == "PASS" and D(row.get("cli_minus_asos_high_f")) is not None:
            out[str(row.get("station") or "").upper()].append(row)
    return out


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def fetch_current_catalog(series_ids: list[str], *, raw_audit_output: Path | None = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Fetch open weather markets using full per-market payloads before resolution.

    Kalshi's list-markets payload can omit rule/source text that is available from
    the individual market endpoint. The resolver must see the full official
    payload or fail closed. This function therefore discovers tickers from the
    list endpoint, then hydrates every ticker with /markets/{ticker}, and stores
    the exact market/event/series inputs used by the resolver for auditability.
    """
    resolved: list[dict[str, Any]] = []
    raw_markets: list[dict[str, Any]] = []
    fetched_at = datetime.now(timezone.utc).isoformat()

    with httpx.Client(timeout=25.0, follow_redirects=True, headers={"User-Agent":"weather-alpha-shadow/0.2"}) as client:
        series_cache: dict[str, dict[str, Any]] = {}
        event_cache: dict[str, dict[str, Any]] = {}

        for series in series_ids:
            sp = get_json(client, f"/series/{series}", {"include_product_metadata":"true"})
            srow = sp.get("series", sp)
            if isinstance(srow, dict):
                series_cache[series] = srow

            payload = get_json(client, "/markets", {"series_ticker": series, "status":"open", "limit":1000})
            markets = payload.get("markets") or []

            for listed_market in markets:
                if not isinstance(listed_market, dict):
                    continue
                ticker = str(listed_market.get("ticker") or "")
                if not ticker:
                    continue

                # Critical: hydrate the slim list payload with the full official
                # market record. Never certify EXACT from inferred city/ticker.
                detail_payload = get_json(client, f"/markets/{ticker}")
                detail_market = detail_payload.get("market", detail_payload)
                market = dict(listed_market)
                if isinstance(detail_market, dict):
                    market.update(detail_market)
                raw_markets.append(market)

                event_id = str(market.get("event_ticker") or "")
                if event_id and event_id not in event_cache:
                    ep = get_json(client, f"/events/{event_id}")
                    erow = ep.get("event", ep)
                    if isinstance(erow, dict):
                        event_cache[event_id] = erow

                event = event_cache.get(event_id)
                series_row = series_cache.get(series)
                rec = classify_kalshi_market(market)
                evidence = resolve_weather_rules(market, event, series_row)
                enriched = enrich_catalog_record(rec, evidence)

                if raw_audit_output is not None:
                    append_jsonl(raw_audit_output, {
                        "fetched_at": fetched_at,
                        "series_ticker": series,
                        "contract_id": ticker,
                        "market": market,
                        "event": event,
                        "series": series_row,
                        "resolution": {
                            "status": enriched.status.value,
                            "station": evidence.station,
                            "settlement_date": evidence.settlement_date.isoformat() if evidence.settlement_date else None,
                            "exact": evidence.exact,
                            "series_drift_risk": evidence.series_drift_risk,
                            "station_evidence": list(evidence.station_evidence),
                            "date_evidence": list(evidence.date_evidence),
                            "source_evidence": list(evidence.source_evidence),
                            "reasons": list(evidence.reasons),
                        },
                    })

                resolved.append({
                    "contract_id": enriched.contract_id,
                    "event_id": enriched.event_id,
                    "status": enriched.status.value,
                    "measurement": enriched.measurement.value,
                    "station": enriched.station,
                    "settlement_date": enriched.settlement_date.isoformat() if enriched.settlement_date else None,
                    "weather_event_id": enriched.weather_event_id,
                    "shape": enriched.shape.value if enriched.shape else None,
                    "lower": enriched.lower,
                    "upper": enriched.upper,
                    "settlement_source": enriched.settlement_source,
                    "reasons": list(enriched.reasons),
                    "station_evidence": list(evidence.station_evidence),
                    "date_evidence": list(evidence.date_evidence),
                    "source_evidence": list(evidence.source_evidence),
                    "series_drift_risk": evidence.series_drift_risk,
                    "market": market,
                })
    return resolved, raw_markets


async def fetch_obs(station: str, start: datetime, end: datetime) -> list[TimedTemperature]:
    provider = IemAsosOneMinuteArchive()
    try:
        rows = await provider.fetch([station], start, end)
    except Exception:
        return []
    out = []
    for row in rows:
        if row.temperature_f is not None:
            out.append(TimedTemperature(row.valid_time, Decimal(str(row.temperature_f))))
    return out


def evaluate_once(args: argparse.Namespace, prior_by_station: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    series_ids = [x.strip().upper() for x in args.series.split(",") if x.strip()]
    catalog, _ = fetch_current_catalog(series_ids, raw_audit_output=args.raw_audit_output)
    exact = [r for r in catalog if r.get("status") == "EXACT"]
    counts = Counter(r.get("status") for r in catalog)
    print(f"as_of={now.isoformat()} current_contracts={len(catalog)} status_counts={dict(counts)} exact={len(exact)}")

    rows: list[dict[str, Any]] = []
    obs_cache: dict[tuple[str, str], list[TimedTemperature]] = {}
    for contract in catalog:
        market = contract["market"]
        ticker = str(contract.get("contract_id") or "")
        yes_bid = market_price(market, "yes_bid")
        yes_ask = market_price(market, "yes_ask")
        no_bid = market_price(market, "no_bid")
        no_ask = market_price(market, "no_ask")
        if yes_ask is None and no_bid is not None:
            yes_ask = Decimal("1") - no_bid
        if no_ask is None and yes_bid is not None:
            no_ask = Decimal("1") - yes_bid

        row: dict[str, Any] = {
            "snapshot_time": now.isoformat(),
            "mode": "SHADOW_ONLY_NO_ORDER_SUBMISSION",
            "contract_id": ticker,
            "event_id": contract.get("event_id"),
            "catalog_status": contract.get("status"),
            "station": contract.get("station"),
            "settlement_date": contract.get("settlement_date"),
            "shape": contract.get("shape"),
            "lower": contract.get("lower"),
            "upper": contract.get("upper"),
            "yes_bid": None if yes_bid is None else str(yes_bid),
            "yes_ask": None if yes_ask is None else str(yes_ask),
            "no_bid": None if no_bid is None else str(no_bid),
            "no_ask": None if no_ask is None else str(no_ask),
            "volume_fp": market.get("volume_fp"),
            "open_interest_fp": market.get("open_interest_fp"),
            "market_updated_time": market.get("updated_time"),
            "catalog_reasons": contract.get("reasons"),
            "station_evidence": contract.get("station_evidence"),
            "date_evidence": contract.get("date_evidence"),
            "source_evidence": contract.get("source_evidence"),
            "series_drift_risk": contract.get("series_drift_risk"),
            "price_floor": str(args.price_floor),
            "minimum_edge": str(args.minimum_edge),
            "benchmark_latency_seconds": args.benchmark_latency_seconds,
        }

        if contract.get("status") != "EXACT" or not contract.get("station") or not contract.get("settlement_date"):
            row["decision"] = "AUDIT_ONLY_UNRESOLVED_NO_TRADE"
            rows.append(row)
            continue

        station = str(contract["station"]).upper()
        day = date.fromisoformat(str(contract["settlement_date"])[:10])
        window = local_standard_settlement_window(station_clock(station), day)
        if not (window.start_utc <= now < window.end_utc):
            row["decision"] = "OUTSIDE_SETTLEMENT_WINDOW"
            rows.append(row)
            continue

        key = (station, day.isoformat())
        if key not in obs_cache:
            obs_cache[key] = asyncio.run(fetch_obs(station, window.start_utc, now))
        state = surface_state(obs_cache[key], as_of=now)
        locked, reasons = lock_gate(
            state,
            min_minutes_since_high=args.min_minutes_since_high,
            min_drop_from_high_f=args.min_drop_from_high_f,
            max_positive_slope_f_per_min=args.max_positive_slope,
        )
        prior = [
            D(x.get("cli_minus_asos_high_f"))
            for x in prior_by_station.get(station, [])
            if str(x.get("settlement_date") or "")[:10] < day.isoformat()
        ]
        prior = [x for x in prior if x is not None]
        posterior = None
        if state.high_so_far_f is not None:
            posterior = empirical_settlement_posterior(
                high_so_far_f=state.high_so_far_f,
                prior_basis_samples_f=prior,
                shape=str(contract.get("shape") or ""),
                lower=D(contract.get("lower")),
                upper=D(contract.get("upper")),
                minimum_samples=args.minimum_prior_samples,
            )
        p_yes = None if posterior is None else posterior.probability_yes
        yes_edge = None if p_yes is None or yes_ask is None else p_yes - yes_ask
        p_no = None if p_yes is None else Decimal("1") - p_yes
        no_edge = None if p_no is None or no_ask is None else p_no - no_ask

        candidates: list[tuple[Decimal, str, Decimal | None]] = []
        if yes_edge is not None:
            candidates.append((yes_edge, "YES", yes_ask))
        if no_edge is not None:
            candidates.append((no_edge, "NO", no_ask))
        best = max(candidates, default=None, key=lambda x: x[0])
        decision = "NO_EDGE"
        if p_yes is None:
            decision = "INSUFFICIENT_PRIOR"
        elif not locked:
            decision = "LOCK_GATE_FAIL"
        elif best is not None and best[0] >= args.minimum_edge:
            if best[2] is not None and best[2] >= args.price_floor:
                decision = "SHADOW_SIGNAL"
            else:
                decision = "PRICE_FLOOR_REJECT"

        row.update({
            "decision": decision,
            "lock_gate_pass": locked,
            "lock_gate_reasons": list(reasons),
            "observations_used": state.observations_used,
            "high_so_far_f": None if state.high_so_far_f is None else str(state.high_so_far_f),
            "latest_temp_f": None if state.latest_temp_f is None else str(state.latest_temp_f),
            "minutes_since_high": None if state.minutes_since_high is None else str(state.minutes_since_high),
            "drop_from_high_f": None if state.drop_from_high_f is None else str(state.drop_from_high_f),
            "slope_15m_f_per_min": None if state.slope_15m_f_per_min is None else str(state.slope_15m_f_per_min),
            "prior_basis_samples": len(prior),
            "posterior_yes_probability": None if p_yes is None else str(p_yes),
            "gross_yes_edge": None if yes_edge is None else str(yes_edge),
            "gross_no_edge": None if no_edge is None else str(no_edge),
            "candidate_side": None if best is None else best[1],
            "candidate_price": None if best is None or best[2] is None else str(best[2]),
            "candidate_gross_edge": None if best is None else str(best[0]),
            "causality": "STRICTLY_PRIOR_SETTLEMENT_BASIS_PLUS_ASOS_AT_OR_BEFORE_SNAPSHOT",
        })
        rows.append(row)

    for row in rows:
        append_jsonl(args.output, row)

    print("ticker | status | station | day | yes_bid/ask | no_bid/ask | p_yes | decision")
    for row in sorted(rows, key=lambda r: (str(r.get("settlement_date")), str(r.get("contract_id")))):
        print(
            f"{row.get('contract_id')} | {row.get('catalog_status')} | {row.get('station')} | {row.get('settlement_date')} | "
            f"{row.get('yes_bid')}/{row.get('yes_ask')} | {row.get('no_bid')}/{row.get('no_ask')} | "
            f"{row.get('posterior_yes_probability')} | {row.get('decision')}"
        )
    print(f"snapshot_rows={len(rows)} shadow_signals={sum(r.get('decision') == 'SHADOW_SIGNAL' for r in rows)} output={args.output}")
    return rows


def main() -> int:
    p = argparse.ArgumentParser(description="Live Kalshi daily-high settlement shadow monitor; never submits orders")
    p.add_argument("--series", default=",".join(DEFAULT_SERIES))
    p.add_argument("--history", type=Path, default=Path("/data/weather/results/settlement_reconstruction/cli_asos_history.parquet"))
    p.add_argument("--output", type=Path, default=Path("/data/weather/live/settlement_shadow.jsonl"))
    p.add_argument("--raw-audit-output", type=Path, default=Path("/data/weather/live/current_market_resolution_audit.jsonl"))
    p.add_argument("--price-floor", type=Decimal, default=Decimal("0.15"))
    p.add_argument("--minimum-edge", type=Decimal, default=Decimal("0.05"))
    p.add_argument("--minimum-prior-samples", type=int, default=20)
    p.add_argument("--min-minutes-since-high", type=Decimal, default=Decimal("60"))
    p.add_argument("--min-drop-from-high-f", type=Decimal, default=Decimal("1.0"))
    p.add_argument("--max-positive-slope", type=Decimal, default=Decimal("0.02"))
    p.add_argument("--benchmark-latency-seconds", type=int, default=300)
    p.add_argument("--loop-seconds", type=int, default=0, help="0 = one snapshot; otherwise repeat forever")
    args = p.parse_args()

    prior = load_prior_basis(args.history)
    print("mode=SHADOW_ONLY_NO_ORDER_SUBMISSION live_order_submission=false")
    while True:
        try:
            evaluate_once(args, prior)
        except Exception as exc:
            print(f"shadow_cycle_error={type(exc).__name__}:{exc}", flush=True)
        if args.loop_seconds <= 0:
            break
        time.sleep(max(10, args.loop_seconds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())