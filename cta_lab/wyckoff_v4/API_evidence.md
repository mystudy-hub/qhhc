# API summary — evidence.py (spec 8.3 direction evidence + 8.4 structure hypotheses)

Read this instead of the source. Covers 8.3.1 wave groups, 8.3.2 B-sequence
current-valid filter, 8.3.3 paths + tri-state dominance, 8.4 structure
hypotheses (C-group acceptance cases C01-C05).

## Enums / exception

- `SideGroup` — DEMAND / SUPPLY / NEUTRAL / NOT_EVALUABLE (P and B group values).
- `ActivityGroup` — UP_MORE_ACTIVE / DOWN_MORE_ACTIVE / BALANCED / NOT_EVALUABLE
  (group A describes activity direction only, never a verdict).
- `Dominance` — DEMAND / SUPPLY / NEUTRAL / NOT_EVALUABLE (NEUTRAL is distinct
  from NOT_EVALUABLE, hence not a TriState).
- `PathName` — the eight 8.3.3 paths: DEMAND_PUSH, SUPPLY_EXHAUSTION,
  DEMAND_BOUNDARY_SUPPORTED, ABSORPTION_RESPONSE (demand side); SUPPLY_PUSH,
  DEMAND_EXHAUSTION, SUPPLY_BOUNDARY_SUPPORTED, DISTRIBUTION_RESPONSE
  (supply mirror; spec reuses the name `boundary_supported` on both sides).
- `ConfigurationError(ValueError)` — raised on the 8.3.3 mutual-exclusion
  violation (demand_result and supply_result both TRUE).

## Tri-state group comparisons

- `group_equal(value, expected) -> TriState` / `group_not_equal(value, expected)`
  — 8.3.3 table: a NOT_EVALUABLE side makes the comparison NOT_EVALUABLE
  (works for SideGroup / ActivityGroup members).

## 8.3.1 wave groups

- `WaveGroupStats` — n_up, n_down, window_wave_ids, med_activity_up/down,
  med_peak_up/down, med_signed_up/down, med_cum_effort_up/down (None exactly
  when inputs were not evaluable; medians over the window waves per direction).
- `WaveGroups` frozen dataclass: `activity` (ActivityGroup) + `activity_reason`,
  `price_outcome` (SideGroup) + `price_outcome_reason`, `up_waning` /
  `down_waning` (TriState, independent — both may be TRUE) + reasons,
  `stats`, `.reasons` (deduplicated tuple of all NE reasons).
  Reason codes: `wave_module_disabled`, `range_not_mature`,
  `wave_min_completed_each_direction_not_met`,
  `wave_activity_rate_not_evaluable`, `wave_excursion_not_evaluable`,
  `waning_wave_count_below_3`.
- `compute_wave_groups(completed_waves, rng, params, current_bar) -> WaveGroups`
  — window = latest at most `wave_stats_max_completed` completed, non-quality-
  limited waves starting after `rng.origin_bar` whose defining closes
  (start_reference_close, extreme_close, end_close) all lie in [S-eps, R+eps].
  Requires `rng.age(current_bar) >= mature_range_age` and
  `>= wave_min_completed_each_direction` waves per direction, else all groups
  NOT_EVALUABLE with reason. A: med activity, k = wave_dominance_ratio
  (`>=` for the k-ratio). P: demand iff med_up(peak) >= k*med_down(peak) AND
  med_up(signed) > med_down(signed) (strict `>`), supply symmetric, else
  neutral. D: per direction with >= 3 window waves, latest peak <
  median of that direction's prior peaks => TRUE else FALSE; < 3 waves => NE.

## 8.3.2 B-sequence current-valid filter

- `SequenceRecord` frozen dataclass: sequence_id, event_code (EventCode),
  evidence_owner_id, confirmation_trigger_id, primitive_event_ids (tuple),
  reference_range_id, reference_range_version, confirmed_bar (stream index),
  available_at (datetime or numeric timestamp; only `<=` ordering needed),
  owner_status (HypothesisStatus), owner_current_valid (bool), evaluable=True.
- `SequenceFilterContext(current_bar, range_id, range_version, decision_time, params)`.
- `sequence_side(code) -> SideGroup | None` — side from the 8.3.2 code table
  (demand: SUPPORT_RESPONSE_SEQUENCE / SPRING_TEST_CONFIRMED /
  SPRING_SOS_SEQUENCE; supply: RESISTANCE_RESPONSE_SEQUENCE / UT_TEST_CONFIRMED
  / UT_SOW_SEQUENCE / UTAD_WEAKNESS_SEQUENCE); None for other codes.
