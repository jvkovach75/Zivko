from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class OutputSpec:
    index: int
    title: str
    code: str | None = None
    start_point: str | None = None
    end_point: str | None = None
    cable_type: str | None = None
    trench_profile: str | None = None
    route_mode: str | None = None
    route_musts: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass
class ConstraintSpec:
    category: str
    description: str
    source: str


@dataclass
class Anchor:
    name: str
    layer: str
    x: float
    y: float
    text: str
    score: float


@dataclass
class LayerClassification:
    layer: str
    kind: str
    reason: str


@dataclass
class RouteSegment:
    output_index: int
    output_code: str | None
    layer: str
    points: list[tuple[float, float]]
    approx_length: float
    note: str
    source_kind: str = "generated"


@dataclass
class QuantityItem:
    code: str
    description: str
    unit: str
    quantity: float
    source: str


@dataclass
class DesignModel:
    source_dxf: str
    project_task_text: str
    condition_texts: list[str]
    algorithm_profile: str = "full_latest"
    algorithm_checks: list[str] = field(default_factory=list)
    input_readiness: list[dict[str, Any]] = field(default_factory=list)
    input_blockers: list[str] = field(default_factory=list)
    outputs: list[OutputSpec] = field(default_factory=list)
    constraints: list[ConstraintSpec] = field(default_factory=list)
    layer_classes: list[LayerClassification] = field(default_factory=list)
    anchors: dict[str, list[Anchor]] = field(default_factory=dict)
    anchor_requirements: list[dict[str, Any]] = field(default_factory=list)
    anchor_diagnostics: list[dict[str, Any]] = field(default_factory=list)
    route_hints: dict[str, list[tuple[float, float]]] = field(default_factory=dict)
    proposed_layers: list[str] = field(default_factory=list)
    route_segments: list[RouteSegment] = field(default_factory=list)
    route_benchmarks: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_dxf": self.source_dxf,
            "project_task_text": self.project_task_text,
            "condition_texts": self.condition_texts,
            "algorithm_profile": self.algorithm_profile,
            "algorithm_checks": list(self.algorithm_checks),
            "input_readiness": list(self.input_readiness),
            "input_blockers": list(self.input_blockers),
            "outputs": [asdict(item) for item in self.outputs],
            "constraints": [asdict(item) for item in self.constraints],
            "layer_classes": [asdict(item) for item in self.layer_classes],
            "anchors": {
                key: [asdict(item) for item in values] for key, values in self.anchors.items()
            },
            "anchor_requirements": list(self.anchor_requirements),
            "anchor_diagnostics": list(self.anchor_diagnostics),
            "route_hints": {key: list(values) for key, values in self.route_hints.items()},
            "proposed_layers": list(self.proposed_layers),
            "route_segments": [asdict(item) for item in self.route_segments],
            "route_benchmarks": list(self.route_benchmarks),
            "warnings": list(self.warnings),
        }


@dataclass
class QuantityReport:
    source_dxf: str
    items: list[QuantityItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_dxf": self.source_dxf,
            "items": [asdict(item) for item in self.items],
            "warnings": list(self.warnings),
        }
