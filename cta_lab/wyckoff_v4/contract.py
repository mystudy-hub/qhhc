"""Wyckoff V4 input contract: series-level declarations, bar records,
eligibility checks and fixed-point price handling.

Spec references:
- Section 4.1 (index input contract): series-level and bar-level fields.
- Section 4.2 (input eligibility): index_source_mismatch rejection for
  main-continuous / back-adjusted / synthetic series without weight
  provenance, missing declared index returns a missing result with no
  auto-substitution, per-constituent weighted H/L ohlc_method rejected,
  ex_post_weights rejected as realtime input, basic per-bar sanity.
- Section 4.3 (price precision): fixed-point integer tick representation at
  price_resolution, exact divisibility, confirm_step computation.
- Section 10.6: price_resolution is positive.
- Section 2.2: I-06 (no silent fallbacks) and I-12 (weighted-index source
  with weights knowable at bar computation time).

SPEC DECISIONS (literal-reading choices):
- Non-weighted source kinds are matched on the exact declared
  index_source values 'main_continuous', 'back_adjusted', 'panama',
  'synthetic'. An undeclared (empty/None) index_source, or a missing
  weight_method / weight_asof_rule / constituent_universe_definition, is
  treated as no weight provenance => index_source_mismatch.
- ohlc_method must be one of the two spec-named synchronous methods
  ('vendor_published', 'synchronous_path'); anything else (including
  per-constituent weighted H/L) is rejected as ohlc_method_invalid. Unknown
  values are never defaulted to a known method (4.1: unknown weight or
  constituent meaning cannot be assumed known).
- Section 4.2 gives explicit reason names only for 'index_source_mismatch'
  and 'ex_post_weights'; the remaining reasons here
  ('ohlc_method_invalid', 'declared_index_missing',
  'data_profile_unsupported') are engineering names for the spec's
  reject/missing rows and are documented as such.
- A declared-but-missing index yields status MISSING with reason
  'declared_index_missing'. The caller must surface the missing entry; this
  module never substitutes another series (4.2, I-12).
- Weight timing is checked per bar: if the weights used for a bar were
  taken as-of a time later than the bar's computation time (received_at if
  present, else bar_end), the bar is ex_post_weights and rejected as a
  realtime input (it may only be used for ex-post description).
- price_resolution <= 0 is a structural contract error and raises
  ValueError naming the field (10.6 positivity, reject not correct).
- to_ticks uses exact decimal division of the price by price_resolution;
  any non-integral result is a ContractViolation (4.3: no self-rounding).
"""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum
import math


class ContractViolation(ValueError):
    """Raised when a price or contract field violates the section 4.3 rules."""


class EligibilityStatus(Enum):
    """Series-level eligibility outcome (4.2)."""

    ELIGIBLE = "eligible"
    REJECTED = "rejected"
    MISSING = "missing"


class EligibilityReason(Enum):
    """Reason codes for eligibility outcomes; values are the spec-named codes
    where the spec names one (index_source_mismatch, ex_post_weights)."""

    INDEX_SOURCE_MISMATCH = "index_source_mismatch"
    OHLC_METHOD_INVALID = "ohlc_method_invalid"
    EX_POST_WEIGHTS = "ex_post_weights"
    DECLARED_INDEX_MISSING = "declared_index_missing"
    DATA_PROFILE_UNSUPPORTED = "data_profile_unsupported"
    DECLARATION_INCOMPLETE = "declaration_incomplete"


# Declared index_source values that are not weighted indices (4.2 row 2:
# main-continuous, back-adjusted / rolled, de-gapped or synthetic series
# carry no constituents and no weights, even when named *_index).
NON_WEIGHTED_INDEX_SOURCES: frozenset[str] = frozenset(
    {
        "main_continuous",
        "back_adjusted",
        "panama",
        "synthetic",
    }
)

# ohlc_method values that keep synchronous extremes (4.1/4.2). Per-constituent
# weighted H/L and any unknown method are rejected.
ALLOWED_OHLC_METHODS: frozenset[str] = frozenset(
    {
        "vendor_published",
        "synchronous_path",
    }
)

DATA_PROFILE_WEIGHTED_INDEX = "weighted_index"


@dataclass(frozen=True)
class SeriesContract:
    """Series-level declarations of section 4.1 (registered before research
    starts; any change is a new data_version)."""

    index_id: str = ""
    index_source: str = ""
    index_method_version: str = ""
    data_version: str = ""
    constituent_universe_definition: str = ""
    weight_method: str = ""
    weight_asof_rule: str = ""
    weight_update_schedule: str = ""
    ohlc_method: str = ""
    price_resolution: float = 0.0
    volume_method: str = ""
    volume_unit: str = ""
    oi_method: str = ""
    revision_policy: str = ""
    data_profile: str = DATA_PROFILE_WEIGHTED_INDEX


