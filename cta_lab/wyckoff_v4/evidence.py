"""Direction evidence and structure hypotheses (spec 8.3 / 8.4, C-group 11.2).

Covers:
- 8.3.1 wave groups (A activity / P price outcome / D waning) with the
  ``wave_stats_max_completed`` window and per-direction medians.
- 8.3.2 current-valid B-sequence filter (range/version match, recency,
  availability, owner validity, owner / shared-trigger dedup).
- 8.3.3 the eight evidence paths and the tri-state dominance decision.
- 8.4 structure hypotheses with duplicate-vote control and the frozen
  larger-scale context relabeling.

SPEC DECISIONS (ambiguities resolved here):
1. Wave-window membership (8.3.1 "all closes inside [S-eps, R+eps]"): a
   completed wave counts when it starts after ``rng.origin_bar`` and its
   complete minimum/maximum close envelope lies within [S-eps, R+eps].
   Legacy constructed WaveRecords without that envelope use their three
   defining closes; engine-created records always carry the full envelope.
2. "Latest" wave of a direction (group D) = the wave in the window with the
   largest ``end_index``; "prior peaks" = the remaining waves of the same
   direction in the window.
3. Sequence side is derived from ``event_code`` via the 8.3.2 code table
   (demand: SUPPORT_RESPONSE_SEQUENCE / SPRING_TEST_CONFIRMED /
   SPRING_SOS_SEQUENCE; supply: RESISTANCE_RESPONSE_SEQUENCE /
   UT_TEST_CONFIRMED / UT_SOW_SEQUENCE / UTAD_WEAKNESS_SEQUENCE).
   Records with other codes are excluded with reason ``side_undefined``.
4. ``history_confirmed`` = sequences whose owner reached confirmation
   (owner status confirmed, invalidated or expired; failed sequences stay
   in history only); ``current_valid`` = sequences
   passing every 8.3.2 current-validity condition (range, recency,
   availability, owner, evaluable) but BEFORE dedup; ``counted`` =
   current_valid after owner / shared-trigger dedup. Dedup processes
   sequences in deterministic (confirmed_bar, sequence_id) order so an
   invalidated alias can never suppress a still-valid vote (invalid ones
   are filtered out before dedup runs).
5. Path names: the spec reuses ``boundary_supported`` on both sides; the
   flat 8-path state dict uses DEMAND_BOUNDARY_SUPPORTED and
   SUPPLY_BOUNDARY_SUPPORTED (all other names are verbatim spec names).
6. ``absorption_response`` / ``distribution_response`` require a counted
   support-response/spring-test (resp. resistance-response/UT-test)
   sequence. When the B group itself is NOT_EVALUABLE that presence flag is
   NOT_EVALUABLE too (never a silent FALSE), so NE propagates.
7. Duplicate-vote control (8.4) removes, per structure entry, the counted
   B sequences whose ``primitive_event_ids`` intersect the primitive ids of
   that entry's qualifying trigger events, recomputes B and the dominance
   used by the entry, and requires the recomputed dominance to be DEMAND /
   SUPPLY (NOT_EVALUABLE never passes). The original dominance is kept
   separately in the output.
8. Larger context (8.4): SignedDelta = (last - first) of the last
   ``larger_context_window+1`` closes BEFORE establishment, normalized by
   ATR, compared against ``larger_context_min_displacement_atr``; ER =
   |SignedDelta| / sum |close-to-close changes| over the same closes.
   Insufficient closes or an unavailable/non-positive ATR => UNKNOWN
   (context not evaluable, no relabeling).
9. ``spring_supply_test_candidate`` / ``ut_demand_test_candidate`` accept a
   count of unfailed (not invalidated) Spring/Shakeout resp. UT-test
   hypotheses; >= 1 means the candidate holds. Direction dominance is not
   required for these two entries.
10. Group comparisons follow the 8.3.3 table: a NOT_EVALUABLE group makes
    equality/inequality NOT_EVALUABLE on either side; dominance has a
    fourth NEUTRAL outcome distinct from NOT_EVALUABLE, so it is its own
    enum (``Dominance``), not a TriState.

Determinism (I-07): no randomness, no wall-clock reads; all times are
caller-supplied; dedup order is (confirmed_bar, sequence_id).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Mapping, Sequence

from cta_lab.wyckoff_v4.params import WyckoffV4Params
from cta_lab.wyckoff_v4.range import TradingRange
from cta_lab.wyckoff_v4.types import (
    EventCode,
    HypothesisStatus,
    TrendState,
    TriState,
    and_,
    not_,
    or_,
    tri_equal,
)
from cta_lab.wyckoff_v4.waves import WaveRecord, WaveStatus


class ConfigurationError(ValueError):
    """8.3.3 mutual-exclusion violation: both demand and supply results TRUE."""


class SideGroup(Enum):
    """P / B group value (8.3.1 price outcome, 8.3.2 bias)."""

    DEMAND = "demand"
    SUPPLY = "supply"
    NEUTRAL = "neutral"
    NOT_EVALUABLE = "not_evaluable"


class ActivityGroup(Enum):
    """A group value (8.3.1): activity direction only, never a verdict."""

    UP_MORE_ACTIVE = "up_more_active"
    DOWN_MORE_ACTIVE = "down_more_active"
    BALANCED = "balanced"
    NOT_EVALUABLE = "not_evaluable"


class Dominance(Enum):
    """Final 8.3.3 dominance (NEUTRAL is distinct from NOT_EVALUABLE)."""

    DEMAND = "demand"
    SUPPLY = "supply"
    NEUTRAL = "neutral"
    NOT_EVALUABLE = "not_evaluable"


class PathName(Enum):
    """The eight 8.3.3 paths (demand side first, then the supply mirror)."""

    DEMAND_PUSH = "demand_push"
    SUPPLY_EXHAUSTION = "supply_exhaustion"
    DEMAND_BOUNDARY_SUPPORTED = "demand_boundary_supported"
    ABSORPTION_RESPONSE = "absorption_response"
    SUPPLY_PUSH = "supply_push"
    DEMAND_EXHAUSTION = "demand_exhaustion"
    SUPPLY_BOUNDARY_SUPPORTED = "supply_boundary_supported"
    DISTRIBUTION_RESPONSE = "distribution_response"


DEMAND_SIDE_SEQUENCE_CODES = frozenset(
    {
        EventCode.SUPPORT_RESPONSE_SEQUENCE,
        EventCode.SPRING_TEST_CONFIRMED,
        EventCode.SPRING_SOS_SEQUENCE,
    }
)
SUPPLY_SIDE_SEQUENCE_CODES = frozenset(
    {
        EventCode.RESISTANCE_RESPONSE_SEQUENCE,
        EventCode.UT_TEST_CONFIRMED,
        EventCode.UT_SOW_SEQUENCE,
        EventCode.UTAD_WEAKNESS_SEQUENCE,
    }
)


# ---------------------------------------------------------------------------
# tri-state helpers for group-valued comparisons (8.3.3 table)
# ---------------------------------------------------------------------------

def _is_not_evaluable(value: Enum) -> bool:
    return value.name == "NOT_EVALUABLE"


def group_equal(value: Enum, expected: Enum) -> TriState:
    """Group equality with NE propagation: an NE side makes the result NE."""
    if _is_not_evaluable(value) or _is_not_evaluable(expected):
        return TriState.NOT_EVALUABLE
    return TriState.TRUE if value == expected else TriState.FALSE


def group_not_equal(value: Enum, expected: Enum) -> TriState:
    if _is_not_evaluable(value) or _is_not_evaluable(expected):
        return TriState.NOT_EVALUABLE
    return TriState.FALSE if value == expected else TriState.TRUE


def _member_of(value: Enum, options: tuple[Enum, ...]) -> TriState:
    """`value in options` under 8.3.3 group-compare semantics."""
    return or_(*(group_equal(value, option) for option in options))


# ---------------------------------------------------------------------------
# 8.3.1 wave groups
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WaveGroupStats:
    """Medians and counts of the 8.3.1 wave window (diagnostics, no votes).

    Medians are None exactly when the corresponding inputs were not
    evaluable (F-19). CumEffort medians are stored alongside the activity
    medians per 8.3.1 (activity rate is what the paths use).
    """

    n_up: int
    n_down: int
    window_wave_ids: tuple[str, ...]
    med_activity_up: float | None
    med_activity_down: float | None
    med_peak_up: float | None
    med_peak_down: float | None
    med_signed_up: float | None
    med_signed_down: float | None
    med_cum_effort_up: float | None
    med_cum_effort_down: float | None


@dataclass(frozen=True)
class WaveGroups:
    """8.3.1 groups A / P / D for one evaluated range and bar."""

    activity: ActivityGroup
    activity_reason: str | None
    price_outcome: SideGroup
    price_outcome_reason: str | None
    up_waning: TriState
    up_waning_reason: str | None
    down_waning: TriState
    down_waning_reason: str | None
    stats: WaveGroupStats

    @property
    def reasons(self) -> tuple[str, ...]:
        unique: list[str] = []
        for reason in (
            self.activity_reason,
            self.price_outcome_reason,
            self.up_waning_reason,
            self.down_waning_reason,
        ):
            if reason is not None and reason not in unique:
                unique.append(reason)
        return tuple(unique)


def _wave_in_band(wave: WaveRecord, rng: TradingRange) -> bool:
    lo = rng.S - rng.epsilon
    hi = rng.R + rng.epsilon
    closes = (
        wave.start_reference_close,
        wave.extreme_close,
        wave.end_close,
        wave.minimum_close if wave.minimum_close is not None else wave.end_close,
        wave.maximum_close if wave.maximum_close is not None else wave.extreme_close,
    )
    return all(close is not None and lo <= close <= hi for close in closes)


def _ne_wave_groups(
    window_wave_ids: tuple[str, ...],
    n_up: int,
    n_down: int,
    reason: str,
) -> WaveGroups:
    stats = WaveGroupStats(
        n_up=n_up,
        n_down=n_down,
        window_wave_ids=window_wave_ids,
        med_activity_up=None,
        med_activity_down=None,
        med_peak_up=None,
        med_peak_down=None,
        med_signed_up=None,
        med_signed_down=None,
        med_cum_effort_up=None,
        med_cum_effort_down=None,
    )
    return WaveGroups(
        activity=ActivityGroup.NOT_EVALUABLE,
        activity_reason=reason,
        price_outcome=SideGroup.NOT_EVALUABLE,
        price_outcome_reason=reason,
        up_waning=TriState.NOT_EVALUABLE,
        up_waning_reason=reason,
        down_waning=TriState.NOT_EVALUABLE,
        down_waning_reason=reason,
        stats=stats,
    )


def compute_wave_groups(
    completed_waves: Sequence[WaveRecord],
    rng: TradingRange,
    params: WyckoffV4Params,
    current_bar: int,
    *, decision_time: datetime | None = None,
) -> WaveGroups:
    """Compute 8.3.1 wave groups A / P / D for ``rng`` at ``current_bar``.

    Window: the latest at most ``wave_stats_max_completed`` completed,
    non-quality-limited waves starting after ``rng.origin_bar`` whose
    defining closes lie in [S-eps, R+eps]. Requires range age >=
    ``mature_range_age`` and at least ``wave_min_completed_each_direction``
    waves per direction, else every group is NOT_EVALUABLE with a reason.
    """
    if not params.wave_module_enabled:
        return _ne_wave_groups((), 0, 0, "wave_module_disabled")
    if rng.age(current_bar) < params.mature_range_age:
        return _ne_wave_groups((), 0, 0, "range_not_mature")

    ordered = sorted(
        (
            w
            for w in completed_waves
            if w.status == WaveStatus.COMPLETED
            and not w.quality_limited
            and w.start_index > rng.origin_bar
            and (w.confirmed_at_index is None or w.confirmed_at_index <= current_bar)
            and (decision_time is None or w.available_at is not None and w.available_at <= decision_time)
            and _wave_in_band(w, rng)
        ),
        key=lambda w: (w.end_index, w.wave_id),
    )
    window = ordered[-params.wave_stats_max_completed :]
    ups = [w for w in window if w.direction == 1]
    downs = [w for w in window if w.direction == -1]
    if len(ups) < params.wave_min_completed_each_direction or (
        len(downs) < params.wave_min_completed_each_direction
    ):
        return _ne_wave_groups(
            tuple(w.wave_id for w in window),
            len(ups),
            len(downs),
            "wave_min_completed_each_direction_not_met",
        )

    window_wave_ids = tuple(w.wave_id for w in window)
    k = params.wave_dominance_ratio

    # Group A: activity (median activity rate per direction).
    activity_values_up = [w.wave_activity_rate for w in ups]
    activity_values_down = [w.wave_activity_rate for w in downs]
    if not all(f.is_available for f in activity_values_up + activity_values_down):
        activity = ActivityGroup.NOT_EVALUABLE
        activity_reason: str | None = "wave_activity_rate_not_evaluable"
        med_activity_up = None
        med_activity_down = None
    else:
        med_activity_up = statistics.median(f.value for f in activity_values_up)
        med_activity_down = statistics.median(
            f.value for f in activity_values_down
        )
        if med_activity_up >= k * med_activity_down:
            activity = ActivityGroup.UP_MORE_ACTIVE
        elif med_activity_down >= k * med_activity_up:
            activity = ActivityGroup.DOWN_MORE_ACTIVE
        else:
            activity = ActivityGroup.BALANCED
        activity_reason = None

    # Group P: price outcome (median peak excursion + signed end displacement).
    peaks_up = [w.peak_excursion for w in ups]
    peaks_down = [w.peak_excursion for w in downs]
    signed_up = [w.signed_end_displacement for w in ups]
    signed_down = [w.signed_end_displacement for w in downs]
    price_reason: str | None = None
    if any(v is None for v in peaks_up + peaks_down + signed_up + signed_down):
        price_outcome = SideGroup.NOT_EVALUABLE
        price_reason = "wave_excursion_not_evaluable"
        med_peak_up = None
        med_peak_down = None
        med_signed_up = None
        med_signed_down = None
    else:
        med_peak_up = statistics.median(peaks_up)
        med_peak_down = statistics.median(peaks_down)
        med_signed_up = statistics.median(signed_up)
        med_signed_down = statistics.median(signed_down)
        if med_peak_up >= k * med_peak_down and med_signed_up > med_signed_down:
            price_outcome = SideGroup.DEMAND
        elif (
            med_peak_down >= k * med_peak_up and med_signed_down > med_signed_up
        ):
            price_outcome = SideGroup.SUPPLY
        else:
            price_outcome = SideGroup.NEUTRAL

    # Group D: waning per direction (independent; both may be TRUE).
    def waning(waves: Sequence[WaveRecord]) -> tuple[TriState, str | None]:
        if len(waves) < 3:
            return TriState.NOT_EVALUABLE, "waning_wave_count_below_3"
        latest = max(waves, key=lambda w: (w.end_index, w.wave_id))
        prior = [w for w in waves if w is not latest]
        peaks = [w.peak_excursion for w in prior]
        if latest.peak_excursion is None or any(p is None for p in peaks):
            return TriState.NOT_EVALUABLE, "wave_excursion_not_evaluable"
        prior_median = statistics.median(peaks)
        if latest.peak_excursion < prior_median:
            return TriState.TRUE, None
        return TriState.FALSE, None

    up_waning, up_waning_reason = waning(ups)
    down_waning, down_waning_reason = waning(downs)

    cum_up = [w.wave_cum_effort for w in ups]
    cum_down = [w.wave_cum_effort for w in downs]
    med_cum_effort_up = (
        statistics.median(f.value for f in cum_up)
        if all(f.is_available for f in cum_up)
        else None
    )
    med_cum_effort_down = (
        statistics.median(f.value for f in cum_down)
        if all(f.is_available for f in cum_down)
        else None
    )

    return WaveGroups(
        activity=activity,
        activity_reason=activity_reason,
        price_outcome=price_outcome,
        price_outcome_reason=price_reason,
        up_waning=up_waning,
        up_waning_reason=up_waning_reason,
        down_waning=down_waning,
        down_waning_reason=down_waning_reason,
        stats=WaveGroupStats(
            n_up=len(ups),
            n_down=len(downs),
            window_wave_ids=window_wave_ids,
            med_activity_up=med_activity_up,
            med_activity_down=med_activity_down,
            med_peak_up=med_peak_up,
            med_peak_down=med_peak_down,
            med_signed_up=med_signed_up,
            med_signed_down=med_signed_down,
            med_cum_effort_up=med_cum_effort_up,
            med_cum_effort_down=med_cum_effort_down,
        ),
    )


# ---------------------------------------------------------------------------
# 8.3.2 B-sequence current-valid filter
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SequenceRecord:
    """A confirmed B-group sequence as seen by the current-valid filter.

    ``available_at`` / ``decision_time`` may be datetimes or numeric
    timestamps; only ``<=`` ordering is required.
    """

    sequence_id: str
    event_code: EventCode
    evidence_owner_id: str
    confirmation_trigger_id: str
    primitive_event_ids: tuple[str, ...]
    reference_range_id: str
    reference_range_version: int
    confirmed_bar: int
    available_at: datetime | float
    owner_status: HypothesisStatus
    owner_current_valid: bool
    evaluable: bool = True


@dataclass(frozen=True)
class SequenceFilterContext:
    """Evaluation point for the 8.3.2 current-valid filter."""

    current_bar: int
    range_id: str
    range_version: int
    decision_time: datetime | float
    params: WyckoffV4Params
    invalid_bar_indices: tuple[int, ...] = ()


@dataclass(frozen=True)
class SequenceFilterResult:
    """8.3.2 filter output: counted votes plus the audit trail."""

    counted: tuple[SequenceRecord, ...]
    history_confirmed: tuple[SequenceRecord, ...]
    current_valid: tuple[SequenceRecord, ...]
    exclusion_reasons: Mapping[str, str] = field(default_factory=dict)


def sequence_side(code: EventCode) -> SideGroup | None:
    """Demand/supply side of a B-group sequence code (8.3.2), else None."""
    if code in DEMAND_SIDE_SEQUENCE_CODES:
        return SideGroup.DEMAND
    if code in SUPPLY_SIDE_SEQUENCE_CODES:
        return SideGroup.SUPPLY
    return None


def filter_current_sequences(
    sequences: Sequence[SequenceRecord],
    context: SequenceFilterContext,
) -> SequenceFilterResult:
    """Filter 8.3.2 B sequences down to currently-valid, deduplicated votes.

    Counting requires: same range id and version; 0 <= current_bar -
    confirmed_bar < bias_sequence_recent_bars; available_at <= decision_time;
    owner CONFIRMED and currently valid; evaluable. Dedup afterwards (one
    vote per owner closed-loop, one vote per shared confirmation trigger),
    so an invalidated alias never suppresses another still-valid vote.
    """
    recent = context.params.bias_sequence_recent_bars
    # History = every sequence whose owner reached confirmation (confirmed,
    # later invalidated or expired owners stay in history, 8.3.2).
    reached_confirmation = (
        HypothesisStatus.CONFIRMED,
        HypothesisStatus.INVALIDATED,
        HypothesisStatus.EXPIRED,
    )
    history_confirmed = tuple(
        s for s in sequences if s.owner_status in reached_confirmation
    )
    exclusion_reasons: dict[str, str] = {}
    current_valid: list[SequenceRecord] = []

    for seq in sequences:
        if (
            seq.reference_range_id != context.range_id
            or seq.reference_range_version != context.range_version
        ):
            exclusion_reasons[seq.sequence_id] = "range_mismatch"
        elif context.current_bar < seq.confirmed_bar:
            exclusion_reasons[seq.sequence_id] = "confirmed_bar_in_future"
        elif context.current_bar - seq.confirmed_bar - sum(seq.confirmed_bar < i <= context.current_bar for i in context.invalid_bar_indices) >= recent:
            exclusion_reasons[seq.sequence_id] = "confirmed_bar_stale"
        elif seq.available_at > context.decision_time:
            exclusion_reasons[seq.sequence_id] = "not_yet_available"
        elif seq.owner_status != HypothesisStatus.CONFIRMED:
            exclusion_reasons[seq.sequence_id] = "owner_not_confirmed"
        elif not seq.owner_current_valid:
            exclusion_reasons[seq.sequence_id] = "owner_not_currently_valid"
        elif not seq.evaluable:
            exclusion_reasons[seq.sequence_id] = "not_evaluable"
        elif sequence_side(seq.event_code) is None:
            exclusion_reasons[seq.sequence_id] = "side_undefined"
        else:
            current_valid.append(seq)

    counted: list[SequenceRecord] = []
    seen_owners: set[str] = set()
    seen_triggers: set[str] = set()
    for seq in sorted(current_valid, key=lambda s: (s.confirmed_bar, s.sequence_id)):
        if seq.evidence_owner_id in seen_owners:
            exclusion_reasons[seq.sequence_id] = "duplicate_owner_vote"
        elif seq.confirmation_trigger_id in seen_triggers:
            exclusion_reasons[seq.sequence_id] = "duplicate_confirmation_trigger"
        else:
            counted.append(seq)
            seen_owners.add(seq.evidence_owner_id)
            seen_triggers.add(seq.confirmation_trigger_id)

    return SequenceFilterResult(
        counted=tuple(counted),
        history_confirmed=history_confirmed,
        current_valid=tuple(current_valid),
        exclusion_reasons=exclusion_reasons,
    )


def evaluate_bias_group(counted: Sequence[SequenceRecord]) -> SideGroup:
    """8.3.2 B group value from counted (deduplicated) sequences.

    One side with >= 1 vote and more votes than the other side wins; equal
    counts (including 0-0) are NEUTRAL. Window/module coverage failures are
    the caller's responsibility (pass SideGroup.NOT_EVALUABLE instead).
    """
    demand = sum(
        1 for s in counted if sequence_side(s.event_code) == SideGroup.DEMAND
    )
    supply = sum(
        1 for s in counted if sequence_side(s.event_code) == SideGroup.SUPPLY
    )
    if demand >= 1 and demand > supply:
        return SideGroup.DEMAND
    if supply >= 1 and supply > demand:
        return SideGroup.SUPPLY
    return SideGroup.NEUTRAL


# ---------------------------------------------------------------------------
# 8.3.3 paths and dominance
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EvidenceGroups:
    """The A / P / B / D group values an evidence evaluation used."""

    activity: ActivityGroup
    price_outcome: SideGroup
    bias: SideGroup
    up_waning: TriState
    down_waning: TriState


@dataclass(frozen=True)
class DirectionEvidence:
    """8.3.3 output: dominance, the TRUE paths, all 8 path states, groups."""

    dominance: Dominance
    dominance_paths: tuple[PathName, ...]
    path_states: Mapping[PathName, TriState]
    evidence_groups: EvidenceGroups
    evaluation_reasons: tuple[str, ...]


def finalize_dominance(demand_result: TriState, supply_result: TriState) -> Dominance:
    """Final 8.3.3 dominance from the two side results.

    One TRUE side wins; both FALSE => NEUTRAL; no TRUE with at least one
    NE => NOT_EVALUABLE; both TRUE violates mutual exclusion and raises
    ConfigurationError.
    """
    if demand_result is TriState.TRUE and supply_result is TriState.TRUE:
        raise ConfigurationError(
            "mutual exclusion violated: demand_result and supply_result "
            "are both TRUE"
        )
    if demand_result is TriState.TRUE:
        return Dominance.DEMAND
    if supply_result is TriState.TRUE:
        return Dominance.SUPPLY
    if demand_result is TriState.FALSE and supply_result is TriState.FALSE:
        return Dominance.NEUTRAL
    return Dominance.NOT_EVALUABLE


def compute_direction_evidence(
    wave_groups: WaveGroups | None,
    b_group: SideGroup,
    counted_sequences: Sequence[SequenceRecord],
    params: WyckoffV4Params,
) -> DirectionEvidence:
    """Evaluate the eight 8.3.3 paths and the final tri-state dominance.

    ``wave_groups`` may be None or contain NOT_EVALUABLE groups (history
    insufficient, module off); ``b_group`` may be NOT_EVALUABLE when the
    lookback window or the required detection modules are not covered.
    """
    reasons: list[str] = []
    if not params.wave_module_enabled:
        wave_groups = None
        reasons.append("wave_module_disabled")
    if wave_groups is None:
        activity = ActivityGroup.NOT_EVALUABLE
        price_outcome = SideGroup.NOT_EVALUABLE
        up_waning = TriState.NOT_EVALUABLE
        down_waning = TriState.NOT_EVALUABLE
        reasons.append("wave_groups_not_evaluable")
    else:
        activity = wave_groups.activity
        price_outcome = wave_groups.price_outcome
        up_waning = wave_groups.up_waning
        down_waning = wave_groups.down_waning
        reasons.extend(wave_groups.reasons)
    if b_group is SideGroup.NOT_EVALUABLE:
        reasons.append("bias_group_not_evaluable")

    support_codes = (EventCode.SUPPORT_RESPONSE_SEQUENCE, EventCode.SPRING_TEST_CONFIRMED)
    resistance_codes = (
        EventCode.RESISTANCE_RESPONSE_SEQUENCE,
        EventCode.UT_TEST_CONFIRMED,
    )

    def response_present(codes: tuple[EventCode, ...]) -> TriState:
        if b_group is SideGroup.NOT_EVALUABLE:
            return TriState.NOT_EVALUABLE
        return (
            TriState.TRUE
            if any(s.event_code in codes for s in counted_sequences)
            else TriState.FALSE
        )

    a = activity
    p = price_outcome
    b = b_group

    path_states: dict[PathName, TriState] = {
        PathName.DEMAND_PUSH: and_(
            group_equal(p, SideGroup.DEMAND),
            group_equal(a, ActivityGroup.UP_MORE_ACTIVE),
        ),
        PathName.SUPPLY_EXHAUSTION: and_(
            group_equal(p, SideGroup.DEMAND),
            group_not_equal(a, ActivityGroup.UP_MORE_ACTIVE),
            tri_equal(down_waning, TriState.TRUE),
        ),
        PathName.DEMAND_BOUNDARY_SUPPORTED: and_(
            group_equal(p, SideGroup.DEMAND),
            group_equal(b, SideGroup.DEMAND),
        ),
        PathName.ABSORPTION_RESPONSE: and_(
            group_equal(a, ActivityGroup.DOWN_MORE_ACTIVE),
            _member_of(p, (SideGroup.DEMAND, SideGroup.NEUTRAL)),
            group_equal(b, SideGroup.DEMAND),
            response_present(support_codes),
        ),
        PathName.SUPPLY_PUSH: and_(
            group_equal(p, SideGroup.SUPPLY),
            group_equal(a, ActivityGroup.DOWN_MORE_ACTIVE),
        ),
        PathName.DEMAND_EXHAUSTION: and_(
            group_equal(p, SideGroup.SUPPLY),
            group_not_equal(a, ActivityGroup.DOWN_MORE_ACTIVE),
            tri_equal(up_waning, TriState.TRUE),
        ),
        PathName.SUPPLY_BOUNDARY_SUPPORTED: and_(
            group_equal(p, SideGroup.SUPPLY),
            group_equal(b, SideGroup.SUPPLY),
        ),
        PathName.DISTRIBUTION_RESPONSE: and_(
            group_equal(a, ActivityGroup.UP_MORE_ACTIVE),
            _member_of(p, (SideGroup.SUPPLY, SideGroup.NEUTRAL)),
            group_equal(b, SideGroup.SUPPLY),
            response_present(resistance_codes),
        ),
    }

    demand_result = and_(
        or_(
            path_states[PathName.DEMAND_PUSH],
            path_states[PathName.SUPPLY_EXHAUSTION],
            path_states[PathName.DEMAND_BOUNDARY_SUPPORTED],
            path_states[PathName.ABSORPTION_RESPONSE],
        ),
        not_(group_equal(p, SideGroup.SUPPLY)),
        not_(group_equal(b, SideGroup.SUPPLY)),
    )
    supply_result = and_(
        or_(
            path_states[PathName.SUPPLY_PUSH],
            path_states[PathName.DEMAND_EXHAUSTION],
            path_states[PathName.SUPPLY_BOUNDARY_SUPPORTED],
            path_states[PathName.DISTRIBUTION_RESPONSE],
        ),
        not_(group_equal(p, SideGroup.DEMAND)),
        not_(group_equal(b, SideGroup.DEMAND)),
    )

    dominance = finalize_dominance(demand_result, supply_result)
    dominance_paths = tuple(
        name for name, state in path_states.items() if state is TriState.TRUE
    )

    return DirectionEvidence(
        dominance=dominance,
        dominance_paths=dominance_paths,
        path_states=path_states,
        evidence_groups=EvidenceGroups(
            activity=activity,
            price_outcome=price_outcome,
            bias=b_group,
            up_waning=up_waning,
            down_waning=down_waning,
        ),
        evaluation_reasons=tuple(reasons),
    )


# ---------------------------------------------------------------------------
# 8.4 structure hypotheses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TriggerEvent:
    """A trigger event feeding the 8.4 structure entries.

    ``currently_valid`` applies to LPS / LPSY (currently valid hold points);
    SOS / INTERNAL_JOC / SOW / INTERNAL_FTI do not require it.
    """

    event_code: EventCode
    bar_index: int
    primitive_event_ids: tuple[str, ...] = ()
    currently_valid: bool = True


@dataclass(frozen=True)
class StructureHypotheses:
    """8.4 structure hypothesis outputs for one range and bar."""

    phase_b_range: bool
    spring_supply_test_candidate: bool
    ut_demand_test_candidate: bool
    accumulation_candidate: bool
    distribution_candidate: bool
    reaccumulation_candidate: bool
    redistribution_candidate: bool
    larger_context: TrendState | None
    dominance: Dominance
    entry_dominance_demand: Dominance
    entry_dominance_supply: Dominance
    dedup_removed_sequence_ids: tuple[str, ...]
    reasons: tuple[str, ...]


def larger_context(
    closes: Sequence[float],
    atr: float | None,
    params: WyckoffV4Params,
) -> TrendState:
    """Frozen larger-scale context (8.4) from closes BEFORE establishment.

    UP when SignedDelta >= larger_context_min_displacement_atr (in ATR
    units) and ER >= larger_context_min_er; DOWN symmetric; otherwise
    NEUTRAL. UNKNOWN when the closes or ATR are not evaluable.
    """
    window = params.larger_context_window
    if atr is None or atr <= 0 or len(closes) < window + 1 or window < 2:
        return TrendState.UNKNOWN
    data = list(closes[-(window + 1):])
    delta = data[-1] - data[0]
    path = sum(abs(data[i + 1] - data[i]) for i in range(len(data) - 1))
    er = abs(delta) / path if path > 0 else 0.0
    min_disp = params.larger_context_min_displacement_atr * atr
    if delta >= min_disp and er >= params.larger_context_min_er:
        return TrendState.UP
    if delta <= -min_disp and er >= params.larger_context_min_er:
        return TrendState.DOWN
    return TrendState.NEUTRAL


_DEMAND_TRIGGER_CODES = (EventCode.SOS, EventCode.INTERNAL_JOC, EventCode.LPS)
_SUPPLY_TRIGGER_CODES = (EventCode.SOW, EventCode.INTERNAL_FTI, EventCode.LPSY)


def _qualifying_triggers(
    trigger_events: Sequence[TriggerEvent],
    codes: tuple[EventCode, ...],
    current_bar: int,
    params: WyckoffV4Params,
) -> list[TriggerEvent]:
    """Triggers within the last bias_signal_recent_bars bars for one side."""
    qualifying: list[TriggerEvent] = []
    for event in trigger_events:
        age = current_bar - event.bar_index
        if not 0 <= age < params.bias_signal_recent_bars:
            continue
        if event.event_code not in codes:
            continue
        if event.event_code in (EventCode.LPS, EventCode.LPSY) and (
            not event.currently_valid
        ):
            continue
        qualifying.append(event)
    return qualifying


def _dedup_controlled_dominance(
    wave_groups: WaveGroups | None,
    counted_sequences: Sequence[SequenceRecord],
    triggers: Sequence[TriggerEvent],
    original_dominance: Dominance,
    params: WyckoffV4Params,
) -> tuple[Dominance, tuple[str, ...]]:
    """Recompute dominance without B sequences sharing facts with triggers.

    When trigger events share primitive_event_ids with counted B sequences,
    those sequences are removed, B and the dominance are recomputed, and the
    recomputed dominance must be TRUE for the entry to pass (NE never
    passes). Without shared facts the original dominance stands.
    """
    shared_ids = {
        fact for trigger in triggers for fact in trigger.primitive_event_ids
    }
    if not shared_ids:
        return original_dominance, ()
    removed = tuple(
        s
        for s in counted_sequences
        if shared_ids.intersection(s.primitive_event_ids)
    )
    if not removed:
        return original_dominance, ()
    remaining = tuple(
        s for s in counted_sequences if not shared_ids.intersection(s.primitive_event_ids)
    )
    b_group = evaluate_bias_group(remaining)
    evidence = compute_direction_evidence(wave_groups, b_group, remaining, params)
    return evidence.dominance, tuple(s.sequence_id for s in removed)


def structure_hypotheses(
    rng: TradingRange,
    wave_groups: WaveGroups | None,
    counted_sequences: Sequence[SequenceRecord],
    direction_evidence: DirectionEvidence,
    trigger_events: Sequence[TriggerEvent],
    params: WyckoffV4Params,
    current_bar: int,
    *,
    unfailed_spring_shakeout: int = 0,
    unfailed_ut_test: int = 0,
    larger_context_closes: Sequence[float] | None = None,
    larger_context_atr: float | None = None,
) -> StructureHypotheses:
    """Evaluate the 8.4 structure hypotheses for ``rng`` at ``current_bar``.

    ``direction_evidence`` is the current 8.3.3 result (its dominance is
    kept in the output unchanged); ``counted_sequences`` are the counted B
    sequences it was built from. ``unfailed_spring_shakeout`` /
    ``unfailed_ut_test`` count unfailed hypotheses; ``larger_context_closes``
    (with ``larger_context_atr``) are the same-period closes before range
    establishment used to freeze the larger-scale context.
    """
    reasons: list[str] = []
    mature = rng.age(current_bar) >= params.mature_range_age
    if not mature:
        reasons.append("range_not_mature")

    demand_triggers = _qualifying_triggers(
        trigger_events, _DEMAND_TRIGGER_CODES, current_bar, params
    )
    supply_triggers = _qualifying_triggers(
        trigger_events, _SUPPLY_TRIGGER_CODES, current_bar, params
    )

    entry_dominance_demand, removed_demand = _dedup_controlled_dominance(
        wave_groups,
        counted_sequences,
        demand_triggers,
        direction_evidence.dominance,
        params,
    )
    entry_dominance_supply, removed_supply = _dedup_controlled_dominance(
        wave_groups,
        counted_sequences,
        supply_triggers,
        direction_evidence.dominance,
        params,
    )

    accumulation_candidate = (
        mature
        and bool(demand_triggers)
        and entry_dominance_demand is Dominance.DEMAND
    )
    distribution_candidate = (
        mature
        and bool(supply_triggers)
        and entry_dominance_supply is Dominance.SUPPLY
    )
    if demand_triggers and not accumulation_candidate:
        reasons.append("accumulation_entry_not_met")
    if supply_triggers and not distribution_candidate:
        reasons.append("distribution_entry_not_met")

    context: TrendState | None = None
    reaccumulation = False
    redistribution = False
    if larger_context_closes is not None:
        context = larger_context(larger_context_closes, larger_context_atr, params)
        reaccumulation = accumulation_candidate and context is TrendState.UP
        redistribution = distribution_candidate and context is TrendState.DOWN

    return StructureHypotheses(
        phase_b_range=mature,
        spring_supply_test_candidate=unfailed_spring_shakeout >= 1,
        ut_demand_test_candidate=unfailed_ut_test >= 1,
        accumulation_candidate=accumulation_candidate,
        distribution_candidate=distribution_candidate,
        reaccumulation_candidate=reaccumulation,
        redistribution_candidate=redistribution,
        larger_context=context,
        dominance=direction_evidence.dominance,
        entry_dominance_demand=entry_dominance_demand,
        entry_dominance_supply=entry_dominance_supply,
        dedup_removed_sequence_ids=removed_demand + removed_supply,
        reasons=tuple(reasons),
    )
