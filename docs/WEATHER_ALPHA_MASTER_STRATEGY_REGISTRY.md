# Weather Alpha Master Strategy Registry

## Purpose

This is the canonical idea inventory for the weather trading project. It is intentionally broader than the active build roadmap. The purpose is to preserve every plausible edge, rank it by expected economic value and implementation cost, identify dependencies, and surface combinations that may be stronger than any standalone signal.

Nothing in this registry is assumed to be true. Each item is a hypothesis until validated against exact settlement semantics, causal data, executable prices, fees, latency, realistic maker fills, and chronological out-of-sample results.

## Priority labels

- **P0 — structural / build now:** highest-conviction, least-crowded, directly tied to settlement truth or execution mechanics.
- **P1 — high-value / test early:** likely useful once P0 data plumbing exists; may become production features or combination filters.
- **P2 — selective / test after P0/P1:** plausible but more crowded, station-specific, or dependent on richer data.
- **P3 — backlog / research only:** interesting but low expected return on engineering time, very latency-sensitive, or difficult to validate.
- **INFRA — enabling infrastructure:** not a strategy by itself but may improve the entire research/live stack.
- **VERIFY — claim/rule must be independently verified before use.**

## Current money-first ordering

### P0 — build and test first

1. Public whole-°C 5-minute feed inversion to posterior over true whole-°F rolling average.
2. Unobserved-minute extreme reconstruction between public 5-minute stamps.
3. Per-station empirical distribution of `CLI extreme - public-feed extreme`.
4. Bracket-boundary trading when market appears to price public feed rather than official CLI.
5. Local Standard Time vs civil/DST observation-window handling.
6. Settlement reconstruction ownership: know the probable final official value before participants treating public feeds as truth.
7. NBM percentile/probability guidance as baseline contract distribution.
8. Empirical fat-tailed forecast-error distributions; no default Gaussian tails.
9. Per-station model skill ranking and station/season residual distributions.
10. Maker favorite-longshot harvest as a current-weather hypothesis.
11. Maker-first execution with exact maker/taker fee routing and a configurable cheap-contract floor.
12. Realistic queue-position simulation plus 1/5/15-minute adverse-selection markouts.
13. Scheduled-release quote protection: avoid being resting liquidity picked off around DSM/CLI/model-release windows.
14. Cross-venue station/source/window basis modeling rather than naive parity.
15. Exact Kalshi/Polymarket US market-rule, station, source, date, bucket, fee and tick mapping.

### P1 — test immediately after P0 plumbing exists

- peak attenuation from rolling 5-minute averaging conditional on temperature volatility
- daily-low LST-window exposure
- midnight-adjacent readings and carryover
- CLI correction/delay probability and corrected-report risk
- NBM hourly release absorption
- model-vintage inference: which forecast cycle market prices are reflecting
- SPECI/off-hour rapid-change triggers
- 1-minute ASOS acceleration and heating stall detection
- sea-breeze crossing / marine-layer burnoff
- pre-frontal compressional warming
- cold-pool/outflow arrival
- dewpoint/humidity heating caps
- NBM station/season bias correction
- cross-model disagreement as uncertainty
- forecast revision autocorrelation as a tested, not assumed, feature
- remaining-heating probability after time T
- adjacent-bracket coherence and implied-distribution shape checks
- phantom-depth / cancellation-rate diagnostics
- bucket-transition liquidity behavior
- secondary-city / thin-market spread capture
- new-listing lazy pricing
- final-hour forced-unwind behavior
- venue price-discovery lead/lag
- fee-aware venue routing
- KNYC-vs-KLGA and other station-basis distributions where both venues actually list related events
- correlated-event exposure controls / double-hit prevention

### P2 — selectively test after P0/P1

- sensor drift/bias and QC-adjustment probability
- probability a spike is struck by CLI quality control
- hourly-vs-daily settlement-source mismatches
- dewpoint/wet-bulb rounding pipelines
- station outage/missing-data semantics
- RTMA/URMA release lag
- AFD text disagreement with guidance
- NWS gridpoint update vs raw NBM
- weekend/holiday staffing effects
- urban heat-island lag
- marine-layer dissipation
- wind/runway orientation heating artifacts
- evaporative cooling from virga
- soil-moisture initialization errors
- snow-cover albedo revisions
- HRRR time-lagged ensembles
- RRFS incremental value
- RTMA-RU incremental value
- topography smoothing/cold-pool bias
- PBL collapse timing
- HRRR smoke/aerosol mismatch
- AFD NLP and confidence scoring
- TAF revisions
- PIREPs and CWSU briefings
- local meteorologist commentary as regime metadata
- dual-pol hydrometeor classification
- cell mergers, VIL, lightning jumps, mesocyclones, dryline location
- station-specific hardware/siting effects
- maker-side box / complete-set coherence
- overnight spread capture
- book imbalance as short-horizon filter
- precipitation/snow/wind/hourly-temperature adjacent contract families

