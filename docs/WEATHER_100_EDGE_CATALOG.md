# Weather 100-Edge Catalog

This file preserves the user's 100-edge research list as hypotheses for the weather trading program. Nothing here is assumed true until verified against exact venue settlement rules, causal timestamps, data latency, executable prices, fees/slippage, realistic fills, and chronological out-of-sample results. Claims about venue resolution semantics, data source timing, station hardware, or legal/access constraints are **VERIFY** items until independently confirmed.

Priority shorthand used below: **P0** = test/build now; **P1** = high-value next; **P2** = selective; **P3** = backlog; **INFRA** = enabling system work; **VERIFY** = must confirm before production use.

## I. Low-Latency Satellite & Hardware Ingestion

1. **AWS S3 SNS Triggers** — subscribe to NOAA GOES/NEXRAD notifications instead of polling to reduce ingest latency. **P1/INFRA**
2. **Byte-Range NetCDF Slicing** — fetch only spatial/variable ranges needed around target stations. **P1/INFRA**
3. **GOES GLM Optical Pulse Counts** — use lightning activity as early convective initiation signal. **P1**
4. **GOES Band 2 Visible Albedo Deficit** — compare observed reflectance/cloud shielding against model-implied solar input. **P1**
5. **GOES Band 13 Clean-IR Cloud-Top Cooling Rate** — use cloud-top dT/dt as developing-convection feature. **P2**
6. **Local SDR Backup** — physical satellite ingest as resilience hedge. **P3/INFRA**
7. **MRMS 2-Minute Grids** — use high-frequency precipitation grids for cooling/arrival timing. **P1**
8. **Direct MADIS TCP/LDM Streams** — lower-latency ASOS ingest if accessible and operationally justified. **P2/INFRA/VERIFY**
9. **Private Weather Station Proxies (CWOP)** — upstream air-mass/front tracking near target station. **P1**
10. **DOT RWIS Highway Sensors** — nearby one-minute-ish surface proxies for fronts/outflow. **P2**

## II. Sub-Hourly Surface & Micro-Meteorology

11. **1-Minute ASOS Acceleration (d²T/dt²)** — detect exact transition from continued heating to stall/cooling. **P0**
12. **Urban Heat-Island Lag** — station-specific thermal inertia model. **P1**
13. **Marine-Layer Dissipation Tracking** — GOES-based burnoff timing for coastal highs. **P1**
14. **Pre-Frontal Compressional Warming** — transient spike before frontal passage. **P1**
15. **Wind Direction vs Runway/Sensor Orientation** — station-specific siting/heat advection effect. **P2/VERIFY**
16. **Evaporative Cooling / Virga** — radar + humidity conditions predicting abrupt cooling. **P1**
17. **Dewpoint Spikes as Heating Caps** — latent-heat shift limiting further sensible warming. **P1**
18. **Soil-Moisture Initialization Error** — wet-ground bias causing model high-temperature error. **P2**
19. **Snow-Cover Albedo Revisions** — observed melt/coverage mismatch vs model initialization. **P2**
20. **Sea-Breeze Fronts** — one-minute wind/temperature shift locking coastal daily high. **P0/P1**

## III. NWP Grid Models & Advanced Physics

21. **RRFS Edge** — test RRFS incremental value relative to current operational baselines; current operational status must be verified at test time. **P2/VERIFY**
22. **RTMA-RU 15-Minute Assimilation** — use rapid analysis updates to detect surface-state errors before model refreshes. **P1/P2**
23. **HRRR Time-Lagged Ensembles** — combine recent deterministic cycles to estimate localized uncertainty/revision direction. **P1**
24. **ECMWF AIFS** — AI-model incremental value for longer horizons. **P2**
25. **GraphCast Local Inference** — longer-horizon AI-model feature if operational benefit exceeds data/compute burden. **P3**
26. **Model Topography Smoothing Errors** — station/terrain residual bias. **P2**
27. **PBL Collapse Timing** — evening decoupling and rapid cooling risk. **P1**
28. **Bilinear Grid Interpolation** — interpolate surrounding grid points to exact station coordinates. **P0/INFRA**
29. **HRRR Smoke/Aerosol Suppression** — smoke/cloud-radiation mismatch as temperature-high bias feature. **P2**
30. **NBM Weighting / Revision Inference** — infer next blend shift before public update. **P2/VERIFY**

## IV. NLP, Forecaster Intent & Unstructured Text

