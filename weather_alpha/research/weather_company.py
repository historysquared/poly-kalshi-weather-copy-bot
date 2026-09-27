from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from weather_alpha.engine.models import ExecutionQuality, ModelEvaluation, Settlement, Side, Signal, SimulatedFill
from weather_alpha.engine.recorder import StateStore


STRATEGY_ID = "weather_company_terminal_high_v1"


def _f(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dt(value: Any) -> datetime:
    if value:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def weather_event_id(row: dict[str, Any]) -> str:
    station = str(row.get("station") or "UNKNOWN")
    day = str(row.get("settlement_date") or "UNKNOWN")[:10]
    return f"{station}_{day}_DAILY_HIGH"


class WeatherCompanyResearchBridge:
    """Bridge the existing JSONL forward runner into the canonical research engine store."""

    def __init__(self, db_path: str | Path, strategy_id: str = STRATEGY_ID) -> None:
        self.store = StateStore(db_path)
        self.strategy_id = strategy_id

    @staticmethod
    def feature_payload(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "surface.high_so_far_f": _f(row.get("high_so_far_f")),
            "surface.latest_temp_f": _f(row.get("latest_temp_f")),
            "surface.minutes_since_high": _f(row.get("minutes_since_high")),
            "surface.drop_from_high_f": _f(row.get("drop_from_high_f")),
            "surface.slope_15m_f_per_min": _f(row.get("slope_15m_f_per_min")),
            "market.executable_ask": _f(row.get("signal_entry_ask") or row.get("tournament_entry_ask") or row.get("entry_ask")),
        }

    def record_evaluation(self, row: dict[str, Any], *, emitted: bool, reason: str) -> int:
        side_text = str(row.get("side") or row.get("tournament_side") or "").upper()
        side = Side(side_text) if side_text in {"YES", "NO"} else None
        p = _f(row.get("provisional_probability_side") or row.get("tournament_probability_side"))
        raw_edge = _f(row.get("gross_edge") or row.get("tournament_gross_edge"))
        evaluation = ModelEvaluation(
            timestamp=_dt(row.get("signal_time") or row.get("snapshot_time")),
            strategy=self.strategy_id,
            venue="kalshi",
            contract_id=str(row.get("ticker") or ""),
            weather_event_id=weather_event_id(row),
            eligible=emitted or reason in {"PAPER_TRADE_ELIGIBLE", "DIAGNOSTIC_PAPER_ELIGIBLE"},
            emitted=emitted,
            reason=reason,
            side=side,
            model_probability=p,
            raw_edge=raw_edge,
            executable_edge=raw_edge,
            execution_quality=ExecutionQuality.TOUCH_EXECUTION,
            data_quality=[str(x) for x in (row.get("lock_gate_reasons") or row.get("track_lock_reasons") or row.get("control_lock_failures") or [])],
            parameters={
                "price_floor": _f(row.get("price_floor") or row.get("track_price_floor")),
                "minimum_edge": _f(row.get("minimum_edge") or row.get("track_minimum_edge")),
                "model_status": row.get("model_status"),
            },
            features=self.feature_payload(row),
        )
        return self.store.evaluation(evaluation)

    def record_signal(self, row: dict[str, Any], *, estimated_fee_per_contract: float = 0.0) -> str:
        side = Side(str(row.get("side") or row.get("tournament_side")).upper())
        probability = float(row.get("provisional_probability_side") or row.get("tournament_probability_side"))
        ask = float(row.get("signal_entry_ask") or row.get("tournament_entry_ask"))
        raw_edge = probability - ask
        executable_edge = raw_edge - float(estimated_fee_per_contract)
        reasoning = (
            f"terminal-high paper heuristic; high={row.get('high_so_far_f')}F; "
            f"minutes_since_high={row.get('minutes_since_high')}; drop={row.get('drop_from_high_f')}F; "
            f"slope15={row.get('slope_15m_f_per_min')}F/min"
        )
        signal_key = "|".join((self.strategy_id, str(row.get("ticker") or ""), str(row.get("signal_time") or ""), side.value))
        signal = Signal(
            timestamp=_dt(row.get("signal_time")),
            strategy=self.strategy_id,
            venue="kalshi",
            contract_id=str(row.get("ticker") or ""),
            weather_event_id=weather_event_id(row),
            side=side,
            model_probability=probability,
            market_probability=ask,
            executable_price=ask,
            raw_edge=raw_edge,
            executable_edge=executable_edge,
            expected_value_per_contract=executable_edge,
            execution_quality=ExecutionQuality.TOUCH_EXECUTION,
            signal_id=str(uuid5(NAMESPACE_URL, signal_key)),
            metadata={
                "station": row.get("station"),
                "settlement_date": row.get("settlement_date"),
                "source_family": row.get("source_family"),
                "reasoning": reasoning,
                "legacy_event_id": row.get("event_id"),
            },
        )
        self.store.signal(signal)
        return signal.signal_id

    def record_fill(self, row: dict[str, Any]) -> int | None:
        signal_id = str(row.get("engine_signal_id") or "")
        if not signal_id:
            return None
        side = Side(str(row.get("side") or row.get("tournament_side")).upper())
        existing = self.store.db.execute(
            "SELECT id FROM fills WHERE signal_id=? AND timestamp=?", (signal_id, _dt(row.get("fill_time")).isoformat())
        ).fetchone()
        if existing:
            return int(existing[0])
        fill = SimulatedFill(
            signal_id=signal_id,
            venue="kalshi",
            contract_id=str(row.get("ticker") or ""),
            side=side,
            executable_price=float(row["fill_price"]),
            contracts=int(row.get("contracts") or 1),
            fee=float(row.get("estimated_taker_fee") or 0.0),
            slippage=0.0,
            fill_model=str(row.get("fill_model") or "LIVE_DELAYED_TOUCH"),
            timestamp=_dt(row.get("fill_time")),
            execution_quality=ExecutionQuality.TOUCH_EXECUTION,
            fee_treatment="ESTIMATED",
        )
        return self.store.fill(fill)

    def record_settlement(self, row: dict[str, Any], market: dict[str, Any]) -> int:
        result = str(row.get("market_result") or "").upper()
        official = Side(result) if result in {"YES", "NO"} else result
        settlement = Settlement(
            venue="kalshi",
            contract_id=str(row.get("ticker") or ""),
            weather_event_id=weather_event_id(row),
            official_result=official,
            resolved_timestamp=_dt(row.get("settlement_ts")),
            official_settlement_value=_f(market.get("expiration_value") or market.get("settlement_value")),
            settlement_source=str(row.get("source_family") or "WEATHER_COMPANY"),
            metadata={"market_status": market.get("status"), "legacy_fill_key": row.get("fill_key")},
        )
        self.store.settlement(settlement)
        return self.store.reconcile_paper("kalshi", settlement.contract_id)
