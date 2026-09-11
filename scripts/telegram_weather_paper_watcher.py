#!/usr/bin/env python3
"""Read-only Telegram watcher for weather paper signals/fills and collector health.

Pattern intentionally mirrors the proven watcher in historysquared/kalshi-15m-lab,
but this file is independent and lives only in the weather repository.
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


def _fmt(value: Any, digits: int = 3, suffix: str = "") -> str:
    if value in (None, ""):
        return "n/a"
    try:
        return f"{float(value):.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return str(value)


def _local_timestamp(raw: Any, timezone_name: str) -> str:
    if not raw:
        return "n/a"
    value = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(ZoneInfo(timezone_name))
    millis = value.microsecond // 1000
    return f"{value.strftime('%Y-%m-%d %I:%M:%S')}.{millis:03d} {value.strftime('%p %Z')}"


def send_telegram(token: str, chat_id: str, text: str) -> None:
    data = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}
    ).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as response:
        body = json.loads(response.read().decode("utf-8"))
    if not body.get("ok"):
        raise RuntimeError(f"Telegram API error: {body}")


def format_weather_event(kind: str, payload: dict[str, Any], timezone_name: str) -> str:
    station = payload.get("station") or "?"
    ticker = payload.get("ticker") or "?"
    side = str(payload.get("side") or payload.get("tournament_side") or "?").upper()
    track = payload.get("track")
    ask = (
        payload.get("fill_price")
        or payload.get("signal_entry_ask")
        or payload.get("tournament_entry_ask")
        or payload.get("entry_ask")
    )
    gross = payload.get("gross_edge") or payload.get("tournament_gross_edge")
    net = payload.get("net_edge_after_fee") or payload.get("tournament_net_edge")
    p_side = payload.get("provisional_probability_side") or payload.get("tournament_probability_side") or payload.get("model_probability_side")
    ts_raw = payload.get("fill_time") or payload.get("signal_time") or payload.get("snapshot_time")
    temp = payload.get("latest_temp_f")
    high = payload.get("high_so_far_f")
    since = payload.get("minutes_since_high")
    drop = payload.get("drop_from_high_f")
    slope = payload.get("slope_15m_f_per_min")
    failures = payload.get("control_lock_failures") or payload.get("track_lock_reasons") or payload.get("lock_gate_reasons") or []
    if isinstance(failures, str):
        failures = [failures]
    icon = "🧪" if str(track or "").startswith("D_") else ("✅" if "FILL" in kind else "🚨")
    title = kind.replace("_", " ")
    lines = [
        f"{icon} WEATHER {title}",
        f"{station} | {side} @ {_fmt(ask, 3)}",
        f"{ticker}",
    ]
    if track:
        lines.append(f"Track {track}")
    if p_side not in (None, "") or gross not in (None, "") or net not in (None, ""):
        lines.append(f"Model {_fmt(p_side,3)} | Gross {_fmt(gross,3)} | Net {_fmt(net,3)}")
    lines.append(f"Temp {_fmt(temp,2)}F | High {_fmt(high,2)}F")
    lines.append(f"Since high {_fmt(since,1,'m')} | Drop {_fmt(drop,2,'F')} | 15m slope {_fmt(slope,4)}")
    if failures:
        lines.append("Gate: " + ", ".join(str(x) for x in failures[:4]))
    lines.append(_local_timestamp(ts_raw, timezone_name))
    lines.append("PAPER ONLY — no live order")
    return "\n".join(lines)


def format_health(payload: dict[str, Any], timezone_name: str) -> str:
    return "\n".join(
        [
            "⚠️ WEATHER DATA COLLECTOR",
            str(payload.get("error") or payload.get("last_error") or "collector health warning"),
            f"Reconnects {payload.get('reconnects', 'n/a')} | Generation {payload.get('generation', 'n/a')}",
            _local_timestamp(payload.get("at") or payload.get("last_message_at"), timezone_name),
        ]
    )


class JsonlTail:
    def __init__(self, path: Path, start_at_end: bool = True):
        self.path = path
        self.offset = path.stat().st_size if start_at_end and path.exists() else 0

    def poll(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        size = self.path.stat().st_size
        if size < self.offset:
            self.offset = 0
        rows: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as fh:
            fh.seek(self.offset)
            for line in fh:
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
            self.offset = fh.tell()
        return rows


def load_state(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"seen": [], "health_signature": None}


def save_state(path: Path, state: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def event_key(kind: str, payload: dict[str, Any]) -> str:
    return "|".join(
        [
            kind,
            str(payload.get("track") or ""),
            str(payload.get("event_id") or ""),
            str(payload.get("ticker") or ""),
            str(payload.get("side") or payload.get("tournament_side") or ""),
            str(payload.get("signal_time") or payload.get("fill_time") or ""),
        ]
    )


def main() -> int:
    p = argparse.ArgumentParser(description="Read-only Telegram alerts for weather paper research")
    p.add_argument("--poll-interval", type=float, default=0.5)
    p.add_argument("--timezone", default="America/Los_Angeles")
    p.add_argument("--state", type=Path, default=Path("/data/weather/live/weather_telegram_state.json"))
    p.add_argument("--replay-existing", action="store_true")
    p.add_argument("--test", action="store_true")
    p.add_argument("--paper-signals", type=Path, default=Path("/data/weather/live/weather_company_paper_signals.jsonl"))
    p.add_argument("--paper-fills", type=Path, default=Path("/data/weather/live/weather_company_paper_fills.jsonl"))
    p.add_argument("--tournament-signals", type=Path, default=Path("/data/weather/live/weather_company_tournament_signals.jsonl"))
    p.add_argument("--tournament-fills", type=Path, default=Path("/data/weather/live/weather_company_tournament_fills.jsonl"))
    p.add_argument("--diagnostic-signals", type=Path, default=Path("/data/weather/live/weather_company_diagnostic_signals.jsonl"))
    p.add_argument("--diagnostic-fills", type=Path, default=Path("/data/weather/live/weather_company_diagnostic_fills.jsonl"))
    p.add_argument("--collector-health", type=Path, default=Path("/data/weather/live/kalshi_l2_health.json"))
    args = p.parse_args()

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise SystemExit("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID must be set")
    if args.test:
        send_telegram(token, chat_id, "✅ Weather live paper watcher is connected. PAPER ONLY.")
        print("telegram test sent", flush=True)
        return 0

    sources = [
        ("PAPER_SIGNAL", args.paper_signals),
        ("PAPER_FILL", args.paper_fills),
        ("TOURNAMENT_SIGNAL", args.tournament_signals),
        ("TOURNAMENT_FILL", args.tournament_fills),
        ("DIAGNOSTIC_SIGNAL", args.diagnostic_signals),
        ("DIAGNOSTIC_FILL", args.diagnostic_fills),
    ]
    tails = {kind: JsonlTail(path, start_at_end=not args.replay_existing) for kind, path in sources}
    state = load_state(args.state)
    seen = set(str(x) for x in state.get("seen", []))
    health_mtime = 0
    health_signature = state.get("health_signature")
    print(f"weather telegram watcher ready sources={len(sources)} seen={len(seen)}", flush=True)

    while True:
        changed = False
        for kind, _ in sources:
            for payload in tails[kind].poll():
                key = event_key(kind, payload)
                if key in seen:
                    continue
                try:
                    send_telegram(token, chat_id, format_weather_event(kind, payload, args.timezone))
                except Exception as exc:
                    print(f"telegram failed kind={kind} key={key}: {type(exc).__name__}:{exc}", flush=True)
                    continue
                seen.add(key)
                changed = True
                print(f"telegram alerted {kind} {payload.get('ticker')}", flush=True)

        if args.collector_health.exists():
            mtime = args.collector_health.stat().st_mtime_ns
            if mtime != health_mtime:
                health_mtime = mtime
                try:
                    health = json.loads(args.collector_health.read_text(encoding="utf-8"))
                except Exception:
                    health = {}
                signature = str(health.get("error") or health.get("last_error") or "")
                if signature and signature != health_signature:
                    try:
                        send_telegram(token, chat_id, format_health(health, args.timezone))
                        health_signature = signature
                        changed = True
                    except Exception as exc:
                        print(f"telegram health alert failed: {type(exc).__name__}:{exc}", flush=True)

        if changed:
            compact_seen = list(seen)[-5000:]
            state = {"seen": compact_seen, "health_signature": health_signature}
            save_state(args.state, state)
        time.sleep(max(0.2, args.poll_interval))


if __name__ == "__main__":
    raise SystemExit(main())
