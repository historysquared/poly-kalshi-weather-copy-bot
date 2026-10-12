#!/usr/bin/env python3
"""Read-only Telegram watcher for weather paper signals/fills and collector health.

Pattern intentionally mirrors the proven watcher in historysquared/kalshi-15m-lab,
but this file is independent and lives only in the weather repository.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


from weather_alpha.ops.health import assess_health, health_alert_transitions

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



def get_telegram_updates(token: str, offset: int | None) -> list[dict[str, Any]]:
    params: dict[str, str] = {"timeout": "0", "allowed_updates": json.dumps(["message"])}
    if offset is not None:
        params["offset"] = str(offset)
    url = f"https://api.telegram.org/bot{token}/getUpdates?{urllib.parse.urlencode(params)}"
    with urllib.request.urlopen(url, timeout=10) as response:
        body = json.loads(response.read().decode("utf-8"))
    if not body.get("ok"):
        raise RuntimeError(f"Telegram getUpdates error: {body}")
    return [x for x in body.get("result", []) if isinstance(x, dict)]


def _json_file(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def format_contrail_trigger(payload: dict[str, Any], timezone_name: str) -> str:
    return "\n".join([
        f"☁️ CONTRAIL Q90 RESEARCH TRIGGER — {payload.get('city') or payload.get('location_key')}",
        f"Prior eve 18-21: {_fmt(payload.get('prior_evening_count'),0)} | {_fmt(payload.get('prior_evening_percentile'),0,'%ile')} | q90 threshold {_fmt(payload.get('prior_evening_q90'),1)}",
        f"Today contrails {_fmt(payload.get('today_contrail_count'),0)} | matched-time {_fmt(payload.get('matched_window_percentile'),0,'%ile')}",
        f"NBM high {_fmt(payload.get('nbm00z_high_f'),1,'F')} ± {_fmt(payload.get('nbm00z_sd_f'),1,'F')}",
        f"Kalshi model-leading bucket: {payload.get('kalshi_top_title') or 'n/a'}",
        "Trade: ABSTAIN — corrected feature is being forward monitored but is not yet a validated executable rule.",
        _local_timestamp(payload.get('signal_time'), timezone_name),
        "RESEARCH/PAPER ONLY — no live order",
    ])


def format_strategy_health(payload: dict[str, Any], timezone_name: str) -> str:
    status = str(payload.get("status") or "UNKNOWN").upper()
    icon = {"OK": "✅", "ABSTAIN": "⏸️", "DEGRADED": "⚠️", "ERROR": "🛑"}.get(status, "ℹ️")
    lines = [
        f"{icon} WEATHER STRATEGY {status}",
        str(payload.get("component") or "unknown"),
        f"Reason: {payload.get('reason') or 'n/a'}",
    ]
    issues = payload.get("issues") or payload.get("feature_failures") or payload.get("missing_components")
    if issues:
        lines.append("Details: " + str(issues)[:700])
    previous = payload.get("previous_status")
    if previous:
        lines.append(f"Previous: {previous} — {payload.get('previous_reason') or 'n/a'}")
    lines.append(_local_timestamp(payload.get("updated_at"), timezone_name))
    lines.append("PAPER ONLY — signals fail closed when required data are unavailable")
    return "\n".join(lines)


def _normalize_location(value: str) -> str:
    return "_".join(value.strip().lower().replace("/", " ").replace("-", " ").split())


def _location_aliases(config_path: Path) -> tuple[dict[str, str], list[str]]:
    try:
        import yaml
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        payload = {}
    locations = payload.get("locations") or {}
    aliases: dict[str, str] = {}
    for key, row in locations.items() if isinstance(locations, dict) else []:
        if not isinstance(row, dict):
            continue
        k = str(key)
        aliases[_normalize_location(k)] = k
        aliases[_normalize_location(str(row.get("name") or k))] = k
        station = str(row.get("settlement_station") or "").lower()
        if station:
            aliases[station] = k
    aliases.update({
        "nyc": "new_york_city", "new_york": "new_york_city", "new_york_city": "new_york_city",
        "la": "los_angeles", "sf": "san_francisco", "dc": "washington_dc",
        "dfw": "dallas", "nola": "new_orleans", "san_diego": "san_diego",
    })
    return aliases, sorted(str(x) for x in locations)


def _midrank(values: list[float], target: float | None) -> float | None:
    if target is None or not values:
        return None
    return 100.0 * (sum(v < target for v in values) + 0.5 * sum(v == target for v in values)) / len(values)


def attach_contrail_percentiles(rows: list[dict[str, Any]], config_path: Path, history_path: Path) -> list[dict[str, Any]]:
    try:
        import yaml
        locations = (yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}).get("locations") or {}
        history = json.loads(history_path.read_text(encoding="utf-8"))
    except Exception as exc:
        for row in rows:
            if isinstance(row, dict):
                row["percentile_error"] = f"{type(exc).__name__}:{exc}"
        return rows
    by_key: dict[str, list[dict[str, Any]]] = {}
    for hist in history if isinstance(history, list) else []:
        if isinstance(hist, dict) and not hist.get("error"):
            by_key.setdefault(str(hist.get("location_key") or ""), []).append(hist)
    now_utc = datetime.now(ZoneInfo("UTC"))
    for row in rows:
        if not isinstance(row, dict) or row.get("error"):
            continue
        key = str(row.get("key") or "")
        loc = locations.get(key) if isinstance(locations, dict) else None
        if not isinstance(loc, dict):
            continue
        tz = ZoneInfo(str(loc.get("timezone") or "UTC"))
        local_now = now_utc.astimezone(tz)
        completed_hour = local_now.hour
        today = local_now.date().isoformat()
        current_hours: dict[int, int] = {}
        for raw_hour, count in (row.get("hourly_counts") or {}).items():
            try:
                dt = datetime.fromisoformat(str(raw_hour).replace("Z", "+00:00")).astimezone(tz)
                if dt.date().isoformat() == today:
                    current_hours[dt.hour] = current_hours.get(dt.hour, 0) + int(count)
            except Exception:
                continue
        last_hour = completed_hour - 1 if completed_hour >= 1 else None
        current_last = None if last_hour is None else float(current_hours.get(last_hour, 0))
        three_hours = list(range(max(0, completed_hour - 3), completed_hour))
        current_three = float(sum(current_hours.get(h, 0) for h in three_hours))
        current_cumulative = float(sum(current_hours.get(h, 0) for h in range(completed_hour)))

        prior_rows = sorted(
            [h for h in by_key.get(key, []) if str(h.get("local_date") or "") < today],
            key=lambda x: str(x.get("local_date") or ""),
        )[-30:]
        last_values: list[float] = []
        three_values: list[float] = []
        cumulative_values: list[float] = []
        for hist in prior_rows:
            hh = {int(k): int(v) for k, v in (hist.get("hourly_counts") or {}).items()}
            if last_hour is not None:
                last_values.append(float(hh.get(last_hour, 0)))
            three_values.append(float(sum(hh.get(h, 0) for h in three_hours)))
            cumulative_values.append(float(sum(hh.get(h, 0) for h in range(completed_hour))))

        prior_evening_count = None
        prior_evening_pct = None
        if prior_rows:
            yesterday = prior_rows[-1]
            yhh = {int(k): int(v) for k, v in (yesterday.get("hourly_counts") or {}).items()}
            prior_evening_count = float(sum(yhh.get(h, 0) for h in range(18, 21)))
            comparison = []
            for hist in prior_rows[:-1]:
                hh = {int(k): int(v) for k, v in (hist.get("hourly_counts") or {}).items()}
                comparison.append(float(sum(hh.get(h, 0) for h in range(18, 21))))
            prior_evening_pct = _midrank(comparison, prior_evening_count)

        row.update({
            "completed_hour_local": completed_hour,
            "last_completed_hour_count": current_last,
            "last_completed_hour_percentile": _midrank(last_values, current_last),
            "rolling_3h_count": current_three,
            "rolling_3h_percentile": _midrank(three_values, current_three),
            "cumulative_completed_hours_count": current_cumulative,
            "matched_time_percentile": _midrank(cumulative_values, current_cumulative),
            "prior_evening_count": prior_evening_count,
            "prior_evening_percentile": prior_evening_pct,
            "percentile_baseline_days": len(prior_rows),
        })
    return rows


def _format_contrail_rows(rows: list[dict[str, Any]], timezone_name: str, *, detail: bool) -> str:
    good = [r for r in rows if isinstance(r, dict) and not r.get("error")]
    bad = [r for r in rows if isinstance(r, dict) and r.get("error")]
    if detail and len(good) == 1:
        r = good[0]
        lines = [
            f"☁️ CONTRAILS NOW — {r.get('name')}",
            f"Detections {r.get('detection_count')} | Frames {r.get('unique_detection_frames')}",
            f"In-box line {float(r.get('total_length_km') or 0):.0f} km | Nearest {_fmt(r.get('nearest_detection_km'),1,' km')}",
            f"Peak hour {r.get('peak_hour_count')} | Last {_local_timestamp(r.get('last_detection_time'), timezone_name)}",
            f"CFI {_fmt(r.get('max_cfi'),2)} | Persistent P {_fmt(r.get('max_persistent_formation_probability'),1,'%')}",
            f"Last full hour {_fmt(r.get('last_completed_hour_count'),0)} ({_fmt(r.get('last_completed_hour_percentile'),0,'%ile')}) | 3h {_fmt(r.get('rolling_3h_count'),0)} ({_fmt(r.get('rolling_3h_percentile'),0,'%ile')})",
            f"Today complete hrs {_fmt(r.get('cumulative_completed_hours_count'),0)} | matched-time {_fmt(r.get('matched_time_percentile'),0,'%ile')}",
            f"Prior eve 18-21 {_fmt(r.get('prior_evening_count'),0)} | {_fmt(r.get('prior_evening_percentile'),0,'%ile')} | baseline n={r.get('percentile_baseline_days','n/a')}",
        ]
        if r.get("forecast_observation_disagreement"):
            lines.append("⚠️ Forecast model currently shows no contrail signal despite observed detections.")
        lines.append("Fresh Google Contrails API pull. CFI/forcing are not surface-temperature degrees.")
        return "\n".join(lines)
    lines = ["☁️ CONTRAILS NOW — fresh API pull", "City | detections | frames | in-box km"]
    for r in sorted(good, key=lambda x: int(x.get("detection_count") or 0), reverse=True):
        lines.append(
            f"{r.get('name')}: {r.get('detection_count')} | {r.get('unique_detection_frames')} | {float(r.get('total_length_km') or 0):.0f} | matched {_fmt(r.get('matched_time_percentile'),0,'%ile')}"
        )
    for r in bad[:5]:
        lines.append(f"⚠️ {r.get('name')}: {r.get('error')}")
    lines.append("Use /contrails city for detail, e.g. /contrails miami")
    return "\n".join(lines)[:3900]


def run_contrail_command(
    *, token: str, chat_id: str, argument: str, repo_root: Path, config_path: Path, history_path: Path, timezone_name: str
) -> None:
    if not os.environ.get("GOOGLE_CONTRAILS_API_KEY"):
        send_telegram(token, chat_id, "🛑 CONTRAIL DATA ERROR\nGOOGLE_CONTRAILS_API_KEY is not loaded. No contrail signal was generated.")
        return
    aliases, keys = _location_aliases(config_path)
    arg = _normalize_location(argument or "all")
    selected: list[str]
    detail = arg != "all"
    if arg == "all":
        selected = keys
    else:
        key = aliases.get(arg)
        if key is None:
            send_telegram(token, chat_id, f"Unknown contrail location: {argument}. Try /contrails all or a city name.")
            return
        selected = [key]
    send_telegram(token, chat_id, f"Fetching fresh Google Contrails data for {'all tracked cities' if arg == 'all' else selected[0]}…")
    with tempfile.NamedTemporaryFile(prefix="contrails_", suffix=".json", delete=False) as tmp:
        output_path = Path(tmp.name)
    cmd = [sys.executable, str(repo_root / "scripts/scan_google_contrails.py"), "--config", str(config_path), "--output", str(output_path)]
    for key in selected:
        cmd.extend(["--location", key])
    try:
        proc = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True, timeout=180, env=os.environ.copy())
        if proc.returncode != 0:
            send_telegram(token, chat_id, f"🛑 CONTRAIL DATA ERROR\n{(proc.stderr or proc.stdout)[-1200:]}\nNo contrail signal was generated.")
            return
        try:
            rows = json.loads(output_path.read_text(encoding="utf-8"))
            if isinstance(rows, list):
                rows = attach_contrail_percentiles(rows, config_path, history_path)
        except Exception as exc:
            send_telegram(token, chat_id, f"🛑 CONTRAIL DATA ERROR\nCould not parse fresh result: {type(exc).__name__}: {exc}\nNo contrail signal was generated.")
            return
        send_telegram(token, chat_id, _format_contrail_rows(rows if isinstance(rows, list) else [], timezone_name, detail=detail))
    except Exception as exc:
        send_telegram(token, chat_id, f"🛑 CONTRAIL DATA ERROR\n{type(exc).__name__}: {exc}\nNo contrail signal was generated.")
    finally:
        try:
            output_path.unlink()
        except OSError:
            pass


def format_ops_status(strategy_status_dir: Path, collector_health: Path, timezone_name: str) -> str:
    lines = ["📋 WEATHER OPS STATUS"]
    paths = sorted(strategy_status_dir.glob("*.json")) if strategy_status_dir.exists() else []
    if not paths:
        lines.append("Strategies: no health files yet")
    for path in paths:
        row = assess_health(_json_file(path))
        status = str(row.get("status") or "UNKNOWN")
        lines.append(f"{row.get('component') or path.stem}: {status} — {row.get('reason') or 'n/a'}")
    collector = assess_health(_json_file(collector_health), timestamp_keys=("generated_at", "last_message_at"), max_age_seconds=30)
    if collector:
        err = collector.get("reason") if collector.get("status") == "ERROR" else None
        lines.append(
            f"Kalshi L2: {'ERROR — ' + str(err) if err else 'OK'} | tickers {collector.get('current_tickers','n/a')} | gaps {collector.get('sequence_gaps','n/a')} | reconnects {collector.get('reconnects','n/a')}"
        )
        lines.append(f"L2 last message: {_local_timestamp(collector.get('last_message_at'), timezone_name)}")
    lines.append("All strategy processes are PAPER/SHADOW; live order submission is disabled.")
    return "\n".join(lines)[:3900]

def format_weather_event(kind: str, payload: dict[str, Any], timezone_name: str) -> str:
    station = payload.get("station") or "?"
    ticker = payload.get("ticker") or "?"
    side = str(payload.get("side") or payload.get("tournament_side") or "?").upper()
    track = payload.get("track")
    strategy = payload.get("strategy")
    ask = (
        payload.get("fill_price")
        or payload.get("signal_entry_ask")
        or payload.get("tournament_entry_ask")
        or payload.get("entry_ask")
    )
    gross = payload.get("gross_edge") or payload.get("tournament_gross_edge")
    net = payload.get("net_edge_after_fee") or payload.get("tournament_net_edge")
    p_side = payload.get("provisional_probability_side") or payload.get("tournament_probability_side") or payload.get("model_probability_side") or payload.get("model_probability")
    ts_raw = payload.get("fill_time") or payload.get("signal_time") or payload.get("snapshot_time")
    temp = payload.get("latest_temp_f")
    high = payload.get("high_so_far_f")
    since = payload.get("minutes_since_high")
    drop = payload.get("drop_from_high_f")
    slope = payload.get("slope_15m_f_per_min")
    failures = payload.get("control_lock_failures") or payload.get("track_lock_reasons") or payload.get("lock_gate_reasons") or []
    if isinstance(failures, str):
        failures = [failures]
    icon = "🧪" if str(track or "").startswith("D_") else ("📈" if "ALPHA" in kind else ("✅" if "FILL" in kind else "🚨"))
    title = kind.replace("_", " ")
    lines = [
        f"{icon} WEATHER {title}",
        f"{station} | {side} @ {_fmt(ask, 3)}",
        f"{ticker}",
    ]
    if track:
        lines.append(f"Track {track}")
    if strategy:
        lines.append(f"Strategy {strategy}")
    if p_side not in (None, "") or gross not in (None, "") or net not in (None, ""):
        lines.append(f"Model {_fmt(p_side,3)} | Gross {_fmt(gross,3)} | Net {_fmt(net,3)}")
    lines.append(f"Temp {_fmt(temp,2)}F | High {_fmt(high,2)}F")
    slope_text = "MISSING" if slope in (None, "") else _fmt(slope, 4)
    lines.append(f"Since high {_fmt(since,1,'m')} | Drop {_fmt(drop,2,'F')} | 15m slope {slope_text}")
    if slope in (None, ""):
        lines.append("⚠️ 15m slope unavailable in source data; fail-closed strategies must abstain.")
    if failures:
        lines.append("Gate: " + ", ".join(str(x) for x in failures[:4]))
    if payload.get("history_n") not in (None, ""):
        lines.append(f"Empirical history n={payload.get('history_n')} | Terminal P {_fmt(payload.get('terminal_probability'),3)}")
    if payload.get("reasoning"):
        lines.append("Why: " + str(payload.get("reasoning"))[:280])
    if payload.get("near_control_signal"):
        lines.append(
            "Near control: time "
            + _fmt(payload.get("control_minutes_margin"), 1, "m")
            + " | drop "
            + _fmt(payload.get("control_drop_margin_f"), 2, "F")
            + " | slope "
            + _fmt(payload.get("control_slope_margin_f_per_min"), 4)
        )
    lines.append(_local_timestamp(ts_raw, timezone_name))
    lines.append("PAPER ONLY — no live order")
    return "\n".join(lines)


def format_health(payload: dict[str, Any], timezone_name: str) -> str:
    error = payload.get("error") or payload.get("last_error")
    title = "⚠️ WEATHER DATA COLLECTOR" if error else "✅ WEATHER DATA COLLECTOR RECOVERED"
    return "\n".join(
        [
            title,
            str(error or "collector error cleared"),
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
    p.add_argument("--remaining-heating-signals", type=Path, default=Path("/data/weather/live/remaining_heating_shadow_signals.jsonl"))
    p.add_argument("--alpha-signals", type=Path, default=Path("/data/weather/live/live_alpha_signals.jsonl"))
    p.add_argument("--contrail-q90-triggers", type=Path, default=Path("/data/weather/live/contrail_q90_triggers.jsonl"))
    p.add_argument("--collector-health", type=Path, default=Path("/data/weather/status/kalshi_l2_archive_health.json"))
    p.add_argument("--strategy-health-events", type=Path, default=Path("/data/weather/live/strategy_health_events.jsonl"))
    p.add_argument("--strategy-status-dir", type=Path, default=Path("/data/weather/status/strategies"))
    p.add_argument("--contrail-config", type=Path, default=Path("config/contrail_locations.yaml"))
    p.add_argument("--contrail-history", type=Path, default=Path("/data/weather/normalized/contrails/contrail_hourly_history.json"))
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
        ("V2_SHADOW_SIGNAL", args.remaining_heating_signals),
        ("LIVE_ALPHA_SIGNAL", args.alpha_signals),
        ("CONTRAIL_Q90_TRIGGER", args.contrail_q90_triggers),
        ("STRATEGY_HEALTH", args.strategy_health_events),
    ]
    tails = {kind: JsonlTail(path, start_at_end=not args.replay_existing) for kind, path in sources}
    state = load_state(args.state)
    seen = set(str(x) for x in state.get("seen", []))
    freshness_signatures = state.get("freshness_signatures") or {}
    update_offset = state.get("telegram_update_offset")
    repo_root = Path(__file__).resolve().parents[1]
    command_threads: set[int] = set()
    print(f"weather telegram watcher ready sources={len(sources)} seen={len(seen)} commands=true", flush=True)

    while True:
        changed = False
        for kind, _ in sources:
            for payload in tails[kind].poll():
                key = event_key(kind, payload)
                if key in seen:
                    continue
                try:
                    if kind == "STRATEGY_HEALTH":
                        text = format_strategy_health(payload, args.timezone)
                    elif kind == "CONTRAIL_Q90_TRIGGER":
                        text = format_contrail_trigger(payload, args.timezone)
                    else:
                        text = format_weather_event(kind, payload, args.timezone)
                    send_telegram(token, chat_id, text)
                except Exception as exc:
                    print(f"telegram failed kind={kind} key={key}: {type(exc).__name__}:{exc}", flush=True)
                    continue
                seen.add(key)
                changed = True
                print(f"telegram alerted {kind} {payload.get('ticker')}", flush=True)

        try:
            updates = get_telegram_updates(token, None if update_offset is None else int(update_offset))
            for update in updates:
                uid = int(update.get("update_id") or 0)
                update_offset = max(int(update_offset or 0), uid + 1)
                message = update.get("message") if isinstance(update.get("message"), dict) else {}
                message_chat = str((message.get("chat") or {}).get("id") or "")
                if message_chat != str(chat_id):
                    continue
                text = str(message.get("text") or "").strip()
                if not text.startswith("/"):
                    continue
                parts = text.split(maxsplit=1)
                command = parts[0].split("@", 1)[0].lower()
                argument = parts[1].strip() if len(parts) > 1 else ""
                if command == "/contrails":
                    if uid not in command_threads:
                        command_threads.add(uid)
                        def _worker(update_id: int = uid, arg: str = argument) -> None:
                            try:
                                run_contrail_command(
                                    token=token, chat_id=chat_id, argument=arg, repo_root=repo_root,
                                    config_path=args.contrail_config, history_path=args.contrail_history, timezone_name=args.timezone,
                                )
                            finally:
                                command_threads.discard(update_id)
                        threading.Thread(target=_worker, daemon=True).start()
                elif command in {"/status", "/strategies"}:
                    send_telegram(token, chat_id, format_ops_status(args.strategy_status_dir, args.collector_health, args.timezone))
                elif command in {"/help", "/weather"}:
                    send_telegram(token, chat_id, "Weather commands:\n/contrails — fresh all-city contrail pull\n/contrails miami — fresh city detail\n/status — collectors + strategy health\n/strategies — same strategy health summary")
            if updates:
                changed = True
        except Exception as exc:
            print(f"telegram command poll failed: {type(exc).__name__}:{exc}", flush=True)

        # Evaluate age every poll; a dead process leaves an unchanged file.
        freshness_events, next_signatures = health_alert_transitions(
            args.strategy_status_dir, args.collector_health, freshness_signatures)
        for payload in freshness_events:
            try:
                send_telegram(token, chat_id, format_strategy_health(payload, args.timezone))
                freshness_signatures[payload["component"]] = next_signatures[payload["component"]]
                changed = True
            except Exception as exc:
                print(f"telegram freshness alert failed: {type(exc).__name__}:{exc}", flush=True)
        previous_freshness_signatures = dict(freshness_signatures)
        for component, signature in next_signatures.items():
            if not any(event["component"] == component for event in freshness_events):
                freshness_signatures[component] = signature
        if freshness_signatures != previous_freshness_signatures:
            changed = True

        if changed:
            compact_seen = list(seen)[-5000:]
            state = {"seen": compact_seen, "telegram_update_offset": update_offset, "freshness_signatures": freshness_signatures}
            save_state(args.state, state)
        time.sleep(max(0.2, args.poll_interval))


if __name__ == "__main__":
    raise SystemExit(main())
