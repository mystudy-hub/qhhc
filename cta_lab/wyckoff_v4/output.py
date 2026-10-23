"""Immutable publication records and deterministic JSON (spec 6.0, 9.2)."""
from __future__ import annotations

import json
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from .types import EventCode, TriState, TrendState
from .params import PARAMETER_VERSION, RULE_VERSION


def primitive(value: Any) -> Any:
    if isinstance(value, TriState):
        return {TriState.TRUE: True, TriState.FALSE: False,
                TriState.NOT_EVALUABLE: "not_evaluable"}[value]
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if is_dataclass(value):
        return {f.name: primitive(getattr(value, f.name)) for f in fields(value)}
    if isinstance(value, dict):
        return {str(primitive(k)): primitive(v) for k, v in value.items()}
    if isinstance(value, (set, frozenset)):
        return sorted(primitive(v) for v in value)
    if isinstance(value, (list, tuple)):
        return [primitive(v) for v in value]
    return value


def dumps(value: Any) -> str:
    return json.dumps(primitive(value), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False)


@dataclass(frozen=True)
class ContextSnapshot:
    snapshot_id: str
    asof_bar: int
    available_at: datetime
    context_for_next_bar: TrendState
    range_id: str | None = None
    range_version: int | None = None
    S: float | None = None
    R: float | None = None
    epsilon: float | None = None
    range_age: int = 0
    dominance: str = "not_evaluable"
    direction_evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class Occurrence:
    event_id: str
    event_type: EventCode
    pattern_start: int
    pattern_end: int
    event_time: datetime
    detected_at: datetime
    available_at: datetime
    onset_snapshot: ContextSnapshot | None = None
    reference_range_id: str | None = None
    reference_range_version: int | None = None
    frozen_S: float | None = None
    frozen_R: float | None = None
    frozen_epsilon: float | None = None
    frozen_internal_level: float | None = None
    strength: str = "ordinary"
    sub_type: str | None = None
    aliases: tuple[EventCode, ...] = ()
    linked_event_ids: tuple[str, ...] = ()
    observed_metrics: tuple[tuple[str, Any], ...] = ()
    standard_baseline_metrics: tuple[tuple[str, Any], ...] = ()
    threshold_margins: tuple[tuple[str, float], ...] = ()
    rejection_reasons: tuple[str, ...] = ()
    quality_flags: tuple[str, ...] = ()
    acceptance_id: str | None = None
    evidence_owner_id: str | None = None
    confirmation_trigger_id: str | None = None
    primitive_event_ids: tuple[str, ...] = ()
    index_id: str = ""
    data_profile: str = "weighted_index"
    index_source: str = ""
    index_method_version: str = ""
    timeframe: str = ""
    baseline_mode: str = ""
    rule_version: str = RULE_VERSION
    parameter_version: str = PARAMETER_VERSION
    data_version: str = ""
    data_asof: datetime | None = None
    onset_snapshot_id: str | None = None

    @property
    def metrics(self) -> dict[str, Any]:
        return dict(self.observed_metrics)


@dataclass(frozen=True)
class RuleEvaluation:
    rule: str
    bar: int
    result: TriState
    reasons: tuple[str, ...] = ()
    threshold_margins: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True)
class OiAnnotation:
    bar_index: int
    feature_available_at: datetime
    recorded_at: datetime
    oi_z: float
    oi_quadrant: str | None
    data_version: str


@dataclass(frozen=True)
class Transition:
    object_type: str
    object_id: str
    previous_status: str | None
    status: str
    reason: str
    effective_bar: int
    available_at: datetime
    transaction_id: str


@dataclass(frozen=True)
class Publication:
    """JSON storage prevents callers or later transactions mutating history."""
    snapshot_json: str
    events: tuple[Occurrence, ...]
    transitions: tuple[Transition, ...]
    evaluations: tuple[RuleEvaluation, ...]
    processing_order: tuple[int, ...]

    @property
    def snapshot(self) -> dict[str, Any]:
        return json.loads(self.snapshot_json)

    def to_json(self) -> str:
        return dumps({"snapshot": self.snapshot, "events": self.events,
                      "transitions": self.transitions, "evaluations": self.evaluations,
                      "processing_order": self.processing_order})


def dec(value: float) -> Decimal:
    return Decimal(str(value))


def at_least_step(close: float, reference: float, resolution: float,
                  units: int, direction: int = 1) -> bool:
    return direction * (dec(close) - dec(reference)) >= dec(resolution) * units


def outside(close: float, level: float, epsilon: float, direction: int) -> bool:
    return direction * (dec(close) - dec(level)) > dec(epsilon)