31. **AFD NLP** — detect phrases indicating model busts, delayed clearing, lowered highs, uncertainty. **P1**
32. **TAF Revisions** — amendments in cloud/wind regime as leading temperature-state change. **P1**
33. **PIREP Cloud Information** — real-time cloud-base/top proxy for solar blockage. **P2**
34. **CWSU Briefings** — forecaster timing/convective context where publicly available. **P2/VERIFY**
35. **SPECI Triggers** — special reports for wind shifts/weather changes as high-frequency regime-change signal. **P0/P1**
36. **Confidence Sentiment** — AFD uncertainty language as spread/sizing control. **P2**
37. **Previous-Shift/Human-Revision Inertia** — test whether official forecast revisions lag model shifts. **P2**
38. **ATC Audio Transcription** — pilot/weather commentary extraction if lawful and operationally practical. **P3/VERIFY**
39. **Local Meteorologist Social Feeds** — unstructured regime metadata/model-bust annotations. **P3**
40. **NWS Partner Chat** — only if explicitly authorized and lawful. **P3/VERIFY**

## V. Convective & Severe-Weather Alpha

41. **Dual-Pol Hydrometeor Classification** — distinguish rain/hail/melting precipitation for cooling magnitude. **P2**
42. **Cold-Pool Outflow Boundaries** — radar velocity/surface tracking for cooling arrival. **P1**
43. **Cell-Merger Intensification** — merger state as stronger precipitation/cooling risk. **P2**
44. **Lightning-Jump Heuristic** — rapidly rising GLM activity as developing-convection feature. **P1/P2**
45. **VIL Collapse / Microburst Risk** — convective collapse as rapid cold-pool trigger. **P2**
46. **Tornado Debris Signature** — primarily station-data failure/void/review kill-switch, not baseline alpha. **P3/RISK**
47. **Anomalous-Propagation Filtering** — prevent false rain/cooling signal from radar artifacts. **P1/INFRA**
48. **Mesocyclone Tracking** — project storm arrival at target station. **P2**
49. **CAPPI** — constant-altitude radar representation for storm mass/timing. **P2**
50. **Dryline Bulges** — humidity/temperature regime boundary tracking. **P2**

## VI. Station Quirks & Hardware Artifacts

51. **Sensor Aspiration Failure Detection** — unphysical heating spikes as QC/risk feature. **P2/VERIFY**
52. **KNYC Microclimate** — station-specific residual model for Central Park rather than airport proxies. **P0/P1**
53. **Thermometer Precision / Response-Time Differences** — station hardware metadata as conditional feature. **P2/VERIFY**
54. **Station-Siting Artifacts** — local exhaust/pavement/traffic effects where documented. **P2/VERIFY**
55. **Wind-Siting Blocking** — direction-dependent mixing errors caused by local obstacles. **P2/VERIFY**
56. **Freezing-Rain Sensor Icing** — sensor-response anomaly / QC risk. **P3/VERIFY**
57. **Backup-Sensor Handoffs** — failover sensor bias/reconciliation. **P2/VERIFY**
58. **Maintenance Windows** — avoid or flag periods with station maintenance/NOTAM risk. **P1/RISK**
59. **Precipitation Gauge Under-Catch** — wind-dependent precipitation measurement bias. **P2**
60. **Snow-Depth Sensor Blind Spots** — blowing snow/grass/artifact risk. **P3**

## VII. Settlement Rules & Legal Semantics

61. **Trace Precipitation Semantics** — exact treatment of trace amounts per venue/series. **P0/VERIFY**
62. **Missing-Data Semantics** — exact venue behavior when required observation data are missing. **P0/VERIFY**
63. **Local Standard Time vs DST** — reconstruct the exact climate-day window in UTC. **P0**
64. **Midnight Temperature Carryover** — previous-night reading can become next climate day's high. **P0/P1**
65. **Integer / Unit Rounding Pipeline** — preserve native precision and model exact settlement conversion/rounding. **P0/VERIFY**
66. **Hourly / 6h / 24h Override Semantics** — verify how climate products reconcile extrema and corrections. **P1/VERIFY**
67. **Human QC / Manual Override Delay** — probability/timing of official correction of anomalous spikes. **P1/VERIFY**
68. **Re-Opening / Corrected Settlement Risk** — defensive reconciliation if venue re-settles corrected data. **P1/VERIFY**
69. **Hourly-vs-Daily Source Mismatch** — never assume same source; map exact source per series/event. **P0/VERIFY**
70. **Polymarket US Resolution Source** — independently map exact oracle/source/rules before cross-venue claims. **P0/VERIFY**

