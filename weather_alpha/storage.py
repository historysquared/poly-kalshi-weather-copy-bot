from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json

class JsonlArchive:
    def __init__(self, root: str = "data/archive"):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def append(self, stream: str, payload: dict) -> None:
        path = self.root / f"{stream}.jsonl"
        row = {"archived_at": datetime.now(timezone.utc).isoformat(), **payload}
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, separators=(",", ":"), default=str) + "\n")
