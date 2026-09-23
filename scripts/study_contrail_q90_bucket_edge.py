#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd


def load_rows(contrail_path: Path, nbm_path: Path, bucket_path: Path) -> pd.DataFrame:
    contrails = json.loads(contrail_path.read_text(encoding="utf-8"))
    nbm = json.loads(nbm_path.read_text(encoding="utf-8"))
    buckets = json.loads(bucket_path.read_text(encoding="utf-8"))
    hourly = {(r["location_key"], r["local_date"]): {int(k): int(v) for k, v in (r.get("hourly_counts") or {}).items()}
              for r in contrails if not r.get("error")}
    forecast = {(r["location_key"], r["date"]): r for r in nbm if not r.get("error")}
    rows: list[dict] = []
    for br in buckets:
        city, day = br["city"], br["date"]
        nr = forecast.get((city, day))
        if nr is None or not br.get("validation_ok", True):
            continue
        previous = (pd.Timestamp(day) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")
        counts = hourly.get((city, previous), {})
        prior_18_21 = float(sum(counts.get(hour, 0) for hour in range(18, 21)))
        rows.append({
            "city": city,
            "date": day,
            "feature_count": prior_18_21,
            "nbm_high_f": float(nr["nbm_max_f"]),
            "nbm_sd_f": max(0.75, float(nr.get("nbm_max_sd_f") or 2.0)),
            "actual_high_f": float(br["expiration_value"]),
            "bucket": br,
        })
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["date"])
    return df.sort_values(["date", "city"]).reset_index(drop=True)


def attach_rolling_q90(df: pd.DataFrame, *, lookback_days: int, min_history_days: int) -> pd.DataFrame:
    out = df.copy()
    out["q90"] = np.nan
    out["is_top90"] = False
    for idx, row in out.iterrows():
        prior = out[(out.city == row.city) & (out.date < row.date) & (out.date >= row.date - pd.Timedelta(days=lookback_days))]
        if len(prior) < min_history_days:
            continue
        q90 = float(prior.feature_count.quantile(0.90))
        out.at[idx, "q90"] = q90
        out.at[idx, "is_top90"] = bool(row.feature_count >= q90)
    return out


def fixed_effect_step(history: pd.DataFrame) -> float | None:
    history = history[history.q90.notna()].copy()
    if history.empty or history.is_top90.sum() < 2:
        return None
    y = (history.actual_high_f - history.nbm_high_f).to_numpy(float)
    indicator = history.is_top90.astype(float).to_numpy()
    cities = sorted(history.city.unique())
    cols = [np.ones(len(history)), indicator]
    cols.extend((history.city == city).astype(float).to_numpy() for city in cities[1:])
    x = np.column_stack(cols)
    coef = np.linalg.lstsq(x, y, rcond=None)[0]
    return float(coef[1])


def normal_cdf(x: float, mean: float, sd: float) -> float:
    return 0.5 * (1 + math.erf((x - mean) / (sd * math.sqrt(2))))


def contract_probabilities(br: dict, mean: float, sd: float) -> dict[str, float]:
    out: dict[str, float] = {}
    for contract in br["contracts"]:
        st = contract.get("strike_type")
        lo, hi = contract.get("floor_strike"), contract.get("cap_strike")
        if st == "less" and hi is not None:
            p = normal_cdf(float(hi) - 0.5, mean, sd)
        elif st == "greater" and lo is not None:
            p = 1 - normal_cdf(float(lo) + 0.5, mean, sd)
        elif st == "between" and lo is not None and hi is not None:
            p = normal_cdf(float(hi) + 0.5, mean, sd) - normal_cdf(float(lo) - 0.5, mean, sd)
        else:
            p = 0.0
        out[str(contract["ticker"])] = max(0.0, p)
    total = sum(out.values())
    return {k: v / total for k, v in out.items()} if total else out


def main() -> int:
    p = argparse.ArgumentParser(description="Walk-forward city-specific q90 contrail test against exact Kalshi buckets")
    p.add_argument("--contrails", type=Path, required=True)
    p.add_argument("--nbm", type=Path, required=True)
    p.add_argument("--buckets", type=Path, required=True)
    p.add_argument("--lookback-days", type=int, default=90)
    p.add_argument("--min-history-days", type=int, default=20)
    p.add_argument("--output", type=Path, default=Path("/data/weather/results/contrail_q90_bucket_edge.json"))
    args = p.parse_args()

    df = attach_rolling_q90(load_rows(args.contrails, args.nbm, args.buckets), lookback_days=args.lookback_days,
                            min_history_days=args.min_history_days)
    details: list[dict] = []
    for idx, row in df[df.q90.notna() & df.is_top90].iterrows():
        history = df[(df.date < row.date) & df.q90.notna()]
        effect = fixed_effect_step(history)
        if effect is None:
            continue
        baseline = contract_probabilities(row.bucket, row.nbm_high_f, row.nbm_sd_f)
        adjusted = contract_probabilities(row.bucket, row.nbm_high_f + effect, row.nbm_sd_f)
        winner = str(row.bucket["winner_contracts"][0])
        base_top = max(baseline, key=baseline.get)
        adj_top = max(adjusted, key=adjusted.get)
        details.append({
            "date": row.date.strftime("%Y-%m-%d"), "city": row.city,
            "feature_count": row.feature_count, "city_q90": row.q90,
            "nbm_high_f": row.nbm_high_f, "actual_high_f": row.actual_high_f,
            "forecast_error_f": row.actual_high_f - row.nbm_high_f,
            "walk_forward_adjustment_f": effect,
            "winner": winner, "baseline_top_bucket": base_top, "adjusted_top_bucket": adj_top,
            "baseline_hit": base_top == winner, "adjusted_hit": adj_top == winner,
            "baseline_winner_probability": baseline.get(winner, 0.0),
            "adjusted_winner_probability": adjusted.get(winner, 0.0),
        })

    result = {
        "definition": "Top 90th percentile is calculated independently from each city's own preceding contrail series only.",
        "feature": "prior_local_day_18_21_contrail_detection_count",
        "lookback_days": args.lookback_days,
        "min_history_days": args.min_history_days,
        "eligible_city_days": int(df.q90.notna().sum()),
        "extreme_city_days": len(details),
        "independent_extreme_dates": len({r["date"] for r in details}),
        "mean_forecast_error_f": float(np.mean([r["forecast_error_f"] for r in details])) if details else None,
        "baseline_bucket_hits": sum(bool(r["baseline_hit"]) for r in details),
        "adjusted_bucket_hits": sum(bool(r["adjusted_hit"]) for r in details),
        "corrected": sum((not r["baseline_hit"]) and r["adjusted_hit"] for r in details),
        "harmed": sum(r["baseline_hit"] and (not r["adjusted_hit"]) for r in details),
        "mean_winner_probability_change": float(np.mean([r["adjusted_winner_probability"] - r["baseline_winner_probability"] for r in details])) if details else None,
        "details": details,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "details"}, indent=2, sort_keys=True))
    print(f"output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
