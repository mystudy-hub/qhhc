# API summary — waves.py (spec 8.1 segmentation + 8.2 wave fields)

Read this instead of the source. Only dive into waves.py when debugging a
concrete bug (grep + targeted read).

## Constants
- `CONSTITUENT_CHANGE_FLAG = "constituent_change"` — bar quality flag: the bar
  stays in its wave; the WaveRecord is kept WITH `constituent_change=True`
  (never excluded). (data_gap uses `indicators.DATA_GAP_FLAG`.)

## WaveStatus (Enum)
- `FORMING = "forming"`, `COMPLETED = "completed"` — forming waves never enter
  statistics (8.1).

## WaveRecord (frozen dataclass, full 8.2 field set)
- Identity/lifecycle: `wave_id` (`"w-0001"` style, monotonic, deterministic),
  `direction` (+1 up / -1 down / 0 while the initial wave is still initializing),
  `status`, `start_index`/`end_index` (stream indices of bars a..e),
  `reference_index` (bar holding the start reference close; outside the wave),
  `confirmed_at_index` (bar t of the reversal, completed waves only).
- Times: `start_time`/`end_time` (bar_start of bars a/e), `confirmed_at`
  (bar_end of the confirmation bar), `available_at` (latest contributing
  bar_end/received_at; for forming waves the arrival that fixed direction, None
  while initializing).
- Prices/threshold: `start_reference_close` (anchor close for wave 1, else
  C_(t-1)), `extreme_close` (running extreme E; None while direction 0),
  `end_close` (last valid close in a..e), `reversal_threshold` (rho, frozen at
  wave start), `atr_at_start` (A0 = the frozen rho's ATR), `bar_count` (e-a+1,
  stream bars, data_gap bars included).
- Volume/effort: `raw_volume_sum` (valid bars only; anchor bar and reversal bar
  never included), `baseline_volume_sum` (None if any bar's Bv unavailable),
  `wave_activity_rate` (NumFeature, Effort(a..e) = sum V / sum Bv),
  `wave_cum_effort` (NumFeature, CumEffort(a..e) = sum RVOL).
- Derived (float | None; None exactly when inputs not evaluable):
  `peak_excursion = d*(E-R)/A0` (>0 on completed waves),
  `signed_end_displacement = d*(C_e-R)/A0` (CAN be negative — C04 shape),
  `giveback = peak - signed`, `path_distance` (None if a data_gap bar lies in
  a..e), `directional_efficiency`, `wave_speed = peak/bar_count`,
  `mean_bar_ns` (None on gap/unevaluable NS).
- `wave_oiz` (OiZWindow — descriptive only; equals
  `IndicatorState.oi_z_window(a, e)`).
- Flags: `quality_limited` (data_gap inside the wave: record kept, excluded
  from direction statistics), `constituent_change`.

## WaveState(params: WyckoffV4Params)
- `.feed(features: BarFeatures) -> None` — advance by one bar; feed the
  BarFeatures returned by `IndicatorState.advance(bar)` in strict stream
  order (else ValueError). Causal closes-only segmentation:
  - anchor = first valid close; wave 1 spans bars anchor+1.. (anchor bar volume
    belongs to no wave); `wave_initializing` (direction 0) until
    `|C - anchor| >= rho` (equality included).
  - rho = `params.wave_reversal_atr * ATR_previous`, frozen per wave from the
    prior-state ATR at creation t (`features[t].atr_baseline = ATR_(t-1)`);
    if unavailable there (the usual case for the anchor), it freezes at the
    first subsequent bar with an available `atr_baseline`. A0 = that same ATR.
  - Up wave: reversal confirmed at bar t when `C_t <= E - rho` (equality
    included; symmetric `>= E + rho` for down). Old wave ends at t-1
    (`confirmed_at` = bar t's bar_end); the reversal bar's volume belongs to
    the NEW wave; new reference close = C_(t-1) (last valid close before t).
    Volume is never used to confirm reversals.
- `.completed_waves -> tuple[WaveRecord, ...]` (confirmation order).
- `.current_wave -> WaveRecord | None` (snapshot of the forming wave; rebuilt
  per access, deterministic).
- `.waves -> tuple[WaveRecord, ...]` (completed + forming).

## run_segmentation(bars, wave_reversal_atr=(1.0, 1.5, 2.0), *, params=None)
- 8.1 sensitivity helper: runs the full causal pipeline once per multiplier
  over `bars` (Sequence[BarV4]) and returns `{multiplier: completed_count}`.
  Deterministic; `params=None` uses spec-default WyckoffV4Params.

## Invariants (assert in consumers / tests)
- Completed waves: `giveback * atr_at_start < reversal_threshold` STRICTLY
  (spec 8.2; reversal fires at the first bar with `C_t <= E - rho`, so
  `C_e > E - rho`). At the confirmation bar itself `d*(E - C_t) >= rho`.
- `peak_excursion > 0` on completed waves.