@dataclass(frozen=True)
class BarV4:
    """Bar-level record of section 4.1.

    open_interest is optional; when present, oi_available_at is required
    (4.1). quality_flags is an immutable frozenset for determinism (I-07).
    """

    bar_start: datetime
    bar_end: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    trading_day: str | None = None
    session_id: str | None = None
    is_closed: bool = True
    received_at: datetime | None = None
    open_interest: float | None = None
    oi_available_at: datetime | None = None
    quality_flags: frozenset[str] = field(default_factory=frozenset)

    @property
    def key(self) -> tuple[str | None, datetime]:
        """Primary key: (session_id, bar_start); unique within a series."""
        return (self.session_id, self.bar_start)


@dataclass(frozen=True)
class SeriesEligibility:
    """Result of the section 4.2 series-level eligibility check."""

    status: EligibilityStatus
    reason: EligibilityReason | None = None

    @property
    def eligible(self) -> bool:
        return self.status is EligibilityStatus.ELIGIBLE


def check_series_contract(
    contract: SeriesContract, index_available: bool = True
) -> SeriesEligibility:
    """Section 4.2 series-level eligibility.

    `index_available` reports whether the declared index actually exists in
    the data store. When it does not, the result is MISSING and the caller
    must surface a missing entry; no other series is ever substituted.
    """
    if contract.data_profile != DATA_PROFILE_WEIGHTED_INDEX:
        return SeriesEligibility(
            EligibilityStatus.REJECTED, EligibilityReason.DATA_PROFILE_UNSUPPORTED
        )
    if not math.isfinite(contract.price_resolution) or contract.price_resolution <= 0:
        raise ValueError(
            f"price_resolution must be positive, got {contract.price_resolution!r}"
        )
    if (
        not contract.index_source
        or contract.index_source in NON_WEIGHTED_INDEX_SOURCES
        or not contract.weight_method
        or not contract.weight_asof_rule
        or not contract.constituent_universe_definition
    ):
        return SeriesEligibility(
            EligibilityStatus.REJECTED, EligibilityReason.INDEX_SOURCE_MISMATCH
        )
    if contract.ohlc_method not in ALLOWED_OHLC_METHODS:
        return SeriesEligibility(
            EligibilityStatus.REJECTED, EligibilityReason.OHLC_METHOD_INVALID
        )
    if not index_available:
        return SeriesEligibility(
            EligibilityStatus.MISSING, EligibilityReason.DECLARED_INDEX_MISSING
        )
    required = ("index_id", "index_method_version", "data_version", "weight_update_schedule",
                "volume_method", "volume_unit", "revision_policy")
    if any(not getattr(contract, name) for name in required):
        return SeriesEligibility(EligibilityStatus.REJECTED, EligibilityReason.DECLARATION_INCOMPLETE)
    return SeriesEligibility(EligibilityStatus.ELIGIBLE)


def check_weight_timing(bar: BarV4, weight_asof_at: datetime) -> EligibilityReason | None:
    """Section 4.2 weight-timing check for one bar (I-12).

    Returns EX_POST_WEIGHTS when the weights used for this bar were taken
    as-of a time later than the bar's computation time (received_at if
    present, else bar_end); such bars are rejected as realtime input and may
    only be flagged ex_post_weights for ex-post description. Returns None
    when the weights were knowable in time.
    """
    computation_time = max(bar.bar_end, bar.received_at or bar.bar_end)
    if weight_asof_at > computation_time:
        return EligibilityReason.EX_POST_WEIGHTS
    return None


# Bar sanity problem codes (section 4.2 basic legality).
BAR_PROBLEM_HIGH_BELOW_BODY = "high_below_max_open_close"
BAR_PROBLEM_LOW_ABOVE_BODY = "low_above_min_open_close"
BAR_PROBLEM_HIGH_LOW_INVERTED = "high_below_low"
BAR_PROBLEM_NEGATIVE_VOLUME = "negative_volume"
BAR_PROBLEM_NEGATIVE_OI = "negative_open_interest"
BAR_PROBLEM_OI_WITHOUT_TIMESTAMP = "open_interest_without_oi_available_at"
BAR_PROBLEM_BAR_TIME_INVERTED = "bar_end_before_bar_start"
BAR_PROBLEM_DUPLICATE_KEY = "duplicate_bar_key"
BAR_PROBLEM_BAR_START_OUT_OF_ORDER = "bar_start_out_of_order"


