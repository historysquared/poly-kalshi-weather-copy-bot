# Google Contrails API audit — 2026-09-21

## What the official v2 API says

- `GET /v2/detections` returns observational contrail detections as GeoJSON LineStrings.
- A detections request can span at most 24 hours.
- `bounds` is a polygon filter: Google returns detections that intersect any part of the polygon.
- Detections are observational and are not mapped to unique flights.
- Detection data can change after the fact because models can be upgraded and data can be backfilled.
- GOES-West detections are supported.
- `GET /v2/grids` is forecast data, not observed detections. Grids are 0.25-degree, hourly, four-dimensional fields.
- Official flight levels are FL270 through FL440 in 10-FL increments.
- CFI ranges 0–4 and is a warming-severity index derived from expected effective energy forcing; it is not a surface-temperature change.
- Persistent formation probability is 0–100%, though Google notes practical calibrated values are typically no higher than about 40%.
- `GetAttributionMetrics` accepts at most a 14-day span, but the public docs do not document a real-time availability lag.

## Problems found in our implementation / interpretation

1. **Feature count is not unique contrail count.**
   The same persistent contrail can be returned as a LineString in several satellite frames.
   We now label it `feature_detection_count` and record detections per unique frame.

2. **The 150-km setting is not a circular radius.**
   Our helper creates an approximate square whose half-width is 150 km north/south/east/west.
   The corners are farther from the station. Outputs now explicitly record the square-bound semantics.

3. **Raw line-length sums are not clipped to the query polygon.**
   Google returns any LineString that intersects the polygon; the returned geometry can extend outside it.
   Therefore `total_length_km` is a raw intersecting-feature diagnostic, not kilometres of contrail physically inside the box.
   Do not use it as an exposure quantity until geometry is clipped or masks are integrated.

4. **We compared unlike time windows.**
   A current midnight-to-now Stayton scan had been compared with a 12:00–20:00 historical baseline.
   That percentile was invalid. The current-day reporter now suppresses percentile output when windows differ.
   Market-city panels use identical local-time windows for every day.

5. **Forecast-grid zeros are not the observed-detection series.**
   On 2026-09-21 the API returned zero CFI / persistent-formation probability / expected forcing over multiple US cities even while observed detections were numerous.
   Raw NetCDF inspection confirmed the API really returned zeros; this was not an xarray parsing error.
   Treat forecast-vs-observation disagreement as a model feature / miss, not as proof observations are absent.

6. **Forecast flight-level coverage was incomplete.**
   We queried FL300–FL400. Official grids support FL270–FL440.
   The provider now requests the full documented range by default.

7. **Recent attribution metrics have an empirical lag.**
   On 2026-09-21/22, OBSERVATION attribution requests for Sep 16–21 returned OUT_OF_RANGE, while Sep 15 was accepted.
   Google documents the endpoint and 14-day maximum span but not this freshness lag.
   Treat the latest supported attribution date as dynamic and cache/probe it rather than assuming real-time availability.

## Research implications

For trading research, prioritize:
- same-local-time feature counts;
- unique detection frames;
- detections per frame;
- peak-hour feature count;
- detection-mask area/probability when added;
- forecast-vs-observed disagreement;
- lag-correct joins to weather forecasts and market prices.

Do not treat raw intersecting LineString length, CFI, or attribution forcing as a direct surface-temperature adjustment.
