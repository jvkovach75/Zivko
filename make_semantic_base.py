from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import ezdxf


REMOVE_ALL_ENTITY_LAYERS = {
    "10kV",
    "10kV Puhovo",
    "10kV ka Krstac Kula",
    "10kV Fapromal",
    "10kV ka Lisice 3",
    "L2_Elektroene",
    "EE 150 mm2 10kV",
    "Elektrovodovi",
    "Novi kabl 10kV",
    "EE Tekst_1",
    "EE Razvodni ormani",
    "EE kote",
    "EL Svetiljke",
    "Schreder",
    "Struja",
}

TEXT_LAYER = "Text Zivko"

LABELS = {
    "ts 35/10kv krstac": 'TS 35/10kV "Krstac"',
    "k03": "K03",
    "k04": "K04",
    "k05": "K05",
    "k06": "K06",
    "uzb stub broj 1": "Novi UZB stub broj 1",
    "krstac-kula": 'TS 10/0,4kV "Krstac - Kula"',
    "fapromal": 'MBTS 10/0,4kV "Fapromal"',
    "lisice 3": 'PTS 10/0,4kV "Lisice 3"',
    "lisice 1": 'TS 10/0,4kV "Lisice 1"',
    "puhovo - krstac": 'DV 10kV "Puhovo - Krstac"',
}

MATCH_PATTERNS = {
    "ts 35/10kv krstac": ['postojeća trafostanica 35/10kv "krstac"', 'postojeca trafostanica 35/10kv "krstac"', 'ts 35/10kv "krstac"'],
    "k03": ["k03"],
    "k04": ["k04"],
    "k05": ["k05"],
    "k06": ["k06"],
    "uzb stub broj 1": ["uzb stub", "novi uzb stub", "stub br. 1", "stub broj 1"],
    "krstac-kula": ['"krstac - kula"', '"krstac-kula"', "krstac - kula"],
    "fapromal": ["fapromal"],
    "lisice 3": ['"lisice 3"', "lisice 3"],
    "lisice 1": ['"lisice 1"', "lisice 1"],
    "puhovo - krstac": ['"puhovo - krstac"', "puhovo - krstac"],
}

FORBIDDEN_ROUTE_TEXT_PARTS = [
    "novi kablovski vod",
    "novi kablovski vodovi",
    "xhe 49-a",
    "podzemno",
    "kablovskom rovu",
    "dimenzija 0,8",
    "izvodna celija",
    "izvodna ćelija",
    "pvc cevi",
    "duzina trase",
]


def load_anchors(path: Path) -> dict[str, dict]:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    anchors = data.get("anchors", data)
    result = {}
    for key, value in anchors.items():
        if isinstance(value, list):
            value = value[0] if value else None
        if not isinstance(value, dict):
            continue
        if "x" not in value or "y" not in value:
            continue
        result[key.lower().strip()] = {"x": float(value["x"]), "y": float(value["y"])}
    return result


def normalize_text(text: str) -> str:
    return (
        text.lower()
        .replace("ć", "c")
        .replace("č", "c")
        .replace("š", "s")
        .replace("ž", "z")
        .replace("đ", "dj")
        .replace("\n", " ")
        .replace("\r", " ")
        .replace("  ", " ")
        .strip()
    )


def entity_text(entity) -> str:
    if entity.dxftype() == "MTEXT":
        return entity.plain_text()
    if entity.dxftype() == "TEXT":
        return entity.dxf.text
    return ""


def entity_insert(entity):
    if entity.dxftype() == "MTEXT":
        return entity.dxf.insert
    if entity.dxftype() == "TEXT":
        return entity.dxf.insert
    return None


def distance_xy(point, x: float, y: float) -> float:
    return math.hypot(float(point[0]) - x, float(point[1]) - y)


