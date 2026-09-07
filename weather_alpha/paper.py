from __future__ import annotations
from dataclasses import asdict, dataclass
from pathlib import Path
import json
from .models import Signal

@dataclass
class PaperPosition:
    ticker: str
    side: str
    entry_price: float
    contracts: int
    model_probability: float

class PaperPortfolio:
    def __init__(self, path: str = "data/paper_portfolio.json", starting_cash: float = 1000.0):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.cash = starting_cash
        self.positions: list[PaperPosition] = []
        if self.path.exists():
            self._load()

    def _load(self):
        data = json.loads(self.path.read_text())
        self.cash = float(data["cash"])
        self.positions = [PaperPosition(**p) for p in data.get("positions", [])]

    def save(self):
        self.path.write_text(json.dumps({"cash": self.cash, "positions": [asdict(p) for p in self.positions]}, indent=2))

    def enter(self, signal: Signal, dollars: float = 10.0) -> PaperPosition | None:
        if signal.entry_price <= 0:
            return None
        contracts = int(min(self.cash, dollars) // signal.entry_price)
        if contracts < 1:
            return None
        cost = contracts * signal.entry_price
        self.cash -= cost
        pos = PaperPosition(signal.ticker, signal.side, signal.entry_price, contracts, signal.model_probability)
        self.positions.append(pos)
        self.save()
        return pos
