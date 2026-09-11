from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


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
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                out.append(row)
    return out


def parse_day(value: str) -> date:
    return date.fromisoformat(value)


def row_day(row: dict[str, Any]) -> date | None:
    for key in ("signal_time", "fill_time", "snapshot_time", "settlement_ts", "generated_at"):
        value = row.get(key)
        if not value:
            continue
        try:
            return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
        except ValueError:
            continue
    value = row.get("settlement_date")
    if value:
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            pass
    return None


def unique_latest(rows: list[dict[str, Any]], key_fields: tuple[str, ...]) -> list[dict[str, Any]]:
    seen: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        key = tuple(row.get(k) for k in key_fields)
        seen[key] = row
    return list(seen.values())


def pct(value: Decimal | None) -> str:
    if value is None:
        return "-"
    return f"{value * Decimal('100'):.2f}%"


def build_report(args: argparse.Namespace) -> dict[str, Any]:
    target = args.day
    signals = [r for r in read_jsonl(args.signals) if row_day(r) == target]
    fills = [r for r in read_jsonl(args.fills) if row_day(r) == target and r.get("fill_status") == "PAPER_FILLED"]
    settled_all = [r for r in read_jsonl(args.settled) if r.get("score_status") == "SETTLED_SCORED"]
    settled = [r for r in settled_all if row_day(r) == target or str(r.get("settlement_date") or "")[:10] == target.isoformat()]
    dash_all = [r for r in read_jsonl(args.dashboard_history) if row_day(r) == target]
    contracts_all = [r for r in read_jsonl(args.contract_history) if row_day(r) == target]

    dash = unique_latest(dash_all, ("event_id",))
    contracts_latest = unique_latest(contracts_all, ("ticker",))

    wins = sum(1 for r in settled if r.get("won") is True)
    losses = sum(1 for r in settled if r.get("won") is False)
    pnl = sum((D(r.get("net_pnl")) or Decimal("0") for r in settled), Decimal("0"))
    capital = sum((D(r.get("capital_at_risk")) or Decimal("0") for r in settled), Decimal("0"))
    roi = None if capital == 0 else pnl / capital

    reasons = Counter(str(r.get("decision") or "UNKNOWN") for r in contracts_latest)
    eligible_rows = [r for r in contracts_latest if r.get("decision") == "PAPER_TRADE_ELIGIBLE"]

    signal_events = {str(r.get("event_id") or "") for r in signals}
    missed = [r for r in eligible_rows if str(r.get("event_id") or "") not in signal_events]
    missed.sort(key=lambda r: D(r.get("net_edge")) or D(r.get("gross_edge")) or Decimal("-999"), reverse=True)

    newest_audit = sorted(
        contracts_latest,
        key=lambda r: str(r.get("snapshot_time") or ""),
        reverse=True,
    )[: args.audit_rows]

    by_station = Counter(str(r.get("station") or "UNKNOWN") for r in signals)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "day": target.isoformat(),
        "mode": "WEATHER_COMPANY_PAPER_DAILY_REPORT",
        "live_order_submission": False,
        "summary": {
            "signals": len(signals),
            "fills": len(fills),
            "settled_trades": len(settled),
            "wins": wins,
            "losses": losses,
            "win_rate": None if not settled else wins / len(settled),
            "net_pnl": str(pnl),
            "capital_at_risk": str(capital),
            "roi": None if roi is None else str(roi),
            "dashboard_events": len(dash),
            "dashboard_contracts": len(contracts_latest),
            "eligible_latest": len(eligible_rows),
            "missed_latest": len(missed),
        },
        "signal_counts_by_station": dict(sorted(by_station.items())),
        "no_trade_reasons_latest": dict(sorted(reasons.items())),
        "signals": signals,
        "fills": fills,
        "settled": settled,
        "missed_opportunities_latest": missed[: args.missed_rows],
        "newest_manual_audit_rows": newest_audit,
    }


