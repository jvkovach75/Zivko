from __future__ import annotations

from .config import PROPOSED_ELECTRICAL_LAYERS
from .models import DesignModel


def apply_default_rules(design: DesignModel) -> DesignModel:
    design.proposed_layers = list(PROPOSED_ELECTRICAL_LAYERS)

    if not design.outputs:
        design.warnings.append("No outputs were parsed from the project task.")

    needs_krstac_anchor = any(
        "krstac" in (output.start_point or "").lower()
        or "krstac" in (output.end_point or "").lower()
        or "krstac" in (output.title or "").lower()
        for output in design.outputs
    )
    if needs_krstac_anchor and "ts 35/10kv krstac" not in design.anchors:
        design.warnings.append("Could not find a strong anchor for TS 35/10kV Krstac in the drawing.")

    for output in design.outputs:
        if not output.cable_type:
            output.cable_type = "XHE 49-A 3x1x150 mm2, 10 kV"
        if not output.route_mode:
            output.route_mode = "underground"

    return design
