from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any

from weather_alpha.backtest.validation import EventReturn, Verdict, validate_strategy


@dataclass(frozen=True)
class EconomicScorecard:
    strategy: str
    evaluations: int
    signals: int
    fills: int
    settled_fills: int
    wins: int
    losses: int
    win_rate: float | None
    net_pnl: float
    capital_at_risk: float
    roi: float | None
    independent_dates: int
    stations: int
    max_drawdown: float
    validation_verdict: str
    validation_reason: str
    mean_markout_by_horizon: dict[int, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _payload(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _signal_context(payload: dict[str, Any]) -> tuple[str, str]:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    day = str(metadata.get("settlement_date") or "")[:10]
    station = str(metadata.get("station") or "UNKNOWN")
    return day, station


def _drawdown(pnls: list[float]) -> float:
    equity = peak = 0.0
    worst = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        worst = min(worst, equity - peak)
    return worst


def score_event_returns(strategy: str, rows: list[EventReturn]) -> EconomicScorecard:
    ordered = sorted(rows, key=lambda row: (row.date, row.event_id))
    net = sum(row.pnl for row in ordered)
    capital = sum(row.capital for row in ordered)
    wins = sum(int(row.pnl > 0) for row in ordered)
    dates = {row.date for row in ordered if row.date and row.date != "UNKNOWN"}
    stations = {row.station for row in ordered if row.station and row.station != "UNKNOWN"}
    validation = validate_strategy(ordered) if ordered else None
    return EconomicScorecard(
        strategy=strategy,
        evaluations=len(ordered),
        signals=len(ordered),
        fills=len(ordered),
        settled_fills=len(ordered),
        wins=wins,
        losses=len(ordered) - wins,
        win_rate=(wins / len(ordered)) if ordered else None,
        net_pnl=net,
        capital_at_risk=capital,
        roi=(net / capital) if capital > 0 else None,
        independent_dates=len(dates),
        stations=len(stations),
        max_drawdown=_drawdown([row.pnl for row in ordered]),
        validation_verdict=(validation.verdict.value if validation else Verdict.INSUFFICIENT_DATA.value),
        validation_reason=(validation.reason if validation else "no settled events"),
    )


def build_scorecards(db_path: str | Path, strategy: str | None = None) -> list[EconomicScorecard]:
    path = Path(db_path)
    if not path.exists():
        return []
    db = sqlite3.connect(path)
    try:
        strategies = [strategy] if strategy else [
            row[0]
            for row in db.execute(
                "SELECT DISTINCT strategy FROM signals UNION SELECT DISTINCT strategy FROM model_evaluations ORDER BY 1"
            )
        ]
        out: list[EconomicScorecard] = []
        for name in strategies:
            if not name:
                continue
            evaluations = int(db.execute(
                "SELECT COUNT(*) FROM model_evaluations WHERE strategy=?", (name,)
            ).fetchone()[0])
            signal_rows = db.execute(
                "SELECT signal_id,payload FROM signals WHERE strategy=? ORDER BY timestamp", (name,)
            ).fetchall()
            signal_payloads = {sid: _payload(payload) for sid, payload in signal_rows}
            signal_ids = tuple(signal_payloads)
            fill_rows: list[tuple] = []
            marks = ""
            if signal_ids:
                marks = ",".join("?" for _ in signal_ids)
                fill_rows = db.execute(
                    f"SELECT id,signal_id,payload FROM fills WHERE signal_id IN ({marks}) ORDER BY timestamp",
                    signal_ids,
                ).fetchall()

            settled: list[tuple[float, float, bool, str, str, str]] = []
            for fill_id, sid, raw in fill_rows:
                pnl_row = db.execute(
                    "SELECT pnl,reconciled_at FROM paper_pnl WHERE fill_id=?", (fill_id,)
                ).fetchone()
                if pnl_row is None:
                    continue
                fill = _payload(raw)
                pnl = float(pnl_row[0])
                price = float(fill.get("executable_price") or 0.0)
                contracts = int(fill.get("contracts") or 0)
                fee = float(fill.get("fee") or 0.0)
                capital = contracts * price + fee
                day, station = _signal_context(signal_payloads.get(sid, {}))
                settled.append((pnl, capital, pnl > 0, day, station, sid))

            net = sum(row[0] for row in settled)
            capital = sum(row[1] for row in settled)
            wins = sum(int(row[2]) for row in settled)
            dates = {row[3] for row in settled if row[3]}
            stations = {row[4] for row in settled if row[4] and row[4] != "UNKNOWN"}
            event_rows = [
                EventReturn(
                    strategy=name,
                    event_id=sid,
                    date=day or "UNKNOWN",
                    station=station,
                    latency_seconds=0,
                    execution_case="FORWARD_PAPER",
                    pnl=pnl,
                    capital=cap,
                )
                for pnl, cap, _won, day, station, sid in settled
            ]
            validation = validate_strategy(event_rows) if event_rows else None

            markout_by_horizon: dict[int, list[float]] = {}
            fill_by_signal = {sid: _payload(raw) for _fid, sid, raw in fill_rows}
            if signal_ids:
                mark_rows = db.execute(
                    f"SELECT signal_id,horizon,executable_value FROM marks WHERE signal_id IN ({marks})", signal_ids
                ).fetchall()
                for sid, horizon, value in mark_rows:
                    if value is None:
                        continue
                    fill = fill_by_signal.get(sid)
                    if not fill:
                        continue
                    entry = float(fill.get("executable_price") or 0.0)
                    markout_by_horizon.setdefault(int(horizon), []).append(float(value) - entry)

            verdict = validation.verdict.value if validation else Verdict.INSUFFICIENT_DATA.value
            reason = validation.reason if validation else "no settled fills"
            out.append(EconomicScorecard(
                strategy=name,
                evaluations=evaluations,
                signals=len(signal_rows),
                fills=len(fill_rows),
                settled_fills=len(settled),
                wins=wins,
                losses=len(settled) - wins,
                win_rate=(wins / len(settled)) if settled else None,
                net_pnl=net,
                capital_at_risk=capital,
                roi=(net / capital) if capital > 0 else None,
                independent_dates=len(dates),
                stations=len(stations),
                max_drawdown=_drawdown([row[0] for row in settled]),
                validation_verdict=verdict,
                validation_reason=reason,
                mean_markout_by_horizon={
                    horizon: mean(values) for horizon, values in sorted(markout_by_horizon.items()) if values
                },
            ))
        return out
    finally:
        db.close()
