# cta_lab/wyckoff_v4

Wyckoff V4 weighted-index market recognizer, implementing
`docs/WYCKOFF_MARKET_RECOGNITION_V4.md`.

Invariants that cut across every module: determinism (I-07), no silent
fallbacks for missing data or unimplemented areas (I-06), tri-state
condition propagation (F-19), fixed-point prices at `price_resolution`
(F-13, section 4.3), and windows that count valid trading bars with both
ends inclusive (F-05).

## Module map

| Module | Spec sections | Responsibility |
|---|---|---|
| `types.py` | 2.2, 2.3, 6.8, 7.1, 8.3.3 | TriState logic (`and_`/`or_`/`not_`, group compare), MarketRegime / TrendState / RangeState / HypothesisStatus / CountingOrigin / AcceptanceMode enums, complete 6.8 EventCode table. |
| `params.py` | 10.1-10.6 | `WyckoffV4Params`: every table 10.1-10.5 parameter with exact defaults, mode switches, rule/parameter versions, `validate()` enforcing every 10.6 constraint, derived `snapshot_ring_length` (F-15). |
| `contract.py` | 4.1-4.3 | `SeriesContract` / `BarV4`, eligibility checks (index_source_mismatch, ohlc rejection, ex_post_weights, declared-index-missing), bar sanity, fixed-point `to_ticks`/`from_ticks`, boundary epsilon, `confirm_step`. |
| `indicators.py` | 4.4–4.6 | Dual ATR/Bv, separate preparation/commit, causal pivots and descriptive OI. |
| `range.py` | 5 | Generic ranges, frozen S/R, causal Creek/Ice and outside counters. |
| `events.py` | 6 | Climax clusters, absorption, Spring/UT watches, breakouts and SOS/SOW. |
| `lifecycle.py` | 7 | Acceptance, dependency invalidation, AR/ST/Test and LPS/LPSY transactions. |
| `waves.py / evidence.py` | 8 | Causal segmentation, A/P/B/D groups and tri-state dominance. |
| `engine.py / output.py` | 9 | Eleven-step closed-bar transactions, immutable snapshots and deterministic JSON. |
| `evaluation.py` | 11.4 | Availability-aware full OHLC bar evaluation and barrier ambiguity. |

## Tests

`tests/` contains self-contained pytest fixtures (constructed bars and
contracts, no database or network access).

```powershell
.\.venv\Scripts\python.exe -m pytest cta_lab/wyckoff_v4/tests/ -q -p no:cacheprovider
```

Includes A01–A07, B01–B18, C01–C05, D01–D20 named scenarios and review regressions.
Some B/D scenarios combine detectors and constructed lifecycle records; they are not all
raw OHLC end-to-end tests. A separate complete-engine replay compares all 70 prefixes,
including breakout, acceptance and invalidation. Review details: [REVIEW.md](REVIEW.md).
Raw OHLC paths also cover SC/BC–AR–ST, Spring/UT tests, JOC/FTI pullbacks and
Phase D/E. Dependency graph and active-object uniqueness are checked before publication.

## Usage

```python
from cta_lab.wyckoff_v4 import SeriesContract, WyckoffV4Engine

contract = SeriesContract(
    index_id="MY_WEIGHTED_INDEX", index_source="declared_vendor",
    index_method_version="1", data_version="realtime-1",
    constituent_universe_definition="all listed delivery months",
    weight_method="open_interest", weight_asof_rule="previous trading day close",
    weight_update_schedule="daily", ohlc_method="vendor_published",
    price_resolution=0.01, volume_method="sum_raw", volume_unit="contracts",
    revision_policy="versioned",
)
engine = WyckoffV4Engine(contract, timeframe="1d")
# bar: BarV4, with a genuine received_at. Unclosed bars return None.
publication = engine.process_bar(bar)
if publication is not None:
    print(publication.snapshot["market_regime"])
    print(publication.to_json())
```

Intraday timeframes use session-matched volume samples. The input adapter supplies
the actual source, calendar/session information and `data_gap` flags. `decision_time`,
`compute_delay` and `weight_asof_at` can register actual decision and weight timing.
`append_available_oi(asof)` appends newly available descriptions without rewriting snapshots.
`evaluate_ohlc` requires the caller's expected trading intervals, so missing first bars
cannot be silently skipped.

## Scope

This is an executable research baseline (`v4-default-unvalidated`), without database,
market-feed or trade-execution integration, real-market calibration or out-of-sample results.
`timestamp_path` evaluation is not implemented. Internal levels in `range_only` mode and
Phase C without Spring/UT report `not_implemented` as required by the specification.
Full history and transaction state copies favor auditability; memory/throughput have not
been optimized for millions of bars. Threshold margins cover major factual conditions,
not every numeric condition in the specification.
