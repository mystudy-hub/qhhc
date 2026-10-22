# API summary — foundation modules (types, params, contract, indicators)

Auto-generated index of the phase-1 public surface. Later phases: read THIS file
instead of reading module sources. Only dive into a source file when debugging a
concrete bug (then read the minimal range via grep + targeted read).

## types.py
- `TriState` enum: TRUE / FALSE / NOT_EVALUABLE (members: `TriState.TRUE` etc.)
- `and_(*values)`, `or_(*values)`, `not_(value)` — 8.3.3 operator table
- `tri_equal(l, r)`, `tri_not_equal(l, r)` — group compare (NE side => NE)
- Enums: `MarketRegime`, `TrendState` (UP/DOWN/NEUTRAL/UNKNOWN), `RangeState` (9 states:
  building/active/exit_up_candidate/exit_down_candidate/exit_up_accepted/exit_down_accepted/
  retired/expired/superseded), `HypothesisStatus` (candidate/confirmed/invalidated/expired),
  `CountingOrigin` (PRE/INCL/POST), `AcceptanceMode` (hold/pullback/late_hold),
  `EventCode` — complete 6.8 table, 55 members (names == spec codes, e.g. SC_FEATURE,
  SPRING_LOW_VOLUME, BREAKOUT_UP, LPS, UTAD_WEAKNESS_SEQUENCE, DATA_GAP, ...).

## params.py
- `WyckoffV4Params` frozen dataclass — EVERY table 10.1–10.5 parameter at spec defaults
  (attribute names == spec parameter names). `.validate()` enforces all 10.6 constraints,
  ValueError names the offending parameter. Constants on the class: `RULE_VERSION='4.0'`,
  `PARAMETER_VERSION='v4-default-unvalidated'`, derived `snapshot_ring_length` (F-15).
- Mode enums: `BaselineMode` (ROBUST/STANDARD), `IntradayVolumeMode` (SESSION_MATCHED),
  `OiMode` (ANNOTATE), `CreekMode` (RANGE_AND_INTERNAL / RANGE_ONLY).
- Construct: `WyckoffV4Params()` for defaults; keyword-override any parameter for tests
  (e.g. `WyckoffV4Params(atr_period=2, event_min_range_age=3)` then `.validate()`).

## contract.py
- `SeriesContract` dataclass — all 4.1 series fields (index_id, index_source, price_resolution,
  ohlc_method, volume_method, weight_method, weight_asof_rule, oi_method, revision_policy, ...).
- `BarV4` frozen record — (bar_start, bar_end, open, high, low, close, volume, trading_day,
  session_id, is_closed=True, received_at=None, open_interest=None, oi_available_at=None,
  quality_flags=frozenset).
- `check_series_contract(contract, index_available=True) -> SeriesEligibility`
  (status EligibilityStatus; reason EligibilityReason — index_source_mismatch, ex_post_weights,
  declared_index_missing, ohlc_method_invalid, ...).
- `check_weight_timing(bar, weight_asof_at)`, `check_bar(bar) -> tuple[str,...]`,
  `check_bar_sequence(bars) -> tuple[str,...]`.
- Fixed-point: `to_ticks(price, resolution) -> int` (raises ContractViolation on non-multiple),
  `from_ticks(ticks, resolution) -> float`, `boundary_epsilon(units, atr_fraction, resolution, atr_ref)`,
  `confirm_step(units, resolution)`.

## indicators.py
- `IndicatorState(params, *, intraday=False, oi_method='')`:
  - `.advance(bar: BarV4) -> BarFeatures` — F-16 ordered; call once per valid closed bar.
  - `.effort(start, end) -> NumFeature`, `.cum_effort(start, end) -> NumFeature` — inclusive
    0-based stream indices of advanced bars.
  - `.oi_z_window(start, end) -> OiZWindow`.
  - `.features` — history of BarFeatures; `.swing_points`, `.swing_discards`.
- `BarFeatures` fields: index, bar, valid, zero_volume, spread,
  ns/cp/lw/uw/net_move/gap/rvol (all `NumFeature`), atr_baseline (NumFeature; selected mode),
  atr_baseline_standard/robust (float|None), bv_baseline (+standard/robust), tr/atr/bv capping
  ratios, oi (OiFeatures).
- `NumFeature`: `.value` (float|None), `.reason` (NotEvaluableReason|None), `.available`
  property (bool). `NumFeature.not_evaluable(reason)`, `NumFeature.is_available()`.
- `NotEvaluableReason` — exact reason-code names (oi_method_unknown, oi_missing,
  oi_history_insufficient, oi_zero_scale, oi_not_yet_available, bv_unavailable, ...).
- `SwingPoint(kind, index, price, bar_start, confirmed_at_index, usable_from_index)` —
  confirmed at j+k, usable from j+k+1, immutable. `SwingKind` HIGH/LOW.
- `true_range(high, low, close, prev_close)`, `effort_ratio(volumes, baselines)`,
  `cumulative_effort(rvols)` — pure helpers.
- `OiFeatures` (d_oi, oi_z, oi_quadrant + per-field available_at) with `.realtime_view()`
  for late-OI handling; `OiQuadrant` per the 4.6 table.