### P3 — backlog / only if proven worth it

- local SDR satellite backup
- direct MADIS TCP/LDM if accessible and operationally justified
- ATC audio transcription
- restricted/partner-only chat feeds unless legally authorized and access is appropriate
- full local AIFS/GraphCast inference unless cheaper operational alternatives fail
- tornado/debris-signature trading ideas except as risk/QC kill switches
- very low-level radar products unless specific station tests justify them
- most media/behavior/calendar anomalies as standalone strategies
- ENSO/MJO standalone trading signals
- highly speculative station hardware folklore without documented evidence
- extreme systems optimization such as DPDK/kernel bypass, AVX-512 parsing, L1-cache hand-packing, or NIC co-location before profiling shows they matter

---

# Strategy inventory

## I. Settlement measurement and official-resolution mechanics

| ID | Priority | Hypothesis / edge | Notes / dependencies |
|---|---|---|---|
| S01 | P0 | Invert public whole-°C 5-min feed to posterior over true whole-°F rolling average | Core settlement-reconstruction thesis |
| S02 | P0 | Model unobserved minutes between public 5-min stamps | Requires 1-min/HF ASOS history and official CLI comparison |
| S03 | P1 | Peak attenuation from rolling-5-min averaging conditional on dT/dt volatility | Combine with S01/S02 |
| S04 | P0 | Per-station empirical distribution of CLI high minus public-feed max | Must stratify station/season/DST |
| S05 | P2 | Sensor drift/bias history predicts QC adjustments | Requires documented sensor history where available |
| S06 | P2 | Probability a spike is struck by CLI QC | Treat as posterior uncertainty, not certainty |
| S07 | P0 | Trade bucket boundaries where market prices feed rather than CLI | Only after S01-S04 prove a real basis |
| S08 | P0 | LST-vs-DST settlement-window artifact | Exact station/event rules required |
| S09 | P1 | Midnight-adjacent readings on rapid-change nights | Natural extension of S08 |
| S10 | P1 | Daily lows are more exposed to settlement-window artifacts | Test empirically |
| S11 | P1/VERIFY | Price delay/review when preliminary climate data conflict with longer-period extrema | Must verify exact venue rules per series |
| S12 | P1/VERIFY | Trade pre-resolution uncertainty when preliminary CLI may be corrected | Needs correction history and venue behavior |
| S13 | P2 | Dewpoint/wet-bulb contracts have distinct rounding pipeline | Only where such contracts exist |
| S14 | P2/VERIFY | Station outage / missing-data treatment creates resolution uncertainty | Exact venue rules required |
| S15 | P1 | Midnight carryover can lock next day's high before sunrise | Useful in daily high state model |
| S16 | P0 | Settlement reconstruction as a state variable through the day | Derived posterior feeds all strategies |
| S17 | P1/VERIFY | Manual review / corrected CLI creates re-settlement risk | Build reconciliation support regardless |
| S18 | P2 | Hourly vs daily contracts use different settlement sources | Never cross-trade without explicit source mapping |

## II. Forecast distribution and station skill

