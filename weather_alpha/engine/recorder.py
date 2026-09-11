from __future__ import annotations

import atexit
import json
import os
import sqlite3
import threading
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .models import ForwardMark, ModelEvaluation, Settlement, Signal, SimulatedFill, jsonable


class StateStore:
    """Small transactional state store; raw/high-frequency history belongs in Parquet."""

    def __init__(self, path: Path | str):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS signals(
                signal_id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, strategy TEXT NOT NULL,
                venue TEXT NOT NULL, contract_id TEXT NOT NULL, weather_event_id TEXT NOT NULL,
                side TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS fills(
                id INTEGER PRIMARY KEY, signal_id TEXT NOT NULL, timestamp TEXT NOT NULL, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS marks(
                signal_id TEXT NOT NULL, horizon INTEGER NOT NULL, marked_at TEXT NOT NULL,
                venue TEXT NOT NULL, contract_id TEXT NOT NULL,
                yes_bid REAL, yes_ask REAL, no_bid REAL, no_ask REAL, executable_value REAL,
                PRIMARY KEY(signal_id,horizon)
            );
            CREATE TABLE IF NOT EXISTS settlements(
                venue TEXT NOT NULL, contract_id TEXT NOT NULL, weather_event_id TEXT NOT NULL,
                result TEXT NOT NULL, settlement_value REAL, resolved_at TEXT NOT NULL,
                payload TEXT NOT NULL, PRIMARY KEY(venue,contract_id)
            );
            CREATE TABLE IF NOT EXISTS paper_pnl(
                fill_id INTEGER PRIMARY KEY, venue TEXT NOT NULL, contract_id TEXT NOT NULL,
                pnl REAL NOT NULL, reconciled_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS model_evaluations(
                id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, strategy TEXT NOT NULL,
                venue TEXT NOT NULL, contract_id TEXT NOT NULL, emitted INTEGER NOT NULL, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS strategy_errors(
                id INTEGER PRIMARY KEY, timestamp TEXT NOT NULL, strategy TEXT NOT NULL,
                venue TEXT NOT NULL, contract_id TEXT NOT NULL, error TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS orders(
                client_order_id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, venue TEXT NOT NULL,
                contract_id TEXT NOT NULL, status TEXT NOT NULL, payload TEXT NOT NULL
            );
            """
        )
        self.db.commit()

    def signal(self, value: Signal) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO signals VALUES(?,?,?,?,?,?,?,?)",
            (value.signal_id, value.timestamp.isoformat(), value.strategy, value.venue,
             value.contract_id, value.weather_event_id, value.side.value, json.dumps(jsonable(value))),
        )
        self.db.commit()

    def evaluation(self, value: ModelEvaluation) -> int:
        cur = self.db.execute(
            "INSERT INTO model_evaluations(timestamp,strategy,venue,contract_id,emitted,payload) VALUES(?,?,?,?,?,?)",
            (value.timestamp.isoformat(), value.strategy, value.venue, value.contract_id,
             int(value.emitted), json.dumps(jsonable(value))),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def strategy_error(self, timestamp: datetime, strategy: str, venue: str, contract_id: str, error: str) -> int:
        cur = self.db.execute(
            "INSERT INTO strategy_errors(timestamp,strategy,venue,contract_id,error) VALUES(?,?,?,?,?)",
            (timestamp.isoformat(), strategy, venue, contract_id, error),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def fill(self, value: SimulatedFill) -> int:
        cur = self.db.execute(
            "INSERT INTO fills(signal_id,timestamp,payload) VALUES(?,?,?)",
            (value.signal_id, value.timestamp.isoformat(), json.dumps(jsonable(value))),
        )
        self.db.commit()
        return int(cur.lastrowid)

    def mark(self, value: ForwardMark) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO marks VALUES(?,?,?,?,?,?,?,?,?,?)",
            (value.signal_id, value.horizon_seconds, value.mark_timestamp.isoformat(), value.venue,
             value.contract_id, value.yes_bid, value.yes_ask, value.no_bid, value.no_ask,
             value.executable_value),
        )
        self.db.commit()

    def settlement(self, value: Settlement) -> None:
        result = value.official_result.value if hasattr(value.official_result, "value") else str(value.official_result)
        self.db.execute(
            "INSERT OR REPLACE INTO settlements VALUES(?,?,?,?,?,?,?)",
            (value.venue, value.contract_id, value.weather_event_id, result, value.official_settlement_value,
             value.resolved_timestamp.isoformat(), json.dumps(jsonable(value))),
        )
        self.db.commit()

    def reconcile_paper(self, venue: str, contract_id: str) -> int:
        settlement = self.db.execute(
            "SELECT result,resolved_at FROM settlements WHERE venue=? AND contract_id=?",
            (venue, contract_id),
        ).fetchone()
        if not settlement:
            return 0
        result, resolved_at = settlement
        rows = self.db.execute(
            "SELECT fills.id,fills.payload,signals.side FROM fills JOIN signals USING(signal_id) "
            "WHERE signals.venue=? AND signals.contract_id=?",
            (venue, contract_id),
        ).fetchall()
        count = 0
        for fill_id, payload, side in rows:
            value = json.loads(payload)
            won = str(result).upper() == str(side).upper()
            contracts = int(value["contracts"])
            price = float(value["executable_price"])
            fee = float(value["fee"])
            pnl = contracts * ((1.0 if won else 0.0) - price) - fee
            self.db.execute(
                "INSERT OR REPLACE INTO paper_pnl VALUES(?,?,?,?,?)",
                (fill_id, venue, contract_id, pnl, resolved_at),
            )
            count += 1
        self.db.commit()
        return count


class ParquetRecorder:
    """Buffered partitioned writer with atomic batch finalization."""

    def __init__(self, root: Path | str, *, batch_size: int = 1000, flush_interval: float = 30.0):
        self.root = Path(root)
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self._buffers: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
        self._last_flush = time.monotonic()
        self._lock = threading.Lock()
        atexit.register(self.flush)

    def append(self, record: dict, timestamp: datetime, venue: str, stream: str) -> list[Path]:
        key = (timestamp.date().isoformat(), venue, stream)
        with self._lock:
            self._buffers[key].append(jsonable(record))
            due = len(self._buffers[key]) >= self.batch_size or time.monotonic() - self._last_flush >= self.flush_interval
        return self.flush() if due else []

    def flush(self) -> list[Path]:
        try:
            import pyarrow as pa
            import pyarrow.parquet as pq
        except ImportError as exc:
            if any(self._buffers.values()):
                raise RuntimeError("Install pyarrow to record Parquet") from exc
            return []
        with self._lock:
            batches, self._buffers = self._buffers, defaultdict(list)
            self._last_flush = time.monotonic()
        paths: list[Path] = []
        for (date, venue, stream), records in batches.items():
            if not records:
                continue
            folder = self.root / f"date={date}" / f"venue={venue}" / f"stream={stream}"
            folder.mkdir(parents=True, exist_ok=True)
            final = folder / f"part-{uuid4().hex}.parquet"
            temp = final.with_suffix(".parquet.tmp")
            pq.write_table(pa.Table.from_pylist(records), temp)
            os.replace(temp, final)
            paths.append(final)
        return paths

    def close(self) -> list[Path]:
        paths = self.flush()
        try:
            atexit.unregister(self.flush)
        except Exception:
            pass
        return paths
