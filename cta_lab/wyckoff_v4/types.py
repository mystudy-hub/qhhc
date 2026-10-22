"""Wyckoff V4 core types: tri-state logic and recognition enums.

Spec references:
- Section 2.2 (I-class invariants), especially I-06 (missing data is never
  silence) and I-07 (determinism).
- Section 2.3 (F-class conventions), especially F-19 (tri-state propagation;
  NOT_EVALUABLE never coerces to neutral).
- Section 8.3.3 (tri-state operator table): AND / OR / NOT / equality /
  inequality semantics implemented here.
- Section 6.8 (event code summary): the complete EventCode table.
- Section 7.1 (counting origin registration, F-12) for CountingOrigin.
- Section 7.5 (acceptance modes) for AcceptanceMode.

SPEC DECISIONS (literal-reading choices):
- TriState members are TRUE / FALSE / NOT_EVALUABLE; the spec's 8.3.3 table
  defines the operators, so there are no convenience coercions: any use of a
  raw bool where a TriState is required is a caller error, not converted here.
- TrendState is not enumerated in the spec sections read for this phase; the
  minimal set UP / DOWN / NEUTRAL / UNKNOWN is used (signed displacement
  decision per table 10.1 trend parameters, plus an unknown state when trend
  evidence is not evaluable). This can be revisited when the trend phase lands.
- EventCode members carry their table names as values (uppercase) so output
  codes are stable and independent of enum iteration order.
"""

from enum import Enum


class TriState(Enum):
    """Three-valued logic per F-19 and the 8.3.3 operator table."""

    TRUE = "TRUE"
    FALSE = "FALSE"
    NOT_EVALUABLE = "NOT_EVALUABLE"

    def __bool__(self) -> bool:
        raise TypeError("TriState requires explicit and_/or_/not_ or identity comparison")


def and_(*values: TriState) -> TriState:
    """8.3.3 AND: any FALSE => FALSE, else any NOT_EVALUABLE => NOT_EVALUABLE, else TRUE."""
    if not values:
        raise ValueError("and_ requires at least one TriState operand")
    for value in values:
        if not isinstance(value, TriState):
            raise TypeError(f"and_ operand is not a TriState: {value!r}")
    if TriState.FALSE in values:
        return TriState.FALSE
    if TriState.NOT_EVALUABLE in values:
        return TriState.NOT_EVALUABLE
    return TriState.TRUE


def or_(*values: TriState) -> TriState:
    """8.3.3 OR: any TRUE => TRUE, else any NOT_EVALUABLE => NOT_EVALUABLE, else FALSE."""
    if not values:
        raise ValueError("or_ requires at least one TriState operand")
    for value in values:
        if not isinstance(value, TriState):
            raise TypeError(f"or_ operand is not a TriState: {value!r}")
    if TriState.TRUE in values:
        return TriState.TRUE
    if TriState.NOT_EVALUABLE in values:
        return TriState.NOT_EVALUABLE
    return TriState.FALSE


def not_(value: TriState) -> TriState:
    """8.3.3 NOT: swaps TRUE/FALSE, keeps NOT_EVALUABLE."""
    if not isinstance(value, TriState):
        raise TypeError(f"not_ operand is not a TriState: {value!r}")
    if value is TriState.TRUE:
        return TriState.FALSE
    if value is TriState.FALSE:
        return TriState.TRUE
    return TriState.NOT_EVALUABLE


def tri_equal(left: TriState, right: TriState) -> TriState:
    """8.3.3 group equality: either side NOT_EVALUABLE => NOT_EVALUABLE; else compare."""
    for value in (left, right):
        if not isinstance(value, TriState):
            raise TypeError(f"tri_equal operand is not a TriState: {value!r}")
    if TriState.NOT_EVALUABLE in (left, right):
        return TriState.NOT_EVALUABLE
    return TriState.TRUE if left is right else TriState.FALSE


def tri_not_equal(left: TriState, right: TriState) -> TriState:
    """8.3.3 group inequality: either side NOT_EVALUABLE => NOT_EVALUABLE; else compare."""
    for value in (left, right):
        if not isinstance(value, TriState):
            raise TypeError(f"tri_not_equal operand is not a TriState: {value!r}")
    if TriState.NOT_EVALUABLE in (left, right):
        return TriState.NOT_EVALUABLE
    return TriState.TRUE if left is not right else TriState.FALSE


class MarketRegime(Enum):
    """Background market regime classification (table 10.1 context parameters)."""

    UPTREND = "uptrend"
    DOWNTREND = "downtrend"
    RANGE = "range"
    TRANSITION = "transition"
    UNKNOWN = "unknown"


class TrendState(Enum):
    """Trend classification; UNKNOWN when trend evidence is not evaluable."""

    UP = "up"
    DOWN = "down"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


class RangeState(Enum):
    """Range lifecycle states (section 5, F-07 append-only transitions)."""

    BUILDING = "building"
    ACTIVE = "active"
    EXIT_UP_CANDIDATE = "exit_up_candidate"
    EXIT_DOWN_CANDIDATE = "exit_down_candidate"
    EXIT_UP_ACCEPTED = "exit_up_accepted"
    EXIT_DOWN_ACCEPTED = "exit_down_accepted"
    RETIRED = "retired"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class HypothesisStatus(Enum):
    """Hypothesis lifecycle states (section 7)."""

    CANDIDATE = "candidate"
    CONFIRMED = "confirmed"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"