| ID | Priority | Hypothesis / edge | Notes / dependencies |
|---|---|---|---|
| F01 | P0 | NBM percentile/probability guidance -> bracket probabilities | Primary forecast baseline |
| F02 | P0 | Empirical fat-tailed forecast errors outperform Gaussian tails | Station/season conditional |
| F03 | P0 | Per-station model skill ranking | Different stations may favor different sources |
| F04 | P1 | NBM TMAX/TMIN station-season bias correction | Residual model |
| F05 | P1 | Ensemble spread conditions uncertainty but is not probability itself | Calibration layer |
| F06 | P1 | Cross-model disagreement estimates conditional volatility | Useful for sizing and abstention |
| F07 | P1 | Forecast revision momentum may autocorrelate | Must test before use |
| F08 | P1 | Clear-sky/cloudy conditional bias | Combine satellite/NBM state |
| F09 | P1 | Wind/mixing state changes forecast error | Station-specific |
| F10 | P1 | Convective cooling errors are systematically late | Requires convective state |
| F11 | P1 | Frontal timing error dominates bust days | Could become uncertainty gate |
| F12 | P2 | AI model forecasts add 1-3 day value beyond NBM | Only after baseline |
| F13 | P2 | Synoptic analogs condition residual distribution | Research after simpler residual models |
| F14 | P1 | Remaining-heating physical cap after solar noon | Strong same-day feature |
| F15 | P1 | Probability of new high after T conditional on dT/dt/dewpoint/cloud | Core late-day probability model |
| F16 | P2 | HRRR cycle revision magnitude predicts near-term fair-value change | Incremental only |
| F17 | P2 | HRRR time-lagged ensemble approximates localized uncertainty | Test vs NBM spread |
| F18 | P2 | RRFS prototype/operational feed adds incremental skill | Time-sensitive; validate current operational status |
| F19 | P2 | RTMA-RU surface analysis corrects model state before next model cycle | Incremental data layer |
| F20 | P2 | Topography smoothing creates site-specific bias | Especially valleys/high terrain |
| F21 | P2 | PBL collapse timing causes evening cooling forecast errors | Daily low use case |
| F22 | P2 | Bilinear grid interpolation beats nearest-point extraction | Low-cost engineering improvement |
| F23 | P2 | Smoke/aerosol mismatch suppresses highs | Event-specific |
| F24 | P3 | Reverse-engineer NBM weighting to predict next official blend update | High effort, uncertain payoff |

## III. Surface, satellite and micro-meteorology

| ID | Priority | Hypothesis / edge | Notes / dependencies |
|---|---|---|---|
| M01 | P1 | 1-minute ASOS temperature acceleration detects heating stall | Cheap, interpretable |
| M02 | P1 | Urban heat-island lag improves station-specific cooling model | Site-specific |
| M03 | P1 | Marine-layer dissipation timing controls coastal highs | KLAX/KSFO/KBOS class |
| M04 | P1 | Pre-frontal compressional warming produces brief high spike | Needs front ETA |
| M05 | P2 | Wind relative to runway/siting affects station temperature | Verify station-specific evidence |
| M06 | P1 | Virga/evaporative cooling causes rapid temp drop | Radar + humidity |
| M07 | P1 | Dewpoint rise caps sensible heating | Combine with F15 |
| M08 | P2 | Soil-moisture error drives daytime high bias | Requires land-state data |
| M09 | P2 | Snow-cover albedo mismatch drives warming errors | Satellite + model |
| M10 | P1 | Sea-breeze frontal crossing locks coastal daily high | Strong state transition |
| M11 | P2 | GOES visible albedo deficit detects unforecast cloud shielding | Useful only if latency/value proven |
| M12 | P2 | GOES IR cloud-top cooling detects developing convection | Convective state |
| M13 | P2 | GLM pulse/lightning jump anticipates convective cooling | Could be P1 at high-convection stations if tests support |
| M14 | P2 | MRMS 2-min precipitation improves arrival timing | Data-heavy but potentially useful |
| M15 | P2 | CWOP/private-station proxies front-run target-station changes | QC plus front tracking |
| M16 | P2 | DOT RWIS sensors proxy nearby air-mass transitions | Location-specific |
| M17 | P2 | Cold-pool outflow boundary arrival predicts rapid cooling | Radar velocity / surface network |
| M18 | P2 | Cell-merger intensification increases cooling risk | Convective niche |
| M19 | P2 | VIL collapse / microburst risk predicts cooling | Convective niche |
| M20 | P2 | Dryline position predicts heat/humidity regime transitions | Plains markets |
| M21 | P3 | Mesocyclone/CAPPI/TDS ideas | Mostly risk/QC unless specific edge proven |
| M22 | P3 | Local SDR weather-satellite backup | Resilience, not primary alpha |

## IV. Text, aviation and release-timing information

