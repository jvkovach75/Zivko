from __future__ import annotations

from collections import defaultdict
import math
import os
import re
import shutil
import subprocess
import unicodedata
import heapq
from itertools import combinations
from pathlib import Path
from typing import Iterable

import ezdxf
from ezdxf.math import Vec2
from ezdxf.render.mleader import ConnectionSide, HorizontalConnection, LeaderType, TextAlignment

from .config import (
    ANCHOR_CONTEXT,
    ANCHOR_KEYWORDS,
    ALLOWED_CORRIDOR_LAYER_PATTERNS,
    BASE_LAYER_PATTERNS,
    CONDITIONAL_CORRIDOR_LAYER_PATTERNS,
    CORRIDOR_CLASS_WEIGHTS,
    CORRIDOR_FALLBACK_LAYER_PATTERNS,
    CONDITION_LAYER_PATTERNS,
    EXPECTED_ROUTE_LENGTHS,
    EXPECTED_ROUTE_LENGTHS_BY_CODE,
    FINAL_LEARNED_ROUTE_PRIORS_BY_CODE,
    EXISTING_NETWORK_LAYER_PATTERNS,
    FINAL_PROJECT_LAYER_PATTERNS,
    OUTPUT_GUIDE_LAYERS,
    OUTPUT_GUIDE_LAYERS_BY_CODE,
    PROJECT_OUTPUT_ROUTE_LAYERS_BY_CODE,
    PROJECT_LEADER_LAYER,
    PROJECT_LAYER_PATTERNS,
    PROJECT_TEXT_LAYER,
    PREFERRED_CORRIDOR_LAYER_PATTERNS,
    ROAD_CORRIDOR_LAYER_PATTERNS,
    KRSTAC_LEADER_LAYER_ATTRS,
    KRSTAC_ROUTE_ENTITY_ATTRS_BY_LAYER,
    KRSTAC_TEXT_ENTITY_ATTRS,
    KRSTAC_TEXT_LAYER_ATTRS,
)
from .models import Anchor, DesignModel, LayerClassification, RouteSegment
from .path_utils import resolve_existing_path


ODA_FILE_CONVERTER = Path(
    os.environ.get("ODA_FILE_CONVERTER", r"C:\Program Files\ODA\ODAFileConverter 27.1.0\ODAFileConverter.exe")
)
ODA_SAFE_REGAPPS = {
    "ACAD",
    "AcadAnnotative",
    "ACAD_MLEADERVER",
    "HATCHBACKGROUNDCOLOR",
    "EZDXF",
}


def _converted_cache_dir() -> Path:
    return Path(__file__).resolve().parent.parent / ".converted_inputs"


def _run_oda_conversion_variants(source_dxf: Path, cache_key: str) -> tuple[Path | None, list[str]]:
    cache_dir = _converted_cache_dir()
    work_in = cache_dir / f"{cache_key}_in"
    work_out = cache_dir / f"{cache_key}_out"
    work_in.mkdir(parents=True, exist_ok=True)
    work_out.mkdir(parents=True, exist_ok=True)

    staged_dxf = work_in / source_dxf.name
    shutil.copy2(source_dxf, staged_dxf)

    command_variants = [
        [
            str(ODA_FILE_CONVERTER),
            str(work_in),
            str(work_out),
            "ACAD2018",
            "DWG",
            "0",
            "1",
            "*.dxf",
        ],
        [
            str(ODA_FILE_CONVERTER),
            str(work_in),
            str(work_out),
            "ACAD2018",
            "DWG",
            "0",
            "1",
        ],
        [
            str(ODA_FILE_CONVERTER),
            str(work_in),
            str(work_out),
            "ACAD2013",
            "DWG",
            "0",
            "1",
            "*.dxf",
        ],
        [
            str(ODA_FILE_CONVERTER),
            str(work_in),
            str(work_out),
            "ACAD2013",
            "DWG",
            "0",
            "1",
        ],
    ]

    attempts: list[str] = []
    for cmd in command_variants:
        completed = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        produced = list(work_out.rglob("*.dwg")) if completed.returncode == 0 else []
        attempts.append(
            "CMD: "
            + " ".join(cmd)
            + f"\nRET: {completed.returncode}\nDWG_COUNT: {len(produced)}\nSTDOUT: {completed.stdout}\nSTDERR: {completed.stderr}"
        )
        if completed.returncode != 0 or not produced:
            continue
        produced.sort(key=lambda item: item.stat().st_mtime_ns, reverse=True)
        return produced[0], attempts
    return None, attempts


def _sanitize_dxf_for_oda(source_dxf: Path) -> Path:
    cache_dir = _converted_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    sanitized = cache_dir / f"{source_dxf.stem}_{int(source_dxf.stat().st_mtime_ns)}_oda_sanitized.dxf"
    if sanitized.exists() and sanitized.stat().st_size > 0:
        return sanitized

    doc = ezdxf.readfile(str(source_dxf))
    for entity in doc.entitydb.values():
        xdata = getattr(entity, "xdata", None)
        if xdata:
            for appid in list(xdata.data.keys()):
                entity.discard_xdata(appid)

    current_appids = [entry.dxf.name for entry in doc.appids]
    for appid in current_appids:
        if appid not in ODA_SAFE_REGAPPS:
            doc.appids.discard(appid)

    doc.audit()
    doc.saveas(str(sanitized))
    return sanitized


def resolve_input_document(path: str | Path) -> Path:
    source = resolve_existing_path(path)
    if source.exists():
        source = source.resolve()
    suffix = source.suffix.lower()
    if suffix == ".dxf":
        return source
    if suffix != ".dwg":
        raise ValueError(f"Nepodrzan ulazni format: {source.suffix}. Koristi DWG ili DXF.")
    return _convert_dwg_to_dxf(source)


def _convert_dwg_to_dxf(source_dwg: Path) -> Path:
    if not ODA_FILE_CONVERTER.exists():
        raise ValueError(
            "DWG ulaz je izabran, ali ODA File Converter nije pronadjen. "
            "Potrebno je da bude instaliran da bi se DWG automatski pretvorio u DXF."
        )

    cache_dir = _converted_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    source_key = f"{source_dwg.stem}_{int(source_dwg.stat().st_mtime_ns)}"
    work_in = cache_dir / f"{source_key}_in"
    work_out = cache_dir / f"{source_key}_out"
    work_in.mkdir(parents=True, exist_ok=True)
    work_out.mkdir(parents=True, exist_ok=True)

    staged_dwg = work_in / source_dwg.name
    staged_dxf = work_out / f"{source_dwg.stem}.dxf"
    if staged_dxf.exists() and staged_dxf.stat().st_size > 0:
        return staged_dxf

    shutil.copy2(source_dwg, staged_dwg)
    cmd = [
        str(ODA_FILE_CONVERTER),
        str(work_in),
        str(work_out),
        "ACAD2013",
        "DXF",
        "0",
        "1",
        "*.dwg",
    ]
    completed = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if completed.returncode != 0:
        raise ValueError(
            "Automatska DWG->DXF konverzija nije uspela.\n"
            f"Komanda: {' '.join(cmd)}\n"
            f"STDOUT: {completed.stdout}\nSTDERR: {completed.stderr}"
        )
    if not staged_dxf.exists() or staged_dxf.stat().st_size == 0:
        raise ValueError(
            "ODA File Converter je zavrsio bez greske, ali izlazni DXF nije napravljen."
        )
    return staged_dxf


def convert_dxf_to_dwg(source_dxf: str | Path, output_dwg: str | Path) -> Path:
    source = resolve_existing_path(source_dxf)
    if not source.exists():
        raise ValueError(f"Ulazni DXF nije pronadjen: {source}")
    if source.suffix.lower() != ".dxf":
        raise ValueError(f"Za DXF->DWG konverziju ocekujem DXF, a dobio sam: {source.suffix}")
    if not ODA_FILE_CONVERTER.exists():
        raise ValueError(
            "Automatski finalni DWG nije moguce napraviti jer ODA File Converter nije pronadjen."
        )

    output = Path(output_dwg).expanduser().resolve()
    source_key = f"{source.stem}_{int(source.stat().st_mtime_ns)}_dwg"

    attempts: list[str] = []
    produced, raw_attempts = _run_oda_conversion_variants(source, source_key)
    attempts.extend(raw_attempts)
    if produced is None:
        sanitized_source = _sanitize_dxf_for_oda(source)
        produced, sanitized_attempts = _run_oda_conversion_variants(sanitized_source, f"{source_key}_sanitized")
        attempts.append(
            f"SANITIZED_RETRY_SOURCE: {sanitized_source}"
        )
        attempts.extend(sanitized_attempts)

    if produced is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(produced, output)
        return output

    raise ValueError(
        "Automatska DXF->DWG konverzija nije uspela. "
        "Probana je i automatska ODA sanacija DXF-a, ali bez izlaznog DWG-a. "
        "Interni DXF je napravljen, ali finalni DWG treba sacuvati kroz DWG FastView.\n\n"
        + "\n\n".join(attempts)
    )


def open_document(path: str | Path) -> ezdxf.document.Drawing:
    resolved = resolve_input_document(path)
    return ezdxf.readfile(str(resolved))


def classify_layers(doc: ezdxf.document.Drawing) -> list[LayerClassification]:
    results: list[LayerClassification] = []
    for layer in doc.layers:
        name = layer.dxf.name
        lower = name.lower()
        kind = "unknown"
        reason = "No rule matched."

        if any(pattern in lower for pattern in PROJECT_LAYER_PATTERNS):
            kind = "project"
            reason = "Matched final project layer pattern."
        elif any(pattern in lower for pattern in EXISTING_NETWORK_LAYER_PATTERNS):
            kind = "existing_network"
            reason = "Matched existing network layer pattern."
        elif any(pattern in lower for pattern in CONDITION_LAYER_PATTERNS):
            kind = "condition"
            reason = "Matched condition layer pattern."
        elif any(pattern in lower for pattern in BASE_LAYER_PATTERNS):
            kind = "base"
            reason = "Matched base layer pattern."

        results.append(LayerClassification(layer=name, kind=kind, reason=reason))
    return results


def extract_text_entities(doc: ezdxf.document.Drawing) -> list[dict]:
    modelspace = doc.modelspace()
    results: list[dict] = []
    for entity in modelspace:
        kind = entity.dxftype()
        layer = entity.dxf.get("layer", "0")
        if kind in {"TEXT", "MTEXT"}:
            try:
                text = entity.plain_text() if kind == "MTEXT" else entity.dxf.text
            except Exception:
                continue
            insert = entity.dxf.get("insert", (0.0, 0.0, 0.0))
            x = float(insert[0])
            y = float(insert[1])
            results.append({"text": text, "x": x, "y": y, "layer": layer, "source": kind})
        elif kind == "INSERT":
            insert = entity.dxf.get("insert", (0.0, 0.0, 0.0))
            x = float(insert[0])
            y = float(insert[1])
            block_name = entity.dxf.get("name", "")
            if block_name:
                results.append({"text": block_name, "x": x, "y": y, "layer": layer, "source": "INSERT"})
            try:
                for attrib in entity.attribs:
                    text = attrib.dxf.get("text", "")
                    if text:
                        results.append({"text": text, "x": x, "y": y, "layer": layer, "source": "ATTRIB"})
            except Exception:
                pass
    return results


def find_anchors(doc: ezdxf.document.Drawing) -> dict[str, list[Anchor]]:
    texts = extract_text_entities(doc)
    anchors: dict[str, list[Anchor]] = {}

    for anchor_name, keywords in ANCHOR_KEYWORDS.items():
        matches: list[Anchor] = []
        for item in texts:
            score = _score_anchor_match(anchor_name, item["text"], item["layer"], keywords)
            if score > 0:
                matches.append(
                    Anchor(
                        name=anchor_name,
                        layer=item["layer"],
                        x=item["x"],
                        y=item["y"],
                        text=item["text"],
                        score=score,
                    )
                )
        matches.sort(key=lambda item: item.score, reverse=True)
        if matches:
            matches = [_refine_anchor_candidate(doc, anchor) for anchor in matches]
            anchors[anchor_name] = matches[:5]

    return anchors


def _refine_anchor_candidate(doc: ezdxf.document.Drawing, anchor: Anchor) -> Anchor:
    name = anchor.name.lower().strip()
    if name.startswith("k0") or name == "ts 35/10kv krstac":
        return anchor
    if _normalize_text(anchor.layer) != _normalize_text(PROJECT_TEXT_LAYER):
        return anchor

    preferred_layers = {
        "mreza",
        "šaht",
        "šaht",
        "0",
        "gralin-1",
        "l1_gra_zgrade",
        "vis_lin1_gran_objek",
    }
    best_point = None
    best_layer = None
    best_distance = float("inf")
    radius = 35.0

    for entity in doc.modelspace():
        layer = entity.dxf.get("layer", "0")
        layer_n = _normalize_text(layer)
        if layer_n not in preferred_layers:
            continue
        points = _entity_points(entity)
        if entity.dxftype() == "INSERT":
            insert = entity.dxf.get("insert", (0.0, 0.0, 0.0))
            points = [(float(insert[0]), float(insert[1]))]
        if not points:
            continue
        for point in points:
            distance = math.dist((anchor.x, anchor.y), point)
            if distance <= radius and distance < best_distance:
                best_distance = distance
                best_point = point
                best_layer = layer

    if best_point is None:
        return anchor
    return Anchor(
        name=anchor.name,
        layer=best_layer or anchor.layer,
        x=float(best_point[0]),
        y=float(best_point[1]),
        text=anchor.text,
        score=anchor.score,
    )


