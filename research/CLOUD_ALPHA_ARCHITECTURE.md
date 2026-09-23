# Cloud / Surface Alpha Architecture

## Objective

Add an independent physical-reality signal layer to the weather strategy tournament. The layer is designed to detect when observed cloud/heating/front conditions diverge from the HRRR baseline quickly enough to move the probability distribution of the final official daily high before prediction-market prices fully adjust.

This is a feature layer, not a settlement source. Settlement remains whatever source/rules each Kalshi or Polymarket US contract explicitly specifies.

## Correct current operational satellites

- GOES-19 = operational GOES-East since 2025-04-07.
- GOES-18 = operational GOES-West.
- GOES-16 = historical East data / backup, not the primary live East feed.

## Data flow

```text
NOAA AWS GOES-19 / GOES-18 ABI CMIP
  Band 2 reflectance (daytime cloud/solar attenuation)
  Band 13 brightness temperature (day/night cloud structure)
                    |
                    v
        station-centered 10-20 km crop
                    |
                    v
 reflectance / transmission / cold-cloud features
                    |
                    +-------------------+
                                        |
IEM/NCEI ASOS 1-minute history --------+----> AlphaDeltaEngine
  temperature slope                    |        - solar mismatch
  pressure tendency                    |        - dT/dt
  wind/front features (next)           |        - temp error
                                        |        - cloud structure
NOAA AWS HRRR --------------------------+        - pressure tendency
  2 m temperature                              |
  total cloud cover                            v
  downward shortwave                 signed alpha_delta [-1,+1]
                                              |
                                              v
                                  calibrated daily-high shift
                                              |
                                              v
                              bucket probability redistribution
                                              |
                                              v
                    Kalshi / Polymarket executable orderbook replay
```

## Important modeling rule

`cloud_optical_depth_proxy` is deliberately a proxy, not an official ABI Level-2 cloud optical depth retrieval. Band 2 reflectance is transformed into a monotonic attenuation feature and must be calibrated historically. Do not report this proxy as measured physical COD.

Similarly, the AlphaDelta score is not a probability. The backtest must learn how an alpha score conditional on station, season, local hour, cloud regime and horizon changes the residual distribution of the final daily high versus HRRR/NBM/ensemble baseline.

## Module 1 implementation

`weather_alpha/providers/goes.py`

- anonymous S3 access to `noaa-goes19`, `noaa-goes18`, and historical `noaa-goes16`
- ABI-L2-CMIPC object discovery
- Band 2 and Band 13 retrieval
- GOES fixed-grid geolocation using projection metadata
- station-centered crop
- Band 2 mean/P90 reflectance
- solar transmission proxy
- optical-depth-like proxy
- Band 13 mean brightness temperature
- cold-cloud fraction

The next optimization is byte/range/chunk-aware access or Lambda/SQS notification ingestion so a live system does not repeatedly download full CONUS NetCDF objects.

## Surface implementation

`weather_alpha/providers/surface.py`

- historical IEM ASOS one-minute adapter for backtests
- temperature velocity
- pressure velocity

Operational live adapters should conform to the same `SurfaceObservation` structure. Candidate fast live sources include MADIS HFMETAR / 5-minute ASOS and Synoptic high-frequency feeds where configured.

## HRRR implementation

`weather_alpha/providers/hrrr.py`

- NOAA AWS HRRR public archive
- `.idx` parsing
- HTTP byte-range retrieval for only desired GRIB messages
- 2 m temperature
- total cloud cover
- downward shortwave radiation
- nearest-gridpoint extraction

Backtests must use explicit HRRR cycles and record when each cycle/object became available. A valid-time join alone is not sufficient and can introduce lookahead.

## Module 2 implementation

`weather_alpha/alpha_delta.py`

Current transparent components:

- satellite solar-transmission mismatch versus HRRR cloud baseline
- observed temperature velocity
- current observed-vs-HRRR temperature error
- Band 13 cold-cloud structure
- pressure tendency
- time-to-expected-peak weighting
- freshness/completeness confidence

Sign convention:

- positive = physical observations imply higher/hotter daily max than HRRR baseline
- negative = physical observations imply lower/cooler daily max than HRRR baseline

The default weights are research priors only. They are parameters to test, not production constants.

## Backtest additions

Add these strategy/tournament variants:

1. Satellite cloud-suppression DOWN / lower-bucket signal.
2. Satellite clearer-than-HRRR upside signal.
3. Rapid front / pressure-rise + temperature-rollover bucket elimination.
4. Marine-layer delayed burnoff for coastal stations.
5. AlphaDelta-filtered NO tails.
6. AlphaDelta-adjusted adjacent YES baskets.
7. AlphaDelta as a feature in the calibrated ensemble rather than a standalone trading rule.

For every signal, test delays of 0, 30s, 1m, 2m, 5m, 10m and 15m between data receipt and executable order. This is necessary to determine whether the edge survives realistic data and execution latency.

## Historical training target

For each station-day and each event time `t`:

```text
residual_final_high(t) = official_final_daily_high - baseline_model_expected_high(t)
```

Fit/validate how the satellite/surface features explain that residual. The trading engine should then convert the conditional residual distribution into exact contract bucket probabilities.

## No-lookahead requirements

Every feature row must retain at least:

- source observation time
- source publication/object time where available
- receive/ingest time
- model cycle
- model valid time
- satellite object key and scan/end time
- market quote source time / receive time
- settlement rule version

Historical weather inputs may not be joined merely by nearest timestamp if the observation/model was not yet published at the simulated decision time.