- `filter_current_sequences(sequences, context) -> SequenceFilterResult` with
  `counted`, `history_confirmed` (owner reached confirmation: confirmed /
  invalidated / expired), `current_valid` (passes all current-validity
  conditions, pre-dedup), `exclusion_reasons` (sequence_id -> reason).
  Counting requires: same range id AND version; 0 <= current_bar -
  confirmed_bar < bias_sequence_recent_bars; available_at <= decision_time;
  owner CONFIRMED and currently valid; evaluable; side defined. Dedup runs
  AFTER validity filtering in deterministic (confirmed_bar, sequence_id)
  order — one vote per owner closed-loop, one vote per shared
  confirmation_trigger_id — so an invalidated alias never suppresses a
  still-valid vote. Reason codes: `range_mismatch`, `confirmed_bar_in_future`,
  `confirmed_bar_stale`, `not_yet_available`, `owner_not_confirmed`,
  `owner_not_currently_valid`, `not_evaluable`, `side_undefined`,
  `duplicate_owner_vote`, `duplicate_confirmation_trigger`.
- `evaluate_bias_group(counted) -> SideGroup` — one side with >= 1 vote and
  more votes than the other wins; equal counts (incl. 0-0) NEUTRAL. Window /
  module coverage failures are the caller's responsibility: pass
  `SideGroup.NOT_EVALUABLE` straight to `compute_direction_evidence`.

## 8.3.3 paths + dominance

- `EvidenceGroups` — activity, price_outcome, bias, up_waning, down_waning
  (the A / P / B / D values one evaluation used).
- `DirectionEvidence` frozen dataclass: `dominance` (Dominance),
  `dominance_paths` (tuple of TRUE PathName), `path_states`
  (Mapping[PathName, TriState], all 8), `evidence_groups`, `evaluation_reasons`.
- `compute_direction_evidence(wave_groups, b_group, counted_sequences, params)
  -> DirectionEvidence` — `wave_groups` may be None or contain NE groups.
  `wave_module_enabled=False` forces A/P/D to NE (reason
  `wave_module_disabled`). absorption/distribution_response additionally
  require a counted support-response/spring-test (resp.
  resistance-response/UT-test) sequence; when B itself is NE that presence
  flag is NE too (never a silent FALSE). demand_result = OR(demand paths)
  AND NOT(P=supply) AND NOT(B=supply); supply symmetric (8.3.3 AND/OR/NOT
  table via types.and_/or_/not_/tri_equal).
- `finalize_dominance(demand_result, supply_result) -> Dominance` — one TRUE
  side wins; both FALSE => NEUTRAL; no TRUE and >= 1 NE => NOT_EVALUABLE;
  both TRUE => raises `ConfigurationError`.

## 8.4 structure hypotheses

- `TriggerEvent` frozen dataclass: event_code, bar_index, primitive_event_ids,
  currently_valid (applies to LPS / LPSY only).
- `StructureHypotheses` frozen dataclass: phase_b_range,
  spring_supply_test_candidate, ut_demand_test_candidate,
  accumulation_candidate, distribution_candidate, reaccumulation_candidate,
  redistribution_candidate, larger_context (TrendState | None),
  dominance (original, kept separately), entry_dominance_demand /
  entry_dominance_supply (after duplicate-vote control),
  dedup_removed_sequence_ids, reasons.
- `structure_hypotheses(rng, wave_groups, counted_sequences,
  direction_evidence, trigger_events, params, current_bar, *,
  unfailed_spring_shakeout=0, unfailed_ut_test=0, larger_context_closes=None,
  larger_context_atr=None) -> StructureHypotheses`
  - phase_b_range: `rng.age(current_bar) >= mature_range_age`.
  - spring/ut candidates: count of unfailed hypotheses >= 1 (no dominance
    required).
  - accumulation: mature range + dominance DEMAND + a trigger within the last
    `bias_signal_recent_bars` bars (SOS, INTERNAL_JOC, or a currently-valid
    LPS). distribution mirror (SOW, INTERNAL_FTI, currently-valid LPSY,
    dominance SUPPLY).
  - Duplicate-vote control: when the entry's qualifying triggers share
    primitive_event_ids with counted B sequences, those sequences are
    removed, B and the dominance are recomputed and must still be DEMAND /
    SUPPLY — NOT_EVALUABLE never passes. The original dominance is preserved
    in `.dominance`.
  - Larger context: pass the same-period closes BEFORE range establishment
    (plus ATR); UP context relabels accumulation to reaccumulation_candidate,
    DOWN context relabels distribution to redistribution_candidate.
- `larger_context(closes, atr, params) -> TrendState` — SignedDelta over the
  last `larger_context_window+1` closes (N changes) normalized by ATR, `>=`
  larger_context_min_displacement_atr AND ER >= larger_context_min_er => UP;
  symmetric => DOWN; else NEUTRAL; insufficient closes or unavailable ATR =>
  UNKNOWN.

## Invariants

- Missing data never becomes a silent fallback: every NE group/path carries a
  reason string; medians are None when inputs are not evaluable.
- No randomness, no wall-clock: dedup order and outputs are fully
  deterministic (I-07).
- `evaluation_reasons` of DirectionEvidence contains the WaveGroups reasons
  plus `wave_module_disabled` / `wave_groups_not_evaluable` /
  `bias_group_not_evaluable` when applicable.