def check_bar(bar: BarV4) -> tuple[str, ...]:
    """Per-bar basic legality of section 4.2.

    Prices are not required to be positive. Returns problem codes in a fixed
    order (determinism, I-07).
    """
    problems: list[str] = []
    for name in ("open", "high", "low", "close", "volume", "open_interest"):
        value = getattr(bar, name)
        if value is not None and not math.isfinite(value):
            problems.append(f"nonfinite_{name}")
    if not (bar.high >= max(bar.open, bar.close)):
        problems.append(BAR_PROBLEM_HIGH_BELOW_BODY)
    if not (bar.low <= min(bar.open, bar.close)):
        problems.append(BAR_PROBLEM_LOW_ABOVE_BODY)
    if not (bar.high >= bar.low):
        problems.append(BAR_PROBLEM_HIGH_LOW_INVERTED)
    if bar.volume < 0:
        problems.append(BAR_PROBLEM_NEGATIVE_VOLUME)
    if bar.open_interest is not None:
        if bar.open_interest < 0:
            problems.append(BAR_PROBLEM_NEGATIVE_OI)
        if bar.oi_available_at is None:
            problems.append(BAR_PROBLEM_OI_WITHOUT_TIMESTAMP)
    if bar.bar_end < bar.bar_start:
        problems.append(BAR_PROBLEM_BAR_TIME_INVERTED)
    return tuple(problems)


def check_bar_sequence(bars) -> tuple[str, ...]:
    """Sequence-level legality of section 4.2: time ordered, unique keys.

    Returns problem codes in bar order (determinism, I-07).
    """
    problems: list[str] = []
    seen_keys: set = set()
    previous_start: datetime | None = None
    for bar in bars:
        if previous_start is not None and bar.bar_start < previous_start:
            problems.append(BAR_PROBLEM_BAR_START_OUT_OF_ORDER)
        previous_start = bar.bar_start
        key = bar.key
        if key in seen_keys:
            problems.append(BAR_PROBLEM_DUPLICATE_KEY)
        seen_keys.add(key)
    return tuple(problems)


# --- Section 4.3: fixed-point prices -------------------------------------


def to_ticks(price: float, price_resolution: float) -> int:
    """Convert a price to an integer tick count at price_resolution.

    The price must be an exact multiple of price_resolution (4.3); otherwise
    ContractViolation is raised (no self-rounding). Uses exact decimal
    arithmetic so binary-float representations do not create spurious
    acceptances or rejections.
    """
    if not math.isfinite(price_resolution) or price_resolution <= 0:
        raise ValueError(
            f"price_resolution must be positive, got {price_resolution!r}"
        )
    if not math.isfinite(price):
        raise ContractViolation("price must be finite")
    quotient = Decimal(str(price)) / Decimal(str(price_resolution))
    if quotient != quotient.to_integral_value():
        raise ContractViolation(
            f"price {price!r} is not an exact multiple of price_resolution "
            f"{price_resolution!r}"
        )
    return int(quotient)


def from_ticks(ticks: int, price_resolution: float) -> float:
    """Convert an integer tick count back to a price at price_resolution."""
    if not math.isfinite(price_resolution) or price_resolution <= 0:
        raise ValueError(
            f"price_resolution must be positive, got {price_resolution!r}"
        )
    return float(Decimal(ticks) * Decimal(str(price_resolution)))


def boundary_epsilon(
    boundary_resolution_units: int,
    boundary_atr_fraction: float,
    price_resolution: float,
    atr_reference: float,
) -> float:
    """Boundary epsilon: max(resolution units, ATR fraction of the reference ATR)."""
    return max(
        boundary_resolution_units * price_resolution,
        boundary_atr_fraction * atr_reference,
    )


def confirm_step(confirm_step_units: int, price_resolution: float) -> float:
    """Section 4.3: confirm_step = confirm_step_units * price_resolution."""
    if not isinstance(confirm_step_units, int) or isinstance(confirm_step_units, bool):
        raise ValueError(
            f"confirm_step_units must be an int, got {confirm_step_units!r}"
        )
    if confirm_step_units < 1:
        raise ValueError(
            f"confirm_step_units must be >= 1, got {confirm_step_units!r}"
        )
    if not math.isfinite(price_resolution) or price_resolution <= 0:
        raise ValueError(
            f"price_resolution must be positive, got {price_resolution!r}"
        )
    return confirm_step_units * price_resolution
