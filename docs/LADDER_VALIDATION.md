# Structural basket validation

Basket signals require the complete canonical event contract list, unique contract IDs, one event, one settlement basis, and a proven mutually exclusive/exhaustive interval partition. Missing prices must not reduce event membership. A side needs finite prices in [0,1] for every leg and nonnegative finite fees.

Each normalized catalog market can carry `settlement_semantics` with:

- `mapping_status`: `EXACT`
- `evidence`: nonempty provenance of independent rule/precision verification
- `source`, `station`, `date`, `measurement`, `window`: verified settlement identity
- `unit`: `F`
- `value_domain` and `rounding`: `INTEGER` / `WHOLE_DEGREE`, or `CONTINUOUS` / `NONE`
- `lower_inclusive`, `upper_inclusive`: explicit booleans

The basis fields must match across all legs. Inclusivity belongs to each contract. Integer domains require integral boundaries; exclusive bounds are converted to the adjacent integer before checking adjacency. Continuous adjacent endpoints require exactly one interval to own their shared boundary. One lower unbounded tail, one upper unbounded tail, and at least one finite interval are required. Gaps, overlaps, reversed/empty bounds, duplicate outcomes, mismatched membership, and unknown semantics abstain.

The current live catalog has raw rules and thresholds but does not provide this independently verified precision metadata. The shadow does not infer it from a ticker, subtitle, series name, or integer-looking threshold. Basket signals therefore abstain until the catalog truth layer supplies evidence. This change does not implement that separate verification pipeline.

Health records include ladder rejection counts/reasons. Baskets are labeled conditional on normal binary settlement; exchange fallback, cancellation, and fair-price resolution rules are not a guaranteed payout. These signals remain paper/shadow and do not establish actual fill/depth acceptance.
