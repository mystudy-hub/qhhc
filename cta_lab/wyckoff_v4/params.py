"""Wyckoff V4 parameter registry (spec section 10, tables 10.1-10.6).

Every parameter from tables 10.1-10.5 is present with its exact default value,
plus the mode switches (baseline_mode, intraday_volume_mode, oi_mode,
creek_mode, wave_module_enabled) and the version identifiers rule_version /
parameter_version required by I-07 (determinism).

validate() enforces every constraint of section 10.6 and raises ValueError
naming the offending parameter. Rejection never silently corrects a value.

SPEC DECISIONS (literal-reading choices):
- Mode switches are single-purpose enums. Only members the spec names in the
  sections read for this phase exist ('robust'/'standard' for baseline_mode
  via F-03, 'session_matched', 'annotate', 'range_and_internal'); later phases
  extend them after reading their own spec sections rather than inventing
  modes here.
- "warmup_bars covers all enabled windows" (10.6) is implemented over the
  lookback windows needed before the first evaluable bar: indicator history
  (atr_period, volume_period, session_baseline_days, oi_window, trend_window,
  trend_sma_fast, trend_sma_slow, larger_context_window), structural lookback
  (generic_range_window, climax_new_extreme_window, pivot_confirm_bars).
  Forward-looking response windows and range lifetime caps (e.g.
  range_max_age) are not warmup history. volume_period and
  session_baseline_days are both included because the parameter object does
  not know the timeframe (daily vs intraday) of the series it will be used
  with; the comparison is therefore conservative.
- 10.6 requires positivity for ATR and volume (activity-rate) multipliers;
  those are the float fields validated below. Other ratio parameters are only
  constrained where 10.6 states a constraint (e.g.
  0 < breakout_fail_deep_fraction < 1, wave_dominance_ratio > 1).
- price_resolution is a series-level field (section 4.1) and its positivity
  constraint is enforced in contract.py where it is declared.
- rule_version is validated to equal '4.0': this package implements only the
  V4 ruleset, and running V4 code under another rule_version label would be a
  silent mislabel rather than a different ruleset.
"""

from dataclasses import dataclass, fields
from enum import Enum
import math


class BaselineMode(Enum):
    """F-03 baseline mode: 'robust' truncates ATR input and volume baseline."""

    STANDARD = "standard"
    ROBUST = "robust"


class IntradayVolumeMode(Enum):
    """Intraday activity-rate baseline mode (section 4.5)."""

    SESSION_MATCHED = "session_matched"


class OiMode(Enum):
    """Open-interest usage mode (table 10.1, default 'annotate')."""

    ANNOTATE = "annotate"


class CreekMode(Enum):
    """Creek/Ice reference mode (table 10.2, F-06).

    range_only: internal JOC/FTI and internal LPS/LPSY events output
    not_implemented (spec 5.6).
    """

    RANGE_AND_INTERNAL = "range_and_internal"
    RANGE_ONLY = "range_only"


RULE_VERSION = "4.0"
PARAMETER_VERSION = "v4-default-unvalidated"

# Float fields that must be strictly positive (10.6: ATR and volume/activity
# multipliers are positive). ATR-family thresholds first, then volume/effort
# (rvol/ns) thresholds.
_POSITIVE_FLOAT_FIELDS: tuple[str, ...] = (
    # ATR-family (values expressed in ATR units)
    "tr_cap_mult",
    "boundary_atr_fraction",
    "range_min_width_atr",
    "range_max_width_atr",
    "trend_min_displacement_atr",
    "trend_min_abs_slope_atr",
    "larger_context_min_displacement_atr",
    "pivot_min_reaction_atr",
    "cluster_min_span_atr",
    "low_progress_max_move_atr",
    "spring_max_depth_atr",
    "strong_bar_min_move_atr",
    "st_near_support_atr",
    "test_near_support_atr",
    "ar_reversal_atr",
    "lps_min_retrace_atr",
    "lps_response_min_move_atr",
    "phase_e_min_distance_atr",
    "wave_reversal_atr",
    "gap_flag_min_atr",
    # volume / activity-rate family
    "volume_cap_mult",
    "climax_min_rvol",
    "climax_min_ns",
    "cluster_min_effort",
    "cluster_stop_min_rvol",
    "absorption_min_rvol",
    "absorption_max_ns",
    "spring_low_volume_max_rvol",
    "spring_high_volume_min_rvol",
    "strong_break_min_rvol",
    "strong_break_min_ns",
    "sos_sow_min_rvol",
    "sos_sow_min_ns",
    "st_max_effort_vs_climax",
    "st_max_ns_vs_climax",
    "test_max_rvol",
    "test_max_ns",
    "test_max_effort_vs_spring",
    "lps_max_pullback_effort",
    "lps_max_pullback_ns",
    "strict_backup_max_mean_rvol",
    "strict_backup_max_mean_ns",
    "backup_turn_min_rvol",
    "oi_flat_band",
)