def render_text(report: dict[str, Any]) -> str:
    s = report["summary"]
    win_rate_text = "-" if s["win_rate"] is None else f"{s['win_rate']:.4f}"
    roi_text = "-" if s["roi"] is None else pct(D(s["roi"]))
    lines = [
        f"WEATHER COMPANY PAPER DAILY REPORT — {report['day']}",
        f"generated={report['generated_at']} live_order_submission=false",
        "",
        "SUMMARY",
        f"signals={s['signals']} fills={s['fills']} settled={s['settled_trades']} wins={s['wins']} losses={s['losses']} "
        f"win_rate={win_rate_text} net_pnl={s['net_pnl']} capital_at_risk={s['capital_at_risk']} roi={roi_text}",
        f"dashboard_events={s['dashboard_events']} dashboard_contracts={s['dashboard_contracts']} "
        f"eligible_latest={s['eligible_latest']} missed_latest={s['missed_latest']}",
        "",
        "NO-TRADE / DECISION REASONS (latest snapshot per contract)",
    ]
    for reason, count in report["no_trade_reasons_latest"].items():
        lines.append(f"{reason}: {count}")

    lines.extend(["", "SIGNALS"])
    if not report["signals"]:
        lines.append("none")
    for r in report["signals"]:
        lines.append(
            f"{r.get('signal_time')} {r.get('station')} {r.get('ticker')} side={r.get('side')} "
            f"ask={r.get('signal_entry_ask')} p={r.get('provisional_probability_side')} edge={r.get('gross_edge')} "
            f"high={r.get('high_so_far_f')}"
        )

    lines.extend(["", "FILLS"])
    if not report["fills"]:
        lines.append("none")
    for r in report["fills"]:
        lines.append(
            f"{r.get('fill_time')} {r.get('station')} {r.get('ticker')} side={r.get('side')} "
            f"fill={r.get('fill_price')} fee={r.get('estimated_taker_fee')} capital={r.get('capital_at_risk')}"
        )

    lines.extend(["", "SETTLED / P&L"])
    if not report["settled"]:
        lines.append("none")
    for r in report["settled"]:
        lines.append(
            f"{r.get('ticker')} side={r.get('side')} result={r.get('market_result')} won={r.get('won')} "
            f"fill={r.get('fill_price')} pnl={r.get('net_pnl')} roi={r.get('roi')}"
        )

    lines.extend(["", "TOP MISSED ELIGIBLE OPPORTUNITIES (latest snapshot only)"])
    if not report["missed_opportunities_latest"]:
        lines.append("none")
    for r in report["missed_opportunities_latest"]:
        lines.append(
            f"{r.get('snapshot_time')} {r.get('station')} {r.get('ticker')} side={r.get('side')} "
            f"p_yes={r.get('model_probability_yes')} ask={r.get('entry_ask')} gross_edge={r.get('gross_edge')} "
            f"net_edge={r.get('net_edge')} decision={r.get('decision')}"
        )

    lines.extend(["", "NEWEST MANUAL-AUDIT ROWS"])
    for r in report["newest_manual_audit_rows"]:
        lines.append(
            f"{r.get('snapshot_time')} {r.get('station')} {r.get('ticker')} bucket={r.get('bucket_label')} "
            f"temp={r.get('latest_temp_f')} high={r.get('high_so_far_f')} rem_min={r.get('minutes_remaining')} "
            f"yes={r.get('yes_bid')}/{r.get('yes_ask')} no={r.get('no_bid')}/{r.get('no_ask')} "
            f"p_yes={r.get('model_probability_yes')} side={r.get('side')} ask={r.get('entry_ask')} "
            f"net_edge={r.get('net_edge')} decision={r.get('decision')}"
        )

    return "\n".join(lines) + "\n"


def main() -> int:
    p = argparse.ArgumentParser(description="Build a compact daily report for the Weather Company forward paper test")
    p.add_argument("--day", type=parse_day, default=datetime.now(timezone.utc).date())
    p.add_argument("--signals", type=Path, default=Path("/data/weather/live/weather_company_paper_signals.jsonl"))
    p.add_argument("--fills", type=Path, default=Path("/data/weather/live/weather_company_paper_fills.jsonl"))
    p.add_argument("--settled", type=Path, default=Path("/data/weather/live/weather_company_paper_settled.jsonl"))
    p.add_argument("--dashboard-history", type=Path, default=Path("/data/weather/live/weather_company_dashboard_history.jsonl"))
    p.add_argument("--contract-history", type=Path, default=Path("/data/weather/live/weather_company_contract_history.jsonl"))
    p.add_argument("--text-output", type=Path, default=Path("/data/weather/live/weather_company_daily_report.txt"))
    p.add_argument("--json-output", type=Path, default=Path("/data/weather/live/weather_company_daily_report.json"))
    p.add_argument("--missed-rows", type=int, default=10)
    p.add_argument("--audit-rows", type=int, default=20)
    args = p.parse_args()

    report = build_report(args)
    args.text_output.parent.mkdir(parents=True, exist_ok=True)
    args.text_output.write_text(render_text(report), encoding="utf-8")
    args.json_output.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(f"report_day={report['day']} output={args.text_output} json={args.json_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