| ID | Priority | Hypothesis / edge | Notes / dependencies |
|---|---|---|---|
| T01 | P1 | NBM hourly cycle release vs market absorption | Measure lag empirically |
| T02 | P1 | SPECI reports as rapid-change trigger | Public and low-cost |
| T03 | P1 | 5-min/HF-METAR vs hourly METAR precision divergence | Settlement-state input |
| T04 | P1 | Scheduled DSM/CLI window should trigger maker cancel/requote | Protect against pickoff |
| T05 | P1 | Which forecast vintage market is pricing | Useful meta-feature |
| T06 | P2 | RTMA/URMA release lag | Event timing study |
| T07 | P2 | AFD explicit disagreement with guidance | NLP feature |
| T08 | P2 | NWS gridpoint forecast update vs NBM timing | Test lag |
| T09 | P2 | Weekend/holiday staffing affects publication timing | Likely conditioning variable |
| T10 | P2 | AFD confidence-language scoring adjusts spread/size | Risk control more than standalone edge |
| T11 | P2 | TAF revisions precede broader forecast changes | Aviation proxy |
| T12 | P2 | PIREPs refine cloud/base information | Public-data availability varies |
| T13 | P2 | CWSU briefings add forecaster intent | Public availability dependent |
| T14 | P3 | Local meteorologist social posts | Unstructured/noisy |
| T15 | P3 | ATC audio transcription | Legal/operational burden; low priority |
| T16 | P3/VERIFY | Partner-only NWS chat | Only if authorized and lawful |

## V. Market microstructure and execution

| ID | Priority | Hypothesis / edge | Notes / dependencies |
|---|---|---|---|
| X01 | P0 | Maker favorite-longshot harvest | Retest on current weather data |
| X02 | P0 | Maker-first routing where economics support it | Exact fee schedule required |
| X03 | P0 | Configurable cheap-contract floor; ~15c is provisional | Test 5/10/15/20/25c |
| X04 | P0 | Back-of-queue fill simulation | Required for maker validity |
| X05 | P0 | Post-fill 1/5/15m adverse-selection markouts | Maker promotion gate |
| X06 | P0 | Scheduled-release pickoff protection | Cancel before known high-risk windows |
| X07 | P1 | Taker fee squeeze removes small apparent edges | Exact per-series fees |
| X08 | P1 | Phantom-depth cancellation rate identifies non-executable liquidity | Live + historical where possible |
| X09 | P1 | Dual-sided YES/NO book reconstruction reveals synthetic spread compression | Already natural in PMXT |
| X10 | P1 | Adjacent-bracket coherence / unimodality violations | Distribution consistency |
| X11 | P1 | Ladder log-concavity kinks | Related to X10 |
| X12 | P1 | Sum-of-asks > 1 maker-side box | Need exact contract completeness/fees |
| X13 | P1 | Sum-of-bids < 1 complete-set sale | Venue mechanics required |
| X14 | P1 | Bucket transition panic-selling creates maker opportunity | Combine settlement posterior + queue model |
| X15 | P1 | Overnight / low-participation spread capture | Station/venue dependent |
| X16 | P1 | Secondary cities may be less efficient | Capacity may be lower |
| X17 | P1 | New listings may open with lazy/incoherent pricing | Easy to test |
| X18 | P1 | Final-hour forced unwinds | Flow study |
| X19 | P1 | Book imbalance as short-horizon filter | Not standalone core thesis |
| X20 | P1 | Correlated-event exposure / double-hit prevention | Risk architecture |
| X21 | P1 | Fractional Kelly constrained by depth and model uncertainty | Sizing after edge proof |
| X22 | P2 | Friday/holiday illiquidity | Conditioning variable |
| X23 | P2 | Correlated-city spread/hedge trades | Requires basis model |

## VI. Cross-venue and relative value

| ID | Priority | Hypothesis / edge | Notes / dependencies |
|---|---|---|---|
| C01 | P0 | KNYC/CLI vs KLGA/other-source basis distribution | Only if relevant venue contracts actually exist and rules are verified |
| C02 | P1 | Source-methodology wedge across venues | Explicit basis, not arbitrage |
| C03 | P1 | LST vs midnight/civil-window mismatch across venues | Exact rule mapping required |
| C04 | P2/VERIFY | Robinhood/IBKR weather basis | Verify product/rules/availability before work |
| C05 | P2 | Days where settlement sources diverge may produce both-leg winners | Must model as basis distribution |
| C06 | P1 | Route directional leg to lower all-in cost venue | Fees + spreads + fill probability |
| C07 | P1 | Measure which venue leads price discovery by time of day | Useful for lag trading and routing |
| C08 | P1 | Bracket-geometry mismatch permits only partial hedges | Hedge-ratio model |
| C09 | P2 | Venue-specific resolution/dispute risk can be priced | Needs actual US rule history |
| C10 | P0/VERIFY | Direct Kalshi-vs-Polymarket-US parity only for EXACT_EQUIVALENT events | Never assume equivalent station/source/window |

## VII. Behavior and regime conditioning

Treat these primarily as conditioning variables, not standalone strategies, until strong OOS evidence exists.

