# API summary — range.py (trend context, trading range, internal Creek/Ice)

Covers spec 5.1–5.6. Pure functions + frozen records; stdlib only; deterministic
content-hash IDs (I-07). Read this instead of the source.

## 5.1 Trend context

- `trend_at(closes, atr, params) -> TrendState` — spec 5.1 classification at the
  last bar of `closes` (C_u = closes[-1]); `atr` is ATR_u (the trend denominator;
  event-time callers must pass ATR_(t-1)). UNKNOWN = not evaluable.
- `trend_at_detailed(closes, atr, params) -> TrendEvaluation` — adds the reason:
  'history_insufficient' | 'atr_unavailable' | 'atr_zero' | None.
  `TrendEvaluation(state: TrendState, reason: str|None)`.
- Requirements: len(closes) >= max(trend_sma_slow, trend_window+1, trend_sma_fast);
  atr must be > 0. n = trend_window uses n price changes / n+1 closes; OLS slope
  uses the last n closes. ER denominator 0 (complete prices) is defined as 0.
- `sma(values, period) -> float|None`, `ols_slope(values) -> float|None` — helpers.
- `TrendSnapshot(asof_bar, trend_now, context_for_next_bar, context_available_at=None)`
  — `trend_now` and `context_for_next_bar` store the SAME `trend_at(u)` value;
  `context_available_at` is bookkeeping (actual availability time, e.g. bar end).
  `make_trend_snapshot(asof_bar, closes, atr, params, context_available_at=None)`
  computes trend_at once and stores it in both fields.

## 5.2/5.5/5.3 TradingRange

- `TradingRange` frozen dataclass fields: `range_id, version, state (RangeState),
  S, R, M=(S+R)/2, W=R-S, A_ref, epsilon, origin_bar, established_bar,
  parent_range_id=None, outside_up_streak=0, outside_down_streak=0`.
  Boundaries/epsilon/A_ref are frozen per version.
- `range_id` = deterministic sha256 content hash ("rng-" + 12 hex); stable across
  versions (`next_version` keeps it).
- Methods:
  - `age(current_index)` — valid bars from origin inclusive (origin + current).
  - `bars_since_established(current_index)` — tracked separately.
  - `update_outside_streaks(close) -> (up, down)` — 5.5 counters vs frozen
    boundaries: close > R+eps ⇒ up+1 else 0; close < S-eps ⇒ down+1 else 0.
    Pure (no mutation).
  - `with_outside_streaks(up, down)` — frozen copy with new counters.
  - `next_version(state, parent_range_id=None)` — version+1, counters reset to 0
    (new version effective from the NEXT bar; never backfilled).
  - `touches_support(high, low)` / `touches_resistance(high, low)` /
    `touches_any_boundary(high, low)` — bar range intersects [S-eps, S+eps] /
    [R-eps, R+eps].
  - `validate_width(params) -> str|None` — 5.2 width reason.
- `RangeBar(index, high, low, close, is_gap=False)` NamedTuple — minimal bar view
  (index = 0-based stream index; is_gap marks a DATA_GAP placeholder bar).
- `RangeRejection(reason, detail='')` — establishment rejection.
  Reason codes: `WIDTH_TOO_LARGE='width_too_large'`, `WIDTH_TOO_SMALL='width_too_small'`,
  `ER_TOO_HIGH='er_too_high'`, `INTERIOR_FRACTION='interior_fraction'`,
  `TOUCHES='touches'`, `DATA_GAP='data_gap'`, `ATR_REF_UNAVAILABLE='atr_ref_unavailable'`.
- `establish_path_a(origin_bar, established_bar, s, r, a_ref, params,
  price_resolution, *, state=ACTIVE, parent_range_id=None, version=1,
  epsilon=None) -> TradingRange | RangeRejection` — path-A constructor (origin =
  first climax bar; a_ref = A_SC/A_BC). epsilon defaults to
  `contract.boundary_epsilon(...)` from a_ref. Width validated (both sides).
- Width rule: `range_min_width_atr*A_ref <= W <= range_max_width_atr*A_ref`.

## 5.4 Path B establishment

- `establish_path_b(bars, params, atr_ref, price_resolution, *, state=ACTIVE,
  parent_range_id=None, version=1) -> TradingRange | RangeRejection` —
  implements 5.4 exactly. `bars` = raw recent bars ending at the establishment
  bar with **at least generic_range_window + 1 entries** (last
  `generic_range_window` form the window; one extra prior close feeds the window
  ER) — fewer raises ValueError. `atr_ref` = ATR of the bar BEFORE establishment.
- Check order: atr available → data_gap → width → ER → interior fraction →
  touches. Rejection reasons as above (path B keeps the 5.2 two-sided width rule;
  width_too_small is the engineering name for the lower bound).
- Interior: >= range_interior_close_fraction of window closes in [S+eps, R-eps].
- `greedy_touch_positions(bars, level, epsilon, min_separation_bars) -> tuple[int,...]`
  — 5.4 greedy per-side touch selection: candidates = bars whose range intersects
  [level-eps, level+eps]; a candidate counts when it is >= min_separation_bars
  positions after the last COUNTED touch AND >=1 bar strictly between them is
  fully outside the area. Extreme-defining bar is an ordinary candidate (counts
  at most once). Returns positions (indices into `bars`).

## 5.5 Lifecycle predicates (pure; section 7.5 applies them)

- `should_expire(rng, current_index, params, clear_outside_trend) -> bool` —
  age >= range_max_age and no clear outside trend (expiry carries no direction).
- `should_supersede(rng, current_index, params, last_touch_index,
  interior_range_completed) -> bool` — age >= range_stale_min_age AND no
  boundary touch within the last range_stale_bars bars (last_touch_index =
  stream index of most recent touch or None) AND an interior path-B range
  completed. New range sets parent_range_id -> old range_id.
- `should_retire(phase_e_supported, new_path_b_formed_outside) -> bool` —
  Phase E supported then a new path-B consolidation forms outside.

## 5.6 Internal Creek / Ice

- `internal_creek(swing_points, rng, current_bar) -> SwingPoint|None` — most
  recent confirmed & usable swing HIGH after rng.origin_bar with price in
  [M, R+eps]; ordered by confirmed_at_index. None when absent.
- `internal_ice(swing_points, rng, current_bar) -> SwingPoint|None` — symmetric
  swing LOW in [S-eps, M].
- `checked_internal_creek(...) / checked_internal_ice(...,
  creek_mode: CreekMode)` — with `CreekMode.RANGE_ONLY` return
  `NotImplementedMarker(feature)` (callers emit not_implemented); with
  RANGE_AND_INTERNAL they delegate to the functions above.
- `NotImplementedMarker(feature: str)` frozen dataclass.
- Note: `CreekMode.RANGE_ONLY` was added to params.py (spec 5.6 / 5.6 line:
  "range_only 时这些事件输出 not_implemented"); it was missing in phase 1.
