#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from weather_alpha.research.scorecard import build_scorecards


def pct(value: float | None) -> str:
    return "-" if value is None else f"{100.0 * value:.2f}%"


def main() -> int:
    p = argparse.ArgumentParser(description="Build the canonical economic scorecard from the research state store")
    p.add_argument("--db", type=Path, default=Path("/data/weather/live/weather_research.sqlite3"))
    p.add_argument("--strategy")
    p.add_argument("--json-output", type=Path, default=Path("/data/weather/live/weather_signal_scorecard.json"))
    p.add_argument("--text-output", type=Path, default=Path("/data/weather/live/weather_signal_scorecard.md"))
    args = p.parse_args()

    cards = build_scorecards(args.db, strategy=args.strategy)
    payload = [card.to_dict() for card in cards]
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    lines = [
        "# Weather Signal Economic Scorecard",
        "",
        "Strategy | Evals | Signals | Fills | Settled | W-L | ROI | Net P&L | Dates | Stations | Max DD | Verdict",
        "---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---",
    ]
    for card in cards:
        lines.append(
            f"{card.strategy} | {card.evaluations} | {card.signals} | {card.fills} | {card.settled_fills} | "
            f"{card.wins}-{card.losses} | {pct(card.roi)} | {card.net_pnl:.4f} | {card.independent_dates} | "
            f"{card.stations} | {card.max_drawdown:.4f} | {card.validation_verdict}"
        )
        if card.mean_markout_by_horizon:
            marks = ", ".join(f"{h}s={v:+.4f}" for h, v in card.mean_markout_by_horizon.items())
            lines.append(f"  - markouts: {marks}")
        lines.append(f"  - {card.validation_reason}")
    if not cards:
        lines.append("No registered strategy data found in the research store.")
    args.text_output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"json={args.json_output} text={args.text_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
