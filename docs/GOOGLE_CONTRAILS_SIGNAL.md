# Google Contrails weather signal

Research-only integration of the Google Contrails API v2 into Weather Alpha Lab.

## What the Google values mean

- `detections`: Google-produced satellite-observed linear contrail detections (GeoJSON LineStrings).
- `contrails`: Contrail Forcing Index (CFI), a continuous 0–4 contrail-warming severity index.
- `persistent_formation_probability`: probability (0–100; typically much lower in practice) that persistent contrails form at a forecast grid point / flight level.
- `expected_effective_energy_forcing`: Google’s formation-probability-weighted effective energy forcing (J/m), the quantity underlying CFI.\n- `nominal_cocip_effective_energy_forcing`: the nominal CoCiP energy-forcing estimate before that probability weighting.

**None of these is a forecast that a city's surface temperature will change by X °F or °C.** The trading research objective is to estimate that relationship empirically by joining these features to station observations and pre-event weather-model forecast errors.

## Secret

Never commit the key. Set it on the runtime host:

```bash
export GOOGLE_CONTRAILS_API_KEY='...'
```

`.env` is already ignored by this repository.

## City registry

`config/contrail_locations.yaml` contains Stayton, the verified Polymarket US weather station mappings already present in this repository, and a broader set of Kalshi monitoring cities. Static Kalshi station entries are research monitoring points; existing market metadata and settlement resolution remain authoritative.

## Examples

Stayton for the current local day:

```bash
python scripts/scan_google_contrails.py --location stayton_or --output /data/weather/live/contrails_stayton_today.json
```

Polymarket US monitoring cities:

```bash
python scripts/scan_google_contrails.py --venue polymarket_us
```

Kalshi monitoring cities:

```bash
python scripts/scan_google_contrails.py --venue kalshi
```

All configured locations with Telegram alerts when a research threshold fires:

```bash
python scripts/scan_google_contrails.py --telegram --output /data/weather/live/google_contrails_latest.json
```

Telegram uses the repository's existing `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` environment variables.

Current default alert thresholds (5 detections, 150 km aggregate detected-line length, or CFI >=2) are exploratory and are not calibrated trading rules.

## Important API semantics

- Google `GetDetections` returns any detected LineString that intersects the requested polygon. The returned geometry is not guaranteed to be clipped to that polygon. The live scanner therefore reports **clipped in-bounds line length** as the local metric and preserves full-feature length only as a diagnostic.
- `detection_count` counts returned LineStrings across satellite frames; it is not a count of unique physical contrails. `unique_detection_frames` is tracked separately.
- `/v2/grids` is a **forecast** product. CFI, persistent-formation probability, and energy-forcing fields may be zero even when satellite detections later observe contrails. The scanner records this as `forecast_observation_disagreement` rather than treating the zero as an API failure.
- Forecast grids now query all documented flight levels FL270-FL440 and use the same geographic radius as the detection query.
- `GetAttributionMetrics` is treated as a historical attribution product. Same-day live scans do not call it; explicit historical `--start/--end` scans may request it.

## Trading integration path

Do not translate CFI mechanically into degrees. Archive the pre-event model forecast, official station outcome, Google contrail features, satellite cloud/solar features, and contemporaneous market prices. Then estimate a station/horizon-specific residual such as:

`official_high_F - pre_event_model_high_F = f(contrail_features, cloud_features, regime)`

Only use the signal for fair-value adjustments after it improves untouched out-of-sample forecast error and executable trading results.
