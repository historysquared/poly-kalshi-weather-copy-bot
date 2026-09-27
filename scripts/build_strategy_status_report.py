#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from weather_alpha.research.registry import default_registry

ROW = re.compile(r'^\|\s*([SFM TXCBAI]\d{2})\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|$'.replace(' ', ''))


def parse_master(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not re.match(r"^\|\s*[SFMTXCBAI]\d{2}\s*\|", line):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) not in (3, 4):
            raise ValueError(f"unexpected master-registry row shape: {line}")
        master_id, priority, hypothesis = cells[:3]
        notes = cells[3] if len(cells) == 4 else ""
        rows.append({"master_id": master_id, "priority": priority,
                     "hypothesis": hypothesis, "notes": notes})
    return rows


def main() -> int:
    p = argparse.ArgumentParser(description='Build status report for the full Weather Alpha master strategy inventory')
    p.add_argument('--master', type=Path, default=Path('docs/WEATHER_ALPHA_MASTER_STRATEGY_REGISTRY.md'))
    p.add_argument('--json-output', type=Path, default=Path('/data/weather/live/weather_strategy_inventory.json'))
    p.add_argument('--md-output', type=Path, default=Path('/data/weather/live/weather_strategy_inventory.md'))
    args = p.parse_args()
    master = parse_master(args.master)
    registry = default_registry()
    mapped: dict[str, list] = {}
    for signal in registry.signals():
        for mid in signal.master_ids:
            mapped.setdefault(mid, []).append(signal)
    for row in master:
        signals = mapped.get(row['master_id'], [])
        row['executable_signals'] = [s.signal_id for s in signals]
        order = {'IDEA': 0, 'HISTORICAL': 1, 'SHADOW': 2, 'PAPER': 3, 'CANDIDATE': 4, 'PAUSED': 5, 'REJECTED': 5}
        row['stage'] = max((s.stage.value for s in signals), key=lambda x: order.get(x, -1),
                           default=('INFRA' if row['priority'].startswith('INFRA') else 'IDEA'))
        row['implemented'] = bool(signals)
    extras = [s.signal_id for s in registry.signals() if not s.master_ids]
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.write_text(json.dumps({'strategies': master, 'unmapped_executable_signals': extras}, indent=2) + '\n')
    lines = ['# Weather Strategy Status', '', f'Total master items: **{len(master)}**',
             f'Executable-mapped items: **{sum(r["implemented"] for r in master)}**',
             f'Unmapped executable signals: **{len(extras)}**', '',
             '| ID | Priority | Stage | Executable signal(s) | Hypothesis |', '|---|---|---|---|---|']
    for row in master:
        lines.append(f"| {row['master_id']} | {row['priority']} | {row['stage']} | {', '.join(row['executable_signals']) or '—'} | {row['hypothesis']} |")
    if extras:
        lines += ['', '## Executable signals not yet represented in the master registry', ''] + [f'- `{x}`' for x in extras]
    args.md_output.write_text('\n'.join(lines) + '\n')
    print(f"master_items={len(master)} mapped={sum(r['implemented'] for r in master)} extras={len(extras)}")
    print(f'json={args.json_output} md={args.md_output}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
