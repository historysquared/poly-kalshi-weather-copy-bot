from __future__ import annotations

import json
import math
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COMPONENT_MAX_AGE = {
    "contrail_current_collector": 1200,
    "contrail_hourly_history": 27 * 3600,
    "contrail_q90_prior_evening_v1": 1200,
}

DEFAULT_STATUS_DIR = Path("/data/weather/status/strategies")
DEFAULT_EVENTS = Path("/data/weather/live/strategy_health_events.jsonl")


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    tmp.replace(path)


def _load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def assess_health(row: dict[str, Any], *, now: datetime | None = None,
                  timestamp_keys: tuple[str, ...] = ("updated_at",),
                  max_age_seconds: float | None = None) -> dict[str, Any]:
    """Evaluate freshness at read time, even when the file stopped changing."""
    if not row:
        return {"status": "ERROR", "reason": "MISSING_HEALTH"}
    result = dict(row)
    result["reported_status"] = row.get("status")
    result["reported_reason"] = row.get("reason")
    now = now or datetime.now(timezone.utc)
    limit = max_age_seconds if max_age_seconds is not None else row.get("max_age_seconds", COMPONENT_MAX_AGE.get(str(row.get("component")), 180))
    try:
        limit = float(limit)
        if not math.isfinite(limit) or limit <= 0: raise ValueError()
    except (TypeError, ValueError):
        return {**result, "status": "ERROR", "reason": "INVALID_HEALTH_TTL"}
    ages = []
    for key in timestamp_keys:
        try:
            stamp = datetime.fromisoformat(str(row.get(key)).replace("Z", "+00:00"))
            if stamp.tzinfo is None or stamp.utcoffset() is None: raise ValueError()
            age = (now - stamp.astimezone(timezone.utc)).total_seconds()
            if age < 0: raise ValueError()
            ages.append(age)
        except (ValueError, TypeError, OverflowError):
            return {**result, "status": "ERROR", "reason": "INVALID_HEALTH_TIMESTAMP", "invalid_timestamp": key}
    result["health_age_seconds"] = max(ages)
    if max(ages) > limit:
        return {**result, "status": "ERROR", "reason": "STALE_HEALTH"}
    if row.get("error") or row.get("last_error"):
        return {**result, "status": "ERROR", "reason": str(row.get("error") or row.get("last_error"))}
    result.setdefault("status", "OK")
    result.setdefault("reason", "CURRENT_HEALTH")
    return result


def health_alert_transitions(strategy_status_dir: Path, collector_health: Path,
                             previous: dict[str, str], *, now: datetime | None = None) -> tuple[list[dict], dict[str, str]]:
    """Emit one stale/missing/error transition and one recovery, not every poll."""
    paths = {"kalshi_l2": collector_health}
    if strategy_status_dir.exists():
        paths.update({p.stem: p for p in strategy_status_dir.glob("*.json")})
    # Keep monitoring previously observed components if their health file disappears.
    paths.update({k: strategy_status_dir / (k + ".json") for k in previous if k not in paths and k != "kalshi_l2"})
    signatures = dict(previous); events = []
    for component, path in paths.items():
        kwargs = {"max_age_seconds": 30, "timestamp_keys": ("generated_at", "last_message_at")} if component == "kalshi_l2" else {}
        view = assess_health(_load(path), now=now, **kwargs)
        bad = view["status"] == "ERROR"
        signature = str(view["reason"]) if bad else ""
        old = previous.get(component, "")
        if signature != old and (signature or old):
            events.append({**view, "component": component, "previous_status": "ERROR" if old else "OK",
                           "previous_reason": old, "updated_at": (now or datetime.now(timezone.utc)).isoformat()})
        signatures[component] = signature
    return events, signatures


def run_health_subprocess(cmd: list[str], health: Any, reason: str, **kwargs: Any):
    try:
        return subprocess.run(cmd, **kwargs)
    except subprocess.TimeoutExpired:
        health.report("ERROR", reason + "_TIMEOUT", timeout_seconds=kwargs.get("timeout"))
    except OSError as exc:
        health.report("ERROR", reason + "_SPAWN_FAILED", exception_type=type(exc).__name__)
    return None


class StrategyHealth:
    """Persist explicit strategy state and emit only state/reason transitions.

    Signals must never be used as a health proxy. A healthy strategy may abstain,
    while a broken strategy must say why it cannot evaluate.
    """

    def __init__(
        self,
        component: str,
        *,
        status_dir: Path = DEFAULT_STATUS_DIR,
        events_path: Path = DEFAULT_EVENTS,
    ) -> None:
        self.component = component
        self.status_path = status_dir / f"{component}.json"
        self.events_path = events_path

    def report(self, status: str, reason: str, **details: Any) -> dict[str, Any]:
        status = str(status).upper()
        if status not in {"OK", "ABSTAIN", "DEGRADED", "ERROR"}:
            raise ValueError(f"unsupported strategy health status: {status}")
        previous = _load(self.status_path)
        now = datetime.now(timezone.utc).isoformat()
        row: dict[str, Any] = {
            "component": self.component,
            "status": status,
            "reason": str(reason),
            "updated_at": now,
            "live_order_submission": False,
            "max_age_seconds": COMPONENT_MAX_AGE.get(self.component, 180),
            **details,
        }
        _atomic_json(self.status_path, row)
        previous_signature = (previous.get("status"), previous.get("reason"))
        current_signature = (status, str(reason))
        if previous_signature != current_signature:
            event = {
                **row,
                "previous_status": previous.get("status"),
                "previous_reason": previous.get("reason"),
            }
            self.events_path.parent.mkdir(parents=True, exist_ok=True)
            with self.events_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, sort_keys=True, default=str) + "\n")
        return row