| ID | Priority | Hypothesis / edge |
|---|---|---|
| B01 | P3 | Heat-wave media coverage overbids hot tail |
| B02 | P3 | Recency bias after forecast bust |
| B03 | P2 | Round-number bracket preference |
| B04 | P3 | Record-temperature narrative flow |
| B05 | P3 | Weekend retail participation changes |
| B06 | P2 | City-level participant-efficiency differences |
| B07 | P2 | Extreme-tail lottery demand can be faded as maker |
| B08 | P3 | Yesterday's outcome anchors today's pricing |
| B09 | P2 | Season-conditional forecast bias |
| B10 | P2 | Shoulder-season uncertainty widens spreads |
| B11 | P2 | Persistent ridge/trough regimes create autocorrelated model errors |
| B12 | P2 | Post-frontal regime higher skill than pre-frontal regime |
| B13 | P2 | Monsoon convective cooling regime |
| B14 | P3 | ENSO/MJO phase conditioning |
| B15 | P2 | DST transition weekends amplify window artifacts |

## VIII. Adjacent weather contracts

| ID | Priority | Hypothesis / edge | Notes |
|---|---|---|---|
| A01 | P2 | Precip trace vs measurable threshold semantics | Rule-sensitive |
| A02 | P2 | Snow accumulation / missing-day rules | Rule-sensitive |
| A03 | P2 | Wind gust vs sustained definitions | Rule-sensitive |
| A04 | P2 | Hourly temperature uses separate settlement pipeline | Separate model |
| A05 | P2 | Preliminary vs final source rounding divergence | Verify source |
| A06 | P3 | Hurricane advisory-cadence markets | Different research stack |
| A07 | P2 | Monthly/seasonal aggregates as correlated sums | Longer horizon, later |
| A08 | P2 | Synthetic combinations of High/Low/Rain | Must account for correlation and contract rules |

## IX. Data-ingestion and infrastructure ideas

| ID | Priority | Enabler | Comment |
|---|---|---|---|
| I01 | INFRA-P2 | AWS SNS triggers for GOES/NEXRAD | Only once satellite/radar proves economic value |
| I02 | INFRA-P2 | Byte-range/object subsetting | Valuable for large gridded data; validate format support |
| I03 | INFRA-P2 | Direct MADIS/LDM stream | Use if accessible and materially faster |
| I04 | INFRA-P2 | Synoptic HF-ASOS / sub-5-minute source | High potential for S02 if historical/live coverage supports it |
| I05 | INFRA-P2/VERIFY | ASOS OMO/direct 1-minute access | Access/availability must be verified |
| I06 | INFRA-P1 | DuckDB over Parquet | Already aligned with architecture |
| I07 | INFRA-P1 | Date-block bootstrap / walk-forward OOS | Mandatory research governance |
| I08 | INFRA-P1 | Geospatial station/model indexing | Useful when station universe grows |
| I09 | INFRA-P1 | Robust/fuzzy CLI parser with provenance | Prefer structured source when possible; fuzzy parsing as fallback |
| I10 | INFRA-P0 | Wait-and-see kill switch | If feeds/rules/state disagree, abstain |
| I11 | INFRA-P1 | End-to-end execution latency profiling | Measure before optimizing |
| I12 | INFRA-P2 | Region proximity / network placement | Only after endpoint RTT profiling |
| I13 | INFRA-P1 | WebSocket reads + disciplined REST writes under official rate limits | No rate-limit evasion |
| I14 | INFRA-P1 | Fixed-point/Decimal price handling | Already core requirement |
| I15 | INFRA-P2 | Rust hot path for proven bottlenecks | Port only after profiling shows Python is limiting P&L |
| I16 | INFRA-P3 | zero-copy/SIMD/lock-free/core pinning/cache tuning | Premature before proven latency bottleneck |
| I17 | INFRA-P3 | DPDK/kernel bypass | Ignore until strategy horizon actually justifies microseconds |

---

# Combination strategies with highest expected value

The likely edge is not one signal. The strongest candidates combine settlement truth, station state, and execution mechanics.

## Combo A — Settlement Posterior + Boundary + Maker

**Components:** S01 + S02 + S04 + S07 + X02 + X04 + X05 + X06.

1. Infer posterior over true settlement-relevant extreme.
2. Identify contracts where a one-degree hidden/rounding difference changes the winning bucket.
3. Estimate fair probability of adjacent buckets.
4. Quote passively only outside high-risk release windows.
5. Cancel when faster information sources create pickoff risk.
6. Require acceptable queue model and post-fill markouts.