## VIII. Market Microstructure & Order-Book Dynamics

71. **Taker-Fee Squeeze** — exact fee model in every backtest. **P0**
72. **Phantom-Depth Detection** — cancellation rate / persistence before assuming executable depth. **P1**
73. **Adverse-Selection Markouts** — 1/5/15-minute post-fill markouts for maker strategies. **P0**
74. **Dual-Sided Book Reconstruction** — reconstruct complementary YES/NO asks and synthetic spreads from PMXT. **P0**
75. **Back-of-Queue Simulation** — maker fills only after prior queue is consumed. **P0**
76. **Bucket-Edge Transitions** — liquidity/behavior around temperature bracket changes. **P1**
77. **Friday / Off-Hour Illiquidity** — spread/depth conditioning. **P2**
78. **Correlated-Market Spreading** — hedge/relative value across physically linked stations only after basis validation. **P2**
79. **Double-Hit Prevention** — deduplicate multiple sensors/features describing the same physical event. **P0/RISK**
80. **Fractional Kelly Sizing** — size only after calibrated edge, depth, and model uncertainty are established. **P1/RISK**

## IX. Rust High-Performance Architecture

81. **Zero-Copy Deserialization** — reduce allocations in live market-data path. **INFRA/P2**
82. **SIMD NetCDF Parsing** — optimize only after profiler identifies parsing as bottleneck. **INFRA/P3**
83. **Lock-Free Ring Buffers** — isolate market-data ingest from alpha evaluation. **INFRA/P2**
84. **Memory-Mapped Parquet** — efficient research scans over large PMXT datasets. **P1/INFRA**
85. **Kernel-Bypass Networking** — only if measured network-stack latency is economically material. **P3**
86. **Pre-Allocated Structs** — avoid critical-path allocation in execution engine. **INFRA/P2**
87. **Fixed-Point Math** — integer/fixed-point cents/probabilities for reproducible execution arithmetic. **P1/INFRA**
88. **Static Dispatch** — compiler inlining / predictable hot path if Rust migration occurs. **P3/INFRA**
89. **CPU Affinity / Core Pinning** — only after profiling shows scheduling jitter matters. **P3/INFRA**
90. **L1 Cache Optimization** — extreme optimization only after profile evidence. **P3/INFRA**

## X. Cross-Venue & Advanced Trade Mechanics

91. **Execution Latency Profiling** — timestamp Rx → parse → alpha → serialization → Tx. **P0/INFRA**
92. **Region / Network Proximity** — empirically ping/profile venue endpoints before paying for co-location. **P1/INFRA/VERIFY**
93. **Cross-Venue Arb** — only when physical event, source, window, strike/bucket, resolution and fees match exactly. **P1/VERIFY**
94. **API Throughput Optimization** — WebSockets for reads, respect documented venue rate limits; do not use techniques that violate venue policies. **P1/INFRA**
95. **Synthetic Weather Derivatives** — combine related contracts after dependency/correlation modeling. **P2**
96. **DuckDB Analytics** — fast local scans/feature studies over Parquet. **P0/INFRA**
97. **Date-Block Bootstrapping** — resample whole settlement dates/months/seasons, never random trade rows. **P0**
98. **Geohash Spatial Indexing** — fast station/radar/model spatial joins. **P1/INFRA**
99. **Fuzzy NWS CLI Parsing** — robust parsing of formatting/spacing changes while preserving exact source text. **P1/INFRA**
100. **Wait-and-See Kill Switch** — abstain if weather, market, clock, settlement mapping, or latency health disagree. **P0/RISK**

## Immediate test queue extracted from the 100

The fastest path to incremental edge from this list is:

1. ASOS 1-minute first derivative + second derivative + high-so-far + minutes since high.
2. SPECI/wind-shift/sea-breeze state changes.
3. Dewpoint and pressure tendency.
4. NBM baseline probability distribution + station/season residual calibration.
5. HRRR/NBM revision and cross-model disagreement.
6. GOES visible/IR cloud shielding; GLM; MRMS precipitation/outflow timing.
7. Exact settlement reconstruction / source-family mapping / LST handling.
8. Fee, latency, depth, queue, markout and price-floor economics.
9. Station-specific models (KNYC, KMDW, KMIA, KLAX, KDEN).
10. Cross-venue only after exact contract-resolution equivalence is proven.

This catalog is additive to `WEATHER_ALPHA_MASTER_STRATEGY_REGISTRY.md`; where the two overlap, the master registry remains the canonical prioritized research map.