# Lookback windows that warmup_bars must cover (see module docstring).
_WARMUP_WINDOW_FIELDS: tuple[str, ...] = (
    "atr_period",
    "volume_period",
    "session_baseline_days",
    "oi_window",
    "trend_window",
    "trend_sma_fast",
    "trend_sma_slow",
    "larger_context_window",
    "generic_range_window",
    "climax_new_extreme_window",
    "pivot_confirm_bars",
)


@dataclass(frozen=True)
class WyckoffV4Params:
    """All V4 parameters with the exact table 10.1-10.5 defaults (all P: unvalidated)."""

    # --- 10.1 data, indicators, background ---
    warmup_bars: int = 60
    atr_period: int = 14
    tr_cap_mult: float = 3.0
    baseline_mode: BaselineMode = BaselineMode.ROBUST
    volume_period: int = 20
    volume_cap_mult: float = 3.0
    volume_min_samples: int = 15
    intraday_volume_mode: IntradayVolumeMode = IntradayVolumeMode.SESSION_MATCHED
    session_baseline_days: int = 20
    session_min_samples: int = 10
    oi_mode: OiMode = OiMode.ANNOTATE
    oi_window: int = 20
    oi_flat_band: float = 1.0
    trend_window: int = 20
    trend_sma_fast: int = 20
    trend_sma_slow: int = 50
    trend_min_displacement_atr: float = 2.0
    trend_min_abs_slope_atr: float = 0.05
    trend_min_er: float = 0.30
    larger_context_window: int = 50
    larger_context_min_displacement_atr: float = 3.0
    larger_context_min_er: float = 0.30
    regime_persistence_bars: int = 2
    confirm_step_units: int = 1

    # --- 10.2 range and reference levels ---
    boundary_resolution_units: int = 2
    boundary_atr_fraction: float = 0.10
    range_min_width_atr: float = 3.0
    range_max_width_atr: float = 12.0
    generic_range_window: int = 30
    generic_range_max_er: float = 0.25
    range_interior_close_fraction: float = 0.80
    range_min_touches_per_side: int = 2
    touch_min_separation_bars: int = 3
    event_min_range_age: int = 15
    mature_range_age: int = 25
    range_max_age: int = 200
    range_stale_min_age: int = 60
    range_stale_bars: int = 40
    pivot_confirm_bars: int = 3
    pivot_min_reaction_atr: float = 1.0
    creek_mode: CreekMode = CreekMode.RANGE_AND_INTERNAL

    # --- 10.3 factual events ---
    climax_new_extreme_window: int = 20
    climax_min_rvol: float = 2.5
    climax_min_ns: float = 1.8
    sc_min_cp: float = 0.35
    bc_max_cp: float = 0.65
    climax_min_rejection_wick: float = 0.25
    cluster_min_effort: float = 2.0
    cluster_min_span_atr: float = 2.5
    cluster_stop_min_rvol: float = 1.5
    absorption_min_rvol: float = 2.0
    absorption_max_ns: float = 0.7
    low_progress_max_move_atr: float = 0.5
    absorption_response_max_bars: int = 5
    absorption_current_evidence_max_age: int = 10
    spring_max_depth_atr: float = 2.0
    spring_max_pattern_bars: int = 2
    shakeout_max_pattern_bars: int = 3
    spring_min_cp: float = 0.70
    ut_max_cp: float = 0.30
    spring_low_volume_max_rvol: float = 0.8
    spring_high_volume_min_rvol: float = 1.3
    strong_break_min_rvol: float = 1.8
    strong_break_min_ns: float = 1.5
    joc_min_cp: float = 0.75
    fti_max_cp: float = 0.25
    strong_bar_min_move_atr: float = 0.5
    sos_sow_min_rvol: float = 1.4
    sos_sow_min_ns: float = 1.3
    sos_min_cp: float = 0.70
    sow_max_cp: float = 0.30
    gap_flag_min_atr: float = 1.0

    # --- 10.4 sequences, confirmation, invalidation ---
    ar_window_min: int = 3
    ar_window_max: int = 8
    ar_reversal_close_steps: int = 2
    ar_reversal_atr: float = 0.5
    st_window_min: int = 5
    st_window_max: int = 15
    st_response_max_bars: int = 3
    st_near_support_atr: float = 0.5
    st_max_effort_vs_climax: float = 0.60
    st_max_ns_vs_climax: float = 0.70
    test_window_min: int = 3
    test_window_max: int = 10
    test_near_support_atr: float = 0.5
    test_max_rvol: float = 0.85
    test_max_ns: float = 1.0
    test_max_effort_vs_spring: float = 0.65
    test_response_max_bars: int = 3
    spring_sos_window_min: int = 1
    spring_sos_window_max: int = 10
    spring_fail_consecutive_closes: int = 2
    breakout_hold_closes: int = 3
    breakout_fail_inside_closes: int = 2
    breakout_fail_deep_fraction: float = 0.50
    breakout_validity_window: int = 12
    body_retrace_fraction: float = 0.50
    lps_min_pullback_bars: int = 2
    lps_max_pullback_bars: int = 8
    lps_max_pullback_effort: float = 0.90
    lps_max_pullback_ns: float = 1.0
    lps_min_retrace_atr: float = 0.5
    lps_response_min_move_atr: float = 0.3
    lps_window_max: int = 15
    lps_current_max_age: int = 20
    phase_d_followthrough_bars: int = 10
    strict_backup_min_pullback_bars: int = 3
    strict_backup_max_mean_rvol: float = 0.85
    strict_backup_max_mean_ns: float = 1.0
    backup_turn_min_rvol: float = 1.2
    utad_response_window_min: int = 1
    utad_response_window_max: int = 12
    phase_e_min_outside_closes: int = 5
    phase_e_min_distance_atr: float = 2.0
    phase_e_min_er: float = 0.40

    # --- 10.5 waves and structure ---
    wave_module_enabled: bool = True
    wave_reversal_atr: float = 1.0
    wave_stats_max_completed: int = 6
    wave_min_completed_each_direction: int = 2
    wave_dominance_ratio: float = 1.2
    bias_signal_recent_bars: int = 10
    bias_sequence_recent_bars: int = 20
    structural_opposite_persistence_bars: int = 2

    # --- versions (I-07) ---
    rule_version: str = RULE_VERSION
    parameter_version: str = PARAMETER_VERSION

    @property
    def snapshot_ring_length(self) -> int:
        """F-15: ring length = max(3, spring_max_pattern_bars, shakeout_max_pattern_bars) + 1."""
        return max(3, self.spring_max_pattern_bars, self.shakeout_max_pattern_bars) + 1

    def validate(self) -> None:
        """Enforce every section 10.6 constraint; raise ValueError naming the parameter."""
        self._validate_modes_and_versions()
        self._validate_positive_ints()
        self._validate_positive_floats()
        self._validate_window_pairs()
        self._validate_cross_constraints()
        self._validate_warmup_coverage()

    # -- helpers ---------------------------------------------------------

    def _validate_modes_and_versions(self) -> None:
        checks = (
            ("baseline_mode", self.baseline_mode, BaselineMode),
            ("intraday_volume_mode", self.intraday_volume_mode, IntradayVolumeMode),
            ("oi_mode", self.oi_mode, OiMode),
            ("creek_mode", self.creek_mode, CreekMode),
        )
        for name, value, enum_type in checks:
            if not isinstance(value, enum_type):
                raise ValueError(
                    f"{name} must be a {enum_type.__name__}, got {value!r}"
                )
        if not isinstance(self.wave_module_enabled, bool):
            raise ValueError(
                f"wave_module_enabled must be a bool, got {self.wave_module_enabled!r}"
            )
        if self.rule_version != RULE_VERSION:
            raise ValueError(
                f"rule_version must be {RULE_VERSION!r}, got {self.rule_version!r}"
            )
        if not isinstance(self.parameter_version, str) or not self.parameter_version:
            raise ValueError(
                f"parameter_version must be a non-empty string, got {self.parameter_version!r}"
            )

    def _validate_positive_ints(self) -> None:
        # 10.6: bar counts are positive integers (bool is not an acceptable int).
        for field in fields(self):
            if field.type is not int:
                continue
            value = getattr(self, field.name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{field.name} must be a positive int, got {value!r}")

    def _validate_positive_floats(self) -> None:
        for item in fields(self):
            if item.type is float:
                value = getattr(self, item.name)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ValueError(f"{item.name} must be a finite number")
        for name in _POSITIVE_FLOAT_FIELDS:
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive number, got {value!r}")

    def _validate_window_pairs(self) -> None:
        pairs = (
            ("ar_window_min", "ar_window_max"),
            ("st_window_min", "st_window_max"),
            ("test_window_min", "test_window_max"),
            ("spring_sos_window_min", "spring_sos_window_max"),
            ("utad_response_window_min", "utad_response_window_max"),
        )
        for min_name, max_name in pairs:
            if getattr(self, min_name) > getattr(self, max_name):
                raise ValueError(
                    f"{min_name} must be <= {max_name}: "
                    f"{min_name}={getattr(self, min_name)}, "
                    f"{max_name}={getattr(self, max_name)}"
                )
        # 10.6 states lps_min_pullback_bars < lps_max_pullback_bars (strict).
        if self.lps_min_pullback_bars >= self.lps_max_pullback_bars:
            raise ValueError(
                f"lps_min_pullback_bars must be < lps_max_pullback_bars: "
                f"lps_min_pullback_bars={self.lps_min_pullback_bars}, "
                f"lps_max_pullback_bars={self.lps_max_pullback_bars}"
            )

    def _validate_cross_constraints(self) -> None:
        if self.volume_min_samples > self.volume_period:
            raise ValueError("volume_min_samples must not exceed volume_period")
        if self.session_min_samples > self.session_baseline_days:
            raise ValueError("session_min_samples must not exceed session_baseline_days")
        for name in ("trend_min_er", "larger_context_min_er", "generic_range_max_er",
                     "phase_e_min_er", "range_interior_close_fraction", "sc_min_cp",
                     "bc_max_cp", "climax_min_rejection_wick", "spring_min_cp",
                     "ut_max_cp", "joc_min_cp", "fti_max_cp", "sos_min_cp",
                     "sow_max_cp", "body_retrace_fraction"):
            if not 0 <= getattr(self, name) <= 1:
                raise ValueError(f"{name} must be in [0, 1]")
        if self.spring_low_volume_max_rvol >= self.spring_high_volume_min_rvol:
            raise ValueError(
                f"spring_low_volume_max_rvol must be < spring_high_volume_min_rvol: "
                f"spring_low_volume_max_rvol={self.spring_low_volume_max_rvol}, "
                f"spring_high_volume_min_rvol={self.spring_high_volume_min_rvol}"
            )
        if self.range_min_width_atr >= self.range_max_width_atr:
            raise ValueError(
                f"range_min_width_atr must be < range_max_width_atr: "
                f"range_min_width_atr={self.range_min_width_atr}, "
                f"range_max_width_atr={self.range_max_width_atr}"
            )
        if self.mature_range_age < self.event_min_range_age:
            raise ValueError(
                f"mature_range_age must be >= event_min_range_age: "
                f"mature_range_age={self.mature_range_age}, "
                f"event_min_range_age={self.event_min_range_age}"
            )
        if self.spring_max_pattern_bars not in (1, 2):
            raise ValueError(
                f"spring_max_pattern_bars must be 1 or 2, got {self.spring_max_pattern_bars!r}"
            )
        if self.shakeout_max_pattern_bars < self.spring_max_pattern_bars:
            raise ValueError(
                f"shakeout_max_pattern_bars must be >= spring_max_pattern_bars: "
                f"shakeout_max_pattern_bars={self.shakeout_max_pattern_bars}, "
                f"spring_max_pattern_bars={self.spring_max_pattern_bars}"
            )
        if self.strict_backup_min_pullback_bars > self.lps_max_pullback_bars:
            raise ValueError(
                f"strict_backup_min_pullback_bars must be <= lps_max_pullback_bars: "
                f"strict_backup_min_pullback_bars={self.strict_backup_min_pullback_bars}, "
                f"lps_max_pullback_bars={self.lps_max_pullback_bars}"
            )
        if self.wave_dominance_ratio <= 1:
            raise ValueError(
                f"wave_dominance_ratio must be > 1, got {self.wave_dominance_ratio!r}"
            )
        if not (0 < self.breakout_fail_deep_fraction < 1):
            raise ValueError(
                f"breakout_fail_deep_fraction must be strictly between 0 and 1, "
                f"got {self.breakout_fail_deep_fraction!r}"
            )

    def _validate_warmup_coverage(self) -> None:
        required = max(getattr(self, name) for name in _WARMUP_WINDOW_FIELDS)
        required = max(required, self.trend_window + 1, self.generic_range_window + 1,
                       self.larger_context_window + 1, self.atr_period + 1,
                       self.climax_new_extreme_window + 3, 2 * self.pivot_confirm_bars + 1)
        if self.warmup_bars < required:
            raise ValueError(
                f"warmup_bars must cover all enabled windows "
                f"(largest enabled lookback window is {required}): "
                f"warmup_bars={self.warmup_bars}"
            )
