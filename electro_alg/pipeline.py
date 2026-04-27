from __future__ import annotations

import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
from pathlib import Path

from .dxf_ops import (
    add_plan_overlay,
    build_route_segments,
    classify_layers,
    convert_dxf_to_dwg,
    extract_text_entities,
    find_anchors,
    open_document,
    save_document,
)
from .config import (
    OVERHEAD_CONDUCTOR_PHASE_FACTOR,
    OVERHEAD_CONDUCTOR_ROUTE_RESERVE_FACTOR,
    OVERHEAD_CONDUCTOR_TERMINAL_ALLOWANCE_PER_PHASE,
    PROJECT_OUTPUT_ROUTE_LAYERS_BY_CODE,
    UNDERGROUND_CABLE_PHASE_FACTOR,
    UNDERGROUND_CABLE_ROUTE_RESERVE_FACTOR,
    UNDERGROUND_CABLE_TERMINAL_ALLOWANCE_PER_PHASE,
)
from .models import Anchor, DesignModel, QuantityItem, QuantityReport
from .path_utils import resolve_existing_path
from .rules import apply_default_rules
from .task_parser import (
    _normalize_search_text,
    assess_input_readiness,
    apply_project_musts,
    load_input_text,
    parse_constraints,
    parse_project_outputs,
)


def _load_input_texts_parallel(paths: list[str | Path], max_workers: int = 8) -> list[str]:
    if not paths:
        return []
    worker_count = max(1, min(max_workers, len(paths)))
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        return list(pool.map(load_input_text, paths))


def _load_manual_anchors(anchors_path: str | None) -> dict:
    if not anchors_path:
        return {}
    path = resolve_existing_path(anchors_path)
    if not path.exists():
        raise ValueError(f"Anchors JSON nije pronadjen: {path}")
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if "anchors" in data and isinstance(data["anchors"], dict):
        data = data["anchors"]

    anchors = {}
    for name, raw in data.items():
        entries = raw if isinstance(raw, list) else [raw]
        parsed_entries = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            if "x" not in item or "y" not in item:
                continue
            parsed_entries.append(
                Anchor(
                    name=name,
                    layer=item.get("layer", "MANUAL_ANCHORS"),
                    x=float(item["x"]),
                    y=float(item["y"]),
                    text=item.get("text", name),
                    score=float(item.get("score", 1000.0)),
                )
            )
        if parsed_entries:
            anchors[name.lower().strip()] = parsed_entries
    return anchors


def _load_manual_route_hints(anchors_path: str | None) -> dict[str, list[tuple[float, float]]]:
    if not anchors_path:
        return {}
    path = resolve_existing_path(anchors_path)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    routes = data.get("routes", {})
    parsed = {}
    for code, points in routes.items():
        if not isinstance(points, list):
            continue
        clean_points = []
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            clean_points.append((float(point[0]), float(point[1])))
        if len(clean_points) >= 2:
            parsed[str(code)] = clean_points
    return parsed


def _merge_anchor_maps(detected: dict, manual: dict) -> dict:
    merged = {}
    keys = set(detected) | set(manual)
    for key in keys:
        manual_entries = manual.get(key, [])
        detected_entries = list(detected.get(key, []))
        combined = manual_entries + detected_entries
        if not combined:
            continue
        combined.sort(key=lambda item: item.score, reverse=True)
        merged[key] = combined
    return merged


def _augment_anchors_from_route_hints(outputs, anchors: dict, route_hints: dict[str, list[tuple[float, float]]]) -> dict:
    def _append_candidate(key: str, point: tuple[float, float], label: str) -> None:
        candidates = list(anchors.get(key, []))
        for existing in candidates:
            if math.dist((existing.x, existing.y), point) <= 5.0:
                return
        candidates.append(
            Anchor(
                name=key,
                layer="MANUAL_ROUTE_HINT",
                x=float(point[0]),
                y=float(point[1]),
                text=label,
                score=850.0,
            )
        )
        candidates.sort(key=lambda item: item.score, reverse=True)
        anchors[key] = candidates

    for output in outputs:
        code = (getattr(output, "code", None) or str(output.index)).strip()
        points = route_hints.get(code)
        if not points or len(points) < 2:
            continue
        start_key = (output.start_point or "").lower().strip()
        end_key = (output.end_point or "").lower().strip()
        if start_key:
            _append_candidate(start_key, points[0], f"Route hint {code} start")
        if end_key:
            _append_candidate(end_key, points[-1], f"Route hint {code} end")
    return anchors


def _name_aliases(name: str) -> list[str]:
    normalized = _normalize_search_text(name or "")
    aliases = {normalized}
    if not normalized:
        return []
    match = re.search(r"\bk0([0-9])\b", normalized)
    if match:
        aliases.add(f"k0{match.group(1)}")
    if "uzb stub broj" in normalized or "uz stub" in normalized:
        num_match = re.search(r"stub\s+broj\s+([0-9]+)", normalized)
        if not num_match:
            num_match = re.search(r"stub\s+([0-9]+)", normalized)
        if num_match:
            num = num_match.group(1)
            aliases.update(
                {
                    f"uzb stub broj {num}",
                    f"uz stub broj {num}",
                    f"novi uzb stub broj {num}",
                    f"postojeci uzb stub broj {num}",
                    f"novi stub br. {num}",
                    f"novi stub br {num}",
                }
            )
    for token in ("krstac kula", "fapromal", "lisice 1", "lisice 3", "puhovo - krstac", "puhovo krstac"):
        if token in normalized:
            aliases.add(token)
    if normalized.startswith("mbts "):
        aliases.add(normalized.replace("mbts ", "").strip())
    if normalized.startswith("pts "):
        aliases.add(normalized.replace("pts ", "").strip())
    if normalized.startswith("ts 10/0,4kv "):
        aliases.add(normalized.replace("ts 10/0,4kv ", "").strip())
    return [alias for alias in aliases if alias]


def _match_alias_score(text: str, aliases: list[str]) -> int:
    score = 0
    for alias in aliases:
        if alias and alias in text:
            score = max(score, len(alias))
    return score


def _entity_points_from_handle(doc, handle: str) -> list[tuple[float, float]]:
    if not handle:
        return []
    entity = doc.entitydb.get(handle)
    if entity is None:
        return []
    kind = entity.dxftype()
    if kind == "LWPOLYLINE":
        return [(float(x), float(y)) for x, y, *_ in entity.get_points("xy")]
    if kind == "LINE":
        start = entity.dxf.start
        end = entity.dxf.end
        return [(float(start.x), float(start.y)), (float(end.x), float(end.y))]
    if kind == "POLYLINE":
        try:
            return [(float(v.dxf.location.x), float(v.dxf.location.y)) for v in entity.vertices]
        except Exception:
            return []
    return []


def _orient_points_from_leader_texts(
    points: list[tuple[float, float]],
    matched_records: list[dict],
    start_aliases: list[str],
    end_aliases: list[str],
) -> list[tuple[float, float]]:
    if len(points) < 2:
        return points
    start_point = points[0]
    end_point = points[-1]
    start_related = []
    end_related = []
    for record in matched_records:
        normalized_text = record.get("_normalized_text", "")
        arrow = tuple(record.get("arrow_point", [])[:2])
        if len(arrow) < 2:
            continue
        if _match_alias_score(normalized_text, start_aliases) > 0:
            start_related.append(arrow)
        if _match_alias_score(normalized_text, end_aliases) > 0:
            end_related.append(arrow)

    def _cost(route_start: tuple[float, float], route_end: tuple[float, float]) -> float:
        total = 0.0
        for arrow in start_related:
            total += math.dist(arrow, route_start)
        for arrow in end_related:
            total += math.dist(arrow, route_end)
        return total

    normal_cost = _cost(start_point, end_point)
    reversed_cost = _cost(end_point, start_point)
    if reversed_cost + 1.0 < normal_cost:
        return list(reversed(points))
    return points


def _orient_points_against_known_anchors(
    points: list[tuple[float, float]],
    start_name: str,
    end_name: str,
    learned_anchors: dict[str, dict],
) -> list[tuple[float, float]]:
    if len(points) < 2:
        return points
    refs = []
    if start_name and start_name in learned_anchors:
        refs.append(("start", (learned_anchors[start_name]["x"], learned_anchors[start_name]["y"])))
    if end_name and end_name in learned_anchors:
        refs.append(("end", (learned_anchors[end_name]["x"], learned_anchors[end_name]["y"])))
    if not refs:
        return points

    def _cost(route_start: tuple[float, float], route_end: tuple[float, float]) -> float:
        total = 0.0
        for kind, ref in refs:
            total += math.dist(ref, route_start if kind == "start" else route_end)
        return total

    normal_cost = _cost(points[0], points[-1])
    reversed_cost = _cost(points[-1], points[0])
    if reversed_cost + 1.0 < normal_cost:
        return list(reversed(points))
    return points