def select_semantic_texts(doc: ezdxf.document.Drawing, anchors: dict[str, dict]) -> set[str]:
    keep_handles: set[str] = set()
    candidates = [
        entity
        for entity in doc.modelspace()
        if entity.dxf.get("layer", "0") == TEXT_LAYER and entity.dxftype() in {"TEXT", "MTEXT"}
    ]
    for key, anchor in anchors.items():
        patterns = [normalize_text(value) for value in MATCH_PATTERNS.get(key, [key])]
        best_entity = None
        best_score = None
        for entity in candidates:
            text = normalize_text(entity_text(entity))
            if not text:
                continue
            if any(part in text for part in FORBIDDEN_ROUTE_TEXT_PARTS):
                continue
            if not any(pattern in text for pattern in patterns):
                continue
            insert = entity_insert(entity)
            if insert is None:
                continue
            dist = distance_xy(insert, anchor["x"], anchor["y"])
            length_penalty = max(len(text) - 90, 0) * 0.5
            newline_penalty = text.count("  ") * 2.0
            score = dist + length_penalty + newline_penalty
            if best_score is None or score < best_score:
                best_score = score
                best_entity = entity
        if best_entity is not None:
            keep_handles.add(best_entity.dxf.handle)
    return keep_handles


def remove_entities(doc: ezdxf.document.Drawing, keep_text_handles: set[str]) -> None:
    msp = doc.modelspace()
    for entity in list(msp):
        layer = entity.dxf.get("layer", "0")
        if layer in REMOVE_ALL_ENTITY_LAYERS:
            msp.delete_entity(entity)
            continue
        if layer == TEXT_LAYER and entity.dxftype() in {"TEXT", "MTEXT", "LEADER", "MULTILEADER", "INSERT"}:
            if entity.dxf.handle in keep_text_handles:
                continue
            msp.delete_entity(entity)


def add_semantic_labels(doc: ezdxf.document.Drawing, anchors: dict[str, dict]) -> None:
    if TEXT_LAYER not in {layer.dxf.name for layer in doc.layers}:
        doc.layers.add(TEXT_LAYER)
    msp = doc.modelspace()
    existing_normalized = {
        normalize_text(entity_text(entity))
        for entity in msp
        if entity.dxf.get("layer", "0") == TEXT_LAYER and entity.dxftype() in {"TEXT", "MTEXT"}
    }
    exemplar = next(
        (
            entity
            for entity in msp
            if entity.dxf.get("layer", "0") == TEXT_LAYER and entity.dxftype() == "MTEXT"
        ),
        None,
    )
    base_attribs = {
        "layer": TEXT_LAYER,
        "char_height": 5.0,
        "style": "VIP",
        "attachment_point": 7,
        "color": 4,
    }
    if exemplar is not None:
        base_attribs.update(
            {
                "char_height": exemplar.dxf.get("char_height", 5.0),
                "style": exemplar.dxf.get("style", "VIP"),
                "attachment_point": exemplar.dxf.get("attachment_point", 7),
                "color": exemplar.dxf.get("color", 4),
            }
        )
    for key, label in LABELS.items():
        anchor = anchors.get(key)
        if not anchor:
            continue
        if normalize_text(label) in existing_normalized:
            continue
        x = anchor["x"]
        y = anchor["y"]
        dxfattribs = dict(base_attribs)
        dxfattribs["insert"] = (x, y)
        msp.add_mtext(
            label,
            dxfattribs=dxfattribs,
        )


def make_semantic_base(source_dxf: Path, anchors_json: Path, output_dxf: Path) -> None:
    doc = ezdxf.readfile(str(source_dxf))
    anchors = load_anchors(anchors_json)
    keep_text_handles = select_semantic_texts(doc, anchors)
    remove_entities(doc, keep_text_handles)
    add_semantic_labels(doc, anchors)
    doc.saveas(str(output_dxf))


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a semantic base DXF: keep situation, keep key labels, remove route overlays.")
    parser.add_argument("--source-dxf", required=True)
    parser.add_argument("--anchors-json", required=True)
    parser.add_argument("--output-dxf", required=True)
    args = parser.parse_args()

    make_semantic_base(
        Path(args.source_dxf),
        Path(args.anchors_json),
        Path(args.output_dxf),
    )
    print(f"Semantic base written to {args.output_dxf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