class CountingOrigin(Enum):
    """Counting origin registration for invalidation counters (F-12, section 7.1).

    PRE: counter starts strictly before the pattern/event bar.
    INCL: counter includes the event bar itself.
    POST: counter starts strictly after the event bar.
    """

    PRE = "pre"
    INCL = "incl"
    POST = "post"


class AcceptanceMode(Enum):
    """Breakout acceptance modes (sections 7.5 / 10.4)."""

    HOLD = "hold"
    PULLBACK = "pullback"
    LATE_HOLD = "late_hold"


class EventCode(Enum):
    """Complete event code table of section 6.8.

    Factual events, structural labels, sequences, structural points and
    data-status codes are all part of the single 6.8 table; no other event
    codes may be invented (I-06: unimplemented areas surface as
    not_implemented, not as silence).
    """

    SC_FEATURE = "SC_FEATURE"
    SC_CLUSTER_FEATURE = "SC_CLUSTER_FEATURE"
    BC_FEATURE = "BC_FEATURE"
    BC_CLUSTER_FEATURE = "BC_CLUSTER_FEATURE"
    EFFORT_RESULT_NARROW_BAR = "EFFORT_RESULT_NARROW_BAR"
    EFFORT_RESULT_LOW_PROGRESS = "EFFORT_RESULT_LOW_PROGRESS"
    SUPPORT_UNDERCUT = "SUPPORT_UNDERCUT"
    DEEP_UNDERCUT = "DEEP_UNDERCUT"
    SPRING_LOW_VOLUME = "SPRING_LOW_VOLUME"
    SPRING_HIGH_VOLUME = "SPRING_HIGH_VOLUME"
    SPRING_OTHER = "SPRING_OTHER"
    SHAKEOUT_RECLAIM = "SHAKEOUT_RECLAIM"
    UNDERCUT_RECLAIM_BREAKOUT = "UNDERCUT_RECLAIM_BREAKOUT"
    UNDERCUT_NOT_RECLAIMED = "UNDERCUT_NOT_RECLAIMED"
    RESISTANCE_OVERSHOOT = "RESISTANCE_OVERSHOOT"
    DEEP_OVERSHOOT = "DEEP_OVERSHOOT"
    UT_FEATURE = "UT_FEATURE"
    UPTHRUST_DEEP_REJECTION = "UPTHRUST_DEEP_REJECTION"
    UPTHRUST_REVERSAL_BREAKDOWN = "UPTHRUST_REVERSAL_BREAKDOWN"
    OVERSHOOT_NOT_REJECTED = "OVERSHOOT_NOT_REJECTED"
    BREAKOUT_UP = "BREAKOUT_UP"
    BREAKOUT_DOWN = "BREAKOUT_DOWN"
    FAILED_UP_BREAKOUT = "FAILED_UP_BREAKOUT"
    FAILED_DOWN_BREAKOUT = "FAILED_DOWN_BREAKOUT"
    BREAKOUT_HOLD_CONFIRMED = "BREAKOUT_HOLD_CONFIRMED"
    BREAKOUT_ACCEPTANCE_INVALIDATED = "BREAKOUT_ACCEPTANCE_INVALIDATED"
    SOS = "SOS"
    SOW = "SOW"
    INTERNAL_JOC = "INTERNAL_JOC"
    INTERNAL_FTI = "INTERNAL_FTI"
    STRONG_UP_BAR = "STRONG_UP_BAR"
    STRONG_DOWN_BAR = "STRONG_DOWN_BAR"
    ST_OBSERVED = "ST_OBSERVED"
    TEST_OBSERVED = "TEST_OBSERVED"
    UT_TEST_OBSERVED = "UT_TEST_OBSERVED"
    LPS_PULLBACK_OBSERVED = "LPS_PULLBACK_OBSERVED"
    LPSY_RALLY_OBSERVED = "LPSY_RALLY_OBSERVED"
    SC_STOPPING_SEQUENCE = "SC_STOPPING_SEQUENCE"
    BC_STOPPING_SEQUENCE = "BC_STOPPING_SEQUENCE"
    SUPPORT_RESPONSE_SEQUENCE = "SUPPORT_RESPONSE_SEQUENCE"
    RESISTANCE_RESPONSE_SEQUENCE = "RESISTANCE_RESPONSE_SEQUENCE"
    TEST_RESPONSE_CONFIRMED = "TEST_RESPONSE_CONFIRMED"
    SPRING_TEST_CONFIRMED = "SPRING_TEST_CONFIRMED"
    SPRING_SOS_SEQUENCE = "SPRING_SOS_SEQUENCE"
    UT_TEST_CONFIRMED = "UT_TEST_CONFIRMED"
    UT_SOW_SEQUENCE = "UT_SOW_SEQUENCE"
    UTAD_WEAKNESS_SEQUENCE = "UTAD_WEAKNESS_SEQUENCE"
    LPS = "LPS"
    LPSY = "LPSY"
    DATA_GAP = "DATA_GAP"
    INDEX_SOURCE_MISMATCH = "INDEX_SOURCE_MISMATCH"
    EX_POST_WEIGHTS = "EX_POST_WEIGHTS"
    CONSTITUENT_CHANGE = "CONSTITUENT_CHANGE"
    DELIVERY_SEASON = "DELIVERY_SEASON"
    WARMUP_INCOMPLETE = "WARMUP_INCOMPLETE"