def learn_bootstrap_from_final(
    final_dxf_path: str,
    leader_route_map_path: str,
    project_task_text_path: str,
    condition_paths: list[str] | None = None,
    output_json: str | None = None,
) -> dict:
    condition_paths = condition_paths or []
    loaded_texts = _load_input_texts_parallel([project_task_text_path, *condition_paths])
    project_task_text = loaded_texts[0]
    condition_texts = loaded_texts[1:]
    outputs = parse_project_outputs(project_task_text)
    constraints: list = []
    for path, text in zip(condition_paths, condition_texts):
        constraints.extend(parse_constraints(path, text=text))
    outputs = apply_project_musts(outputs, project_task_text, condition_texts, constraints)

    final_doc = open_document(final_dxf_path)
    raw_records = json.loads(resolve_existing_path(leader_route_map_path).read_text(encoding="utf-8"))
    records: list[dict] = []
    for record in raw_records:
        normalized_text = _normalize_search_text(record.get("text", ""))
        route = record.get("nearest_route") or {}
        handle = route.get("handle")
        arrow = tuple((record.get("arrow_point") or [])[:2])
        if not handle or len(arrow) < 2:
            continue
        records.append(
            {
                **record,
                "_normalized_text": normalized_text,
                "_handle": str(handle),
                "_layer": str(route.get("layer", "")),
            }
        )

    anchors: dict[str, dict] = {}
    routes: dict[str, list[list[float]]] = {}
    learning: dict[str, dict] = {}

    for output in outputs:
        code = (output.code or str(output.index)).strip()
        start_aliases = _name_aliases(output.start_point or "")
        end_aliases = _name_aliases(output.end_point or "")
        handle_scores: dict[str, float] = defaultdict(float)
        handle_records: dict[str, list[dict]] = defaultdict(list)

        for record in records:
            text = record["_normalized_text"]
            route_layer = record["_layer"]
            normalized_layer = _normalize_search_text(route_layer)
            start_score = _match_alias_score(text, start_aliases)
            end_score = _match_alias_score(text, end_aliases)
            score = 0.0
            if start_score:
                score += 5.0 + start_score / 100.0
            if end_score:
                score += 5.0 + end_score / 100.0
            if f'k0{output.index}' in text and output.code and output.code.isdigit():
                score += 2.0
            if code.lower() in text:
                score += 1.0
            expected_layer = PROJECT_OUTPUT_ROUTE_LAYERS_BY_CODE.get(code)
            if expected_layer and _normalize_search_text(expected_layer) == normalized_layer:
                score += 4.0
            elif expected_layer:
                end_layer_match = max(
                    (_match_alias_score(normalized_layer, [alias]) for alias in end_aliases if len(alias) >= 4),
                    default=0,
                )
                if end_layer_match:
                    score += 2.0
            route_mode = (output.route_mode or "").strip().lower()
            if route_mode == "overhead":
                if "elektroene" in normalized_layer or "puhovo" in normalized_layer:
                    score += 3.0
                if "krstac kula" in normalized_layer or "fapromal" in normalized_layer or "lisice 3" in normalized_layer:
                    score -= 2.5
            if score <= 0:
                continue
            handle = record["_handle"]
            handle_scores[handle] += score
            handle_records[handle].append(record)

        if not handle_scores:
            continue

        best_handle = max(
            handle_scores,
            key=lambda handle: (handle_scores[handle], len(handle_records[handle])),
        )
        points = _entity_points_from_handle(final_doc, best_handle)
        if len(points) < 2:
            continue
        points = _orient_points_from_leader_texts(points, handle_records[best_handle], start_aliases, end_aliases)
        points = _orient_points_against_known_anchors(
            points,
            (output.start_point or "").lower().strip(),
            (output.end_point or "").lower().strip(),
            anchors,
        )
        routes[code] = [[round(x, 3), round(y, 3)] for x, y in points]

        start_name = (output.start_point or "").lower().strip()
        end_name = (output.end_point or "").lower().strip()
        if start_name and start_name not in anchors:
            anchors[start_name] = {
                "x": round(points[0][0], 3),
                "y": round(points[0][1], 3),
                "layer": final_doc.entitydb.get(best_handle).dxf.layer,
                "text": f"Learned from final route {code} start",
                "score": 1250,
            }
        if end_name and end_name not in anchors:
            anchors[end_name] = {
                "x": round(points[-1][0], 3),
                "y": round(points[-1][1], 3),
                "layer": final_doc.entitydb.get(best_handle).dxf.layer,
                "text": f"Learned from final route {code} end",
                "score": 1250,
            }

        learning[code] = {
            "route_handle": best_handle,
            "route_layer": final_doc.entitydb.get(best_handle).dxf.layer,
            "match_score": round(handle_scores[best_handle], 3),
            "matched_leaders": [record.get("leader_handle") for record in handle_records[best_handle]],
            "matched_texts": [record.get("text") for record in handle_records[best_handle]],
            "start_point": output.start_point,
            "end_point": output.end_point,
        }

    payload = {
        "anchors": anchors,
        "routes": routes,
        "learning": learning,
    }
    if output_json:
        Path(output_json).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def _distance(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(ax - bx, ay - by)


def _output_semantics_by_point(outputs) -> dict[str, list[dict]]:
    semantics: dict[str, list[dict]] = defaultdict(list)
    for output in outputs:
        musts = dict(getattr(output, "route_musts", {}) or {})
        for role, point_name in (("start", output.start_point), ("end", output.end_point)):
            raw_key = (point_name or "").lower().strip()
            if not raw_key:
                continue
            anchor_type = musts.get(f"{role}_anchor_type")
            physical_target = musts.get(f"{role}_physical_target")
            key = _normalize_search_text(point_name or "") or raw_key
            if anchor_type in {
                "existing_uzb_stub",
                "existing_uzb_stub_exterior_connection",
                "new_uzb_stub",
                "new_uzb_stub_exterior_connection",
            } and physical_target:
                key = _normalize_search_text(str(physical_target)) or raw_key
            semantics[key].append(
                {
                    "raw_key": raw_key,
                    "code": output.code or str(output.index),
                    "role": role,
                    "anchor_type": anchor_type,
                    "anchor_region": musts.get(f"{role}_anchor_region"),
                    "physical_target": physical_target,
                    "functional_target": musts.get(f"{role}_functional_target"),
                    "corridor_alignment_target": musts.get("corridor_alignment_target"),
                    "parcel_hint": musts.get(f"{role}_parcel_hint"),
                    "ko_hint": musts.get(f"{role}_ko_hint"),
                    "prev_parcel_hint": musts.get(f"{role}_prev_parcel_hint"),
                    "prev_ko_hint": musts.get(f"{role}_prev_ko_hint"),
                    "next_parcel_hint": musts.get(f"{role}_next_parcel_hint"),
                    "next_ko_hint": musts.get(f"{role}_next_ko_hint"),
                    "parcel_corridor": list(musts.get("parcel_corridor") or []),
                    "route_mode": output.route_mode,
                }
            )
    return semantics


def _target_text_points(doc, target_text: str | None) -> list[tuple[float, float, str]]:
    if not target_text:
        return []
    target = _normalize_search_text(target_text)
    if not target:
        return []
    generic_tokens = {
        "ts", "pts", "mbts", "uzb", "uz", "stub", "novi", "postojeci", "postojeca",
        "veza", "trafostanici", "trafostanica", "trafostanice", "kv", "10", "0", "4",
        "broj", "za", "na", "od", "do", "u", "sa", "kv",
    }
    target_tokens = {
        token
        for token in target.split()
        if (len(token) >= 2 or token.isdigit()) and token not in generic_tokens
    }
    if target_tokens and all(token.isdigit() for token in target_tokens):
        return []
    if not target_tokens:
        target_tokens = {token for token in target.split() if len(token) >= 2 or token.isdigit()}
    if not target_tokens:
        return []

    hits: list[tuple[float, float, str, int, int]] = []
    for item in extract_text_entities(doc):
        text = _normalize_search_text(str(item.get("text", "")))
        if not text:
            continue
        text_tokens = {
            token
            for token in text.split()
            if (len(token) >= 2 or token.isdigit()) and token not in generic_tokens
        }
        if not text_tokens:
            text_tokens = set(text.split())
        overlap = len(target_tokens & text_tokens)
        if target in text:
            overlap += 3
        required_overlap = 1 if len(target_tokens) == 1 else min(2, len(target_tokens))
        if overlap < required_overlap:
            continue
        hits.append((float(item["x"]), float(item["y"]), str(item["layer"]), overlap, len(text_tokens)))

    hits.sort(key=lambda item: (item[3], -item[4]), reverse=True)
    return [(x, y, layer) for x, y, layer, _, _ in hits[:5]]


def _shaft_signal_points(doc) -> list[tuple[float, float, str, str]]:
    signals: list[tuple[float, float, str, str]] = []
    for item in extract_text_entities(doc):
        layer = _normalize_search_text(str(item.get("layer", "")))
        text = _normalize_search_text(str(item.get("text", "")))
        if "saht" not in layer and "saht" not in text and not re.fullmatch(r"eo[0-9]+", text):
            continue
        signals.append(
            (
                float(item["x"]),
                float(item["y"]),
                str(item.get("layer", "")),
                str(item.get("text", "")),
            )
        )
    return signals


def _point_segment_distance(
    px: float,
    py: float,
    ax: float,
    ay: float,
    bx: float,
    by: float,
) -> float:
    vx = bx - ax
    vy = by - ay
    wx = px - ax
    wy = py - ay
    c1 = vx * wx + vy * wy
    if c1 <= 0.0:
        return math.dist((px, py), (ax, ay))
    c2 = vx * vx + vy * vy
    if c2 <= c1:
        return math.dist((px, py), (bx, by))
    t = c1 / c2
    qx = ax + t * vx
    qy = ay + t * vy
    return math.dist((px, py), (qx, qy))


def _nearest_point_on_segment(
    px: float,
    py: float,
    ax: float,
    ay: float,
    bx: float,
    by: float,
) -> tuple[float, float]:
    vx = bx - ax
    vy = by - ay
    wx = px - ax
    wy = py - ay
    c1 = vx * wx + vy * wy
    if c1 <= 0.0:
        return ax, ay
    c2 = vx * vx + vy * vy
    if c2 <= c1:
        return bx, by
    t = c1 / c2
    return ax + t * vx, ay + t * vy


def _entity_distance_to_point(entity, x: float, y: float) -> float | None:
    try:
        kind = entity.dxftype()
        if kind == "INSERT":
            return math.dist((x, y), (float(entity.dxf.insert.x), float(entity.dxf.insert.y)))
        if kind == "POINT":
            return math.dist((x, y), (float(entity.dxf.location.x), float(entity.dxf.location.y)))
        if kind == "TEXT":
            return math.dist((x, y), (float(entity.dxf.insert.x), float(entity.dxf.insert.y)))
        if kind == "MTEXT":
            return math.dist((x, y), (float(entity.dxf.insert.x), float(entity.dxf.insert.y)))
        if kind == "LINE":
            return _point_segment_distance(
                x,
                y,
                float(entity.dxf.start.x),
                float(entity.dxf.start.y),
                float(entity.dxf.end.x),
                float(entity.dxf.end.y),
            )
        if kind == "LWPOLYLINE":
            pts = [(float(px), float(py)) for px, py, *_ in entity.get_points("xy")]
        elif kind == "POLYLINE":
            pts = [(float(v.dxf.location.x), float(v.dxf.location.y)) for v in entity.vertices]
        else:
            return None
        if len(pts) < 2:
            return None
        best = None
        for a, b in zip(pts, pts[1:]):
            dist = _point_segment_distance(x, y, a[0], a[1], b[0], b[1])
            if best is None or dist < best:
                best = dist
        return best
    except Exception:
        return None


def _corridor_projection_points(
    doc,
    origin_x: float,
    origin_y: float,
    *,
    layers: tuple[str, ...] = ("10kv", "elektrovodovi"),
    min_distance: float = 35.0,
    max_distance: float = 140.0,
    limit: int = 5,
) -> list[tuple[float, float, str, float]]:
    rows: list[tuple[float, float, str, float]] = []
    for entity in doc.modelspace():
        layer = str(entity.dxf.get("layer", "")).lower()
        if layer not in layers:
            continue
        kind = entity.dxftype()
        pts: list[tuple[float, float]] = []
        if kind == "LINE":
            pts = [
                (float(entity.dxf.start.x), float(entity.dxf.start.y)),
                (float(entity.dxf.end.x), float(entity.dxf.end.y)),
            ]
        elif kind == "LWPOLYLINE":
            pts = [(float(x), float(y)) for x, y, *_ in entity.get_points("xy")]
        elif kind == "POLYLINE":
            pts = [(float(v.dxf.location.x), float(v.dxf.location.y)) for v in entity.vertices]
        if len(pts) < 2:
            continue
        best = None
        best_q = None
        for a, b in zip(pts, pts[1:]):
            qx, qy = _nearest_point_on_segment(origin_x, origin_y, a[0], a[1], b[0], b[1])
            dist = math.dist((origin_x, origin_y), (qx, qy))
            if best is None or dist < best:
                best = dist
                best_q = (qx, qy)
        if best is None or best_q is None:
            continue
        if best < min_distance or best > max_distance:
            continue
        rows.append((best_q[0], best_q[1], layer, best))
    rows.sort(key=lambda item: item[3])
    deduped: list[tuple[float, float, str, float]] = []
    for x, y, layer, dist in rows:
        if any(math.dist((x, y), (ox, oy)) <= 5.0 for ox, oy, _, _ in deduped):
            continue
        deduped.append((x, y, layer, dist))
        if len(deduped) >= limit:
            break
    return deduped


def _local_object_context(doc, x: float, y: float, radius: float = 18.0) -> dict:
    cache = getattr(_local_object_context, "_cache", None)
    if cache is None:
        cache = {}
        setattr(_local_object_context, "_cache", cache)
    cache_key = (id(doc), round(x, 3), round(y, 3), round(radius, 1))
    if cache_key in cache:
        return cache[cache_key]

    context = {
        "building_distance": None,
        "stub_signal_distance": None,
        "corridor_distance": None,
        "stub_signal_names": set(),
    }
    for entity in doc.modelspace():
        layer = str(entity.dxf.get("layer", "")).lower()
        dist = _entity_distance_to_point(entity, x, y)
        if dist is None or dist > radius:
            continue
        if layer == "l1_gra_zgrade":
            current = context["building_distance"]
            context["building_distance"] = dist if current is None else min(current, dist)
        if layer in {"10kv", "t2_elektroene", "l2_elektroene", "elektrovodovi"}:
            current = context["corridor_distance"]
            context["corridor_distance"] = dist if current is None else min(current, dist)
        if entity.dxftype() == "INSERT":
            name = str(entity.dxf.get("name", ""))
            lower_name = name.lower()
            if layer in {"t2_elektroene", "elektrovodovi", "l2_elektroene"} or any(
                token in lower_name for token in ("ele", "pik", "tk", "p2ele")
            ):
                current = context["stub_signal_distance"]
                context["stub_signal_distance"] = dist if current is None else min(current, dist)
                if name:
                    context["stub_signal_names"].add(name)
    cache[cache_key] = context
    return context


def _semantic_cluster_bonus(doc, cluster: dict, anchor_type: str | None) -> tuple[float, str]:
    if not anchor_type:
        return 0.0, "generic"

    context = _local_object_context(doc, float(cluster["x"]), float(cluster["y"]))
    building_distance = context["building_distance"]
    stub_distance = context["stub_signal_distance"]
    corridor_distance = context["corridor_distance"]

    bonus = 0.0
    mode = "generic"
    if anchor_type in {"existing_substation", "existing_substation_exterior_connection"}:
        if building_distance is not None:
            bonus += 42.0 - min(building_distance, 20.0) * 1.6
            mode = "building_adjacent"
        else:
            bonus -= 24.0
        if stub_distance is not None:
            bonus += max(0.0, 8.0 - min(stub_distance, 16.0) * 0.25)
    elif anchor_type in {"existing_uzb_stub", "existing_uzb_stub_exterior_connection"}:
        if stub_distance is not None:
            bonus += 36.0 - min(stub_distance, 20.0) * 1.2
            mode = "anchored_to_existing_signal"
        else:
            bonus -= 12.0
        if building_distance is not None:
            bonus += max(0.0, 6.0 - min(building_distance, 18.0) * 0.25)
    elif anchor_type in {"new_uzb_stub", "new_uzb_stub_exterior_connection"}:
        if stub_distance is not None:
            bonus += 20.0 - min(stub_distance, 18.0) * 0.8
            mode = "anchored_to_existing_signal"
        elif corridor_distance is not None or "10kv" in cluster["layers"] or "elektrovodovi" in cluster["layers"]:
            bonus += 12.0
            mode = "placed_on_corridor"
        if building_distance is not None:
            bonus -= 10.0
    elif anchor_type == "shaft_connection":
        if corridor_distance is not None:
            bonus += max(0.0, 8.0 - min(corridor_distance, 16.0) * 0.3)
    return bonus, mode


def _parse_dv_stub_anchor_name(name: str) -> dict | None:
    match = re.match(
        r"(?P<kind>novi|postojeci)\s+dv\s+stub\s+10\s+kv\s+(?P<parcel>[0-9/]+)\s+ko\s+(?P<ko>[a-z0-9]+)",
        name.lower().strip(),
    )
    if not match:
        return None
    return match.groupdict()


def _electro_insert_candidates(doc) -> list[Anchor]:
    candidates: list[Anchor] = []
    for entity in doc.modelspace():
        if entity.dxftype() != "INSERT":
            continue
        layer = entity.dxf.get("layer", "")
        lower_layer = layer.lower()
        if "elektro" not in lower_layer:
            continue
        block_name = entity.dxf.get("name", "")
        score = 50.0
        if block_name.upper().startswith("ELE"):
            score = 100.0
        elif "ELE" in block_name.upper():
            score = 80.0
        candidates.append(
            Anchor(
                name=block_name or "electro_insert",
                layer=layer,
                x=float(entity.dxf.insert.x),
                y=float(entity.dxf.insert.y),
                text=block_name,
                score=score,
            )
        )
    return candidates


def _parcel_label_points(doc, parcel: str) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    target = _normalize_search_text(parcel)
    if not target:
        return points
    for item in extract_text_entities(doc):
        text = _normalize_search_text(str(item["text"]).strip())
        if text != target:
            continue
        layer = _normalize_search_text(str(item["layer"]))
        if "broj parcele" not in layer and "brojparcele" not in layer:
            continue
        points.append((float(item["x"]), float(item["y"])))
    return points


def _parcel_closed_boundary_points(
    doc,
    parcel: str,
    *,
    search_radius: float = 120.0,
    endpoint_merge_tolerance: float = 1.5,
    max_cycle_len: int = 8,
    _retry_expanded_cycle: bool = True,
) -> list[tuple[float, float]]:
    label_points = _parcel_label_points(doc, parcel)
    if not label_points:
        return []
    px, py = label_points[0]

    def _segment_distance(p1: tuple[float, float], p2: tuple[float, float]) -> float:
        return _point_segment_distance(px, py, p1[0], p1[1], p2[0], p2[1])

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

    def _point_in_polygon(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
        x, y = point
        inside = False
        count = len(polygon)
        for idx in range(count):
            x1, y1 = polygon[idx]
            x2, y2 = polygon[(idx + 1) % count]
            if ((y1 > y) != (y2 > y)) and (x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1):
                inside = not inside
        return inside

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
            return _parcel_closed_boundary_points(
                doc,
                parcel,
                search_radius=search_radius,
                endpoint_merge_tolerance=endpoint_merge_tolerance,
                max_cycle_len=12,
                _retry_expanded_cycle=False,
            )
        return []
    candidates.sort(key=lambda item: item[0])
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
            qx, qy = _nearest_point_on_segment(tx, ty, a[0], a[1], b[0], b[1])
            rows.append((qx, qy, math.dist((tx, ty), (qx, qy))))
    rows.sort(key=lambda item: item[2])
    deduped: list[tuple[float, float, float]] = []
    for qx, qy, dist in rows:
        if any(math.dist((qx, qy), (ox, oy)) <= 4.0 for ox, oy, _ in deduped):
            continue
        deduped.append((qx, qy, dist))
        if len(deduped) >= limit:
            break
    return deduped


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


def _building_projection_points(
    doc,
    parcel_points: list[tuple[float, float]],
    *,
    max_distance: float = 40.0,
    limit: int = 4,
) -> list[tuple[float, float, str, float]]:
    rows: list[tuple[float, float, str, float]] = []
    for entity in doc.modelspace():
        layer = str(entity.dxf.get("layer", "")).lower()
        if layer != "l1_gra_zgrade":
            continue
        kind = entity.dxftype()
        pts: list[tuple[float, float]] = []
        if kind == "LINE":
            pts = [
                (float(entity.dxf.start.x), float(entity.dxf.start.y)),
                (float(entity.dxf.end.x), float(entity.dxf.end.y)),
            ]
        elif kind == "LWPOLYLINE":
            pts = [(float(x), float(y)) for x, y, *_ in entity.get_points("xy")]
        elif kind == "POLYLINE":
            pts = [(float(v.dxf.location.x), float(v.dxf.location.y)) for v in entity.vertices]
        if len(pts) < 2:
            continue
        best = None
        best_q = None
        for px, py in parcel_points:
            for a, b in zip(pts, pts[1:]):
                qx, qy = _nearest_point_on_segment(px, py, a[0], a[1], b[0], b[1])
                dist = math.dist((px, py), (qx, qy))
                if best is None or dist < best:
                    best = dist
                    best_q = (qx, qy)
        if best is None or best_q is None or best > max_distance:
            continue
        rows.append((best_q[0], best_q[1], layer, best))
    rows.sort(key=lambda item: item[3])
    deduped: list[tuple[float, float, str, float]] = []
    for x, y, layer, dist in rows:
        if any(math.dist((x, y), (ox, oy)) <= 5.0 for ox, oy, _, _ in deduped):
            continue
        deduped.append((x, y, layer, dist))
        if len(deduped) >= limit:
            break
    return deduped


def _augment_stub_anchors_from_geometry(doc, outputs, anchors: dict) -> dict:
    electro_candidates = _electro_insert_candidates(doc)
    if not electro_candidates:
        return anchors

    dv_specs = []
    for output in outputs:
        for point_name in [output.start_point, output.end_point]:
            if not point_name:
                continue
            key = point_name.lower().strip()
            parsed = _parse_dv_stub_anchor_name(key)
            if not parsed:
                continue
            dv_specs.append((key, parsed))

    if not dv_specs:
        return anchors

    used_coords: list[tuple[float, float]] = []
    unresolved: list[tuple[str, dict]] = []
    for key, parsed in dv_specs:
        if key in anchors:
            used_coords.extend((anchor.x, anchor.y) for anchor in anchors[key])
            continue

        parcel_points = _parcel_label_points(doc, parsed["parcel"])
        if parcel_points:
            best_anchor = None
            best_distance = None
            for px, py in parcel_points:
                for candidate in electro_candidates:
                    distance = _distance(px, py, candidate.x, candidate.y)
                    if best_distance is None or distance < best_distance:
                        best_distance = distance
                        best_anchor = candidate
            if best_anchor is not None:
                anchors[key] = [
                    Anchor(
                        name=key,
                        layer=best_anchor.layer,
                        x=best_anchor.x,
                        y=best_anchor.y,
                        text=f"{best_anchor.text} @ parcel {parsed['parcel']}",
                        score=best_anchor.score + max(0.0, 500.0 - (best_distance or 0.0)) / 10.0,
                    )
                ]
                used_coords.append((best_anchor.x, best_anchor.y))
                continue
        unresolved.append((key, parsed))

    if unresolved and used_coords:
        for key, parsed in unresolved:
            best_anchor = None
            best_score = None
            for candidate in electro_candidates:
                if any(abs(candidate.x - ux) < 0.01 and abs(candidate.y - uy) < 0.01 for ux, uy in used_coords):
                    continue
                min_dist = min(_distance(candidate.x, candidate.y, ux, uy) for ux, uy in used_coords)
                kind_bonus = 1000.0 if parsed["kind"] == "novi" and candidate.text.upper().startswith("ELE") else 0.0
                score = min_dist + kind_bonus + candidate.score
                if best_score is None or score > best_score:
                    best_score = score
                    best_anchor = candidate
            if best_anchor is not None:
                anchors[key] = [
                    Anchor(
                        name=key,
                        layer=best_anchor.layer,
                        x=best_anchor.x,
                        y=best_anchor.y,
                        text=f"{best_anchor.text} inferred for {key}",
                        score=best_anchor.score,
                    )
                ]
                used_coords.append((best_anchor.x, best_anchor.y))

    return anchors


def _existing_network_topology_clusters(doc) -> list[dict]:
    network_layers = {"t2_elektroene", "l2_elektroene", "elektrovodovi", "10kv"}
    raw_points: list[tuple[float, float, str, str]] = []

    for entity in doc.modelspace():
        layer = str(entity.dxf.get("layer", "")).lower()
        if layer not in network_layers:
            continue
        kind = entity.dxftype()
        if kind == "INSERT":
            raw_points.append((float(entity.dxf.insert.x), float(entity.dxf.insert.y), layer, entity.dxf.get("name", "")))
            continue
        if kind == "LINE":
            raw_points.append((float(entity.dxf.start.x), float(entity.dxf.start.y), layer, kind))
            raw_points.append((float(entity.dxf.end.x), float(entity.dxf.end.y), layer, kind))
            continue
        if kind == "LWPOLYLINE":
            pts = [(float(x), float(y)) for x, y, *_ in entity.get_points("xy")]
            if len(pts) >= 2:
                raw_points.append((pts[0][0], pts[0][1], layer, kind))
                raw_points.append((pts[-1][0], pts[-1][1], layer, kind))
            continue
        if kind == "POLYLINE":
            pts = [(float(v.dxf.location.x), float(v.dxf.location.y)) for v in entity.vertices]
            if len(pts) >= 2:
                raw_points.append((pts[0][0], pts[0][1], layer, kind))
                raw_points.append((pts[-1][0], pts[-1][1], layer, kind))

    clusters: list[dict] = []
    radius = 8.0
    for x, y, layer, label in raw_points:
        chosen = None
        for cluster in clusters:
            if math.dist((x, y), (cluster["x"], cluster["y"])) <= radius:
                chosen = cluster
                break
        if chosen is None:
            chosen = {
                "x": x,
                "y": y,
                "n": 0,
                "layers": defaultdict(int),
                "labels": defaultdict(int),
            }
            clusters.append(chosen)
        chosen["x"] = (chosen["x"] * chosen["n"] + x) / (chosen["n"] + 1)
        chosen["y"] = (chosen["y"] * chosen["n"] + y) / (chosen["n"] + 1)
        chosen["n"] += 1
        chosen["layers"][layer] += 1
        chosen["labels"][str(label)] += 1

    for cluster in clusters:
        layer_bonus = 0.0
        if "t2_elektroene" in cluster["layers"]:
            layer_bonus += 2.5
        if "elektrovodovi" in cluster["layers"]:
            layer_bonus += 2.5
        if "10kv" in cluster["layers"]:
            layer_bonus += 1.5
        if "l2_elektroene" in cluster["layers"]:
            layer_bonus += 1.0
        cluster["support_score"] = cluster["n"] + layer_bonus
    return clusters


def _augment_distribution_anchors_from_network_topology(doc, outputs, anchors: dict) -> dict:
    clusters = _existing_network_topology_clusters(doc)
    if not clusters:
        return anchors

    semantics_by_point = _output_semantics_by_point(outputs)
    shaft_signals = _shaft_signal_points(doc)
    point_usage = defaultdict(int)
    for output in outputs:
        for point_name in [output.start_point, output.end_point]:
            if point_name:
                point_usage[point_name.lower().strip()] += 1

    cluster_pool = sorted(clusters, key=lambda item: item["support_score"], reverse=True)
    hub = cluster_pool[0]
    hub_point = (hub["x"], hub["y"])

    def append_candidate(key: str, cluster: dict, score: float, text: str) -> None:
        candidates = list(anchors.get(key, []))
        point = (cluster["x"], cluster["y"])
        for existing in candidates:
            if math.dist((existing.x, existing.y), point) <= 5.0:
                return
        candidates.append(
            Anchor(
                name=key,
                layer=max(cluster["layers"].items(), key=lambda item: item[1])[0],
                x=float(cluster["x"]),
                y=float(cluster["y"]),
                text=text,
                score=score,
            )
        )
        candidates.sort(key=lambda item: item.score, reverse=True)
        anchors[key] = candidates[:5]

    def append_point_candidate(key: str, x: float, y: float, layer: str, score: float, text: str) -> None:
        candidates = list(anchors.get(key, []))
        point = (x, y)
        for existing in candidates:
            if math.dist((existing.x, existing.y), point) <= 5.0:
                return
        candidates.append(
            Anchor(
                name=key,
                layer=layer,
                x=float(x),
                y=float(y),
                text=text,
                score=score,
            )
        )
        candidates.sort(key=lambda item: item.score, reverse=True)
        anchors[key] = candidates[:5]

    def nearest_cluster_to_point(x: float, y: float, max_distance: float = 220.0) -> dict | None:
        best = None
        best_dist = None
        for cluster in cluster_pool:
            dist = math.dist((x, y), (cluster["x"], cluster["y"]))
            if dist > max_distance:
                continue
            if best_dist is None or dist < best_dist:
                best = cluster
                best_dist = dist
        return best

    def far_terminal_clusters(limit: int = 5, prefer_mixed: bool = False) -> list[dict]:
        ranked = []
        for cluster in cluster_pool:
            dist_hub = math.dist((cluster["x"], cluster["y"]), hub_point)
            if dist_hub < 180.0:
                continue
            score = dist_hub / 18.0 + cluster["support_score"] * 0.35
            if prefer_mixed and len(cluster["layers"]) > 1:
                score += 3.5
            if "elektrovodovi" in cluster["layers"]:
                score += 2.5
            if "10kv" in cluster["layers"]:
                score += 1.5
            if "l2_elektroene" in cluster["layers"]:
                score += 0.8
            if "t2_elektroene" in cluster["layers"] and cluster["n"] <= 2:
                score += 1.2
            ranked.append((score, cluster))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [cluster for _, cluster in ranked[:limit]]

    # Semantic pass: interpret start/end types before generic topology fallback.
    for key, semantic_entries in semantics_by_point.items():
        point_route_modes = {entry.get("route_mode") for entry in semantic_entries if entry.get("route_mode")}
        is_transition_anchor = "underground" in point_route_modes and "overhead" in point_route_modes
        shared_parcels = {
            (str(entry.get("parcel_hint")), str(entry.get("ko_hint")))
            for entry in semantic_entries
            if entry.get("parcel_hint") and entry.get("ko_hint")
        }
        shared_anchor_types = {str(entry.get("anchor_type") or "") for entry in semantic_entries}
        if len(shared_parcels) == 1:
            shared_parcel, _shared_ko = next(iter(shared_parcels))
            boundary_points = _parcel_closed_boundary_points(doc, shared_parcel)
            end_neighbors: list[tuple[str, str]] = []
            start_neighbors: list[tuple[str, str]] = []
            for entry in semantic_entries:
                role = str(entry.get("role") or "")
                if role == "end" and entry.get("prev_parcel_hint") and entry.get("prev_ko_hint"):
                    end_neighbors.append((str(entry["prev_parcel_hint"]), str(entry["prev_ko_hint"])))
                if role == "start" and entry.get("next_parcel_hint") and entry.get("next_ko_hint"):
                    start_neighbors.append((str(entry["next_parcel_hint"]), str(entry["next_ko_hint"])))
            if boundary_points and end_neighbors and start_neighbors:
                end_neighbor_points: list[tuple[float, float]] = []
                start_neighbor_points: list[tuple[float, float]] = []
                for parcel, _ko in end_neighbors:
                    end_neighbor_points.extend(_parcel_closed_boundary_points(doc, parcel) or _parcel_label_points(doc, parcel))
                for parcel, _ko in start_neighbors:
                    start_neighbor_points.extend(_parcel_closed_boundary_points(doc, parcel) or _parcel_label_points(doc, parcel))
                end_boundary = _boundary_entry_candidates(boundary_points, end_neighbor_points, limit=1)
                start_boundary = _boundary_entry_candidates(boundary_points, start_neighbor_points, limit=1)
                if end_boundary and start_boundary:
                    ex, ey, ed = end_boundary[0]
                    sx, sy, sd = start_boundary[0]
                    mx = (ex + sx) / 2.0
                    my = (ey + sy) / 2.0
                    if shared_anchor_types & {"existing_uzb_stub", "existing_uzb_stub_exterior_connection"}:
                        append_point_candidate(
                            key,
                            mx,
                            my,
                            "granica kat.parcela",
                            912.0 - (ed + sd) * 1.3,
                            f"Shared parcel {shared_parcel} existing UZB midpoint for {key}",
                        )
                        ranked_existing_shared = []
                        seen_shared_clusters: set[tuple[float, float]] = set()
                        for seed_x, seed_y, seed_label, seed_cost in (
                            (mx, my, "midpoint", (ed + sd) / 2.0),
                            (ex, ey, f"from {end_neighbors[0][0]}", ed),
                            (sx, sy, f"to {start_neighbors[0][0]}", sd),
                        ):
                            cluster = nearest_cluster_to_point(seed_x, seed_y, max_distance=90.0)
                            if cluster is None:
                                continue
                            cluster_key = (round(cluster["x"], 3), round(cluster["y"], 3))
                            if cluster_key in seen_shared_clusters:
                                continue
                            seen_shared_clusters.add(cluster_key)
                            bonus, mode = _semantic_cluster_bonus(doc, cluster, "existing_uzb_stub_exterior_connection")
                            seed_dist = math.dist((seed_x, seed_y), (cluster["x"], cluster["y"]))
                            score = 940.0 + cluster["support_score"] * 0.8 + bonus - seed_cost * 1.1 - seed_dist * 1.7
                            ranked_existing_shared.append((score, cluster, mode, seed_label))
                        ranked_existing_shared.sort(key=lambda item: item[0], reverse=True)
                        for score, cluster, mode, seed_label in ranked_existing_shared[:3]:
                            append_candidate(
                                key,
                                cluster,
                                score,
                                f"Shared parcel {shared_parcel} existing UZB {mode} via {seed_label} for {key}",
                            )
                        if key in anchors:
                            continue
                    if is_transition_anchor:
                        append_point_candidate(
                            key,
                            mx,
                            my,
                            "granica kat.parcela",
                            910.0 - (ed + sd) * 1.4,
                            f"Transition parcel {shared_parcel} midpoint for {key}",
                        )
                        for x, y, layer, dist in _corridor_projection_points(
                            doc,
                            mx,
                            my,
                            min_distance=0.0,
                            max_distance=35.0,
                            limit=2,
                        ):
                            append_point_candidate(
                                key,
                                x,
                                y,
                                layer,
                                905.0 - dist * 2.0,
                                f"Transition parcel {shared_parcel} corridor snap for {key}",
                            )
        for item in semantic_entries:
            anchor_type = item.get("anchor_type")
            parcel_hint = item.get("parcel_hint")
            parcel_points = _parcel_label_points(doc, parcel_hint) if parcel_hint else []
            explicit_stub_number = bool(re.search(r"broj\s+\d+", str(item.get("physical_target") or "")))
            if anchor_type == "ts_switch_cell":
                ts_hits = _target_text_points(doc, item.get("anchor_region") or "ts 35/10kv krstac")
                if ts_hits:
                    for x, y, layer in ts_hits[:1]:
                        append_point_candidate(
                            key,
                            x,
                            y,
                            layer,
                            900.0,
                            f"TS switch-cell region for {key}",
                        )
                else:
                    append_candidate(key, hub, 700.0 + hub["support_score"], f"TS region topology for {key}")
                continue

            if anchor_type in {"existing_substation", "existing_substation_exterior_connection"} and parcel_points:
                for x, y, layer, dist in _building_projection_points(doc, parcel_points):
                    append_point_candidate(
                        key,
                        x,
                        y,
                        layer,
                        940.0 - dist * 2.0,
                        f"Parcel {parcel_hint} building connection for {key}",
                    )
                if key in anchors:
                    continue

            target_hits = (
                _target_text_points(doc, item.get("physical_target"))
                or _target_text_points(doc, item.get("functional_target"))
                or _target_text_points(doc, key)
            )
            parcel_boundary_points = _parcel_closed_boundary_points(doc, parcel_hint) if parcel_hint else []
            prev_parcel = item.get("prev_parcel_hint") if item.get("role") == "end" else None
            prev_parcel_points = (
                (_parcel_closed_boundary_points(doc, prev_parcel) or _parcel_label_points(doc, prev_parcel))
                if prev_parcel
                else []
            )
            if not target_hits:
                if anchor_type in {"new_uzb_stub", "new_uzb_stub_exterior_connection"}:
                    boundary_points = parcel_boundary_points
                    neighbor_parcel = item.get("prev_parcel_hint") if item.get("role") == "end" else item.get("next_parcel_hint")
                    neighbor_points = (
                        (_parcel_closed_boundary_points(doc, neighbor_parcel) or _parcel_label_points(doc, neighbor_parcel))
                        if neighbor_parcel
                        else []
                    )
                    if boundary_points and neighbor_points:
                        for x, y, dist in _boundary_entry_candidates(boundary_points, neighbor_points, limit=3):
                            append_point_candidate(
                                key,
                                x,
                                y,
                                "granica kat.parcela",
                                835.0 - dist * 2.2,
                                f"Parcel {parcel_hint} minimal-entry boundary from {neighbor_parcel} for {key}",
                            )
                    if parcel_points:
                        for px, py in parcel_points[:2]:
                            for x, y, layer, dist in _corridor_projection_points(
                                doc,
                                px,
                                py,
                                min_distance=0.0,
                                max_distance=120.0,
                                limit=3,
                            ):
                                append_point_candidate(
                                    key,
                                    x,
                                    y,
                                    layer,
                                    700.0 - dist * 1.1,
                                    f"Parcel {parcel_hint} corridor target for {key}",
                                )
                            if not explicit_stub_number:
                                cluster = nearest_cluster_to_point(px, py, max_distance=90.0)
                                if cluster is not None:
                                    bonus, mode = _semantic_cluster_bonus(doc, cluster, anchor_type)
                                    parcel_dist = math.dist((px, py), (cluster["x"], cluster["y"]))
                                    append_candidate(
                                        key,
                                        cluster,
                                        760.0 + bonus - parcel_dist * 1.1,
                                        f"Parcel {parcel_hint} new UZB {mode} for {key}",
                                    )
                    if is_transition_anchor:
                        for x, y, layer, dist in _corridor_projection_points(
                            doc,
                            hub_point[0],
                            hub_point[1],
                            min_distance=30.0,
                            max_distance=120.0,
                            limit=3,
                        ):
                            append_point_candidate(
                                key,
                                x,
                                y,
                                layer,
                                640.0 - dist * 0.35,
                                f"Semantic transition new UZB placed_on_corridor for {key}",
                            )
                        if key in anchors:
                            continue
                    ranked_new_uzb = []
                    for cluster in cluster_pool:
                        dist_hub = math.dist((cluster["x"], cluster["y"]), hub_point)
                        if is_transition_anchor and (dist_hub < 25.0 or dist_hub > 220.0):
                            continue
                        if not is_transition_anchor and dist_hub < 90.0 and not explicit_stub_number:
                            continue
                        bonus, mode = _semantic_cluster_bonus(doc, cluster, anchor_type)
                        score = 540.0 + cluster["support_score"] * 0.7 + bonus
                        if is_transition_anchor:
                            score += max(0.0, 18.0 - abs(dist_hub - 85.0) / 8.0)
                        else:
                            score += dist_hub / 20.0
                        if "10kv" in cluster["layers"]:
                            score += 2.5
                        if "elektrovodovi" in cluster["layers"]:
                            score += 2.0
                        ranked_new_uzb.append((score, cluster, mode))
                    ranked_new_uzb.sort(key=lambda item: item[0], reverse=True)
                    for score, cluster, mode in ranked_new_uzb[:3]:
                        append_candidate(
                            key,
                            cluster,
                            score,
                            f"Semantic new UZB {mode} for {key}",
                        )
                    continue
                if anchor_type in {"existing_uzb_stub", "existing_uzb_stub_exterior_connection"} and parcel_points:
                    ranked_existing = []
                    for px, py in parcel_points[:2]:
                        cluster = nearest_cluster_to_point(px, py, max_distance=140.0)
                        if cluster is None:
                            continue
                        bonus, mode = _semantic_cluster_bonus(doc, cluster, anchor_type)
                        parcel_dist = math.dist((px, py), (cluster["x"], cluster["y"]))
                        ranked_existing.append((720.0 + bonus - parcel_dist * 1.2, cluster, mode))
                    ranked_existing.sort(key=lambda item: item[0], reverse=True)
                    for score, cluster, mode in ranked_existing[:2]:
                        append_candidate(
                            key,
                            cluster,
                            score,
                            f"Parcel {parcel_hint} existing UZB {mode} for {key}",
                        )
                    if key in anchors:
                        continue
                if anchor_type in {"existing_substation", "existing_substation_exterior_connection"}:
                    ranked_substations = []
                    for cluster in far_terminal_clusters(limit=5, prefer_mixed=True):
                        bonus, mode = _semantic_cluster_bonus(doc, cluster, anchor_type)
                        score = 610.0 + math.dist((cluster["x"], cluster["y"]), hub_point) / 18.0 + bonus
                        ranked_substations.append((score, cluster, mode))
                    ranked_substations.sort(key=lambda item: item[0], reverse=True)
                    for score, cluster, mode in ranked_substations[:3]:
                        append_candidate(
                            key,
                            cluster,
                            score,
                            f"Semantic substation {mode} for {key}",
                        )
                    continue
                if anchor_type != "shaft_connection":
                    continue

            if anchor_type in {"existing_substation", "existing_substation_exterior_connection", "shaft_connection"}:
                candidate_points = list(target_hits[:2])
                if anchor_type == "shaft_connection" and shaft_signals:
                    scored_shafts = []
                    shaft_entry_candidates = (
                        _boundary_entry_candidates(parcel_boundary_points, prev_parcel_points, limit=2)
                        if parcel_boundary_points and prev_parcel_points
                        else []
                    )
                    shaft_entry_points = [(x, y) for x, y, _ in shaft_entry_candidates] or prev_parcel_points[:2]
                    for x, y, layer, text in shaft_signals:
                        cluster = nearest_cluster_to_point(x, y, max_distance=140.0)
                        cluster_dist = None
                        score = 0.0
                        if cluster is not None:
                            cluster_dist = math.dist((x, y), (cluster["x"], cluster["y"]))
                            if cluster_dist <= 30.0:
                                score += cluster["support_score"] * 0.25 - cluster_dist * 0.45
                        if parcel_boundary_points and _point_in_polygon((x, y), parcel_boundary_points):
                            score += 160.0
                        if parcel_points:
                            score += max(0.0, 110.0 - min(math.dist((x, y), point) for point in parcel_points) * 1.0)
                        if shaft_entry_points:
                            score += max(0.0, 95.0 - min(math.dist((x, y), point) for point in shaft_entry_points) * 0.95)
                        if prev_parcel_points:
                            score += max(0.0, 70.0 - min(math.dist((x, y), point) for point in prev_parcel_points) * 0.7)
                        scored_shafts.append((score, x, y, layer, text, cluster, cluster_dist))
                    scored_shafts.sort(key=lambda item: item[0], reverse=True)
                    for score, x, y, layer, text, cluster, cluster_dist in scored_shafts[:2]:
                        append_point_candidate(
                            key,
                            x,
                            y,
                            layer,
                            860.0 + score,
                            f"Semantic shaft signal {text} for {key}",
                        )
                        if cluster is not None and cluster_dist is not None and cluster_dist <= 30.0:
                            append_candidate(
                                key,
                                cluster,
                                830.0 + score - cluster_dist * 0.8,
                                f"Semantic shaft cluster near {text} for {key}",
                            )
                    if key in anchors:
                        continue
                    candidate_points = [(x, y, layer) for _, x, y, layer, _, _, _ in scored_shafts[:2]]

                for x, y, layer in candidate_points:
                    cluster = nearest_cluster_to_point(x, y, max_distance=180.0)
                    if cluster is not None:
                        bonus, mode = _semantic_cluster_bonus(doc, cluster, anchor_type)
                        append_candidate(
                            key,
                            cluster,
                            650.0 + cluster["support_score"] + bonus,
                            f"Semantic {anchor_type} {mode} near {item.get('functional_target') or key}",
                        )
                    else:
                        append_point_candidate(
                            key,
                            x,
                            y,
                            layer,
                            620.0,
                            f"Semantic {anchor_type} label for {key}",
                        )
                current_candidates = list(anchors.get(key, []))
                if current_candidates and all(
                    math.dist((candidate.x, candidate.y), hub_point) < 180.0
                    for candidate in current_candidates[:2]
                ):
                    for cluster in far_terminal_clusters(limit=3, prefer_mixed=True):
                        bonus, mode = _semantic_cluster_bonus(doc, cluster, anchor_type)
                        append_candidate(
                            key,
                            cluster,
                            780.0 + math.dist((cluster["x"], cluster["y"]), hub_point) / 18.0 + bonus,
                            f"Semantic substation terminal {mode} for {key}",
                        )
                continue

            if anchor_type in {
                "existing_uzb_stub",
                "existing_uzb_stub_exterior_connection",
                "new_uzb_stub",
                "new_uzb_stub_exterior_connection",
            }:
                for x, y, layer in target_hits[:2]:
                    cluster = nearest_cluster_to_point(x, y, max_distance=260.0)
                    if cluster is not None:
                        bonus, mode = _semantic_cluster_bonus(doc, cluster, anchor_type)
                        append_candidate(
                            key,
                            cluster,
                            600.0 + cluster["support_score"] + bonus,
                            f"Semantic {anchor_type} {mode} near {item.get('physical_target') or key}",
                        )
                    else:
                        append_point_candidate(
                            key,
                            x,
                            y,
                            layer,
                            560.0,
                            f"Semantic {anchor_type} label for {key}",
                        )
                current_candidates = list(anchors.get(key, []))
                if anchor_type in {"new_uzb_stub", "new_uzb_stub_exterior_connection"} and current_candidates and not explicit_stub_number:
                    if all(
                        math.dist((candidate.x, candidate.y), hub_point) < 180.0
                        for candidate in current_candidates[:2]
                    ):
                        for cluster in far_terminal_clusters(limit=3, prefer_mixed=False):
                            bonus, mode = _semantic_cluster_bonus(doc, cluster, anchor_type)
                            append_candidate(
                                key,
                                cluster,
                                760.0 + math.dist((cluster["x"], cluster["y"]), hub_point) / 20.0 + bonus,
                                f"Semantic unnamed new UZB {mode} for {key}",
                            )

    alias_keys: dict[str, set[str]] = defaultdict(set)
    for semantic_key, entries in semantics_by_point.items():
        for entry in entries:
            raw_key = str(entry.get("raw_key") or "")
            if raw_key and raw_key != semantic_key:
                alias_keys[semantic_key].add(raw_key)

    for semantic_key, raw_keys in alias_keys.items():
        if semantic_key not in anchors:
            continue
        source_anchors = anchors[semantic_key]
        if not source_anchors:
            continue
        source_best = max((anchor.score for anchor in source_anchors), default=0.0)
        for raw_key in raw_keys:
            existing_best = max((anchor.score for anchor in anchors.get(raw_key, [])), default=float("-inf"))
            if existing_best >= source_best:
                continue
            anchors[raw_key] = [
                Anchor(
                    name=raw_key,
                    layer=anchor.layer,
                    x=anchor.x,
                    y=anchor.y,
                    text=f"{anchor.text} [semantic alias of {semantic_key}]",
                    score=anchor.score,
                )
                for anchor in source_anchors
            ]

    # Junction-like points: K* and UZB should prefer strongest supported clusters.
    used_junctions: list[tuple[float, float]] = []
    junction_keys = [key for key in point_usage if key.startswith("k0") or "uzb stub" in key]
    for key in junction_keys:
        if key in anchors:
            used_junctions.extend((anchor.x, anchor.y) for anchor in anchors[key])
            continue
        best = None
        best_score = None
        for cluster in cluster_pool:
            point = (cluster["x"], cluster["y"])
            spread_penalty = 0.0
            if used_junctions:
                spread_penalty = min(math.dist(point, other) for other in used_junctions) / 200.0
            score = cluster["support_score"] + spread_penalty
            if best_score is None or score > best_score:
                best = cluster
                best_score = score
        if best is not None:
            append_candidate(key, best, 450.0 + (best_score or 0.0), f"Topology junction for {key}")
            used_junctions.append((best["x"], best["y"]))

    # Terminal-like points should be far from the hub and may appear more than once (shared names).
    terminal_keys = [key for key in point_usage if key not in junction_keys and key != "ts 35/10kv krstac"]
    for key in terminal_keys:
        if key in anchors:
            continue
        ranked = []
        for cluster in cluster_pool:
            point = (cluster["x"], cluster["y"])
            dist_hub = math.dist(point, hub_point)
            score = dist_hub / 25.0 + cluster["support_score"] * 0.25
            if "l2_elektroene" in cluster["layers"]:
                score += 1.5
            ranked.append((score, cluster))
        ranked.sort(key=lambda item: item[0], reverse=True)
        keep = 2 if point_usage[key] > 1 else 1
        for score, cluster in ranked[:keep]:
            append_candidate(key, cluster, 350.0 + score, f"Topology terminal for {key}")

    return anchors


def _derive_anchor_requirements(outputs, constraints) -> list[dict]:
    categories = {constraint.category for constraint in constraints}
    results = []
    for output in outputs:
        code = output.code or str(output.index)
        search_tokens = []
        for token in [output.start_point, output.end_point, output.title]:
            if token and token not in search_tokens:
                search_tokens.append(token)

        requirements = {
            "code": code,
            "title": output.title,
            "route_mode": output.route_mode,
            "route_musts": dict(getattr(output, "route_musts", {}) or {}),
            "required_points": [point for point in [output.start_point, output.end_point] if point],
            "search_tokens": search_tokens,
            "needs_bridge_protection": output.route_mode == "underground" and "bridge" in categories,
            "needs_pvc_110": output.route_mode == "underground" and "crossing" in categories,
            "needs_road_offset_check": "roads" in categories,
            "needs_gas_clearance_check": "gas" in categories,
        }
        results.append(requirements)
    return results


def _derive_anchor_diagnostics(anchor_requirements: list[dict], anchors: dict, route_hints: dict, built_route_codes: set[str] | None = None) -> list[dict]:
    built_route_codes = built_route_codes or set()
    diagnostics = []
    for requirement in anchor_requirements:
        code = requirement["code"]
        missing = [point for point in requirement["required_points"] if point.lower().strip() not in anchors]
        has_route = code in route_hints or code in built_route_codes
        diagnostics.append(
            {
                "code": code,
                "required_points": list(requirement["required_points"]),
                "missing_points": missing,
                "resolved_points": [point for point in requirement["required_points"] if point.lower().strip() in anchors],
                "has_route_hint": code in route_hints,
                "has_route": has_route,
                "status": "resolved"
                if not missing and has_route
                else ("partial" if (not missing or has_route) else "unresolved"),
            }
        )
    return diagnostics


def _point_on_or_in_parcel(
    point: tuple[float, float],
    boundary: list[tuple[float, float]],
    *,
    boundary_tolerance: float = 1.5,
) -> bool:
    if len(boundary) < 3:
        return False
    if _point_in_polygon(point, boundary):
        return True
    closed = boundary + [boundary[0]]
    px, py = point
    min_dist = min(
        _point_segment_distance(px, py, ax, ay, bx, by)
        for (ax, ay), (bx, by) in zip(closed, closed[1:])
    )
    return min_dist <= boundary_tolerance


def _best_anchor_candidate(anchors: dict, point_name: str | None) -> Anchor | None:
    if not point_name:
        return None
    candidates: list[Anchor] = []
    raw_key = point_name.lower().strip()
    normalized_key = _normalize_search_text(point_name)
    for key in [raw_key, normalized_key]:
        if key and key in anchors:
            candidates.extend(anchors.get(key, []))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item.score, reverse=True)
    return candidates[0]


def _validate_named_anchor_parcel_musts(doc, outputs, anchors: dict) -> list[str]:
    warnings: list[str] = []
    for output in outputs:
        code = getattr(output, "code", None) or str(output.index)
        musts = dict(getattr(output, "route_musts", {}) or {})
        for role, point_name in (("start", output.start_point), ("end", output.end_point)):
            if not musts.get(f"must_{role}_on_named_anchor_parcel"):
                continue
            parcel = musts.get(f"{role}_parcel_hint")
            ko = musts.get(f"{role}_ko_hint")
            if not parcel or not ko:
                warnings.append(
                    f"Izvod {code}: {role} anchor '{point_name}' ima parcelni must, ali parcela/KO nisu popunjeni."
                )
                continue

            anchor = _best_anchor_candidate(anchors, point_name)
            if anchor is None:
                warnings.append(
                    f"Izvod {code}: {role} anchor '{point_name}' nije pronadjen, pa se ne moze proveriti parcela {parcel} k.o. {ko}."
                )
                continue

            boundary = _parcel_closed_boundary_points(doc, str(parcel))
            if boundary:
                if not _point_on_or_in_parcel((anchor.x, anchor.y), boundary):
                    warnings.append(
                        f"Izvod {code}: {role} anchor '{point_name}' je izabran van obavezne parcele {parcel} k.o. {ko}."
                    )
                continue

            label_points = _parcel_label_points(doc, str(parcel))
            if label_points:
                warnings.append(
                    f"Izvod {code}: {role} anchor '{point_name}' ima must parcelu {parcel} k.o. {ko}, ali granica te parcele nije mogla da se zatvori u podlozi."
                )
            else:
                warnings.append(
                    f"Izvod {code}: {role} anchor '{point_name}' ima must parcelu {parcel} k.o. {ko}, ali ta parcela nije geometrijski pronadjena u podlozi."
                )
    return warnings


def build_design_model(
    source_dxf: str,
    project_task_text_path: str,
    condition_paths: list[str] | None = None,
    anchors_path: str | None = None,
) -> DesignModel:
    doc = open_document(source_dxf)
    condition_paths = condition_paths or []
    loaded_texts = _load_input_texts_parallel([project_task_text_path, *condition_paths])
    project_task_text = loaded_texts[0]
    condition_text_blobs = loaded_texts[1:]
    input_readiness = [assess_input_readiness(project_task_text_path, project_task_text, "project_task")]
    if len(" ".join(project_task_text.split())) < 80:
        raise ValueError(
            "Projektni zadatak nema dovoljno čitljivog teksta. "
            "Izabrani PDF/TXT ekstrakt je verovatno sken bez upotrebljivog OCR-a."
        )
    input_readiness.extend(
        assess_input_readiness(path, text, "condition")
        for path, text in zip(condition_paths, condition_text_blobs)
    )
    input_blockers = [
        f"{Path(item['path']).name}: {', '.join(item['blockers'])}"
        for item in input_readiness
        if item.get("blockers")
    ]
    if input_blockers:
        suffix = "\n".join(f"- {line}" for line in input_blockers)
        raise ValueError(
            "Ulazni projektni zadatak i uslovi moraju biti procitani dovoljno pouzdano pre nastavka.\n"
            f"{suffix}"
        )
    parsed_outputs = parse_project_outputs("\n\n".join([project_task_text] + condition_text_blobs))
    parsed_constraints = [
        constraint
        for path, text in zip(condition_paths, condition_text_blobs)
        for constraint in parse_constraints(path, text=text)
    ]
    parsed_outputs = apply_project_musts(
        parsed_outputs,
        project_task_text,
        condition_text_blobs,
        parsed_constraints,
    )

    detected_anchors = find_anchors(doc)
    manual_anchors = _load_manual_anchors(anchors_path)
    manual_route_hints = _load_manual_route_hints(anchors_path)
    merged_anchors = _merge_anchor_maps(detected_anchors, manual_anchors)
    merged_anchors = _augment_anchors_from_route_hints(parsed_outputs, merged_anchors, manual_route_hints)

    design = DesignModel(
        source_dxf=str(source_dxf),
        project_task_text=str(project_task_text_path),
        condition_texts=[str(path) for path in condition_paths],
        algorithm_profile="full_latest",
        algorithm_checks=[
            "input_readiness_gate",
            "project_musts_from_inputs",
            "terminal_semantics",
            "named_anchor_parcel_semantics",
            "route_contract_semantics",
            "base_anchor_detection",
            "stub_anchor_geometry_augmentation",
            "network_topology_augmentation",
            "route_generation",
            "named_anchor_parcel_validation",
            "anchor_diagnostics",
        ],
        input_readiness=input_readiness,
        input_blockers=input_blockers,
        outputs=parsed_outputs,
        constraints=parsed_constraints,
        layer_classes=classify_layers(doc),
        anchors=merged_anchors,
        anchor_requirements=[],
        anchor_diagnostics=[],
        route_hints=manual_route_hints,
    )
    for item in input_readiness:
        issues = item.get("issues") or []
        if issues:
            design.warnings.append(
                f"{Path(item['path']).name}: " + "; ".join(str(issue) for issue in issues)
            )
    design.anchors = _augment_stub_anchors_from_geometry(doc, design.outputs, design.anchors)
    design.anchors = _augment_distribution_anchors_from_network_topology(doc, design.outputs, design.anchors)
    design.anchor_requirements = _derive_anchor_requirements(design.outputs, design.constraints)
    if manual_anchors:
        design.warnings.append(f"Ucitan manualni anchors JSON: {anchors_path}")
    design = apply_default_rules(design)
    design.route_segments = build_route_segments(design)
    built_route_codes = {(segment.output_code or str(segment.output_index)) for segment in design.route_segments}
    design.anchor_diagnostics = _derive_anchor_diagnostics(
        design.anchor_requirements,
        design.anchors,
        design.route_hints,
        built_route_codes,
    )
    design.warnings.extend(_validate_named_anchor_parcel_musts(doc, design.outputs, design.anchors))
    unresolved = [item for item in design.anchor_diagnostics if item["status"] == "unresolved"]
    if unresolved:
        unresolved_codes = ", ".join(item["code"] for item in unresolved)
        design.warnings.append(
            f"Podloga nema dovoljno semantickog signala za anchor-e bez dodatnog inputa. Neodredjeni izvodi: {unresolved_codes}"
        )
    for benchmark in design.route_benchmarks:
        if benchmark.get("max_segment", 0.0) > max(150.0, benchmark.get("median_segment", 0.0) * 4.0):
            design.warnings.append(
                f"Izvod {benchmark.get('code')}: ruta ima neuobicajeno dug segment ({benchmark.get('max_segment'):.1f}m)."
            )
    return design


def ensure_design_drawable(design: DesignModel) -> None:
    if design.route_segments:
        return
    details = []
    if design.warnings:
        details.extend(design.warnings)
    unresolved = [item for item in design.anchor_diagnostics if item.get("status") == "unresolved"]
    if unresolved:
        for item in unresolved:
            details.append(
                f"Izvod {item.get('code')}: fale tacke {', '.join(item.get('missing_points', []))}"
            )
    suffix = "\n".join(f"- {line}" for line in details) if details else "- Nema generisanih ruta."
    raise ValueError(
        "Nije generisana nijedna ruta, pa crtez ne bi bio izmenjen.\n"
        f"{suffix}"
    )


def apply_design_to_dxf(source_dxf: str, design: DesignModel, output_dxf: str, output_json: str | None = None) -> None:
    doc = open_document(source_dxf)
    add_plan_overlay(doc, design.to_dict())
    save_document(doc, output_dxf)
    if output_json:
        Path(output_json).write_text(
            json.dumps(design.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


def apply_design_to_dwg(
    source_dxf: str,
    design: DesignModel,
    output_dxf: str,
    output_dwg: str | None = None,
    output_json: str | None = None,
) -> dict:
    apply_design_to_dxf(
        source_dxf=source_dxf,
        design=design,
        output_dxf=output_dxf,
        output_json=output_json,
    )
    result = {
        "output_dxf": str(output_dxf),
        "output_dwg": None,
        "dwg_warning": None,
    }
    if not output_dwg:
        return result
    try:
        produced = convert_dxf_to_dwg(output_dxf, output_dwg)
        result["output_dwg"] = str(produced)
    except Exception as exc:
        result["dwg_warning"] = str(exc)
    return result


def extract_quantities(design: DesignModel) -> QuantityReport:
    report = QuantityReport(source_dxf=design.source_dxf)
    grouped_length = defaultdict(float)

    for segment in design.route_segments:
        grouped_length[segment.output_code or str(segment.output_index)] += segment.approx_length

    total_route = 0.0
    total_trench = 0.0
    total_cable = 0.0
    total_overhead_conductor = 0.0
    for output in design.outputs:
        output_code = output.code or str(output.index)
        description_label = output.code or f"{output.index}"
        route_length = grouped_length.get(output_code, 0.0)
        total_route += route_length
        report.items.append(
            QuantityItem(
                code=f"OUT-{output_code}-ROUTE",
                description=f"Izvod {description_label} - duzina trase",
                unit="m",
                quantity=round(route_length, 2),
                source="generated drawing",
            )
        )

        if output.route_mode == "underground":
            trench_length = route_length
            cable_length = (
                route_length * UNDERGROUND_CABLE_PHASE_FACTOR * UNDERGROUND_CABLE_ROUTE_RESERVE_FACTOR
                + UNDERGROUND_CABLE_PHASE_FACTOR * UNDERGROUND_CABLE_TERMINAL_ALLOWANCE_PER_PHASE
            )
            total_trench += trench_length
            total_cable += cable_length
            report.items.append(
                QuantityItem(
                    code=f"OUT-{output_code}-TRENCH",
                    description=f"Izvod {description_label} - duzina kablovskog rova",
                    unit="m",
                    quantity=round(trench_length, 2),
                    source="generated drawing",
                )
            )
            report.items.append(
                QuantityItem(
                    code=f"OUT-{output_code}-CABLE",
                    description=f"Izvod {description_label} - procenjena duzina jednozilnog kabla",
                    unit="m",
                    quantity=round(cable_length, 2),
                    source="generated drawing * 3 single-core conductors + reserve + terminal allowance",
                )
            )

        elif output.route_mode == "overhead":
            conductor_length = (
                route_length * OVERHEAD_CONDUCTOR_PHASE_FACTOR * OVERHEAD_CONDUCTOR_ROUTE_RESERVE_FACTOR
                + OVERHEAD_CONDUCTOR_PHASE_FACTOR * OVERHEAD_CONDUCTOR_TERMINAL_ALLOWANCE_PER_PHASE
            )
            total_overhead_conductor += conductor_length
            report.items.append(
                QuantityItem(
                    code=f"OUT-{output_code}-CONDUCTOR",
                    description=f"Izvod {description_label} - procenjena duzina nadzemnog provodnika",
                    unit="m",
                    quantity=round(conductor_length, 2),
                    source="generated drawing * 3 phase conductors + terminal allowance",
                )
            )

    report.items.append(
        QuantityItem(
            code="TOTAL-ROUTE",
            description="Ukupna duzina generisane trase",
            unit="m",
            quantity=round(total_route, 2),
            source="generated drawing",
        )
    )
    report.items.append(
        QuantityItem(
            code="TOTAL-TRENCH",
            description="Ukupna duzina kablovskog rova",
            unit="m",
            quantity=round(total_trench, 2),
            source="generated drawing",
        )
    )
    report.items.append(
        QuantityItem(
            code="TOTAL-CABLE",
            description="Ukupna procenjena duzina jednozilnog kabla",
            unit="m",
            quantity=round(total_cable, 2),
            source="generated drawing * 3 single-core conductors + reserve + terminal allowance",
        )
    )
    report.items.append(
        QuantityItem(
            code="TOTAL-CONDUCTOR",
            description="Ukupna procenjena duzina nadzemnog provodnika",
            unit="m",
            quantity=round(total_overhead_conductor, 2),
            source="generated drawing * 3 phase conductors + terminal allowance",
        )
    )

    if not design.route_segments:
        report.warnings.append("No route segments were generated, so quantities are empty or zero.")

    return report


def build_report_from_quantities(design: DesignModel, quantities: QuantityReport) -> dict:
    output_rows = []
    work_type_rows = []
    totals = {}

    quantity_map = {item.code: item for item in quantities.items}
    for output in design.outputs:
        code = output.code or str(output.index)
        label = output.code or str(output.index)
        route = quantity_map.get(f"OUT-{code}-ROUTE")
        trench = quantity_map.get(f"OUT-{code}-TRENCH")
        cable = quantity_map.get(f"OUT-{code}-CABLE")
        conductor = quantity_map.get(f"OUT-{code}-CONDUCTOR")
        output_rows.append(
            {
                "izvod": label,
                "naziv": output.title,
                "tip": output.route_mode,
                "trasa_m": route.quantity if route else 0.0,
                "rov_m": trench.quantity if trench else 0.0,
                "kabl_m": cable.quantity if cable else 0.0,
                "provodnik_m": conductor.quantity if conductor else 0.0,
                "kabl_tip": output.cable_type,
                "profil_rova": output.trench_profile,
            }
        )

    for item in quantities.items:
        if item.code.startswith("TOTAL-"):
            totals[item.code] = item.quantity
        elif any(item.code.endswith(suffix) for suffix in ("-TRENCH", "-CABLE", "-CONDUCTOR")):
            work_type_rows.append(
                {
                    "sifra": item.code,
                    "opis": item.description,
                    "jedinica": item.unit,
                    "kolicina": item.quantity,
                    "izvor": item.source,
                }
            )

    return {
        "source_dxf": design.source_dxf,
        "project_task_text": design.project_task_text,
        "outputs": [output.__dict__ for output in design.outputs],
        "quantity_items": quantities.to_dict()["items"],
        "main_book_tables": {
            "by_output": output_rows,
            "by_work_type": work_type_rows,
            "totals": totals,
        },
        "constraints": [constraint.__dict__ for constraint in design.constraints],
        "warnings": list(design.warnings) + list(quantities.warnings),
    }


def render_report_markdown(report: dict) -> str:
    lines = []
    lines.append("# Draft Tabele Za Glavnu Svesku")
    lines.append("")
    lines.append("## Pregled Po Izvodima")
    lines.append("")
    lines.append("| Izvod | Tip | Trasa (m) | Rov (m) | Kabl (m) | Provodnik (m) |")
    lines.append("| --- | --- | ---: | ---: | ---: | ---: |")
    for row in report.get("main_book_tables", {}).get("by_output", []):
        lines.append(
            f"| {row['izvod']} | {row['tip']} | {row['trasa_m']:.2f} | {row['rov_m']:.2f} | {row['kabl_m']:.2f} | {row['provodnik_m']:.2f} |"
        )

    lines.append("")
    lines.append("## Predmer Po Vrstama Radova")
    lines.append("")
    lines.append("| Sifra | Opis | Jedinica | Kolicina |")
    lines.append("| --- | --- | --- | ---: |")
    for row in report.get("main_book_tables", {}).get("by_work_type", []):
        lines.append(
            f"| {row['sifra']} | {row['opis']} | {row['jedinica']} | {row['kolicina']:.2f} |"
        )

    totals = report.get("main_book_tables", {}).get("totals", {})
    if totals:
        lines.append("")
        lines.append("## Ukupno")
        lines.append("")
        for key, value in totals.items():
            lines.append(f"- `{key}`: {value:.2f}")

    warnings = report.get("warnings", [])
    if warnings:
        lines.append("")
        lines.append("## Napomene")
        lines.append("")
        for warning in warnings:
            lines.append(f"- {warning}")

    lines.append("")
    return "\n".join(lines)
