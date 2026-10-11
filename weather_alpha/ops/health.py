from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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