This is the highest-priority combination because the information edge and execution edge reinforce one another.

## Combo B — Settlement Posterior + NBM + Fat Tails

**Components:** S16 + F01 + F02 + F03 + F04 + F15.

Use NBM for the prior distribution, then update with station-specific empirical forecast errors and the reconstructed settlement state. This avoids competing on generic ensemble probability alone.

## Combo C — Favorite Harvest + Weather Veto + Release Shield

**Components:** X01 + X02 + X03 + F01 + F02 + S16 + X06.

The model's job is primarily to veto unsafe favorites/tails. Quote structural bias only when the weather distribution and settlement posterior agree that the favorite is genuinely robust, and withdraw around information releases.

## Combo D — Station Skill + Microclimate Regime

**Components:** F03 + F04 + M02/M03/M04/M10/M17 + F15.

Use per-station model ranking with event-regime modifiers. The key question is not which model is globally best but which source is best at a specific station under the current regime.

## Combo E — Cross-Venue Basis + Price-Discovery Lead/Lag

**Components:** C01/C02/C03 + C07 + C06 + X04/X05.

Estimate the physical settlement basis first. Then trade temporary price deviations only after subtracting the expected station/source/window basis and all execution costs.

## Combo F — LST Window + Midnight Carryover + Daily-Low/High State

**Components:** S08 + S09 + S10 + S15 + F15.

Explicitly model the true observation window and whether the event can effectively be locked by readings that retail participants assign to the wrong civil date.

## Combo G — Front/Sea-Breeze Arrival + Settlement Posterior + Maker/Taker Switch

**Components:** M04/M10/M17 + T02 + S16 + X02/X07.

Before the physical boundary arrives, quote or hold only if adverse-selection risk is low. When a rapid-change trigger fires, switch from passive to taker only if the expected physical-state jump exceeds spread + fee + latency cost.

## Combo H — Adjacent-Ladder Coherence + Continuous Weather Distribution

**Components:** F01/F02/S16 + X10/X11/X12/X13.

Build a continuous settlement distribution, convert it to bracket probabilities, and identify ladder shapes that violate probability coherence. This is more robust than treating each contract independently.

---

# Combination-search policy

Do not brute-force hundreds of arbitrary feature combinations. Use a hierarchy:

1. **Structural core:** settlement reconstruction + exact rule mapping.
2. **Probability layer:** NBM + empirical station residuals + current station state.
3. **Execution layer:** maker/taker choice + queue + markout + fee + spread.
4. **Regime layer:** front/cloud/sea-breeze/convective states only where physically justified.
5. **Cross-venue layer:** explicit basis distributions and venue lead/lag.

A combination advances only if each added component improves untouched chronological OOS net P&L or reduces tail risk. Use ablations to prove incremental value.

---

# What not to let distract the project

- Do not optimize Rust microseconds while settlement mapping, maker fills, or data timing remain uncertain.
- Do not build GraphCast/AIFS locally before NBM + station residuals are benchmarked.
- Do not turn every behavioral/calendar observation into a standalone strategy.
- Do not assume public-feed values equal official settlement.
- Do not call station/source/window differences arbitrage.
- Do not use global Polymarket mechanics as Polymarket US assumptions.
- Do not evade venue rate limits or use unauthorized data feeds.
- Do not promote cheap-tail trading because gross EV looks attractive before fees and execution.

---

# Active build sequence

1. Exact Kalshi and Polymarket US rules/catalogs.
2. CLI + ASOS settlement reconstruction across multiple stations/dates.
3. Weather-only PMXT L2 extraction and causal decision snapshots.
4. Maker/taker simulator with queue-at-back assumption and markouts.
5. Start forward shadow recorder as soon as exact mappings exist.
6. NBM ingestion and station-level empirical residuals.
7. Run Combo A, B and C first.
8. Add station-regime features only after those baselines are scored.
9. Add cross-venue basis once both venue rule maps are exact.
10. Paper -> one-contract live -> scale only after real fill/fee/settlement reconciliation.

## First production candidates to try to monetize

1. **Combo A: settlement posterior + bracket boundary + protected maker.**
2. **Combo C: maker favorite harvest + settlement/NBM veto.**
3. **Combo B: NBM + fat-tailed station residual + settlement posterior.**
4. **Combo F: LST/midnight window artifact.**
5. **Combo E: cross-venue basis + lead/lag.**

Everything else remains stored here and can be promoted when data or failed Tier-1 tests justify moving down the list.