def _normalize_text(value: str) -> str:
    value = value.replace("Ä‡", "c").replace("Ć", "c").replace("č", "c").replace("Č", "c")
    value = value.replace("š", "s").replace("Š", "s").replace("ž", "z").replace("Ž", "z")
    value = value.replace("đ", "d").replace("Đ", "d")
    value = value.replace("Ĺľ", "z").replace("Â˛", "2").replace("%%C", " ")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower()
    value = re.sub(r"[^a-z0-9/]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _score_anchor_match(anchor_name: str, text: str, layer: str, keywords: list[str]) -> float:
    normalized_text = _normalize_text(text)
    normalized_layer = _normalize_text(layer)
    if not normalized_text:
        return 0.0

    context = ANCHOR_CONTEXT.get(anchor_name, {})
    excluded = [_normalize_text(item) for item in context.get("excluded", [])]
    if any(item and item in normalized_text for item in excluded):
        return 0.0

    score = 0.0
    normalized_keywords = [_normalize_text(item) for item in keywords]
    for keyword in normalized_keywords:
        if keyword and keyword in normalized_text:
            score = max(score, len(keyword) / max(8, len(normalized_text)))

    required_any = [_normalize_text(item) for item in context.get("required_any", [])]
    required_all = [_normalize_text(item) for item in context.get("required_all", [])]
    preferred = [_normalize_text(item) for item in context.get("preferred", [])]

    if required_all and not all(item in normalized_text for item in required_all if item):
        return 0.0

    if required_any:
        required_hits = sum(1 for item in required_any if item and item in normalized_text)
        if required_hits == 0:
            return 0.0
        score += 0.2 * required_hits

    preferred_hits = sum(1 for item in preferred if item and item in normalized_text)
    score += 0.08 * preferred_hits

    if anchor_name.startswith("k0"):
        tokens = normalized_text.split()
        if anchor_name not in tokens and anchor_name not in normalized_text:
            return 0.0
        score += 0.4

    if "10kv" in normalized_text or "trafostanica" in normalized_text or "ts" in normalized_text.split():
        score += 0.05
    if "project" in normalized_layer or "10kv" in normalized_layer or "elektro" in normalized_layer:
        score += 0.05

    return score


def ensure_layers(doc: ezdxf.document.Drawing, layers: Iterable[str]) -> None:
    existing = {layer.dxf.name for layer in doc.layers}
    for name in layers:
        if name == PROJECT_TEXT_LAYER:
            if name not in existing:
                doc.layers.add(name, dxfattribs=KRSTAC_TEXT_LAYER_ATTRS)
                existing.add(name)
            else:
                layer = doc.layers.get(name)
                layer.color = KRSTAC_TEXT_LAYER_ATTRS["color"]
                layer.dxf.linetype = KRSTAC_TEXT_LAYER_ATTRS["linetype"]
                layer.dxf.lineweight = KRSTAC_TEXT_LAYER_ATTRS["lineweight"]
            continue
        if name == PROJECT_LEADER_LAYER:
            if name not in existing:
                doc.layers.add(name, dxfattribs=KRSTAC_LEADER_LAYER_ATTRS)
                existing.add(name)
            else:
                layer = doc.layers.get(name)
                layer.color = KRSTAC_LEADER_LAYER_ATTRS["color"]
                layer.dxf.linetype = KRSTAC_LEADER_LAYER_ATTRS["linetype"]
                layer.dxf.lineweight = KRSTAC_LEADER_LAYER_ATTRS["lineweight"]
            continue
        if name not in existing:
            doc.layers.add(name)
            existing.add(name)


def ensure_text_styles(doc: ezdxf.document.Drawing) -> None:
    existing = {style.dxf.name for style in doc.styles}
    if "VIP" not in existing:
        doc.styles.add("VIP", font="arial.ttf", dxfattribs={"width": 1.0})


def add_plan_overlay(doc: ezdxf.document.Drawing, design_dict: dict) -> None:
    output_route_layers = {
        (output.get("code") or str(output.get("index"))).strip(): _project_route_layer_dict(doc, output)
        for output in design_dict.get("outputs", [])
    }
    output_text_layers = {
        (output.get("code") or str(output.get("index"))).strip(): _project_text_layer_dict(doc, output)
        for output in design_dict.get("outputs", [])
    }
    route_layers = {
        output_route_layers[(output.get("code") or str(output.get("index"))).strip()]
        for output in design_dict.get("outputs", [])
    }
    ensure_layers(doc, [*route_layers, *output_text_layers.values(), PROJECT_LEADER_LAYER])
    ensure_text_styles(doc)
    msp = doc.modelspace()
    outputs = design_dict.get("outputs", [])
    anchors = design_dict.get("anchors", {})
    route_segments = design_dict.get("route_segments", [])

    if not outputs:
        return

    routes_by_code = {
        (segment.get("output_code") or str(segment.get("output_index"))): segment
        for segment in route_segments
        if segment.get("points")
    }

    for output in outputs:
        code = (output.get("code") or str(output.get("index"))).strip()
        segment = routes_by_code.get(code)
        if not segment:
            continue

        route_layer = segment["layer"]
        route_attribs = _route_entity_attribs(doc, route_layer)
        route_attribs["layer"] = route_layer
        msp.add_lwpolyline(segment["points"], dxfattribs=route_attribs)
        _add_terminal_markers(msp, doc, output, anchors, segment["points"], route_layer)

        for note_index, (note_text, note_point, target_point) in enumerate(_build_project_notes(doc, output, segment), start=1):
            text_layer = output_text_layers[code]
            text_attribs = _mtext_entity_attribs(doc, text_layer)
            _add_note_mleader(
                msp,
                doc,
                code,
                note_index,
                note_text,
                note_point,
                target_point,
                text_layer=text_layer,
                char_height=float(text_attribs.get("char_height", 6.0)),
            )


def _route_entity_attribs(doc: ezdxf.document.Drawing, layer: str) -> dict:
    if layer in KRSTAC_ROUTE_ENTITY_ATTRS_BY_LAYER:
        return dict(KRSTAC_ROUTE_ENTITY_ATTRS_BY_LAYER[layer])
    exemplar = _find_first_entity_on_layer(doc, layer, {"LWPOLYLINE", "LINE", "POLYLINE"})
    if exemplar is None:
        return {"color": 256, "linetype": "BYLAYER", "lineweight": -1}
    return {
        "color": exemplar.dxf.get("color", 256),
        "linetype": exemplar.dxf.get("linetype", "BYLAYER"),
        "lineweight": exemplar.dxf.get("lineweight", -1),
    }


def _mtext_entity_attribs(doc: ezdxf.document.Drawing, layer: str) -> dict:
    if layer == PROJECT_TEXT_LAYER:
        return dict(KRSTAC_TEXT_ENTITY_ATTRS)
    exemplar = _find_first_entity_on_layer(doc, layer, {"MTEXT"})
    if exemplar is None:
        layer_color = 256
        layer_linetype = "BYLAYER"
        layer_lineweight = -1
        if layer in doc.layers:
            layer_obj = doc.layers.get(layer)
            layer_color = layer_obj.color if hasattr(layer_obj, "color") else 256
            layer_linetype = layer_obj.dxf.get("linetype", "BYLAYER")
            layer_lineweight = layer_obj.dxf.get("lineweight", -1)
        return {
            "color": layer_color,
            "linetype": layer_linetype,
            "lineweight": layer_lineweight,
            "char_height": 5.0,
            "style": "Standard",
            "attachment_point": 7,
        }
    return {
        "color": exemplar.dxf.get("color", 256),
        "linetype": exemplar.dxf.get("linetype", "BYLAYER"),
        "lineweight": exemplar.dxf.get("lineweight", -1),
        "char_height": exemplar.dxf.get("char_height", 5.0),
        "style": exemplar.dxf.get("style", "VIP"),
        "attachment_point": exemplar.dxf.get("attachment_point", 7),
    }


def _leader_entity_attribs(doc: ezdxf.document.Drawing, layer: str) -> dict:
    leader_layer = PROJECT_LEADER_LAYER if layer == PROJECT_TEXT_LAYER else layer
    if leader_layer == PROJECT_LEADER_LAYER:
        layer_obj = doc.layers.get(leader_layer) if leader_layer in doc.layers else None
        return {
            "color": layer_obj.color if layer_obj is not None else KRSTAC_LEADER_LAYER_ATTRS["color"],
            "linetype": layer_obj.dxf.get("linetype", KRSTAC_LEADER_LAYER_ATTRS["linetype"]) if layer_obj is not None else KRSTAC_LEADER_LAYER_ATTRS["linetype"],
            "lineweight": layer_obj.dxf.get("lineweight", KRSTAC_LEADER_LAYER_ATTRS["lineweight"]) if layer_obj is not None else KRSTAC_LEADER_LAYER_ATTRS["lineweight"],
        }
    exemplar = _find_first_entity_on_layer(doc, leader_layer, {"MTEXT", "TEXT", "LWPOLYLINE", "LINE", "SOLID"})
    if exemplar is None:
        return {"color": 256, "linetype": "BYLAYER", "lineweight": -1}
    return {
        "color": exemplar.dxf.get("color", 256),
        "linetype": exemplar.dxf.get("linetype", "BYLAYER"),
        "lineweight": exemplar.dxf.get("lineweight", -1),
    }


def _find_first_entity_on_layer(doc: ezdxf.document.Drawing, layer: str, kinds: set[str]):
    for entity in doc.modelspace():
        if entity.dxftype() not in kinds:
            continue
        if not entity.dxf.hasattr("layer") or entity.dxf.layer != layer:
            continue
        return entity
    return None


def build_route_segments(design: DesignModel) -> list:
    segments = []
    benchmarks: list[dict] = []
    ts_center = _compute_ts_center(design)
    doc = open_document(design.source_dxf)
    for output in design.outputs:
        output_code = (output.code or str(output.index)).strip()
        hinted_points = design.route_hints.get(output_code)
        if hinted_points and len(hinted_points) >= 2:
            hinted_metrics = _route_quality_metrics(hinted_points)
            segments.append(
                RouteSegment(
                    output_index=output.index,
                    output_code=output.code,
                    layer=_project_route_layer_for_output(doc, output),
                    points=hinted_points,
                    approx_length=_polyline_length(hinted_points),
                    note=output.title,
                    source_kind="manual_route_hint",
                )
            )
            benchmarks.append(
                {
                    "code": output_code,
                    "source_kind": "manual_route_hint",
                    **hinted_metrics,
                }
            )
            continue

        start_group, start_lookup_name = _output_anchor_lookup(design, output, "start")
        end_group, end_lookup_name = _output_anchor_lookup(design, output, "end")
        start = _select_anchor_candidate(
            start_group,
            ts_center,
            role="start",
            output=output,
            point_name=start_lookup_name or output.start_point,
        )
        if start is None and ts_center is not None:
            start = Anchor(
                name="ts_center",
                layer="",
                x=ts_center[0],
                y=ts_center[1],
                text="computed TS center",
                score=0.0,
            )
        end = _select_anchor_candidate(
            end_group,
            ts_center,
            role="end",
            output=output,
            start_anchor=start,
            point_name=end_lookup_name or output.end_point,
        )
        if start is None or end is None:
            continue
        points, source_kind, measured_length = _build_guided_route(doc, design, output, start, end)
        if len(points) < 2:
            continue
        sanitized_points = _trim_abnormal_endpoint_spikes(points)
        if len(sanitized_points) >= 2 and sanitized_points != points:
            design.warnings.append(
                f"Izvod {output_code or output.index}: ruta je skracena zbog abnormalnog krajnjeg skoka."
            )
            points = sanitized_points
            source_kind = f"{source_kind}+endpoint_sanitized"
            measured_length = None
        metrics = _route_quality_metrics(points)
        approx_length = measured_length if measured_length is not None else 0.0
        if measured_length is None:
            for a, b in zip(points, points[1:]):
                approx_length += math.dist(a, b)
        segments.append(
            RouteSegment(
                output_index=output.index,
                output_code=output.code,
                layer=_project_route_layer_for_output(doc, output),
                points=points,
                approx_length=approx_length,
                note=output.title,
                source_kind=source_kind,
            )
        )
        benchmarks.append(
            {
                "code": output_code,
                "source_kind": source_kind,
                **metrics,
            }
        )
    design.route_benchmarks = benchmarks
    return segments


def _trim_abnormal_endpoint_spikes(
    points: list[tuple[float, float]],
    *,
    min_jump: float = 150.0,
    ratio: float = 4.0,
) -> list[tuple[float, float]]:
    if len(points) < 4:
        return points
    cleaned = list(points)
    changed = True
    while changed and len(cleaned) >= 4:
        changed = False
        segment_lengths = [math.dist(a, b) for a, b in zip(cleaned, cleaned[1:])]
        if not segment_lengths:
            break
        median = sorted(segment_lengths)[len(segment_lengths) // 2]
        threshold = max(min_jump, median * ratio)
        if segment_lengths and segment_lengths[-1] > threshold:
            cleaned = cleaned[:-1]
            changed = True
            continue
        if segment_lengths and segment_lengths[0] > threshold:
            cleaned = cleaned[1:]
            changed = True
    return cleaned if len(cleaned) >= 2 else points


def _route_quality_metrics(points: list[tuple[float, float]]) -> dict:
    if len(points) < 2:
        return {
            "vertex_count": len(points),
            "length": 0.0,
            "max_segment": 0.0,
            "median_segment": 0.0,
            "start_jump": 0.0,
            "end_jump": 0.0,
        }
    segment_lengths = [math.dist(a, b) for a, b in zip(points, points[1:])]
    ordered = sorted(segment_lengths)
    median = ordered[len(ordered) // 2]
    return {
        "vertex_count": len(points),
        "length": _polyline_length(points),
        "max_segment": max(segment_lengths),
        "median_segment": median,
        "start_jump": segment_lengths[0],
        "end_jump": segment_lengths[-1],
    }


def _guide_layer_penalty(layer: str, preferred_layers: list[str]) -> float:
    if not preferred_layers:
        return 0.0
    try:
        index = preferred_layers.index(layer)
    except ValueError:
        return 35.0
    return float(index) * 8.0


def _route_terminal_jump_penalty(points: list[tuple[float, float]], max_terminal_jump: float) -> float:
    if len(points) < 3:
        return 0.0
    start_jump = math.dist(points[0], points[1])
    end_jump = math.dist(points[-2], points[-1])
    penalty = 0.0
    if start_jump > max_terminal_jump:
        penalty += (start_jump - max_terminal_jump) * 20.0
    if end_jump > max_terminal_jump:
        penalty += (end_jump - max_terminal_jump) * 20.0
    return penalty


def _compute_ts_center(design: DesignModel) -> tuple[float, float] | None:
    start_points = []
    for key in ("k03", "k04", "k05", "k06"):
        candidates = design.anchors.get(key, [])
        if candidates:
            start_points.append((candidates[0].x, candidates[0].y))
    if start_points:
        return (
            sum(p[0] for p in start_points) / len(start_points),
            sum(p[1] for p in start_points) / len(start_points),
        )
    ts_candidates = design.anchors.get("ts 35/10kv krstac", [])
    if ts_candidates:
        return (ts_candidates[0].x, ts_candidates[0].y)
    return None


def _output_route_musts(output) -> dict:
    value = getattr(output, "route_musts", None) or {}
    return dict(value)


def _output_anchor_lookup(
    design: DesignModel,
    output,
    role: str,
) -> tuple[list[Anchor], str | None]:
    route_musts = _output_route_musts(output)
    raw_name = getattr(output, f"{role}_point", None)
    anchor_type = str(route_musts.get(f"{role}_anchor_type") or "")
    physical_target = route_musts.get(f"{role}_physical_target")
    functional_target = route_musts.get(f"{role}_functional_target")
    anchor_region = route_musts.get(f"{role}_anchor_region")

    ordered_names: list[str] = []
    if anchor_type in {
        "existing_uzb_stub",
        "existing_uzb_stub_exterior_connection",
        "new_uzb_stub",
        "new_uzb_stub_exterior_connection",
    }:
        ordered_names.extend([physical_target, raw_name, functional_target])
    elif anchor_type in {
        "existing_substation",
        "existing_substation_exterior_connection",
        "shaft_connection",
    }:
        ordered_names.extend([physical_target, functional_target, raw_name])
    elif anchor_type == "ts_switch_cell":
        ordered_names.extend([raw_name, anchor_region, functional_target])
    else:
        ordered_names.extend([raw_name, physical_target, functional_target, anchor_region])

    keys: list[str] = []
    seen_keys: set[str] = set()
    primary_name: str | None = None
    for name in ordered_names:
        if not name:
            continue
        text = str(name).strip()
        if not text:
            continue
        if primary_name is None:
            primary_name = text
        for key in {text.lower().strip(), _normalize_text(text)}:
            if not key or key in seen_keys:
                continue
            seen_keys.add(key)
            keys.append(key)

    merged: list[Anchor] = []
    seen_anchor_ids: set[tuple[float, float, str, str]] = set()
    for key in keys:
        for anchor in design.anchors.get(key, []):
            anchor_id = (round(anchor.x, 3), round(anchor.y, 3), anchor.layer, anchor.text)
            if anchor_id in seen_anchor_ids:
                continue
            seen_anchor_ids.add(anchor_id)
            merged.append(anchor)
    merged.sort(key=lambda anchor: anchor.score, reverse=True)
    return merged, primary_name or raw_name


def _select_anchor_candidate(
    candidates: list[Anchor],
    ts_center: tuple[float, float] | None,
    role: str,
    output: DesignModel | object,
    start_anchor: Anchor | None = None,
    point_name: str | None = None,
) -> Anchor | None:
    if not candidates:
        return None
    if ts_center is None:
        return candidates[0]

    point_key = (point_name or "").lower().strip()
    near_ts_point = point_key.startswith("k0") or point_key == "ts 35/10kv krstac"
    far_ts_point = bool(point_key) and not near_ts_point

    ranked = []
    for candidate in candidates:
        dist_ts = math.dist((candidate.x, candidate.y), ts_center)
        value = candidate.score
        text_n = _normalize_text(candidate.text)
        if role == "start":
            if near_ts_point:
                value -= dist_ts / 5000.0
            elif far_ts_point:
                value += dist_ts / 4000.0
            else:
                value -= dist_ts / 5000.0
            if near_ts_point and any(token in text_n for token in ["izvodna", "celija", "k03", "k04", "k05", "k06"]):
                value += 0.25
        else:
            if near_ts_point:
                value -= dist_ts / 5000.0
            else:
                value += dist_ts / 4000.0
            if start_anchor is not None:
                value += math.dist((candidate.x, candidate.y), (start_anchor.x, start_anchor.y)) / 5000.0
            if any(token in text_n for token in ["trafostanica", "mbts", "uz stub", "uzb", "lisice", "fapromal", "kula", "puhovo"]):
                value += 0.1
        ranked.append((value, candidate))

    ranked.sort(key=lambda item: item[0], reverse=True)
    return ranked[0][1]


def _build_guided_route(
    doc: ezdxf.document.Drawing,
    design: DesignModel,
    output,
    start: Anchor,
    end: Anchor,
) -> tuple[list[tuple[float, float]], str, float | None]:
    output_index = output.index
    output_code = (getattr(output, "code", None) or "").strip()
    route_musts = _output_route_musts(output)
    must_run_parallel_to_road = bool(route_musts.get("must_run_parallel_to_road"))
    must_follow_existing_network = bool(route_musts.get("must_follow_existing_network"))
    must_use_transport_corridor = bool(route_musts.get("must_use_transport_corridor"))
    must_follow_named_parcels = bool(route_musts.get("must_follow_named_parcels"))
    is_generic_output = bool(output_code and not output_code.isdigit() and output_code not in OUTPUT_GUIDE_LAYERS_BY_CODE)
    if is_generic_output:
        fallback = [(start.x, start.y), (end.x, start.y), (end.x, end.y)]
        fallback_length = _polyline_length(fallback)
        road_parallel_required = must_run_parallel_to_road or (
            (getattr(output, "route_mode", None) or "").strip().lower() == "underground"
            and any(constraint.category == "roads" for constraint in design.constraints)
        )
        corridor_points, corridor_source, corridor_length = _build_corridor_fallback_route(doc, design, output, start, end)
        if corridor_points and corridor_length is not None:
            if road_parallel_required or corridor_length <= fallback_length * 1.20:
                return corridor_points, corridor_source, corridor_length
        if road_parallel_required or must_follow_existing_network or must_follow_named_parcels:
            design.warnings.append(f"Izvod {output_code or output.index}: nije pronadjena ruta koja zadovoljava must koridor.")
            return [], "must_unsatisfied", None
        return fallback, "orthogonal_fallback", fallback_length
    if output_code and output_code in OUTPUT_GUIDE_LAYERS_BY_CODE:
        guide_layers = OUTPUT_GUIDE_LAYERS_BY_CODE[output_code]
    elif is_generic_output:
        guide_layers = _generic_output_guide_layers(doc, output)
    else:
        guide_layers = OUTPUT_GUIDE_LAYERS.get(output_index, [])
    priors = FINAL_LEARNED_ROUTE_PRIORS_BY_CODE.get(output_code, {})
    preferred_layers = priors.get("preferred_layers", [])
    max_terminal_jump = float(priors.get("max_terminal_jump", 180.0))
    for guide_layer in guide_layers:
        if (must_run_parallel_to_road or must_use_transport_corridor) and not _is_road_corridor_layer(guide_layer):
            continue
        if must_follow_existing_network and not _is_existing_network_layer(guide_layer) and not _is_road_corridor_layer(guide_layer):
            continue
        candidates = _collect_guide_geometries(doc, [guide_layer])
        if not candidates:
            continue
        route_candidates = []
        graph_route = _build_graph_route(candidates, (start.x, start.y), (end.x, end.y), output_index, output_code)
        if graph_route:
            attached = _attach_anchors(graph_route, start, end)
            attached_length = _polyline_length(attached)
            core_length = _polyline_length(graph_route)
            parcel_penalty, parcel_ok = _route_parcel_corridor_metrics(doc, attached, route_musts)
            graph_score = (
                _expected_length_score(output_index, attached_length, output_code) * 10.0
                + max(0.0, attached_length - core_length) * 0.5
                + _guide_layer_penalty(guide_layer, preferred_layers)
                + _route_terminal_jump_penalty(attached, max_terminal_jump)
                + parcel_penalty
            )
            if not must_follow_named_parcels or parcel_ok or parcel_penalty <= 600.0:
                route_candidates.append((graph_score, attached, f"guide_graph:{guide_layer}", attached_length))
        component_routes = _collect_component_routes(candidates)
        if component_routes and not is_generic_output:
            ranked_components = []
            component_infos = []
            for component_points, component_length in component_routes:
                start_snap = _min_vertex_distance((start.x, start.y), component_points)
                end_snap = _min_vertex_distance((end.x, end.y), component_points)
                component_infos.append(
                    {
                        "points": component_points,
                        "length": component_length,
                        "start_snap": start_snap,
                        "end_snap": end_snap,
                    }
                )
            max_bundle_size = min(3, len(component_infos))
            for bundle_size in range(1, max_bundle_size + 1):
                for bundle in combinations(component_infos, bundle_size):
                    bundle_length = sum(item["length"] for item in bundle)
                    bundle_start_snap = min(item["start_snap"] for item in bundle)
                    bundle_end_snap = min(item["end_snap"] for item in bundle)
                    expected_score = _expected_length_score(output_index, bundle_length, output_code)
                    score = (
                        expected_score * 10.0
                        + (bundle_start_snap + bundle_end_snap) * 0.02
                        + _guide_layer_penalty(guide_layer, preferred_layers)
                    )
                    overlay_points = max(bundle, key=lambda item: item["length"])["points"]
                    attached_overlay = _attach_anchors(overlay_points, start, end)
                    attached_length = _polyline_length(attached_overlay)
                    parcel_penalty, parcel_ok = _route_parcel_corridor_metrics(doc, attached_overlay, route_musts)
                    if must_follow_named_parcels and not parcel_ok and parcel_penalty > 600.0:
                        continue
                    ranked_components.append((score + parcel_penalty, overlay_points, bundle_length, bundle_size))
            ranked_components.sort(key=lambda item: item[0])
            if ranked_components:
                best_score, best_component, best_component_length, bundle_size = ranked_components[0]
            else:
                best_score = best_component_length = bundle_size = None
                best_component = None
            if best_component is not None and _expected_length_penalty(output_index, best_component_length, output_code) <= 250.0:
                attached_component = _attach_anchors(best_component, start, end)
                attached_component_length = _polyline_length(attached_component)
                route_candidates.append(
                    (
                        best_score + _route_terminal_jump_penalty(attached_component, max_terminal_jump),
                        attached_component,
                        f"guide_component:{guide_layer}:bundle{bundle_size}",
                        attached_component_length,
                    )
                )
        ranked = []
        for points in candidates:
            if len(points) < 2:
                continue
            segment_points = _extract_relevant_subpath(points, (start.x, start.y), (end.x, end.y))
            first = segment_points[0]
            last = segment_points[-1]
            endpoint_cost = min(
                math.dist((start.x, start.y), first) + math.dist((end.x, end.y), last),
                math.dist((start.x, start.y), last) + math.dist((end.x, end.y), first),
            )
            path_cost = _min_vertex_distance((start.x, start.y), segment_points) + _min_vertex_distance((end.x, end.y), segment_points)
            route_points = segment_points
            if math.dist((start.x, start.y), route_points[-1]) < math.dist((start.x, start.y), route_points[0]):
                route_points = list(reversed(route_points))
            attached = _attach_anchors(route_points, start, end)
            route_length = _polyline_length(attached)
            expected_penalty = _expected_length_penalty(output_index, route_length, output_code)
            parcel_penalty, parcel_ok = _route_parcel_corridor_metrics(doc, attached, route_musts)
            if must_follow_named_parcels and not parcel_ok and parcel_penalty > 600.0:
                continue
            ranked.append((
                endpoint_cost
                + path_cost
                + expected_penalty
                + _guide_layer_penalty(guide_layer, preferred_layers)
                + _route_terminal_jump_penalty(attached, max_terminal_jump),
                parcel_penalty,
                attached,
                route_length,
            ))
        if ranked:
            ranked.sort(key=lambda item: item[0] + item[1])
            _score, parcel_penalty, best, route_length = ranked[0]
            route_candidates.append((_score + parcel_penalty, best, f"guide_geometry:{guide_layer}", route_length))
        if route_candidates:
            route_candidates.sort(key=lambda item: item[0])
            _score, best_points, best_source, measured_length = route_candidates[0]
            return best_points, best_source, measured_length
    fallback = [(start.x, start.y), (end.x, start.y), (end.x, end.y)]
    fallback_length = _polyline_length(fallback)
    corridor_points, corridor_source, corridor_length = _build_corridor_fallback_route(doc, design, output, start, end)
    if corridor_points and corridor_length is not None:
        if corridor_length <= fallback_length * 1.05:
            return corridor_points, corridor_source, corridor_length
    if must_run_parallel_to_road or must_follow_existing_network or must_use_transport_corridor or must_follow_named_parcels:
        design.warnings.append(f"Izvod {output_code or output.index}: must koridor iz zadatka/uslova nije pronadjen u podlozi.")
        return [], "must_unsatisfied", None
    return fallback, "orthogonal_fallback", fallback_length


def _generic_output_guide_layers(doc: ezdxf.document.Drawing, output) -> list[str]:
    route_mode = (getattr(output, "route_mode", None) or "").strip().lower()
    if route_mode == "overhead":
        candidates = ["T2_Elektroene", "L2_Elektroene", "Mreza", "Asfaltni put", "Makadamski put"]
    else:
        candidates = ["L2_Elektroene", "T2_Elektroene", "Mreza", "Asfaltni put", "Makadamski put", "Most"]
    return [layer for layer in candidates if layer in doc.layers]


def _project_route_layer(output) -> str:
    code = (getattr(output, "code", None) or str(getattr(output, "index", ""))).strip()
    return PROJECT_OUTPUT_ROUTE_LAYERS_BY_CODE.get(code, "EL_ROUTE")


def _project_route_layer_dict(doc: ezdxf.document.Drawing, output: dict) -> str:
    code = (output.get("code") or str(output.get("index"))).strip()
    mapped = PROJECT_OUTPUT_ROUTE_LAYERS_BY_CODE.get(code)
    if mapped:
        return mapped
    route_mode = (output.get("route_mode") or "").strip().lower()
    return _generic_route_layer_from_mode(doc, route_mode)


def _project_text_layer_dict(doc: ezdxf.document.Drawing, output: dict) -> str:
    route_mode = (output.get("route_mode") or "").strip().lower()
    return _generic_text_layer_from_mode(doc, route_mode)


def _project_route_layer_for_output(doc: ezdxf.document.Drawing, output) -> str:
    code = (getattr(output, "code", None) or str(getattr(output, "index", ""))).strip()
    mapped = PROJECT_OUTPUT_ROUTE_LAYERS_BY_CODE.get(code)
    if mapped:
        return mapped
    route_mode = (getattr(output, "route_mode", None) or "").strip().lower()
    return _generic_route_layer_from_mode(doc, route_mode)


def _generic_route_layer_from_mode(doc: ezdxf.document.Drawing, route_mode: str) -> str:
    if route_mode == "overhead":
        candidates = ["T2_Elektroene", "L2_Elektroene", "EL_ROUTE"]
    else:
        candidates = ["L2_Elektroene", "T2_Elektroene", "EL_ROUTE"]
    for layer in candidates:
        if layer == "EL_ROUTE" or layer in doc.layers:
            return layer
    return "EL_ROUTE"


def _generic_text_layer_from_mode(doc: ezdxf.document.Drawing, route_mode: str) -> str:
    return PROJECT_TEXT_LAYER


def _pick_note_insert(
    points: list[tuple[float, float]],
    offset: float = 18.0,
    side: float = 1.0,
    along: float = 0.0,
) -> tuple[float, float]:
    if len(points) < 2:
        return points[0] if points else (0.0, 0.0)

    best_start = points[0]
    best_end = points[1]
    best_length = -1.0
    for a, b in zip(points, points[1:]):
        seg_len = math.dist(a, b)
        if seg_len > best_length:
            best_start = a
            best_end = b
            best_length = seg_len

    mid_x = (best_start[0] + best_end[0]) / 2.0
    mid_y = (best_start[1] + best_end[1]) / 2.0
    dx = best_end[0] - best_start[0]
    dy = best_end[1] - best_start[1]
    seg_len = math.hypot(dx, dy) or 1.0
    tangent_x = dx / seg_len
    tangent_y = dy / seg_len
    normal_x = -dy / seg_len
    normal_y = dx / seg_len
    return (
        mid_x + tangent_x * along + normal_x * offset * side,
        mid_y + tangent_y * along + normal_y * offset * side,
    )


def _estimate_note_rect(
    note_point: tuple[float, float],
    note_text: str,
    char_height: float = 6.0,
) -> tuple[float, float, float, float]:
    lines = [line for line in note_text.split("\\P") if line] or [note_text]
    longest = max(len(line) for line in lines)
    width = max(24.0, longest * char_height * 0.58)
    height = max(char_height * 1.2, len(lines) * char_height * 1.15)
    x0 = float(note_point[0])
    y1 = float(note_point[1])
    x1 = x0 + width
    y0 = y1 - height
    return (x0, y0, x1, y1)


def _rect_contains_point(rect: tuple[float, float, float, float], point: tuple[float, float], margin: float = 0.0) -> bool:
    x0, y0, x1, y1 = rect
    px, py = point
    return (x0 - margin) <= px <= (x1 + margin) and (y0 - margin) <= py <= (y1 + margin)


def _segment_maybe_hits_rect(
    a: tuple[float, float],
    b: tuple[float, float],
    rect: tuple[float, float, float, float],
    margin: float = 0.0,
) -> bool:
    x0, y0, x1, y1 = rect
    min_x = min(a[0], b[0])
    max_x = max(a[0], b[0])
    min_y = min(a[1], b[1])
    max_y = max(a[1], b[1])
    if max_x < x0 - margin or min_x > x1 + margin or max_y < y0 - margin or min_y > y1 + margin:
        return False
    return True


def _note_rect_collides_with_route(
    rect: tuple[float, float, float, float],
    route_points: list[tuple[float, float]],
    margin: float = 4.0,
) -> bool:
    for a, b in zip(route_points, route_points[1:]):
        if _segment_maybe_hits_rect(a, b, rect, margin=margin):
            return True
    return False


def _note_rect_collides_with_entities(
    doc: ezdxf.document.Drawing,
    rect: tuple[float, float, float, float],
    ignore_layers: set[str] | None = None,
    margin: float = 2.0,
) -> bool:
    ignore_layers = ignore_layers or set()
    for entity in doc.modelspace():
        layer = entity.dxf.get("layer", "0")
        if layer in ignore_layers:
            continue
        points = _entity_points(entity)
        if entity.dxftype() in {"TEXT", "MTEXT", "INSERT"}:
            insert = entity.dxf.get("insert", (0.0, 0.0, 0.0))
            points = [(float(insert[0]), float(insert[1]))]
        for point in points:
            if _rect_contains_point(rect, point, margin=margin):
                return True
        if len(points) >= 2:
            for a, b in zip(points, points[1:]):
                if _segment_maybe_hits_rect(a, b, rect, margin=margin):
                    return True
    return False


def _resolve_note_insert(
    doc: ezdxf.document.Drawing,
    route_points: list[tuple[float, float]],
    note_text: str,
    target_point: tuple[float, float],
    preferred_point: tuple[float, float],
    *,
    char_height: float = 6.0,
    side_preferences: list[float] | None = None,
    along_preferences: list[float] | None = None,
    offset_preferences: list[float] | None = None,
    max_distance_from_target: float | None = None,
) -> tuple[float, float]:
    ignore_layers = {PROJECT_TEXT_LAYER, PROJECT_LEADER_LAYER}
    side_preferences = side_preferences or [1.0, -1.0]
    along_preferences = along_preferences or [0.0, -28.0, 28.0, -56.0, 56.0]
    offset_preferences = offset_preferences or [62.0, 78.0, 94.0, 110.0]

    candidates = [preferred_point]
    for side in side_preferences:
        for offset in offset_preferences:
            for along in along_preferences:
                candidates.append(_pick_note_insert(route_points, offset=offset, side=side, along=along))

    best = preferred_point
    best_score = float("inf")
    for candidate in candidates:
        rect = _estimate_note_rect(candidate, note_text, char_height=char_height)
        route_hit = _note_rect_collides_with_route(rect, route_points, margin=4.0)
        target_hit = _rect_contains_point(rect, target_point, margin=8.0)
        entity_hit = _note_rect_collides_with_entities(doc, rect, ignore_layers=ignore_layers, margin=1.0)
        score = 0.0
        if route_hit:
            score += 1000.0
        if target_hit:
            score += 1000.0
        if entity_hit:
            score += 150.0
        preferred_distance = math.dist(candidate, preferred_point)
        target_distance = math.dist(candidate, target_point)
        score += preferred_distance * 0.1
        if max_distance_from_target is not None and target_distance > max_distance_from_target:
            score += 800.0 + (target_distance - max_distance_from_target) * 10.0
        if score < best_score:
            best = candidate
            best_score = score
            if score <= 0.01:
                break
    return best


def _resolve_terminal_note_insert(
    doc: ezdxf.document.Drawing,
    route_points: list[tuple[float, float]],
    note_text: str,
    target_point: tuple[float, float],
    preferred_point: tuple[float, float],
    *,
    char_height: float = 6.0,
) -> tuple[float, float]:
    rect_pref = _estimate_note_rect(preferred_point, note_text, char_height=char_height)
    width = rect_pref[2] - rect_pref[0]
    height = rect_pref[3] - rect_pref[1]
    tx, ty = target_point
    is_start = route_points and math.dist(target_point, route_points[0]) <= math.dist(target_point, route_points[-1])
    if is_start and len(route_points) >= 2:
        neighbor = route_points[1]
    elif len(route_points) >= 2:
        neighbor = route_points[-2]
    else:
        neighbor = (tx - 1.0, ty)

    dx = tx - neighbor[0]
    dy = ty - neighbor[1]
    seg_len = math.hypot(dx, dy) or 1.0
    tangent_x = dx / seg_len
    tangent_y = dy / seg_len
    back_x = -tangent_x
    back_y = -tangent_y
    normal_x = -tangent_y
    normal_y = tangent_x

    def mk(backoff: float, lateral: float, above: bool) -> tuple[float, float]:
        base_x = tx + back_x * backoff + normal_x * lateral
        base_y = ty + back_y * backoff + normal_y * lateral
        if above:
            return (base_x, base_y + height + 8.0)
        return (base_x, base_y - 8.0)

    candidates = [
        mk(26.0, 22.0, True),
        mk(26.0, -22.0, True),
        mk(26.0, 22.0, False),
        mk(26.0, -22.0, False),
        mk(40.0, 28.0, True),
        mk(40.0, -28.0, True),
        mk(40.0, 28.0, False),
        mk(40.0, -28.0, False),
        preferred_point,
    ]

    best = preferred_point
    best_score = float("inf")
    ignore_layers = {PROJECT_TEXT_LAYER, PROJECT_LEADER_LAYER}
    for candidate in candidates:
        rect = _estimate_note_rect(candidate, note_text, char_height=char_height)
        route_hit = _note_rect_collides_with_route(rect, route_points, margin=4.0)
        target_hit = _rect_contains_point(rect, target_point, margin=10.0)
        entity_hit = _note_rect_collides_with_entities(doc, rect, ignore_layers=ignore_layers, margin=1.0)
        score = 0.0
        if route_hit:
            score += 2000.0
        if target_hit:
            score += 2000.0
        if entity_hit:
            score += 400.0
        score += math.dist(candidate, preferred_point) * 0.2
        score += math.dist(candidate, target_point) * 0.05
        if score < best_score:
            best = candidate
            best_score = score
            if score <= 0.01:
                break
    return best


def _fixed_terminal_note_insert(
    route_points: list[tuple[float, float]],
    note_text: str,
    target_point: tuple[float, float],
    *,
    char_height: float = 6.0,
    position: str = "end",
) -> tuple[float, float]:
    rect_pref = _estimate_note_rect(target_point, note_text, char_height=char_height)
    width = rect_pref[2] - rect_pref[0]
    height = rect_pref[3] - rect_pref[1]
    tx, ty = target_point

    if len(route_points) >= 2:
        neighbor = route_points[1] if position == "start" else route_points[-2]
    else:
        neighbor = (tx - 1.0, ty)

    dx = tx - neighbor[0]
    dy = ty - neighbor[1]

    # Horizontal-dominant ends get text above and behind the end point.
    if abs(dx) >= abs(dy):
        if dx >= 0:
            return (tx - width - 18.0, ty + height + 12.0)
        return (tx + 18.0, ty + height + 12.0)

    # Vertical-dominant ends get text offset to the clearer side.
    if dy >= 0:
        return (tx + 18.0, ty + height + 8.0)
    return (tx + 18.0, ty - 12.0)




def _build_project_notes(
    doc: ezdxf.document.Drawing,
    output: dict,
    segment: dict,
) -> list[tuple[str, tuple[float, float], tuple[float, float]]]:
    code = (output.get("code") or str(output.get("index"))).strip()
    cable_type = (output.get("cable_type") or "").strip()
    trench = (output.get("trench_profile") or "").strip()
    length = float(segment.get("approx_length") or 0.0)
    if cable_type.endswith(", 10 kV"):
        cable_type = cable_type[:-6].strip()
    if trench:
        trench = trench.replace("(", "").replace(")", "")

    route_points = segment.get("points") or []
    notes: list[tuple[str, tuple[float, float], tuple[float, float]]] = []
    preferred_main = _pick_note_insert(route_points, offset=62.0, side=1.0, along=-28.0)
    main_target = _nearest_point_on_polyline(preferred_main, route_points) if route_points else preferred_main
    stable_start = _stable_route_endpoint(route_points, position="start") if route_points else main_target
    stable_end = _stable_route_endpoint(route_points, position="end") if route_points else main_target

    start_context_target = (
        _stabilize_context_target(
            _resolve_existing_context_target(doc, stable_start, route_points),
            stable_start,
            route_points,
        )
        if route_points
        else main_target
    )
    end_context_target = (
        _stabilize_context_target(
            _resolve_existing_context_target(doc, stable_end, route_points),
            stable_end,
            route_points,
        )
        if route_points
        else main_target
    )

    if code == "1A":
        main_text = (
            f"Novi kablovski vod {cable_type}, 10kV\\P"
            f"u kablovskom rovu {trench}\\P"
            "veza K06 - novi UZB stub broj 1\\P"
            "PVC cevi fi 110mm\\P"
            f"duzina trase {length:.1f}m"
        )
        main_point = _resolve_note_insert(doc, route_points, main_text, main_target, preferred_main)
        notes.append((main_text, main_point, main_target))
        notes.append((
            "Novi UZB stub broj 1, 12m/1600dAN\\P"
            "vrsi se prelaz sa podzemnog 10kV voda na nadzemni vod\\P"
            "ugraditi kablovske zavrsnice 10kV i vertikalni rastavljac\\P"
            "potrebno uraditi uzemljenje stuba",
            _resolve_terminal_note_insert(
                doc,
                route_points,
                "Novi UZB stub broj 1, 12m/1600dAN\\P"
                "vrsi se prelaz sa podzemnog 10kV voda na nadzemni vod\\P"
                "ugraditi kablovske zavrsnice 10kV i vertikalni rastavljac\\P"
                "potrebno uraditi uzemljenje stuba",
                end_context_target,
                _pick_terminal_note_insert(route_points, position="end"),
            ),
            end_context_target,
        ))
        return _append_bridge_note_if_needed(doc, notes, route_points)
    if code == "1B":
        main_text = (
            "Novi nadzemni vod 3 x 1 x Al/c 50/8mm, 10kV\\P"
            "od novog UZB stuba broj 1\\P"
            "veza za DV 10kV \"Puhovo - Krstac\"\\P"
            f"duzina trase {length:.1f}m"
        )
        main_point = _resolve_note_insert(doc, route_points, main_text, main_target, preferred_main)
        notes.append((main_text, main_point, main_target))
        return notes
    if code == "2":
        main_text = (
            f"Novi kablovski vod {cable_type}, 10kV\\P"
            f"u kablovskom rovu {trench}\\P"
            "veza K03 - TS 10/0,4kV \"Krstac - Kula\"\\P"
            "PVC cevi fi 110mm\\P"
            f"duzina trase {length:.1f}m"
        )
        main_point = _resolve_note_insert(doc, route_points, main_text, main_target, preferred_main)
        notes.append((main_text, main_point, main_target))
        notes.append((
            "Postojeca TS 10/0,4kV \"Krstac - Kula\"\\P"
            "veza preko kablovskih zavrsnica 10kV\\P"
            "i vertikalnog rastavljaca sa odvodnicima prenapona",
            _resolve_terminal_note_insert(
                doc,
                route_points,
                "Postojeca TS 10/0,4kV \"Krstac - Kula\"\\P"
                "veza preko kablovskih zavrsnica 10kV\\P"
                "i vertikalnog rastavljaca sa odvodnicima prenapona",
                end_context_target,
                _pick_terminal_note_insert(route_points, position="end"),
            ),
            end_context_target,
        ))
        return _append_bridge_note_if_needed(doc, notes, route_points)
    if code == "3":
        main_text = (
            f"Novi kablovski vod {cable_type}, 10kV\\P"
            f"u kablovskom rovu {trench}\\P"
            "veza K05 - MBTS 10/0,4kV \"Fapromal\"\\P"
            "PVC cevi fi 110mm\\P"
            f"duzina trase {length:.1f}m"
        )
        main_point = _resolve_note_insert(doc, route_points, main_text, main_target, preferred_main)
        notes.append((main_text, main_point, main_target))
        return _append_bridge_note_if_needed(doc, notes, route_points)
    if code == "4":
        main_text = (
            f"Novi kablovski vod {cable_type}, 10kV\\P"
            f"u kablovskom rovu {trench}\\P"
            "veza K04 - PTS 10/0,4kV \"Lisice 3\"\\P"
            "PVC cevi fi 110mm\\P"
            f"duzina trase {length:.1f}m"
        )
        main_point = _resolve_note_insert(doc, route_points, main_text, main_target, preferred_main)
        notes.append((main_text, main_point, main_target))
        notes.append((
            "Postojeci UZ stub 12m/1000dAN\\P"
            "vrsi se prelaz sa podzemnog 10kV voda\\P"
            "na nadzemni dalekovod povezan sa PTS \"Lisice 3\"\\P"
            "ugraditi kablovske zavrsnice 10kV",
            _resolve_terminal_note_insert(
                doc,
                route_points,
                "Postojeci UZ stub 12m/1000dAN\\P"
                "vrsi se prelaz sa podzemnog 10kV voda\\P"
                "na nadzemni dalekovod povezan sa PTS \"Lisice 3\"\\P"
                "ugraditi kablovske zavrsnice 10kV",
                end_context_target,
                _pick_terminal_note_insert(route_points, position="end"),
            ),
            end_context_target,
        ))
        return _append_bridge_note_if_needed(doc, notes, route_points)
    if code == "5":
        main_text = (
            f"Novi kablovski vod {cable_type}, 10kV\\P"
            f"u kablovskom rovu {trench}\\P"
            "veza \"Lisice 3\" - TS 10/0,4kV \"Lisice 1\"\\P"
            "PVC cevi fi 110mm\\P"
            f"duzina trase {length:.1f}m"
        )
        main_point = _resolve_note_insert(doc, route_points, main_text, main_target, preferred_main)
        notes.append((main_text, main_point, main_target))
        notes.append((
            "Novi UZ stub 12m/1000dAN\\P"
            "vrsi se prelaz sa podzemnog 10kV voda na nadzemni vod\\P"
            "veza za TS 10/0,4kV \"Lisice 1\"\\P"
            "ugraditi kablovske zavrsnice i uraditi uzemljenje stuba",
            _resolve_terminal_note_insert(
                doc,
                route_points,
                "Novi UZ stub 12m/1000dAN\\P"
                "vrsi se prelaz sa podzemnog 10kV voda na nadzemni vod\\P"
                "veza za TS 10/0,4kV \"Lisice 1\"\\P"
                "ugraditi kablovske zavrsnice i uraditi uzemljenje stuba",
                end_context_target,
                _pick_terminal_note_insert(route_points, position="end"),
            ),
            end_context_target,
        ))
        return _append_bridge_note_if_needed(doc, notes, route_points)
    if (output.get("route_mode") or "").strip().lower() == "underground":
        start_label = _anchor_label_for_note(output.get("start_point"))
        end_label = _anchor_label_for_note(output.get("end_point"))
        main_text = (
            "Kablovski vod 10kV\\P"
            f"3 x XHE 49-A 1x150mm2\\P"
            f"Rov {trench}\\P"
            "PVC fi 110mm\\P"
            f"L = {length:.1f} m"
        )
        compact_main = _pick_note_insert(route_points, offset=42.0, side=1.0, along=-12.0)
        main_point = _resolve_note_insert(
            doc,
            route_points,
            main_text,
            main_target,
            compact_main,
            offset_preferences=[36.0, 42.0, 54.0, 66.0],
            along_preferences=[-18.0, -12.0, 0.0, 12.0, 18.0],
            side_preferences=[1.0, -1.0],
            max_distance_from_target=170.0,
        )
        notes.append((main_text, main_point, main_target))
        start_note = _generic_terminal_note(output, position="start")
        if start_note:
            notes.append((
                start_note,
                _fixed_terminal_note_insert(
                    route_points,
                    start_note,
                    start_context_target,
                    position="start",
                ),
                start_context_target,
            ))
        end_note = _generic_terminal_note(output, position="end")
        if end_note:
            notes.append((
                end_note,
                _fixed_terminal_note_insert(
                    route_points,
                    end_note,
                    end_context_target,
                    position="end",
                ),
                end_context_target,
            ))
        return _append_bridge_note_if_needed(doc, notes, route_points)
    main_text = f"Izvod {code}\\Pduzina trase {length:.1f}m"
    main_point = _resolve_note_insert(doc, route_points, main_text, main_target, preferred_main)
    notes.append((main_text, main_point, main_target))
    return notes


def _anchor_label_for_note(anchor_name: str | None) -> str:
    if not anchor_name:
        return "prikljucna tacka"
    text = str(anchor_name).strip()
    normalized = _normalize_text(text)
    match = re.match(r"(novi|postojeci)\s+dv\s+stub\s+10\s+kv\s+([0-9/]+)\s+ko\s+([a-z0-9]+)", normalized)
    if match:
        kind, parcel, ko_name = match.groups()
        kind_label = "novi DV stub" if kind == "novi" else "postojeci DV stub"
        return f"{kind_label} {parcel} KO {ko_name.title()}"
    return text


def _generic_terminal_note(output: dict, position: str) -> str | None:
    note_texts = output.get("notes") or []
    start_label = _anchor_label_for_note(output.get("start_point"))
    end_label = _anchor_label_for_note(output.get("end_point"))

    if position == "start":
        lines = [start_label]
        if any("12m/1000dan" in _normalize_text(note) for note in note_texts):
            lines.append("Novi AB stub 12m/1000dAN")
        lines.append("Prelaz sa nadzemnog na podzemni 10kV vod")
        if any("pvc" in _normalize_text(note) or "110mm" in _normalize_text(note) for note in note_texts):
            lines.append("Uvod kabla kroz PVC cevi fi 110mm")
        return "\\P".join(lines)

    if position == "end":
        lines = [end_label]
        if any("12m/315dan" in _normalize_text(note) for note in note_texts):
            lines.append("Postojeci AB stub 12m/315dAN")
            lines.append("Prilagodjenje ili zamena prema dokumentaciji")
        else:
            lines.append("Zavrsno vezivanje 10kV kabla na postojeci stub")
        return "\\P".join(lines)

    return None


def _pick_terminal_note_insert(
    points: list[tuple[float, float]],
    position: str = "end",
) -> tuple[float, float]:
    if len(points) < 2:
        return points[0] if points else (0.0, 0.0)
    if position == "start":
        anchor_index = _stable_route_endpoint_index(points, position="start")
        anchor = points[anchor_index]
        neighbor_index = min(len(points) - 1, anchor_index + 1)
        neighbor = points[neighbor_index]
    else:
        anchor_index = _stable_route_endpoint_index(points, position="end")
        anchor = points[anchor_index]
        neighbor_index = max(0, anchor_index - 1)
        neighbor = points[neighbor_index]

    dx = anchor[0] - neighbor[0]
    dy = anchor[1] - neighbor[1]
    seg_len = math.hypot(dx, dy) or 1.0
    normal_x = -dy / seg_len
    normal_y = dx / seg_len
    tangent_x = dx / seg_len
    tangent_y = dy / seg_len
    return (
        anchor[0] + tangent_x * 26.0 + normal_x * 34.0,
        anchor[1] + tangent_y * 26.0 + normal_y * 34.0,
    )


def _append_bridge_note_if_needed(
    doc: ezdxf.document.Drawing,
    notes: list[tuple[str, tuple[float, float], tuple[float, float]]],
    route_points: list[tuple[float, float]],
) -> list[tuple[str, tuple[float, float], tuple[float, float]]]:
    bridge_target = _bridge_target_point(doc, route_points)
    if bridge_target is not None:
        note_text = (
            "Prelaz preko mosta\\P"
            "kablove voditi u novoj PVC cevi fi 160mm\\P"
            "izvesti zastitu i pricvrscenje na konstrukciji"
        )
        preferred = (
            bridge_target[0] + 48.0,
            bridge_target[1] - 54.0,
        )
        note_point = _resolve_note_insert(
            doc,
            route_points,
            note_text,
            bridge_target,
            preferred,
            offset_preferences=[40.0, 52.0, 64.0, 76.0],
            along_preferences=[0.0, 16.0, -16.0, 28.0, -28.0],
            side_preferences=[1.0, -1.0],
            max_distance_from_target=120.0,
        )
        notes.append((note_text, note_point, bridge_target))
    return notes


def _bridge_target_point(
    doc: ezdxf.document.Drawing,
    route_points: list[tuple[float, float]],
) -> tuple[float, float] | None:
    if len(route_points) < 2:
        return None
    bridge_keywords = ("most", "stuba mosta", "ograda mosta", "propust")
    best_target = None
    best_distance = float("inf")

    for entity in doc.modelspace():
        layer = entity.dxf.get("layer", "").lower()
        if not any(keyword in layer for keyword in bridge_keywords):
            continue
        entity_points = _entity_points(entity)
        if entity.dxftype() in {"TEXT", "MTEXT", "INSERT"}:
            insert = entity.dxf.get("insert", (0.0, 0.0, 0.0))
            entity_points = [(float(insert[0]), float(insert[1]))]
        if not entity_points:
            continue
        for point in entity_points:
            route_hit = _nearest_point_on_polyline(point, route_points)
            distance = math.dist(point, route_hit)
            if distance < best_distance:
                best_distance = distance
                best_target = route_hit

    if best_target is None or best_distance > 35.0:
        return None
    return best_target

def _resolve_existing_context_target(
    doc: ezdxf.document.Drawing,
    anchor_point: tuple[float, float],
    route_points: list[tuple[float, float]],
    radius: float = 55.0,
) -> tuple[float, float]:
    best_target = anchor_point
    best_distance = float("inf")
    for entity in doc.modelspace():
        layer = entity.dxf.get("layer", "0")
        if not _is_existing_network_layer(layer):
            continue
        entity_points = _entity_points(entity)
        if entity.dxftype() in {"TEXT", "MTEXT", "INSERT"}:
            insert = entity.dxf.get("insert", (0.0, 0.0, 0.0))
            entity_points = [(float(insert[0]), float(insert[1]))]
        if not entity_points:
            continue
        for point in entity_points:
            dist_anchor = math.dist(anchor_point, point)
            if dist_anchor > radius:
                continue
            route_hit = _nearest_point_on_polyline(point, route_points) if route_points else point
            snap_error = math.dist(point, route_hit)
            score = dist_anchor + snap_error * 0.35
            if score < best_distance:
                best_distance = score
                best_target = point
    return best_target


def _stabilize_context_target(
    target_point: tuple[float, float],
    anchor_point: tuple[float, float],
    route_points: list[tuple[float, float]],
    max_detach: float = 120.0,
) -> tuple[float, float]:
    if not route_points:
        return anchor_point
    route_hit = _nearest_point_on_polyline(target_point, route_points)
    if math.dist(target_point, route_hit) > max_detach:
        return anchor_point
    if math.dist(target_point, anchor_point) > max_detach * 1.5:
        return anchor_point
    return target_point


def _nearest_point_on_polyline(
    point: tuple[float, float],
    points: list[tuple[float, float]],
) -> tuple[float, float]:
    if len(points) < 2:
        return points[0] if points else point
    best_point = points[0]
    best_distance = float("inf")
    for a, b in zip(points, points[1:]):
        projection = _project_point_to_segment_geometry(point, a, b)
        distance = math.dist(point, projection)
        if distance < best_distance:
            best_distance = distance
            best_point = projection
    return best_point


def _stable_route_endpoint(
    route_points: list[tuple[float, float]],
    position: str = "end",
) -> tuple[float, float]:
    index = _stable_route_endpoint_index(route_points, position=position)
    if not route_points:
        return (0.0, 0.0)
    return route_points[index]


def _stable_route_endpoint_index(
    route_points: list[tuple[float, float]],
    position: str = "end",
) -> int:
    if not route_points:
        return 0
    if len(route_points) < 3:
        return 0 if position == "start" else len(route_points) - 1
    segments = [math.dist(a, b) for a, b in zip(route_points, route_points[1:])]
    if not segments:
        return 0 if position == "start" else len(route_points) - 1
    median = sorted(segments)[len(segments) // 2]
    threshold = max(150.0, median * 4.0)
    if position == "start":
        if segments[0] > threshold:
            return 1
        return 0
    if segments[-1] > threshold:
        return len(route_points) - 2
    return len(route_points) - 1


def _project_point_to_segment_geometry(
    point: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
) -> tuple[float, float]:
    ax, ay = a
    bx, by = b
    px, py = point
    dx = bx - ax
    dy = by - ay
    denom = dx * dx + dy * dy
    if denom <= 1e-9:
        return a
    t = ((px - ax) * dx + (py - ay) * dy) / denom
    t = max(0.0, min(1.0, t))
    return (ax + t * dx, ay + t * dy)


def _add_leader(
    msp,
    doc: ezdxf.document.Drawing,
    note_point: tuple[float, float],
    target_point: tuple[float, float],
    note_text: str,
    text_layer: str | None = None,
    char_height: float = 6.0,
) -> None:
    base_layer = text_layer or PROJECT_TEXT_LAYER
    leader_layer = PROJECT_LEADER_LAYER if base_layer == PROJECT_TEXT_LAYER else base_layer
    attribs = _leader_entity_attribs(doc, base_layer)
    attribs["layer"] = leader_layer
    leader_points = _leader_points_from_text_block(note_point, target_point, note_text, char_height)
    msp.add_lwpolyline(leader_points, dxfattribs=attribs)
    _add_filled_arrowhead(msp, target_point, leader_points[-2], attribs)


def _add_note_text_with_classic_leader(
    msp,
    doc: ezdxf.document.Drawing,
    output_code: str,
    note_index: int,
    note_text: str,
    note_point: tuple[float, float],
    target_point: tuple[float, float],
    text_layer: str | None = None,
    char_height: float = 6.0,
) -> None:
    if "VIP" not in {style.dxf.name for style in doc.styles}:
        doc.styles.add("VIP", font="arial.ttf", dxfattribs={"width": 1.0})

    text_layer_name = text_layer or PROJECT_TEXT_LAYER
    text_attribs = _mtext_entity_attribs(doc, text_layer_name)
    text_attribs["layer"] = text_layer_name
    text_attribs["char_height"] = char_height
    text_attribs["attachment_point"] = 7
    text_attribs["style"] = "VIP"

    mtext = msp.add_mtext(note_text, dxfattribs=text_attribs)
    mtext.dxf.insert = Vec2(note_point)
    mtext.dxf.width = 0.0

    leader_attribs = {
        "layer": PROJECT_LEADER_LAYER,
        "color": 1,
        "dimstyle": "VIP",
        "annotation_type": 0,
        "text_height": char_height * 1.164843,
        "text_width": _estimate_text_block_width(note_text, char_height),
    }
    leader_points = _classic_leader_vertices(note_point, target_point, note_text, char_height)
    leader = msp.add_leader(leader_points, dimstyle="VIP", override={}, dxfattribs=leader_attribs)
    leader.dxf.dimstyle = "VIP"
    leader.dxf.annotation_handle = mtext.dxf.handle
    leader.dxf.has_arrowhead = 1
    leader.dxf.has_hookline = 1
    leader.dxf.path_type = 0
    leader.dxf.hookline_direction = 1 if target_point[0] <= note_point[0] else 0
    leader.dxf.horizontal_direction = Vec2((1.0, 0.0))
    leader.dxf.normal_vector = (0.0, 0.0, 1.0)


def _add_note_mleader(
    msp,
    doc: ezdxf.document.Drawing,
    output_code: str,
    note_index: int,
    note_text: str,
    note_point: tuple[float, float],
    target_point: tuple[float, float],
    text_layer: str | None = None,
    char_height: float = 6.0,
) -> None:
    if "VIP" not in {style.dxf.name for style in doc.styles}:
        doc.styles.add("VIP", font="arial.ttf", dxfattribs={"width": 1.0})

    builder = msp.add_multileader_mtext("Standard", dxfattribs={"layer": PROJECT_LEADER_LAYER})
    builder.set_overall_scaling(1.0)
    builder.set_leader_properties(
        color=KRSTAC_LEADER_LAYER_ATTRS["color"],
        linetype="BYLAYER",
        lineweight=KRSTAC_LEADER_LAYER_ATTRS["lineweight"],
        leader_type=LeaderType.straight_lines,
    )
    builder.set_arrow_properties(name="EZ_ARROW_FILLED", size=6.0)
    builder.set_connection_properties(landing_gap=0.0, dogleg_length=18.0)
    builder.set_connection_types(
        left=HorizontalConnection.bottom_of_bottom_line_underline,
        right=HorizontalConnection.bottom_of_bottom_line_underline,
    )
    builder.set_content(
        note_text,
        color=KRSTAC_TEXT_ENTITY_ATTRS["color"],
        char_height=char_height,
        alignment=TextAlignment.left,
        style="VIP",
    )
    side = ConnectionSide.left if target_point[0] <= note_point[0] else ConnectionSide.right
    builder.add_leader_line(side, [Vec2(target_point)])
    builder.build(Vec2(note_point))


def _leader_points_from_text_block(
    note_point: tuple[float, float],
    target_point: tuple[float, float],
    note_text: str,
    char_height: float,
) -> list[tuple[float, float]]:
    nx, ny = note_point
    tx, ty = target_point
    lines = [line for line in note_text.split("\\P") if line] or [note_text]
    line_count = max(1, len(lines))
    longest = max(len(line) for line in lines)
    est_width = max(40.0, longest * char_height * 0.58)
    # Krstac-style callout should visually hug the text block.
    text_gap = 0.0
    text_height = line_count * char_height * 0.58
    underline_y = ny - text_height - text_gap
    left_under = (nx, underline_y)
    right_under = (nx + est_width, underline_y)
    center_x = nx + est_width / 2.0
    if tx <= center_x:
        near_tip = _leader_near_tip(left_under, target_point, backoff=18.0)
        return [right_under, left_under, near_tip, target_point]
    near_tip = _leader_near_tip(right_under, target_point, backoff=18.0)
    return [left_under, right_under, near_tip, target_point]


def _classic_leader_vertices(
    note_point: tuple[float, float],
    target_point: tuple[float, float],
    note_text: str,
    char_height: float,
) -> list[tuple[float, float]]:
    note_x, note_y = note_point
    width = _estimate_text_block_width(note_text, char_height)
    lines = [line for line in note_text.split("\\P") if line] or [note_text]
    attach_x = note_x + width * 0.5
    underline_y = note_y - max(2.0, char_height * 0.35)
    return [
        (float(target_point[0]), float(target_point[1])),
        (float(attach_x), float(underline_y)),
    ]


def _estimate_text_block_width(note_text: str, char_height: float) -> float:
    lines = [line for line in note_text.split("\\P") if line] or [note_text]
    longest = max(len(line) for line in lines)
    return longest * char_height * 0.58


def _leader_near_tip(
    branch_start: tuple[float, float],
    target_point: tuple[float, float],
    backoff: float = 14.0,
) -> tuple[float, float]:
    bx, by = branch_start
    tx, ty = target_point
    dx = bx - tx
    dy = by - ty
    length = math.hypot(dx, dy) or 1.0
    ux = dx / length
    uy = dy / length
    return (tx + ux * backoff, ty + uy * backoff)


def _add_terminal_markers(
    msp,
    doc: ezdxf.document.Drawing,
    output: dict,
    anchors: dict,
    points: list[tuple[float, float]],
    layer: str,
) -> None:
    if len(points) < 2:
        return
    start_name = (output.get("start_point") or "").lower().strip()
    end_name = (output.get("end_point") or "").lower().strip()

    start_anchor = _nearest_anchor_dict(anchors.get(start_name) or [], points[0])
    end_anchor = _nearest_anchor_dict(anchors.get(end_name) or [], points[-1])

    marker_specs = []
    if start_anchor is not None:
        marker_specs.append(((float(start_anchor.get("x", points[0][0])), float(start_anchor.get("y", points[0][1]))), start_anchor.get("layer", layer) or layer))
    else:
        marker_specs.append((points[0], layer))
    if end_anchor is not None:
        marker_specs.append(((float(end_anchor.get("x", points[-1][0])), float(end_anchor.get("y", points[-1][1]))), end_anchor.get("layer", layer) or layer))
    else:
        marker_specs.append((points[-1], layer))

    for center, marker_layer in marker_specs:
        attribs = _leader_entity_attribs(doc, marker_layer)
        attribs["layer"] = marker_layer
        _add_ring_marker(msp, center, attribs, radius=6.5)
        _add_ring_marker(msp, center, attribs, radius=3.5)


def _add_ring_marker(msp, center: tuple[float, float], attribs: dict, radius: float = 4.5) -> None:
    cx, cy = center
    msp.add_circle((cx, cy), radius=radius, dxfattribs=attribs)


def _nearest_anchor_dict(candidates: list[dict], endpoint: tuple[float, float]) -> dict | None:
    if not candidates:
        return None
    best = None
    best_dist = None
    for item in candidates:
        try:
            point = (float(item.get("x", 0.0)), float(item.get("y", 0.0)))
        except Exception:
            continue
        distance = math.dist(point, endpoint)
        if best_dist is None or distance < best_dist:
            best = item
            best_dist = distance
    return best


def _leader_elbow(
    note_point: tuple[float, float],
    target_point: tuple[float, float],
) -> tuple[float, float]:
    nx, ny = note_point
    tx, ty = target_point
    if abs(tx - nx) >= abs(ty - ny):
        return ((nx + tx) / 2.0, ny)
    return (nx, (ny + ty) / 2.0)


def _arrowhead_lines(
    target_point: tuple[float, float],
    from_point: tuple[float, float],
    size: float = 6.0,
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    tx, ty = target_point
    fx, fy = from_point
    dx = fx - tx
    dy = fy - ty
    length = math.hypot(dx, dy) or 1.0
    ux = dx / length
    uy = dy / length
    px = -uy
    py = ux
    a = (tx + ux * size + px * size * 0.45, ty + uy * size + py * size * 0.45)
    b = (tx + ux * size - px * size * 0.45, ty + uy * size - py * size * 0.45)
    return [((tx, ty), a), ((tx, ty), b)]


def _add_filled_arrowhead(
    msp,
    target_point: tuple[float, float],
    from_point: tuple[float, float],
    attribs: dict,
    size: float = 6.0,
) -> None:
    tx, ty = target_point
    fx, fy = from_point
    dx = fx - tx
    dy = fy - ty
    length = math.hypot(dx, dy) or 1.0
    ux = dx / length
    uy = dy / length
    px = -uy
    py = ux
    a = (tx + ux * size + px * size * 0.55, ty + uy * size + py * size * 0.55)
    b = (tx + ux * size - px * size * 0.55, ty + uy * size - py * size * 0.55)
    msp.add_solid([target_point, a, b, b], dxfattribs=attribs)


def _route_is_near_bridge(doc: ezdxf.document.Drawing, route_points: list[tuple[float, float]]) -> bool:
    if len(route_points) < 2:
        return False
    bridge_keywords = ("most", "stuba mosta", "ograda mosta", "propust")
    route_min_x = min(x for x, _ in route_points) - 25.0
    route_max_x = max(x for x, _ in route_points) + 25.0
    route_min_y = min(y for _, y in route_points) - 25.0
    route_max_y = max(y for _, y in route_points) + 25.0

    for entity in doc.modelspace():
        layer = entity.dxf.get("layer", "").lower()
        if not any(keyword in layer for keyword in bridge_keywords):
            continue
        for x, y in _entity_points(entity):
            if route_min_x <= x <= route_max_x and route_min_y <= y <= route_max_y:
                return True
    return False


def _collect_guide_geometries(doc: ezdxf.document.Drawing, layers: list[str]) -> list[list[tuple[float, float]]]:
    msp = doc.modelspace()
    geometries: list[list[tuple[float, float]]] = []
    layer_set = set(layers)
    for entity in msp:
        if entity.dxf.get("layer", "0") not in layer_set:
            continue
        points = _entity_points(entity)
        if len(points) >= 2:
            geometries.append(points)
    return geometries


def _collect_guide_entities(doc: ezdxf.document.Drawing, layers: list[str]) -> list[dict]:
    msp = doc.modelspace()
    entities: list[dict] = []
    layer_set = set(layers)
    for entity in msp:
        layer = entity.dxf.get("layer", "0")
        if layer not in layer_set:
            continue
        points = _entity_points(entity)
        if len(points) >= 2:
            entities.append({"layer": layer, "points": points})
    return entities


def _is_final_project_layer(layer: str) -> bool:
    lower = layer.lower()
    return any(pattern in lower for pattern in FINAL_PROJECT_LAYER_PATTERNS)


def _is_existing_network_layer(layer: str) -> bool:
    lower = layer.lower()
    return any(pattern in lower for pattern in EXISTING_NETWORK_LAYER_PATTERNS)


def _collect_corridor_layers(doc: ezdxf.document.Drawing) -> list[str]:
    names = []
    for layer in doc.layers:
        name = layer.dxf.name
        lower = name.lower()
        if _is_final_project_layer(name):
            continue
        if any(pattern in lower for pattern in CORRIDOR_FALLBACK_LAYER_PATTERNS):
            names.append(name)
    return names


def _is_road_corridor_layer(layer: str) -> bool:
    lower = layer.lower()
    return any(pattern in lower for pattern in ROAD_CORRIDOR_LAYER_PATTERNS)


def _parcel_label_points(doc: ezdxf.document.Drawing, parcel: str | None) -> list[tuple[float, float]]:
    normalized_parcel = _normalize_text(str(parcel or ""))
    if not normalized_parcel:
        return []
    cache = getattr(doc, "_codex_parcel_label_cache", None)
    if cache is None:
        cache = {}
        setattr(doc, "_codex_parcel_label_cache", cache)
    if normalized_parcel in cache:
        return list(cache[normalized_parcel])
    hits: list[tuple[float, float]] = []
    for item in extract_text_entities(doc):
        if _normalize_text(str(item.get("text", ""))) != normalized_parcel:
            continue
        layer = _normalize_text(str(item.get("layer", "")))
        if "broj parcele" not in layer and "brojparcele" not in layer:
            continue
        hits.append((float(item["x"]), float(item["y"])))
    cache[normalized_parcel] = list(hits)
    return hits


def _point_in_polygon(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    if len(polygon) < 3:
        return False
    x, y = point
    inside = False
    count = len(polygon)
    for idx in range(count):
        x1, y1 = polygon[idx]
        x2, y2 = polygon[(idx + 1) % count]
        if ((y1 > y) != (y2 > y)) and (x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1):
            inside = not inside
    return inside


def _parcel_closed_boundary_points(
    doc: ezdxf.document.Drawing,
    parcel: str | None,
    *,
    search_radius: float = 120.0,
    endpoint_merge_tolerance: float = 1.5,
    max_cycle_len: int = 8,
    _retry_expanded_cycle: bool = True,
) -> list[tuple[float, float]]:
    normalized_parcel = _normalize_text(str(parcel or ""))
    if not normalized_parcel:
        return []
    cache = getattr(doc, "_codex_parcel_boundary_cache", None)
    if cache is None:
        cache = {}
        setattr(doc, "_codex_parcel_boundary_cache", cache)
    if normalized_parcel in cache:
        return list(cache[normalized_parcel])
    label_points = _parcel_label_points(doc, parcel)
    if not label_points:
        cache[normalized_parcel] = []
        return []
    px, py = label_points[0]

    def _segment_distance(p1: tuple[float, float], p2: tuple[float, float]) -> float:
        projection = _project_point_to_segment_geometry((px, py), p1, p2)
        return math.dist((px, py), projection)

    seen: set[tuple[tuple[float, float], tuple[float, float]]] = set()
    raw_segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for entity in doc.modelspace():
        layer = str(entity.dxf.get("layer", "")).lower()
        if layer != "granica kat.parcela" or entity.dxftype() != "LINE":
            continue
        p1 = (round(float(entity.dxf.start.x), 3), round(float(entity.dxf.start.y), 3))
        p2 = (round(float(entity.dxf.end.x), 3), round(float(entity.dxf.end.y), 3))
        key = tuple(sorted((p1, p2)))
        if key in seen:
            continue
        seen.add(key)
        if _segment_distance(p1, p2) <= search_radius:
            raw_segments.append((p1, p2))
    if not raw_segments:
        cache[normalized_parcel] = []
        return []

    points = [point for seg in raw_segments for point in seg]
    assign: dict[tuple[float, float], int] = {}
    clusters: list[dict[str, object]] = []
    for point in points:
        if point in assign:
            continue
        idx = len(clusters)
        cluster = [point]
        assign[point] = idx
        changed = True
        while changed:
            changed = False
            for other in points:
                if other in assign and assign[other] == idx:
                    continue
                if any(math.dist(other, member) <= endpoint_merge_tolerance for member in cluster):
                    assign[other] = idx
                    cluster.append(other)
                    changed = True
        cx = sum(x for x, _ in cluster) / len(cluster)
        cy = sum(y for _, y in cluster) / len(cluster)
        clusters.append({"center": (cx, cy), "members": cluster})

    adjacency: dict[int, set[int]] = defaultdict(set)
    for p1, p2 in raw_segments:
        a = assign[p1]
        b = assign[p2]
        if a == b:
            continue
        adjacency[a].add(b)
        adjacency[b].add(a)

    centers = [cluster["center"] for cluster in clusters]

    def _polygon_area(polygon: list[tuple[float, float]]) -> float:
        acc = 0.0
        for idx in range(len(polygon)):
            x1, y1 = polygon[idx]
            x2, y2 = polygon[(idx + 1) % len(polygon)]
            acc += x1 * y2 - x2 * y1
        return abs(acc) / 2.0

    cycles: set[tuple[int, ...]] = set()
    candidates: list[tuple[float, tuple[int, ...], list[tuple[float, float]]]] = []
    for start in range(len(clusters)):
        stack: list[tuple[int, list[int], int | None]] = [(start, [start], None)]
        while stack:
            node, path, previous = stack.pop()
            for nxt in adjacency.get(node, set()):
                if previous is not None and nxt == previous:
                    continue
                if nxt == start and len(path) >= 3:
                    body = path[:]
                    variants = []
                    for seq in (body, list(reversed(body))):
                        for offset in range(len(seq)):
                            variants.append(tuple(seq[offset:] + seq[:offset]))
                    canon = min(variants)
                    if canon in cycles:
                        continue
                    cycles.add(canon)
                    polygon = [centers[idx] for idx in canon]
                    if _point_in_polygon((px, py), polygon):
                        candidates.append((_polygon_area(polygon), canon, polygon))
                    continue
                if nxt in path or len(path) >= max_cycle_len:
                    continue
                stack.append((nxt, path + [nxt], node))
    if not candidates:
        if _retry_expanded_cycle and max_cycle_len < 12:
            result = _parcel_closed_boundary_points(
                doc,
                parcel,
                search_radius=search_radius,
                endpoint_merge_tolerance=endpoint_merge_tolerance,
                max_cycle_len=12,
                _retry_expanded_cycle=False,
            )
            cache[normalized_parcel] = list(result)
            return result
        cache[normalized_parcel] = []
        return []
    candidates.sort(key=lambda item: item[0])
    cache[normalized_parcel] = list(candidates[0][2])
    return candidates[0][2]


def _boundary_entry_candidates(
    boundary: list[tuple[float, float]],
    target_points: list[tuple[float, float]],
    *,
    limit: int = 3,
) -> list[tuple[float, float, float]]:
    if len(boundary) < 2 or not target_points:
        return []
    rows: list[tuple[float, float, float]] = []
    closed = boundary + [boundary[0]]
    for tx, ty in target_points:
        for a, b in zip(closed, closed[1:]):
            projection = _project_point_to_segment_geometry((tx, ty), a, b)
            rows.append((projection[0], projection[1], math.dist((tx, ty), projection)))
    rows.sort(key=lambda item: item[2])
    deduped: list[tuple[float, float, float]] = []
    for qx, qy, dist in rows:
        if any(math.dist((qx, qy), (ox, oy)) <= 4.0 for ox, oy, _ in deduped):
            continue
        deduped.append((qx, qy, dist))
        if len(deduped) >= limit:
            break
    return deduped


def _nearest_route_progress(
    route_points: list[tuple[float, float]],
    point: tuple[float, float],
) -> tuple[float, float]:
    if len(route_points) < 2:
        return 0.0, float("inf")
    best_along = 0.0
    best_distance = float("inf")
    traveled = 0.0
    for a, b in zip(route_points, route_points[1:]):
        seg_len = math.dist(a, b)
        if seg_len <= 1e-6:
            continue
        projection = _project_point_to_segment_geometry(point, a, b)
        distance = math.dist(point, projection)
        if distance < best_distance:
            best_distance = distance
            best_along = traveled + math.dist(a, projection)
        traveled += seg_len
    return best_along, best_distance


def _route_parcel_corridor_metrics(
    doc: ezdxf.document.Drawing,
    route_points: list[tuple[float, float]],
    route_musts: dict,
) -> tuple[float, bool]:
    corridor = list(route_musts.get("parcel_corridor") or [])
    if len(route_points) < 2 or not corridor:
        return 0.0, True

    samples: list[tuple[str, tuple[float, float]]] = []
    for item in corridor:
        parcel = str(item.get("parcel") or "")
        if not parcel:
            continue
        points = _parcel_label_points(doc, parcel)
        if not points:
            continue
        nearest = min(points, key=lambda pt: _distance_point_to_polyline(pt, route_points))
        samples.append((parcel, nearest))

    if len(samples) < 2:
        return 0.0, True

    penalty = 0.0
    last_along = None
    hard_fail = False
    for _parcel, sample in samples:
        along, dist = _nearest_route_progress(route_points, sample)
        penalty += max(0.0, dist - 30.0) * 1.5
        if dist > 120.0:
            penalty += 500.0 + (dist - 120.0) * 3.0
            hard_fail = True
        if last_along is not None and along + 10.0 < last_along:
            penalty += 900.0 + (last_along - along) * 2.0
            hard_fail = True
        last_along = along if last_along is None else max(last_along, along)
    return penalty, not hard_fail


def _build_named_parcel_corridor_route(
    doc: ezdxf.document.Drawing,
    start: Anchor,
    end: Anchor,
    route_musts: dict,
    allowed_layers: list[str],
) -> list[tuple[float, float]] | None:
    corridor = list(route_musts.get("parcel_corridor") or [])
    if not corridor or not allowed_layers:
        return None
    geometries = _collect_guide_geometries(doc, allowed_layers)
    if not geometries:
        return None

    def _project_to_corridor(point: tuple[float, float]) -> tuple[float, float] | None:
        best_point = None
        best_distance = None
        for geom in geometries:
            projected = _nearest_point_on_polyline(point, geom)
            distance = math.dist(point, projected)
            if best_distance is None or distance < best_distance:
                best_distance = distance
                best_point = projected
        return best_point

    parcel_infos: list[dict] = []
    for item in corridor:
        parcel = str(item.get("parcel") or "")
        label_points = _parcel_label_points(doc, parcel)
        if not label_points:
            parcel_infos.append({"parcel": parcel, "labels": [], "boundary": [], "projected_label": None})
            continue
        label_point = min(label_points, key=lambda pt: min(_distance_point_to_polyline(pt, geom) for geom in geometries))
        parcel_infos.append(
            {
                "parcel": parcel,
                "labels": label_points,
                "boundary": _parcel_closed_boundary_points(doc, parcel),
                "projected_label": _project_to_corridor(label_point),
            }
        )

    transitions: list[tuple[float, float] | None] = []
    for idx, info in enumerate(parcel_infos[:-1]):
        nxt = parcel_infos[idx + 1]
        transition_candidates: list[tuple[float, float]] = []
        current_boundary = list(info.get("boundary") or [])
        next_boundary = list(nxt.get("boundary") or [])
        current_labels = list(info.get("labels") or [])
        next_labels = list(nxt.get("labels") or [])

        if current_boundary and next_labels:
            for qx, qy, _dist in _boundary_entry_candidates(current_boundary, next_labels, limit=2):
                transition_candidates.append((qx, qy))
        if next_boundary and current_labels:
            for qx, qy, _dist in _boundary_entry_candidates(next_boundary, current_labels, limit=2):
                transition_candidates.append((qx, qy))

        if not transition_candidates and info.get("projected_label") and nxt.get("projected_label"):
            a = info["projected_label"]
            b = nxt["projected_label"]
            transition_candidates.append(((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0))

        best_transition = None
        best_score = None
        for candidate in transition_candidates:
            projected = _project_to_corridor(candidate)
            if projected is None:
                continue
            score = math.dist(candidate, projected)
            if info.get("projected_label") is not None:
                score += math.dist(projected, info["projected_label"]) * 0.05
            if nxt.get("projected_label") is not None:
                score += math.dist(projected, nxt["projected_label"]) * 0.05
            if best_score is None or score < best_score:
                best_score = score
                best_transition = projected
        transitions.append(best_transition)

    waypoints: list[tuple[float, float]] = []

    def _append_waypoint(point: tuple[float, float] | None) -> None:
        if point is None:
            return
        if waypoints and math.dist(waypoints[-1], point) <= 5.0:
            return
        waypoints.append(point)

    if parcel_infos:
        if not transitions or transitions[0] is None:
            _append_waypoint(parcel_infos[0].get("projected_label"))
    for idx, transition in enumerate(transitions):
        if transition is not None:
            _append_waypoint(transition)
            continue
        _append_waypoint(parcel_infos[idx].get("projected_label"))
        _append_waypoint(parcel_infos[idx + 1].get("projected_label"))
    if parcel_infos:
        if not transitions or transitions[-1] is None:
            _append_waypoint(parcel_infos[-1].get("projected_label"))

    if not waypoints:
        return None

    route = [(start.x, start.y), *waypoints, (end.x, end.y)]
    cleaned = [route[0]]
    for point in route[1:]:
        if math.dist(cleaned[-1], point) > 1.0:
            cleaned.append(point)
    if len(cleaned) < 2:
        return None

    simplified = list(cleaned)
    changed = True
    while changed and len(simplified) > 2:
        changed = False
        base_penalty, base_ok = _route_parcel_corridor_metrics(doc, simplified, route_musts)
        base_length = _polyline_length(simplified)
        for idx in range(1, len(simplified) - 1):
            candidate = simplified[:idx] + simplified[idx + 1 :]
            candidate_penalty, candidate_ok = _route_parcel_corridor_metrics(doc, candidate, route_musts)
            candidate_length = _polyline_length(candidate)
            if candidate_length >= base_length - 5.0:
                continue
            if base_ok and not candidate_ok:
                continue
            if candidate_penalty > base_penalty + 80.0:
                continue
            simplified = candidate
            changed = True
            break
    return simplified if len(simplified) >= 2 else None


def _build_corridor_fallback_route(
    doc: ezdxf.document.Drawing,
    design: DesignModel,
    output,
    start: Anchor,
    end: Anchor,
) -> tuple[list[tuple[float, float]] | None, str | None, float | None]:
    corridor_layers = _collect_corridor_layers(doc)
    if not corridor_layers:
        return None, None, None
    output_index = output.index
    output_code = (getattr(output, "code", None) or "").strip()
    route_mode = getattr(output, "route_mode", None) or ""
    route_musts = _output_route_musts(output)
    must_follow_named_parcels = bool(route_musts.get("must_follow_named_parcels"))
    allow_bridge = any(constraint.category == "bridge" for constraint in design.constraints)
    road_parallel_required = bool(route_musts.get("must_run_parallel_to_road")) or (
        route_mode == "underground"
        and any(constraint.category == "roads" for constraint in design.constraints)
    )
    road_geometries = _collect_guide_geometries(
        doc,
        [layer for layer in corridor_layers if _is_road_corridor_layer(layer)],
    )
    road_layers = [layer for layer in corridor_layers if _is_road_corridor_layer(layer)]
    if road_parallel_required and road_layers:
        road_candidates = _collect_guide_entities(doc, road_layers)
        road_offset_candidates = _build_offset_road_candidates(
            road_candidates,
            road_geometries,
            start,
            end,
            offset_distance=1.0,
        )
        same_side_route = _build_same_side_road_parallel_route(
            road_offset_candidates,
            (start.x, start.y),
            (end.x, end.y),
            output_index,
            output_code,
            route_mode=route_mode,
            allow_bridge=allow_bridge,
        )
        if same_side_route:
            attached = _attach_anchors(same_side_route, start, end)
            parcel_penalty, parcel_ok = _route_parcel_corridor_metrics(doc, attached, route_musts)
            if _max_anchor_attachment_length(attached, start, end) <= 150.0 and (
                not must_follow_named_parcels or parcel_ok or parcel_penalty <= 600.0
            ):
                return attached, "road_parallel_side_graph", _polyline_length(attached)
        for max_snap_distance, stitch_gap in ((500.0, 10.0), (500.0, 20.0), (500.0, 30.0), (700.0, 30.0)):
            road_parallel_graph = _build_graph_route(
                road_candidates,
                (start.x, start.y),
                (end.x, end.y),
                output_index,
                output_code,
                max_snap_distance=max_snap_distance,
                stitch_gap=stitch_gap,
                route_mode=route_mode,
                allow_bridge=allow_bridge,
                road_geometries=road_geometries,
                road_parallel_required=True,
            )
            if not road_parallel_graph:
                continue
            shifted = _offset_route_from_road(
                road_parallel_graph,
                start,
                road_geometries=road_geometries,
                offset_distance=1.0,
            )
            attached = _attach_anchors(shifted, start, end)
            parcel_penalty, parcel_ok = _route_parcel_corridor_metrics(doc, attached, route_musts)
            if _max_anchor_attachment_length(attached, start, end) <= 150.0 and (
                not must_follow_named_parcels or parcel_ok or parcel_penalty <= 600.0
            ):
                return attached, "road_parallel_graph", _polyline_length(attached)
        road_parallel = _build_road_parallel_subpath(road_geometries, start, end)
        if road_parallel is not None:
            shifted = _offset_route_from_road(
                road_parallel,
                start,
                road_geometries=road_geometries,
                offset_distance=1.0,
            )
            attached = _attach_anchors(shifted, start, end)
            parcel_penalty, parcel_ok = _route_parcel_corridor_metrics(doc, attached, route_musts)
            if _max_anchor_attachment_length(attached, start, end) <= 150.0 and (
                not must_follow_named_parcels or parcel_ok or parcel_penalty <= 600.0
            ):
                return attached, "road_parallel_subpath", _polyline_length(attached)
    primary_layers = [layer for layer in corridor_layers if not _is_road_corridor_layer(layer)]
    if route_musts.get("must_follow_existing_network"):
        primary_layers = [layer for layer in primary_layers if _is_existing_network_layer(layer)]
    search_variants: list[tuple[list[str], float, float, str]] = []
    if primary_layers and not road_parallel_required:
        search_variants.extend(
            [
                (primary_layers, 450.0, 20.0, "corridor_graph_primary"),
                (primary_layers, 700.0, 35.0, "corridor_graph_primary_wide_snap"),
            ]
        )
    if road_parallel_required:
        search_variants.extend(
            [
                (corridor_layers, 350.0, 20.0, "corridor_graph_road_parallel"),
                (corridor_layers, 500.0, 30.0, "corridor_graph_road_parallel_wide_snap"),
            ]
        )
    else:
        search_variants.extend(
            [
                (corridor_layers, 400.0, 20.0, "corridor_graph_with_roads_fallback"),
                (corridor_layers, 700.0, 35.0, "corridor_graph_with_roads_wide_snap"),
            ]
        )

    for layer_group, max_snap_distance, stitch_gap, source_name in search_variants:
        candidates = _collect_guide_entities(doc, layer_group)
        if not candidates:
            continue
        graph_route = _build_graph_route(
            candidates,
            (start.x, start.y),
            (end.x, end.y),
            output_index,
            output_code,
            max_snap_distance=max_snap_distance,
            stitch_gap=stitch_gap,
            route_mode=route_mode,
            allow_bridge=allow_bridge,
            road_geometries=road_geometries,
            road_parallel_required=road_parallel_required,
        )
        if not graph_route:
            continue
        attached = _attach_anchors(graph_route, start, end)
        if road_parallel_required and _count_route_road_crossings(attached, road_geometries) > 0:
            continue
        parcel_penalty, parcel_ok = _route_parcel_corridor_metrics(doc, attached, route_musts)
        if must_follow_named_parcels and not parcel_ok and parcel_penalty > 600.0:
            continue
        return attached, source_name, _polyline_length(attached)

    if must_follow_named_parcels:
        if road_parallel_required and road_layers:
            parcel_layers = road_layers
        elif route_musts.get("must_follow_existing_network"):
            parcel_layers = [layer for layer in corridor_layers if _is_existing_network_layer(layer)]
        else:
            parcel_layers = corridor_layers
        parcel_route = _build_named_parcel_corridor_route(doc, start, end, route_musts, parcel_layers)
        if parcel_route:
            parcel_penalty, parcel_ok = _route_parcel_corridor_metrics(doc, parcel_route, route_musts)
            if parcel_ok or parcel_penalty <= 800.0:
                return parcel_route, "named_parcel_corridor", _polyline_length(parcel_route)
    return None, None, None


def _build_offset_road_candidates(
    road_candidates: list[dict],
    road_geometries: list[list[tuple[float, float]]],
    start: Anchor,
    end: Anchor,
    offset_distance: float,
) -> list[dict]:
    shifted_candidates: list[dict] = []
    for item in road_candidates:
        points = item.get("points", [])
        if len(points) < 2:
            continue
        midpoint = points[len(points) // 2]
        fallback_anchor = start if math.dist(midpoint, (start.x, start.y)) <= math.dist(midpoint, (end.x, end.y)) else end
        side_sign = _road_candidate_outward_sign(
            points,
            road_geometries,
            (fallback_anchor.x, fallback_anchor.y),
        )
        shifted_points: list[tuple[float, float]] = []
        for index, point in enumerate(points):
            normal = _route_vertex_normal(points, index)
            shifted_points.append(
                (
                    point[0] + normal[0] * offset_distance * side_sign,
                    point[1] + normal[1] * offset_distance * side_sign,
                )
            )
        shifted_candidates.append(
            {
                "points": shifted_points,
                "layer": item.get("layer", "0"),
                "side_sign": side_sign,
            }
        )
    return shifted_candidates

def _road_candidate_outward_sign(
    points: list[tuple[float, float]],
    road_geometries: list[list[tuple[float, float]]],
    fallback_anchor: tuple[float, float],
) -> float:
    if len(points) < 2 or not road_geometries:
        return -_route_offset_side_sign(points, fallback_anchor)
    sample_indexes = sorted({0, len(points) // 2, len(points) - 1})
    plus_score = 0.0
    minus_score = 0.0
    for index in sample_indexes:
        point = points[index]
        normal = _route_vertex_normal(points, index)
        for distance in (3.0, 5.0, 7.0, 9.0):
            plus_probe = (point[0] + normal[0] * distance, point[1] + normal[1] * distance)
            minus_probe = (point[0] - normal[0] * distance, point[1] - normal[1] * distance)
            plus_near = _nearest_distance_to_geometries(plus_probe, road_geometries)
            minus_near = _nearest_distance_to_geometries(minus_probe, road_geometries)
            if 0.6 < plus_near < 12.0:
                plus_score += 1.0 / plus_near
            if 0.6 < minus_near < 12.0:
                minus_score += 1.0 / minus_near
    if plus_score > minus_score * 1.15:
        return 1.0
    if minus_score > plus_score * 1.15:
        return -1.0
    return -_route_offset_side_sign(points, fallback_anchor)


def _build_same_side_road_parallel_route(
    shifted_candidates: list[dict],
    start_point: tuple[float, float],
    end_point: tuple[float, float],
    output_index: int,
    output_code: str | None,
    route_mode: str,
    allow_bridge: bool,
) -> list[tuple[float, float]] | None:
    if not shifted_candidates:
        return None

    sign_groups: dict[float, list[dict]] = {1.0: [], -1.0: []}
    for item in shifted_candidates:
        sign = float(item.get("side_sign", 1.0))
        sign_groups[1.0 if sign >= 0 else -1.0].append(item)

    best_path = None
    best_cost = float("inf")
    for sign, group in sign_groups.items():
        if not group:
            continue
        path, cost = _build_oriented_segment_route(
            group,
            start_point,
            end_point,
            output_index,
            output_code,
            route_mode=route_mode,
            allow_bridge=allow_bridge,
            stitch_gap=30.0,
            max_snap_distance=500.0,
        )
        if path and cost < best_cost:
            best_cost = cost
            best_path = path
    return best_path


def _build_oriented_segment_route(
    candidates: list[dict],
    start_point: tuple[float, float],
    end_point: tuple[float, float],
    output_index: int,
    output_code: str | None,
    route_mode: str,
    allow_bridge: bool,
    stitch_gap: float,
    max_snap_distance: float,
) -> tuple[list[tuple[float, float]] | None, float]:
    states: list[dict] = []
    for seg_index, item in enumerate(candidates):
        points = item.get("points", [])
        layer = item.get("layer", "0")
        if len(points) < 2:
            continue
        edge_weight_factor = _corridor_weight_for_layer(layer, route_mode=route_mode, allow_bridge=allow_bridge)
        if math.isinf(edge_weight_factor):
            continue
        length = _polyline_length(points)
        if length <= 0:
            continue
        for orient in (0, 1):
            oriented_points = points if orient == 0 else list(reversed(points))
            start_tangent = _unit_vector(oriented_points[0], oriented_points[1])
            end_tangent = _unit_vector(oriented_points[-2], oriented_points[-1])
            states.append(
                {
                    "id": len(states),
                    "seg_index": seg_index,
                    "points": oriented_points,
                    "start": oriented_points[0],
                    "end": oriented_points[-1],
                    "start_tangent": start_tangent,
                    "end_tangent": end_tangent,
                    "length": length,
                    "weight": length * edge_weight_factor,
                }
            )

    if not states:
        return None, float("inf")

    adjacency: dict[int, list[tuple[int, float, float]]] = {state["id"]: [] for state in states}
    by_segment: dict[int, list[dict]] = {}
    for state in states:
        by_segment.setdefault(state["seg_index"], []).append(state)

    for a in states:
        for b in states:
            if a["id"] == b["id"] or a["seg_index"] == b["seg_index"]:
                continue
            gap = math.dist(a["end"], b["start"])
            if gap > stitch_gap:
                continue
            tangent_dot = a["end_tangent"][0] * b["start_tangent"][0] + a["end_tangent"][1] * b["start_tangent"][1]
            if tangent_dot < 0.4:
                continue
            adjacency[a["id"]].append((b["id"], b["weight"] + gap * 18.0, gap))

    distances: dict[int, float] = {}
    previous: dict[int, int | None] = {}
    heap: list[tuple[float, int]] = []
    for state in states:
        start_snap = _distance_point_to_polyline(start_point, state["points"])
        if start_snap <= max_snap_distance:
            initial_cost = start_snap + state["weight"]
            distances[state["id"]] = initial_cost
            previous[state["id"]] = None
            heapq.heappush(heap, (initial_cost, state["id"]))

    best_end_state: int | None = None
    best_total = float("inf")
    while heap:
        distance, state_id = heapq.heappop(heap)
        if distance > distances.get(state_id, float("inf")):
            continue
        state = states[state_id]
        end_snap = _distance_point_to_polyline(end_point, state["points"])
        route_length = distance  # already includes segment weights and stitch penalties
        total = distance + end_snap + _expected_length_penalty(output_index, state["length"], output_code)
        if end_snap <= max_snap_distance and total < best_total:
            best_total = total
            best_end_state = state_id
        for neighbor_id, edge_cost, _gap in adjacency[state_id]:
            nd = distance + edge_cost
            if nd < distances.get(neighbor_id, float("inf")):
                distances[neighbor_id] = nd
                previous[neighbor_id] = state_id
                heapq.heappush(heap, (nd, neighbor_id))

    if best_end_state is None:
        return None, float("inf")

    ordered_state_ids: list[int] = []
    node = best_end_state
    while node is not None:
        ordered_state_ids.append(node)
        node = previous.get(node)
    ordered_state_ids.reverse()

    route: list[tuple[float, float]] = []
    for idx, state_id in enumerate(ordered_state_ids):
        state = states[state_id]
        pts = state["points"]
        if not route:
            route.extend(pts)
            continue
        if math.dist(route[-1], pts[0]) > 1.0:
            route.append(pts[0])
        route.extend(pts[1:])
    return route, best_total


def _unit_vector(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    length = math.hypot(dx, dy) or 1.0
    return (dx / length, dy / length)


def _distance_point_to_polyline(
    point: tuple[float, float],
    points: list[tuple[float, float]],
) -> float:
    if len(points) < 2:
        return math.dist(point, points[0]) if points else float("inf")
    return min(_point_to_segment_distance(point, a, b) for a, b in zip(points, points[1:]))


def _build_road_parallel_subpath(
    road_geometries: list[list[tuple[float, float]]],
    start: Anchor,
    end: Anchor,
) -> list[tuple[float, float]] | None:
    best_route = None
    best_score = float("inf")
    for geometry in road_geometries:
        if len(geometry) < 2:
            continue
        subpath = _extract_relevant_subpath(geometry, (start.x, start.y), (end.x, end.y))
        if len(subpath) < 2:
            continue
        start_snap = _min_vertex_distance((start.x, start.y), geometry)
        end_snap = _min_vertex_distance((end.x, end.y), geometry)
        attached = _attach_anchors(subpath, start, end)
        crossings = _count_route_road_crossings(attached, road_geometries)
        score = crossings * 1_000_000.0 + start_snap * 4.0 + end_snap * 4.0 + _polyline_length(subpath)
        if score < best_score:
            best_score = score
            best_route = attached
    return best_route


def _offset_route_from_road(
    points: list[tuple[float, float]],
    start_anchor: Anchor,
    road_geometries: list[list[tuple[float, float]]] | None = None,
    offset_distance: float = 2.0,
) -> list[tuple[float, float]]:
    if len(points) < 2:
        return points
    local_signs = _road_outward_side_signs(
        points,
        road_geometries or [],
        fallback_anchor=(start_anchor.x, start_anchor.y),
    )
    shifted: list[tuple[float, float]] = []
    for index, point in enumerate(points):
        normal = _route_vertex_normal(points, index)
        side_sign = local_signs[index] if index < len(local_signs) else 1.0
        shifted.append(
            (
                point[0] + normal[0] * offset_distance * side_sign,
                point[1] + normal[1] * offset_distance * side_sign,
            )
        )
    return shifted

def _road_outward_side_signs(
    points: list[tuple[float, float]],
    road_geometries: list[list[tuple[float, float]]],
    fallback_anchor: tuple[float, float],
) -> list[float]:
    if len(points) < 2:
        return [1.0 for _ in points]
    fallback_sign = -_route_offset_side_sign(points, fallback_anchor)
    if not road_geometries:
        return [fallback_sign for _ in points]

    signs: list[float] = []
    sample_distances = (2.5, 4.0, 6.0, 8.0)
    inner_threshold = 10.0
    self_threshold = 0.6
    for index, point in enumerate(points):
        normal = _route_vertex_normal(points, index)
        plus_score = 0.0
        minus_score = 0.0
        for distance in sample_distances:
            plus_probe = (point[0] + normal[0] * distance, point[1] + normal[1] * distance)
            minus_probe = (point[0] - normal[0] * distance, point[1] - normal[1] * distance)
            plus_near = _nearest_distance_to_geometries(plus_probe, road_geometries)
            minus_near = _nearest_distance_to_geometries(minus_probe, road_geometries)
            if self_threshold < plus_near < inner_threshold:
                plus_score += 1.0 / max(plus_near, 0.1)
            if self_threshold < minus_near < inner_threshold:
                minus_score += 1.0 / max(minus_near, 0.1)
        if plus_score == 0.0 and minus_score == 0.0:
            signs.append(fallback_sign)
        elif plus_score > minus_score * 1.15:
            signs.append(1.0)
        elif minus_score > plus_score * 1.15:
            signs.append(-1.0)
        else:
            signs.append(fallback_sign)

    smoothed = list(signs)
    for index in range(1, len(smoothed) - 1):
        if smoothed[index - 1] == smoothed[index + 1] != smoothed[index]:
            smoothed[index] = smoothed[index - 1]
    if smoothed:
        dominant = 1.0 if smoothed.count(1.0) >= smoothed.count(-1.0) else -1.0
        smoothed = [sign if sign == dominant else dominant for sign in smoothed]
    return smoothed


def _nearest_distance_to_geometries(
    point: tuple[float, float],
    geometries: list[list[tuple[float, float]]],
) -> float:
    best = float("inf")
    for geometry in geometries:
        for a, b in zip(geometry, geometry[1:]):
            best = min(best, _point_to_segment_distance(point, a, b))
    return best


def _point_to_segment_distance(
    point: tuple[float, float],
    a: tuple[float, float],
    b: tuple[float, float],
) -> float:
    ax, ay = a
    bx, by = b
    px, py = point
    dx = bx - ax
    dy = by - ay
    denom = dx * dx + dy * dy
    if denom <= 1e-9:
        return math.dist(point, a)
    t = ((px - ax) * dx + (py - ay) * dy) / denom
    t = max(0.0, min(1.0, t))
    nearest = (ax + t * dx, ay + t * dy)
    return math.dist(point, nearest)


def _route_offset_side_sign(
    points: list[tuple[float, float]],
    anchor_point: tuple[float, float],
) -> float:
    if len(points) < 2:
        return 1.0
    a, b = points[0], points[1]
    cross = (b[0] - a[0]) * (anchor_point[1] - a[1]) - (b[1] - a[1]) * (anchor_point[0] - a[0])
    return 1.0 if cross >= 0 else -1.0


def _route_vertex_normal(
    points: list[tuple[float, float]],
    index: int,
) -> tuple[float, float]:
    if len(points) < 2:
        return (0.0, 0.0)
    if index == 0:
        dx = points[1][0] - points[0][0]
        dy = points[1][1] - points[0][1]
    elif index == len(points) - 1:
        dx = points[-1][0] - points[-2][0]
        dy = points[-1][1] - points[-2][1]
    else:
        dx = points[index + 1][0] - points[index - 1][0]
        dy = points[index + 1][1] - points[index - 1][1]
    length = math.hypot(dx, dy) or 1.0
    return (-dy / length, dx / length)


def _collect_component_routes(
    geometries: list[list[tuple[float, float]]],
) -> list[tuple[list[tuple[float, float]], float]]:
    adjacency: dict[tuple[float, float], list[tuple[tuple[float, float], float]]] = {}
    for points in geometries:
        for a, b in zip(points, points[1:]):
            ar = _round_point(a)
            br = _round_point(b)
            if ar == br:
                continue
            w = math.dist(ar, br)
            adjacency.setdefault(ar, []).append((br, w))
            adjacency.setdefault(br, []).append((ar, w))

    routes = []
    for component in _connected_components(adjacency):
        route = _walk_component_route(adjacency, component)
        if len(route) >= 2:
            routes.append((route, _polyline_length(route)))
    return routes


def _build_graph_route(
    geometries: list,
    start_point: tuple[float, float],
    end_point: tuple[float, float],
    output_index: int,
    output_code: str | None = None,
    max_snap_distance: float = 250.0,
    stitch_gap: float = 0.0,
    route_mode: str = "",
    allow_bridge: bool = False,
    road_geometries: list[list[tuple[float, float]]] | None = None,
    road_parallel_required: bool = False,
) -> list[tuple[float, float]] | None:
    if not geometries:
        return None

    adjacency: dict[tuple[float, float], list[tuple[tuple[float, float], float]]] = {}
    node_outward_normals: dict[tuple[float, float], list[tuple[float, float]]] = {}
    for item in geometries:
        if isinstance(item, dict):
            points = item.get("points", [])
            layer = item.get("layer", "0")
        else:
            points = item
            layer = "0"
        edge_weight_factor = _corridor_weight_for_layer(layer, route_mode=route_mode, allow_bridge=allow_bridge)
        if math.isinf(edge_weight_factor):
            continue
        outward_vectors: list[tuple[float, float]] | None = None
        if road_parallel_required and road_geometries and _is_road_corridor_layer(layer):
            midpoint = points[len(points) // 2]
            fallback_anchor = start_point if math.dist(midpoint, start_point) <= math.dist(midpoint, end_point) else end_point
            signs = _road_outward_side_signs(points, road_geometries, fallback_anchor)
            outward_vectors = []
            for index in range(len(points)):
                normal = _route_vertex_normal(points, index)
                sign = signs[index] if index < len(signs) else 1.0
                outward_vectors.append((normal[0] * sign, normal[1] * sign))
            for index, point in enumerate(points):
                node_outward_normals.setdefault(_round_point(point), []).append(outward_vectors[index])
        for a, b in zip(points, points[1:]):
            ar = _round_point(a)
            br = _round_point(b)
            if ar == br:
                continue
            geometric_length = math.dist(ar, br)
            weighted_length = geometric_length * edge_weight_factor
            adjacency.setdefault(ar, []).append((br, weighted_length))
            adjacency.setdefault(br, []).append((ar, weighted_length))

    if not adjacency:
        return None

    if stitch_gap > 0:
        _stitch_graph_gaps(
            adjacency,
            stitch_gap,
            avoid_cross_geometries=None,
            node_outward_normals=node_outward_normals if road_parallel_required else None,
        )

    components = _connected_components(adjacency)
    if not components:
        return None
    best_path = None
    best_cost = float("inf")
    for component in components:
        start_candidates = _nearest_graph_nodes(start_point, component, limit=8, max_distance=max_snap_distance)
        end_candidates = _nearest_graph_nodes(end_point, component, limit=8, max_distance=max_snap_distance)
        if not start_candidates or not end_candidates:
            continue
        for start_node, start_snap in start_candidates:
            for end_node, end_snap in end_candidates:
                path = _shortest_path(adjacency, start_node, end_node)
                if len(path) < 2:
                    continue
                route_length = _polyline_length(path)
                total_cost = start_snap + end_snap + _weighted_path_length(adjacency, path) + _expected_length_penalty(output_index, route_length, output_code)
                if road_parallel_required and road_geometries:
                    total_cost += _count_route_road_crossings(path, road_geometries) * 1_000_000.0
                    if _segment_crosses_any_geometry(start_point, start_node, road_geometries):
                        total_cost += 1_000_000.0
                    if _segment_crosses_any_geometry(end_point, end_node, road_geometries):
                        total_cost += 1_000_000.0
                if total_cost < best_cost:
                    best_cost = total_cost
                    best_path = path

    if not best_path:
        return None
    return best_path


def _count_route_road_crossings(
    route_points: list[tuple[float, float]],
    road_geometries: list[list[tuple[float, float]]],
) -> int:
    if len(route_points) < 2 or not road_geometries:
        return 0
    crossings = 0
    for a, b in zip(route_points, route_points[1:]):
        for road_points in road_geometries:
            for c, d in zip(road_points, road_points[1:]):
                if _segments_cross_properly(a, b, c, d):
                    crossings += 1
    return crossings


def _segment_crosses_any_geometry(
    a: tuple[float, float],
    b: tuple[float, float],
    geometries: list[list[tuple[float, float]]],
) -> bool:
    for geom in geometries:
        for c, d in zip(geom, geom[1:]):
            if _segments_cross_properly(a, b, c, d):
                return True
    return False


def _segments_cross_properly(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> bool:
    if a == c or a == d or b == c or b == d:
        return False
    o1 = _orientation(a, b, c)
    o2 = _orientation(a, b, d)
    o3 = _orientation(c, d, a)
    o4 = _orientation(c, d, b)
    if 0 in (o1, o2, o3, o4):
        return False
    return o1 != o2 and o3 != o4


def _orientation(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
) -> int:
    value = (b[1] - a[1]) * (c[0] - b[0]) - (b[0] - a[0]) * (c[1] - b[1])
    if abs(value) < 1e-6:
        return 0
    return 1 if value > 0 else 2


def _stitch_graph_gaps(
    adjacency: dict[tuple[float, float], list[tuple[tuple[float, float], float]]],
    stitch_gap: float,
    avoid_cross_geometries: list[list[tuple[float, float]]] | None = None,
    node_outward_normals: dict[tuple[float, float], list[tuple[float, float]]] | None = None,
) -> None:
    nodes = list(adjacency)
    for index, a in enumerate(nodes):
        for b in nodes[index + 1 :]:
            distance = math.dist(a, b)
            if distance <= stitch_gap:
                if not _stitch_nodes_are_directionally_compatible(adjacency, a, b):
                    continue
                if node_outward_normals and not _stitch_nodes_have_compatible_outward_side(node_outward_normals, a, b):
                    continue
                if avoid_cross_geometries and _segment_crosses_any_geometry(a, b, avoid_cross_geometries):
                    continue
                stitched_weight = distance * 18.0
                adjacency[a].append((b, stitched_weight))
                adjacency[b].append((a, stitched_weight))


def _stitch_nodes_are_directionally_compatible(
    adjacency: dict[tuple[float, float], list[tuple[tuple[float, float], float]]],
    a: tuple[float, float],
    b: tuple[float, float],
) -> bool:
    tangent_a = _node_tangent(adjacency, a)
    tangent_b = _node_tangent(adjacency, b)
    if tangent_a is None or tangent_b is None:
        return True
    conn = (b[0] - a[0], b[1] - a[1])
    conn_len = math.hypot(conn[0], conn[1]) or 1.0
    conn_u = (conn[0] / conn_len, conn[1] / conn_len)
    align_ab = abs(tangent_a[0] * tangent_b[0] + tangent_a[1] * tangent_b[1])
    align_conn_a = abs(tangent_a[0] * conn_u[0] + tangent_a[1] * conn_u[1])
    align_conn_b = abs(tangent_b[0] * conn_u[0] + tangent_b[1] * conn_u[1])
    return align_ab >= 0.8 and align_conn_a >= 0.6 and align_conn_b >= 0.6


def _node_tangent(
    adjacency: dict[tuple[float, float], list[tuple[tuple[float, float], float]]],
    node: tuple[float, float],
) -> tuple[float, float] | None:
    neighbors = [nb for nb, _w in adjacency.get(node, [])]
    if not neighbors:
        return None
    if len(neighbors) == 1:
        dx = neighbors[0][0] - node[0]
        dy = neighbors[0][1] - node[1]
        length = math.hypot(dx, dy) or 1.0
        return (dx / length, dy / length)
    # For interior nodes, use the two nearest neighbors to estimate the dominant local direction.
    ordered = sorted(neighbors, key=lambda nb: math.dist(node, nb))[:2]
    dx = (ordered[0][0] - node[0]) + (ordered[1][0] - node[0])
    dy = (ordered[0][1] - node[1]) + (ordered[1][1] - node[1])
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        dx = ordered[0][0] - node[0]
        dy = ordered[0][1] - node[1]
    length = math.hypot(dx, dy) or 1.0
    return (dx / length, dy / length)


def _stitch_nodes_have_compatible_outward_side(
    node_outward_normals: dict[tuple[float, float], list[tuple[float, float]]],
    a: tuple[float, float],
    b: tuple[float, float],
) -> bool:
    normal_a = _average_unit_vector(node_outward_normals.get(a, []))
    normal_b = _average_unit_vector(node_outward_normals.get(b, []))
    if normal_a is None or normal_b is None:
        return True
    return (normal_a[0] * normal_b[0] + normal_a[1] * normal_b[1]) >= 0.2


def _average_unit_vector(vectors: list[tuple[float, float]]) -> tuple[float, float] | None:
    if not vectors:
        return None
    sx = sum(item[0] for item in vectors)
    sy = sum(item[1] for item in vectors)
    length = math.hypot(sx, sy)
    if length <= 1e-9:
        return None
    return (sx / length, sy / length)


def _weighted_path_length(
    adjacency: dict[tuple[float, float], list[tuple[tuple[float, float], float]]],
    path: list[tuple[float, float]],
) -> float:
    total = 0.0
    for a, b in zip(path, path[1:]):
        weight = None
        for neighbor, edge_weight in adjacency.get(a, []):
            if neighbor == b:
                weight = edge_weight
                break
        if weight is None:
            weight = math.dist(a, b)
        total += weight
    return total


def _corridor_weight_for_layer(layer: str, route_mode: str = "", allow_bridge: bool = False) -> float:
    lower = layer.lower()
    if _is_final_project_layer(layer):
        return float("inf")
    if any(pattern in lower for pattern in CONDITIONAL_CORRIDOR_LAYER_PATTERNS):
        if route_mode == "overhead":
            return float("inf")
        return CORRIDOR_CLASS_WEIGHTS["conditional"] if allow_bridge else float("inf")
    if _is_existing_network_layer(layer):
        return CORRIDOR_CLASS_WEIGHTS["preferred"]
    if any(pattern in lower for pattern in PREFERRED_CORRIDOR_LAYER_PATTERNS):
        return CORRIDOR_CLASS_WEIGHTS["preferred"]
    if any(pattern in lower for pattern in ROAD_CORRIDOR_LAYER_PATTERNS):
        if route_mode == "underground":
            return CORRIDOR_CLASS_WEIGHTS["road"]
        return CORRIDOR_CLASS_WEIGHTS["allowed"]
    if any(pattern in lower for pattern in ALLOWED_CORRIDOR_LAYER_PATTERNS):
        return CORRIDOR_CLASS_WEIGHTS["allowed"]
    return CORRIDOR_CLASS_WEIGHTS["allowed"]


def _entity_points(entity) -> list[tuple[float, float]]:
    kind = entity.dxftype()
    if kind == "LWPOLYLINE":
        return [(float(x), float(y)) for x, y, *_ in entity.get_points("xy")]
    if kind == "LINE":
        start = entity.dxf.start
        end = entity.dxf.end
        return [(float(start[0]), float(start[1])), (float(end[0]), float(end[1]))]
    if kind == "POLYLINE":
        try:
            return [(float(v.dxf.location.x), float(v.dxf.location.y)) for v in entity.vertices]
        except Exception:
            return []
    return []


def _extract_relevant_subpath(
    points: list[tuple[float, float]],
    start_point: tuple[float, float],
    end_point: tuple[float, float],
) -> list[tuple[float, float]]:
    if len(points) <= 2:
        return points
    start_idx = _nearest_vertex_index(start_point, points)
    end_idx = _nearest_vertex_index(end_point, points)
    if start_idx == end_idx:
        return [points[start_idx]]
    if start_idx < end_idx:
        return points[start_idx : end_idx + 1]
    return list(reversed(points[end_idx : start_idx + 1]))


def _nearest_vertex_index(point: tuple[float, float], points: list[tuple[float, float]]) -> int:
    best_index = 0
    best_distance = float("inf")
    for index, candidate in enumerate(points):
        distance = math.dist(point, candidate)
        if distance < best_distance:
            best_distance = distance
            best_index = index
    return best_index


def _nearest_graph_node(
    point: tuple[float, float],
    nodes: Iterable[tuple[float, float]],
) -> tuple[float, float] | None:
    best = None
    best_distance = float("inf")
    for node in nodes:
        distance = math.dist(point, node)
        if distance < best_distance:
            best_distance = distance
            best = node
    return best


def _nearest_graph_nodes(
    point: tuple[float, float],
    nodes: Iterable[tuple[float, float]],
    limit: int = 5,
    max_distance: float | None = None,
) -> list[tuple[tuple[float, float], float]]:
    ranked = []
    for node in nodes:
        distance = math.dist(point, node)
        if max_distance is not None and distance > max_distance:
            continue
        ranked.append((node, distance))
    ranked.sort(key=lambda item: item[1])
    return ranked[:limit]


def _connected_components(
    adjacency: dict[tuple[float, float], list[tuple[tuple[float, float], float]]],
) -> list[list[tuple[float, float]]]:
    components = []
    visited = set()
    for node in adjacency:
        if node in visited:
            continue
        stack = [node]
        visited.add(node)
        component = []
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbor, _weight in adjacency.get(current, []):
                if neighbor not in visited:
                    visited.add(neighbor)
                    stack.append(neighbor)
        components.append(component)
    return components


def _walk_component_route(
    adjacency: dict[tuple[float, float], list[tuple[tuple[float, float], float]]],
    component: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    component_set = set(component)
    endpoints = [node for node in component if sum(1 for nb, _w in adjacency.get(node, []) if nb in component_set) <= 1]
    start = endpoints[0] if endpoints else component[0]
    route = [start]
    previous = None
    current = start

    while True:
        neighbors = [nb for nb, _w in adjacency.get(current, []) if nb in component_set and nb != previous]
        if not neighbors:
            break
        next_node = neighbors[0]
        route.append(next_node)
        previous, current = current, next_node

    return route


def _shortest_path(
    adjacency: dict[tuple[float, float], list[tuple[tuple[float, float], float]]],
    start: tuple[float, float],
    end: tuple[float, float],
) -> list[tuple[float, float]]:
    distances = {start: 0.0}
    previous: dict[tuple[float, float], tuple[float, float] | None] = {start: None}
    heap: list[tuple[float, tuple[float, float]]] = [(0.0, start)]

    while heap:
        distance, node = heapq.heappop(heap)
        if node == end:
            break
        if distance > distances.get(node, float("inf")):
            continue
        for neighbor, weight in adjacency.get(node, []):
            nd = distance + weight
            if nd < distances.get(neighbor, float("inf")):
                distances[neighbor] = nd
                previous[neighbor] = node
                heapq.heappush(heap, (nd, neighbor))

    if end not in previous:
        return []

    path = []
    node: tuple[float, float] | None = end
    while node is not None:
        path.append(node)
        node = previous.get(node)
    path.reverse()
    return path


def _round_point(point: tuple[float, float]) -> tuple[float, float]:
    return (round(float(point[0]), 3), round(float(point[1]), 3))


def _min_vertex_distance(point: tuple[float, float], points: list[tuple[float, float]]) -> float:
    return min(math.dist(point, item) for item in points)


def _attach_anchors(points: list[tuple[float, float]], start: Anchor, end: Anchor) -> list[tuple[float, float]]:
    route = list(points)
    if math.dist((start.x, start.y), route[0]) > 1.0:
        route.insert(0, (start.x, start.y))
    if math.dist((end.x, end.y), route[-1]) > 1.0:
        route.append((end.x, end.y))
    return route


def _polyline_length(points: list[tuple[float, float]]) -> float:
    total = 0.0
    for a, b in zip(points, points[1:]):
        total += math.dist(a, b)
    return total


def _max_anchor_attachment_length(
    route_points: list[tuple[float, float]],
    start: Anchor,
    end: Anchor,
) -> float:
    if len(route_points) < 2:
        return float("inf")
    start_len = math.dist((start.x, start.y), route_points[1]) if route_points[0] == (start.x, start.y) else math.dist((start.x, start.y), route_points[0])
    end_len = math.dist((end.x, end.y), route_points[-2]) if route_points[-1] == (end.x, end.y) else math.dist((end.x, end.y), route_points[-1])
    return max(start_len, end_len)


def _expected_length_penalty(output_index: int, route_length: float, output_code: str | None = None) -> float:
    bounds = _expected_bounds(output_index, output_code)
    if not bounds:
        return 0.0
    lower, upper = bounds
    if lower <= route_length <= upper:
        return 0.0
    if route_length < lower:
        return (lower - route_length) * 2.5
    return (route_length - upper) * 2.5


def _expected_length_score(output_index: int, route_length: float, output_code: str | None = None) -> float:
    bounds = _expected_bounds(output_index, output_code)
    penalty = _expected_length_penalty(output_index, route_length, output_code)
    if not bounds:
        return penalty
    lower, upper = bounds
    midpoint = (lower + upper) / 2.0
    return penalty + abs(route_length - midpoint)


def _expected_bounds(output_index: int, output_code: str | None = None) -> tuple[float, float] | None:
    if output_code:
        code_bounds = EXPECTED_ROUTE_LENGTHS_BY_CODE.get(output_code)
        if code_bounds:
            return code_bounds
    return EXPECTED_ROUTE_LENGTHS.get(output_index)


def save_document(doc: ezdxf.document.Drawing, output_path: str | Path) -> None:
    doc.saveas(str(output_path))
