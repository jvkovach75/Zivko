from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path
from uuid import uuid4

from pypdf import PdfReader

from .models import ConstraintSpec, OutputSpec
from .path_utils import resolve_existing_path

TESSERACT_EXE_CANDIDATES = [
    Path(r"C:\Program Files\Tesseract-OCR\tesseract.exe"),
    Path(r"C:\Program Files\PDF24\tesseract\tesseract.exe"),
]

GHOSTSCRIPT_EXE_CANDIDATES = [
    Path(r"C:\Program Files\PDF24\gs\bin\gswin64c.exe"),
]

LOCAL_TESSDATA_DIR = Path(__file__).resolve().parent.parent / "tessdata"
OCR_WORK_ROOT = Path(__file__).resolve().parent.parent / "_ocr_tmp"
INPUT_TEXT_CACHE_DIR = Path(__file__).resolve().parent.parent / ".input_text_cache"
INPUT_TEXT_CACHE_VERSION = "v1"


OUTPUT_PATTERNS = [
    (1, ["puhovo - krstac", "k06"]),
    (2, ["krstac-kula", "k03"]),
    (3, ["fapromal", "k05"]),
    (4, ["lisice 3", "k04"]),
    (5, ["lisice 1"]),
]

CYRILLIC_TO_LATIN = str.maketrans(
    {
        "А": "A", "а": "a",
        "Б": "B", "б": "b",
        "В": "V", "в": "v",
        "Г": "G", "г": "g",
        "Д": "D", "д": "d",
        "Ђ": "Dj", "ђ": "dj",
        "Е": "E", "е": "e",
        "Ж": "Z", "ж": "z",
        "З": "Z", "з": "z",
        "И": "I", "и": "i",
        "Ј": "J", "ј": "j",
        "К": "K", "к": "k",
        "Л": "L", "л": "l",
        "Љ": "Lj", "љ": "lj",
        "М": "M", "м": "m",
        "Н": "N", "н": "n",
        "Њ": "Nj", "њ": "nj",
        "О": "O", "о": "o",
        "П": "P", "п": "p",
        "Р": "R", "р": "r",
        "С": "S", "с": "s",
        "Т": "T", "т": "t",
        "Ћ": "C", "ћ": "c",
        "У": "U", "у": "u",
        "Ф": "F", "ф": "f",
        "Х": "H", "х": "h",
        "Ц": "C", "ц": "c",
        "Ч": "C", "ч": "c",
        "Џ": "Dz", "џ": "dz",
        "Ш": "S", "ш": "s",
    }
)


def load_input_text(text_path: str | Path) -> str:
    path = resolve_existing_path(text_path)
    if not path.exists():
        return ""
    if path.suffix.lower() == ".pdf":
        cached = _load_cached_input_text(path)
        if cached:
            return cached

        fallback_text = ""
        fallback = _find_txt_fallback(path)
        if fallback:
            fallback_text = fallback.read_text(encoding="utf-8", errors="ignore")
            if _is_good_enough_without_ocr(path, fallback_text):
                _store_cached_input_text(path, fallback_text)
                return fallback_text

        pdf_text = _read_pdf_text(path)
        quick_best = _best_text_candidate([pdf_text, fallback_text])
        if _is_good_enough_without_ocr(path, quick_best):
            _store_cached_input_text(path, quick_best)
            return quick_best

        ocr_text = _run_windows_ocr(path)
        medium_best = _best_text_candidate([ocr_text, pdf_text, fallback_text])
        if _is_good_enough_without_ocr(path, medium_best):
            _store_cached_input_text(path, medium_best)
            return medium_best

        tesseract_text = _run_tesseract_ocr(path)
        best = _best_text_candidate([tesseract_text, ocr_text, pdf_text, fallback_text])
        _store_cached_input_text(path, best)
        return best
    return path.read_text(encoding="utf-8", errors="ignore")


def parse_project_outputs(task_text: str) -> list[OutputSpec]:
    normalized = " ".join(task_text.split())
    outputs: list[OutputSpec] = []
    lower = normalized.lower()
    search_text = _normalize_search_text(normalized)
    found_indices: set[int] = set()

    krstac_outputs = _parse_krstac_tabular_outputs(task_text)
    if krstac_outputs:
        return krstac_outputs

    for index, keywords in OUTPUT_PATTERNS:
        code_keywords = [keyword for keyword in keywords if keyword.startswith("k")]
        name_keywords = [keyword for keyword in keywords if not keyword.startswith("k")]
        matched_name = any(keyword in search_text for keyword in name_keywords) if name_keywords else False
        matched_codes = all(re.search(rf"\\b{re.escape(keyword)}\\b", search_text) for keyword in code_keywords) if code_keywords else False
        matched = matched_name and (matched_codes or not code_keywords)
        if not matched:
            continue

        if index == 1:
            found_indices.add(1)
            underground = OutputSpec(
                index=1,
                code="1A",
                title="Izvod 1A - kablovski vod 10kV od TS 35/10kV Krstac do novog UZB stuba broj 1",
                start_point="k06",
                end_point="uzb stub broj 1",
                cable_type="XHE 49-A 3x1x150 mm2, 10 kV",
                trench_profile="(0.8m x 0.6m)",
                route_mode="underground",
                notes=["Podzemni deo Izvoda 1 do novog UZB stuba broj 1."],
            )
            overhead = OutputSpec(
                index=1,
                code="1B",
                title="Izvod 1B - nadzemni vod 10kV od novog UZB stuba broj 1 ka DV Puhovo - Krstac",
                start_point="uzb stub broj 1",
                end_point="puhovo - krstac",
                trench_profile=None,
                route_mode="overhead",
                notes=["Nadzemni nastavak Izvoda 1 od novog UZB stuba broj 1 ka DV Puhovo - Krstac."],
            )
            outputs.extend([underground, overhead])
            continue

        title = _title_from_keywords(normalized, keywords)
        output = OutputSpec(index=index, code=str(index), title=title)
        _fill_output_defaults(output)

        if "xhe 49-a" in search_text:
            output.cable_type = "XHE 49-A 3x1x150 mm2, 10 kV"
        if index in (2, 3):
            output.trench_profile = "(0.8m x 0.6m)"
        elif index in (4, 5):
            output.trench_profile = "(0.8m x 0.4m)"
        output.route_mode = "underground"
        outputs.append(output)
        found_indices.add(index)

    if _looks_like_krstac_distribution_task(search_text):
        for index, _keywords in OUTPUT_PATTERNS:
            if index in found_indices:
                continue
            if index == 1:
                outputs.extend(
                    [
                        OutputSpec(
                            index=1,
                            code="1A",
                            title="Izvod 1A - kablovski vod 10kV od TS 35/10kV Krstac do novog UZB stuba broj 1",
                            start_point="k06",
                            end_point="uzb stub broj 1",
                            cable_type="XHE 49-A 3x1x150 mm2, 10 kV",
                            trench_profile="(0.8m x 0.6m)",
                            route_mode="underground",
                            notes=["Dodat kao podrazumevani izvod zbog OCR fallback-a."],
                        ),
                        OutputSpec(
                            index=1,
                            code="1B",
                            title="Izvod 1B - nadzemni vod 10kV od novog UZB stuba broj 1 ka DV Puhovo - Krstac",
                            start_point="uzb stub broj 1",
                            end_point="puhovo - krstac",
                            trench_profile=None,
                            route_mode="overhead",
                            notes=["Dodat kao podrazumevani izvod zbog OCR fallback-a."],
                        ),
                    ]
                )
            else:
                output = OutputSpec(index=index, code=str(index), title=f"Izvod {index}")
                _fill_output_defaults(output)
                output.cable_type = "XHE 49-A 3x1x150 mm2, 10 kV"
                output.trench_profile = "(0.8m x 0.6m)" if index in (2, 3) else "(0.8m x 0.4m)"
                output.route_mode = "underground"
                output.notes.append("Dodat kao podrazumevani izvod zbog OCR fallback-a.")
                outputs.append(output)

    if not outputs:
        generic_output = _parse_generic_single_output(normalized)
        if generic_output is not None:
            return [generic_output]
        for index, _keywords in OUTPUT_PATTERNS:
            output = OutputSpec(index=index, code=str(index), title=f"Izvod {index}")
            _fill_output_defaults(output)
            outputs.append(output)

    return outputs


def _parse_krstac_tabular_outputs(task_text: str) -> list[OutputSpec]:
    search_text = _normalize_search_text(task_text)
    if "krstac" not in search_text or "prikljucna mesta" not in search_text:
        return []

    outputs: list[OutputSpec] = []
    table_blocks = _split_krstac_table_blocks(search_text)
    if not table_blocks:
        return []

    first_block = table_blocks[0]
    if "ko6" in first_block and "novom uzb 12/1600 stubu" in first_block:
        outputs.append(
            OutputSpec(
                index=1,
                code="1A",
                title='Izvod 1A - kablovski vod 10kV, TS 35/10kV "Krstac" - novi UZB stub broj 1',
                start_point="k06",
                end_point="novi uzb stub broj 1",
                cable_type="XHE 49-A 3x1x150 mm2, 10 kV",
                trench_profile="(0.8m x 0.6m)",
                route_mode="underground",
                notes=['Veza za DV 10kV "Puhovo - Krstac".'],
            )
        )
        outputs.append(
            OutputSpec(
                index=1,
                code="1B",
                title='Izvod 1B - nadzemni vod 10kV od novog UZB stuba broj 1 do novog UZB stuba broj 4',
                start_point="novi uzb stub broj 1",
                end_point="novi uzb stub broj 4",
                cable_type="3 x 1 x Al/Ce 50/8, 10 kV",
                trench_profile=None,
                route_mode="overhead",
                notes=['U trasi postojeceg DV 10kV "Puhovo - Krstac".'],
            )
        )

    if len(table_blocks) >= 2 and ("ko3" in table_blocks[1] or "koz" in table_blocks[1]) and "krstac kula" in table_blocks[1]:
        outputs.append(
            OutputSpec(
                index=2,
                code="2",
                title='Izvod 2 - kablovski vod 10kV, veza TS 35/10kV "Krstac" - TS 10/0,4kV "Krstac-Kula"',
                start_point="k03",
                end_point="ts 10/0,4kv krstac-kula",
                cable_type="XHE 49-A 3x1x150 mm2, 10 kV",
                trench_profile="(0.8m x 0.6m)",
                route_mode="underground",
            )
        )

    if len(table_blocks) >= 3 and "ko5" in table_blocks[2] and "fapromal" in table_blocks[2]:
        outputs.append(
            OutputSpec(
                index=3,
                code="3",
                title='Izvod 3 - kablovski vod 10kV, veza TS 35/10kV "Krstac" - MBTS "Fapromal"',
                start_point="k05",
                end_point="mbts fapromal",
                cable_type="XHE 49-A 3x1x150 mm2, 10 kV",
                trench_profile="(0.8m x 0.6m)",
                route_mode="underground",
                notes=["Zavrsetak preko nove sahte za MBTS Fapromal."],
            )
        )

    if len(table_blocks) >= 4 and "ko4" in table_blocks[3] and "lisice 3" in table_blocks[3]:
        outputs.append(
            OutputSpec(
                index=4,
                code="4",
                title='Izvod 4 - kablovski vod 10kV od TS 35/10kV "Krstac" do postojeceg UZB stuba, veza na "Lisice 3"',
                start_point="k04",
                end_point="postojeci uzb stub lisice 3",
                cable_type="XHE 49-A 3x1x150 mm2, 10 kV",
                trench_profile="(0.8m x 0.4m)",
                route_mode="underground",
                notes=['Veza na nadzemni dalekovod 10kV koji je povezan na postojecu trafostanicu PTS 10/0,4kV "Lisice 3".'],
            )
        )
        outputs.append(
            OutputSpec(
                index=5,
                code="5",
                title='Izvod 5 - kablovski vod 10kV od TS 10/0,4kV "Lisice 3" do "Lisice 1"',
                start_point="ts 10/0,4kv lisice 3",
                end_point="lisice 1",
                cable_type="XHE 49-A 3x1x150 mm2, 10 kV",
                trench_profile="(0.8m x 0.4m)",
                route_mode="underground",
            )
        )

    return outputs


def _split_krstac_table_blocks(search_text: str) -> list[str]:
    marker = "prikljucna mesta: predvidja se prikljucenje:"
    blocks: list[str] = []
    start = 0
    while True:
        idx = search_text.find(marker, start)
        if idx < 0:
            break
        next_idx = search_text.find(marker, idx + len(marker))
        end = next_idx if next_idx >= 0 else len(search_text)
        blocks.append(search_text[max(0, idx - 260): min(len(search_text), end + 220)])
        if next_idx < 0:
            break
        start = next_idx
    return blocks


def _looks_like_krstac_distribution_task(lower_text: str) -> bool:
    return "krstac" in lower_text and "10 kv" in lower_text


def _parse_generic_single_output(text: str) -> OutputSpec | None:
    search = _normalize_search_text(text)
    if "kablovskog voda 10 kv" not in search and "kablovski vod 10 kv" not in search:
        return None

    title = "Kablovski vod 10kV"
    route_mode = "underground"
    cable_type = None
    trench_profile = None
    notes: list[str] = []

    route_match = re.search(r"duzina trase[: ]+(\d{2,5})\s*m", search)
    if route_match:
        notes.append(f"Duzina trase iz dokumentacije: {route_match.group(1)} m")

    conductor_match = re.search(r"tip provodnika[: ]+([^\\n\\r]{0,120}?xhe[^\\n\\r]{0,80}?1x150mm2)", search)
    if conductor_match:
        cable_type = conductor_match.group(1).strip().replace("  ", " ")

    trench_match = re.search(r"dimenzije rova[: ]+([0-9,\\.]+\s*x\s*[0-9,\\.]+\s*m)", search)
    if trench_match:
        trench_profile = trench_match.group(1).strip()

    start_point = "novi dv stub 10 kv"
    end_point = "postojeci dv stub 10 kv"

    start_match = re.search(
        r"na novom dv stubu 10 kv na k\.?p\.?br\.?\s*([0-9/]+)\s*ko\s*([a-zcjszdj]+)",
        search,
    )
    end_match = re.search(
        r"na postojecem dv stubu 10 kv na k\.?p\.?br\.?\s*([0-9/]+)\s*ko\s*([a-zcjszdj]+)",
        search,
    )
    if start_match:
        start_point = f"novi dv stub 10 kv {start_match.group(1)} ko {start_match.group(2)}"
    if end_match:
        end_point = f"postojeci dv stub 10 kv {end_match.group(1)} ko {end_match.group(2)}"

    if start_match and end_match:
        title = (
            f"Kablovski vod 10kV od novog DV stuba {start_match.group(1)} KO {start_match.group(2).title()} "
            f"do postojeceg DV stuba {end_match.group(1)} KO {end_match.group(2).title()}"
        )

    if "12m/1000dan" in search:
        notes.append("Novi AB stub 12m/1000dAN je deo prikljucenja.")
    if "12m/315dan" in search:
        notes.append("Postojeci AB stub 12m/315dAN se uklanja ili menja prema dokumentaciji.")
    if "pvc cevi" in search or "110mm" in search:
        notes.append("Predvidjeno polaganje u kablovskoj kanalizaciji / PVC cevi fi 110mm.")

    return OutputSpec(
        index=1,
        code="A",
        title=title,
        start_point=start_point,
        end_point=end_point,
        cable_type=cable_type or "3 x XHE 49-A 1x150mm2, 10 kV",
        trench_profile=trench_profile or "0,80 x 0,40 m",
        route_mode=route_mode,
        notes=notes,
    )


def _title_from_keywords(text: str, keywords: list[str]) -> str:
    lower = text.lower()
    for keyword in keywords:
        idx = lower.find(keyword)
        if idx >= 0:
            return text[max(0, idx - 40): idx + 120].strip()
    return " ".join(keywords)


def _fill_output_defaults(output: OutputSpec) -> None:
    if output.index == 1:
        output.code = output.code or "1"
        output.start_point = "k06"
        output.end_point = "puhovo - krstac"
    elif output.index == 2:
        output.code = output.code or "2"
        output.start_point = "k03"
        output.end_point = "krstac-kula"
    elif output.index == 3:
        output.code = output.code or "3"
        output.start_point = "k05"
        output.end_point = "fapromal"
    elif output.index == 4:
        output.code = output.code or "4"
        output.start_point = "k04"
        output.end_point = "lisice 3"
    elif output.index == 5:
        output.code = output.code or "5"
        output.start_point = "lisice 3"
        output.end_point = "lisice 1"


def parse_constraints(
    text_path: str | Path,
    source_name: str | None = None,
    text: str | None = None,
) -> list[ConstraintSpec]:
    path = Path(text_path)
    if not path.exists():
        return []

    if text is None:
        text = load_input_text(path)
    source = source_name or path.name
    constraints: list[ConstraintSpec] = []
    lower = text.lower()

    def add_if(condition: bool, category: str, description: str) -> None:
        if condition:
            constraints.append(ConstraintSpec(category=category, description=description, source=source))

    add_if("0,6 m" in lower or "0.6 m" in lower, "gas", "Parallelno vodjenje uz gasovod najmanje 0.6 m.")
    add_if("0,3 m" in lower or "0.3 m" in lower, "gas", "Ukrstanje sa gasovodom najmanje 0.3 m.")
    add_if("1,0 m" in lower or "1.0 m" in lower, "roads", "Parallelno vodjenje uz drzavni put najmanje 1.0 m od profila puta.")
    add_if("fi 110" in lower or "110mm" in lower, "crossing", "Kod ukrstanja ili zaštite koristiti PVC fi 110 gde je propisano.")
    add_if("fi 160" in lower or "160mm" in lower, "bridge", "Na mostu ili posebnom prelazu koristiti cev fi 160 gde je propisano.")
    add_if("ručno" in lower or "rucno" in lower, "gas", "U blizini gasovoda izvoditi zemljane radove ručno.")

    return constraints


def apply_project_musts(
    outputs: list[OutputSpec],
    project_task_text: str,
    condition_texts: list[str],
    constraints: list[ConstraintSpec],
) -> list[OutputSpec]:
    full_text = "\n".join([project_task_text, *condition_texts])
    normalized = _normalize_search_text(full_text)
    categories = {constraint.category for constraint in constraints}
    road_offset_m = _extract_min_parallel_road_offset(constraints)
    gas_parallel_clearance_m = _extract_numeric_constraint(constraints, "gas", ("parallelno", "gasovod"))
    gas_crossing_clearance_m = _extract_numeric_constraint(constraints, "gas", ("ukrstanje", "gasovod"))
    traffic_profile_required = any(
        phrase in normalized
        for phrase in (
            "svi planirani podzmeni kablovi se polazu u profilima saobracajnih povrsina",
            "svi planirani podzemni kablovi se polazu u profilima saobracajnih povrsina",
            "u poprecnim profilima svih saobracajnica planirani su nezavisni koridori",
        )
    )

    for output in outputs:
        musts = dict(output.route_musts or {})
        musts["inputs_are_authoritative"] = True
        musts["must_start_at_named_anchor"] = bool(output.start_point)
        musts["must_end_at_named_anchor"] = bool(output.end_point)
        musts["must_respect_route_mode"] = output.route_mode or "underground"

        route_mode = (output.route_mode or "").strip().lower()
        output_text = _normalize_search_text(" ".join([output.title, *output.notes]))
        output_blob = _extract_output_context_blob(normalized, output)
        mentions_existing_network = any(
            token in normalized or token in output_text or token in output_blob
            for token in (
                "postojec",
                "dalekovod",
                "dv ",
                "dv stub",
                "izvodna celija",
                "celija",
                "veza na nadzemni",
                "povezan na postojec",
                "trafostanic",
            )
        )
        musts["must_follow_existing_network"] = mentions_existing_network

        if route_mode == "underground" and ("roads" in categories or traffic_profile_required):
            musts["must_run_parallel_to_road"] = True
            musts["must_avoid_road_crossing"] = True
            musts["must_use_transport_corridor"] = traffic_profile_required
            if road_offset_m is not None:
                musts["road_offset_m"] = road_offset_m
        else:
            musts.setdefault("must_run_parallel_to_road", False)
            musts.setdefault("must_avoid_road_crossing", False)

        if route_mode == "underground" and "bridge" in categories:
            musts["must_support_bridge_crossing"] = True
            musts["bridge_pvc_diameter_mm"] = 160

        if route_mode == "underground" and "crossing" in categories:
            musts["must_support_protected_crossing"] = True
            musts["crossing_pvc_diameter_mm"] = 110

        if "gas" in categories:
            musts["must_respect_gas_clearance"] = True
            if gas_parallel_clearance_m is not None:
                musts["gas_parallel_clearance_m"] = gas_parallel_clearance_m
            if gas_crossing_clearance_m is not None:
                musts["gas_crossing_clearance_m"] = gas_crossing_clearance_m

        if route_mode == "overhead":
            musts["must_follow_existing_overhead_branch"] = mentions_existing_network

        if any(token in output_text or token in output_blob for token in ("prelaz sa nadzemnog na podzemni", "nadzemni nastavak", "veza na nadzemni")):
            musts["must_transition_overhead_underground"] = True

        if "veza na nadzemni dalekovod" in output_blob or "veza za dv" in output_blob:
            musts["must_connect_to_existing_overhead_branch"] = True
            musts["must_follow_existing_network"] = True

        if "u trasi postojeceg nadzemnog dv" in output_blob:
            musts["must_follow_existing_overhead_branch"] = True
            musts["must_follow_existing_network"] = True

        if "nova sahta veza za mbts" in output_blob or "nove sahte veza za mbts" in output_blob:
            musts["must_terminate_via_shaft"] = True

        switch_cell = _extract_switch_cell(output_blob)
        if switch_cell:
            musts["must_start_from_switch_cell"] = switch_cell

        if "postojeca zidana trafostanica" in output_blob or "postojecu zidanu betonsku trafostanic" in output_blob:
            musts["must_terminate_at_existing_substation"] = True
            musts["must_mount_on_existing_masonry_substation"] = True

        if (
            "postojeci uzb stub" in output_blob
            or "postojeci uz stub" in output_blob
            or "od postojeceg uzb stuba" in output_blob
        ):
            musts["must_terminate_at_existing_uzb_stub"] = True

        if (
            "novi uzb stub broj 1" in output_blob
            or "novom uzb 12/1600 stubu" in output_blob
            or "do novog uzb stuba" in output_blob
            or "na novi uzb stub" in output_blob
        ):
            musts["must_terminate_at_new_uzb_stub"] = True

        if "linijskog vertikalnog rastavljaca" in output_blob or "linijskih vertikalnoih rastavljaca" in output_blob:
            musts["must_use_vertical_switch_disconnector"] = True

        if "katodnim odvodnicima prenapona" in output_blob or "odvodnicima prenapona" in output_blob:
            musts["must_use_surge_arresters"] = True

        if "za unutrasnju montazu" in output_blob:
            musts["must_use_indoor_terminations"] = True

        if "za spoljasnju montazu" in output_blob:
            musts["must_use_outdoor_terminations"] = True

        if musts.get("must_use_outdoor_terminations") and musts.get("must_use_indoor_terminations"):
            musts["must_use_indoor_terminations"] = False

        if "armirano betonska" in output_blob and "2m x 2m x 2m" in output_blob:
            musts["shaft_inner_dimensions_m"] = [2.0, 2.0, 2.0]

        if "u trasi postojeceg dv" in output_blob or "u trasi postojeceg nadzemnog dv" in output_blob:
            musts["must_trace_existing_dv_alignment"] = True
            musts["must_follow_existing_network"] = True

        _apply_terminal_semantics(output, output_blob, output_text, musts)
        _apply_named_anchor_parcel_semantics(output, musts)
        _apply_parcel_semantics(output, output_blob, musts)
        _apply_route_contract_semantics(output, musts)
        output.route_musts = musts

    _reconcile_shared_anchor_transition_parcels(outputs)
    for output in outputs:
        musts = dict(output.route_musts or {})
        _apply_route_contract_semantics(output, musts)
        output.route_musts = musts
    return outputs


def _apply_terminal_semantics(output: OutputSpec, output_blob: str, output_text: str, musts: dict) -> None:
    combined = " ".join(part for part in [output_text, output_blob] if part).strip()

    start_name = _normalize_search_text(output.start_point or "")
    end_name = _normalize_search_text(output.end_point or "")
    is_ts_switch_start = bool(re.fullmatch(r"k0[0-9]", start_name))
    generic_start_uzb = "uzb stub" in start_name and "novi" not in start_name and "postojeci" not in start_name
    generic_end_uzb = "uzb stub" in end_name and "novi" not in end_name and "postojeci" not in end_name

    switch_cell = musts.get("must_start_from_switch_cell") or _extract_switch_cell(combined)
    if switch_cell and is_ts_switch_start:
        musts["start_anchor_type"] = "ts_switch_cell"
        musts["start_anchor_region"] = "ts 35/10kv krstac"
        musts["start_switch_cell"] = switch_cell
        musts["start_functional_target"] = f'Izvodna ćelija {switch_cell.upper()} u TS 35/10kV "Krstac"'

    if "ts 35/10kv krstac" in combined and is_ts_switch_start and "start_anchor_type" not in musts:
        musts["start_anchor_region"] = "ts 35/10kv krstac"

    if "novi uzb stub" in start_name or "novi uz stub" in start_name:
        musts["start_anchor_type"] = "new_uzb_stub"
        musts["start_physical_target"] = _format_stub_target(output.start_point, "new")
    elif "postojeci uzb stub" in start_name or "postojeci uz stub" in start_name:
        musts["start_anchor_type"] = "existing_uzb_stub"
        musts["start_physical_target"] = _format_stub_target(output.start_point, "existing")
    elif generic_start_uzb and "novog uzb stuba" in combined:
        musts["start_anchor_type"] = "new_uzb_stub"
        musts["start_physical_target"] = _extract_role_stub_target(combined, role="start", kind="new") or _format_stub_target(output.start_point, "new")
    elif generic_start_uzb and ("postojeceg uzb stuba" in combined or "postojecem uzb stubu" in combined):
        musts["start_anchor_type"] = "existing_uzb_stub"
        musts["start_physical_target"] = _extract_role_stub_target(combined, role="start", kind="existing") or _format_stub_target(output.start_point, "existing")
    elif any(token in start_name for token in ("ts 10/0,4kv", "ts 10/0,4 kv", "pts 10/0,4kv", "pts 10/0,4 kv")):
        # Final uči da tekst može da pominje TS/PTS kao funkcionalni cilj,
        # dok fizički početak ostaje na postojećem UZ stubu vezanom za tu TS.
        if "lisice 3" in start_name:
            musts["start_anchor_type"] = "existing_uzb_stub"
            musts["start_functional_target"] = 'PTS 10/0,4kV "Lisice 3"'
        else:
            musts["start_anchor_type"] = "existing_substation"
            musts["start_functional_target"] = output.start_point
    elif "lisice 3" in start_name and (
        "postojeci uzb stub" in combined
        or "postojeceg uzb stuba" in combined
        or "pts 10/0,4kv" in combined
    ):
        musts["start_anchor_type"] = "existing_uzb_stub"
        musts["start_functional_target"] = 'PTS 10/0,4kV "Lisice 3"'

    if "nova sahta" in combined and "fapromal" in combined:
        musts["end_anchor_type"] = "shaft_connection"
        musts["end_functional_target"] = 'MBTS 10/0,4kV "Fapromal"'
    elif "mbts" in end_name or "mbts" in combined:
        musts["end_anchor_type"] = "shaft_connection"
        musts.setdefault("end_functional_target", output.end_point or 'MBTS 10/0,4kV "Fapromal"')
    elif "postojeci uzb stub" in end_name or "postojeci uz stub" in end_name:
        musts["end_anchor_type"] = "existing_uzb_stub"
        musts["end_physical_target"] = _format_stub_target(output.end_point, "existing")
        if "lisice 3" in combined:
            musts["end_functional_target"] = 'PTS 10/0,4kV "Lisice 3"'
    elif generic_end_uzb and ("postojeceg uzb stuba" in combined or "postojecem uzb stubu" in combined):
        musts["end_anchor_type"] = "existing_uzb_stub"
        musts["end_physical_target"] = _extract_role_stub_target(combined, role="end", kind="existing") or _format_stub_target(output.end_point, "existing")
    elif "novi uzb stub" in end_name or "novi uz stub" in end_name or "lisice 1" in end_name:
        musts["end_anchor_type"] = "new_uzb_stub"
        musts["end_physical_target"] = _format_stub_target(output.end_point, "new")
        if "lisice 1" in combined:
            musts["end_functional_target"] = 'TS 10/0,4kV "Lisice 1"'
    elif musts.get("must_terminate_at_new_uzb_stub") and ("novog uzb stuba" in combined or generic_end_uzb):
        musts["end_anchor_type"] = "new_uzb_stub"
        musts["end_physical_target"] = _extract_role_stub_target(combined, role="end", kind="new") or _format_stub_target(output.end_point, "new")
    elif any(token in end_name for token in ("ts 10/0,4kv", "ts 10/0,4 kv", "pts 10/0,4kv", "pts 10/0,4 kv")):
        musts["end_anchor_type"] = "existing_substation"
        musts["end_functional_target"] = output.end_point
    elif "lisice 3" in end_name and (
        "postojeci uzb stub" in combined
        or "postojecem uzb stubu" in combined
        or "pts 10/0,4kv" in combined
    ):
        musts["end_anchor_type"] = "existing_uzb_stub"
        musts["end_physical_target"] = 'Postojeći UZB stub (veza na PTS "Lisice 3")'
        musts["end_functional_target"] = 'PTS 10/0,4kV "Lisice 3"'

    if (
        musts.get("must_terminate_at_existing_substation")
        and musts.get("must_use_outdoor_terminations")
        and musts.get("must_mount_on_existing_masonry_substation")
    ):
        musts["end_anchor_type"] = "existing_substation_exterior_connection"
        musts["end_structure_type"] = "existing_masonry_substation"
        musts["end_mounting"] = "outdoor"

    if (
        musts.get("start_anchor_type") == "existing_uzb_stub"
        and musts.get("must_use_outdoor_terminations")
        and musts.get("must_use_vertical_switch_disconnector")
    ):
        musts["start_anchor_type"] = "existing_uzb_stub_exterior_connection"
        musts["start_mounting"] = "outdoor"
        musts["start_requires_vertical_switch_disconnector"] = True
        if musts.get("must_use_surge_arresters"):
            musts["start_requires_surge_arresters"] = True
        if "start_physical_target" not in musts and "lisice 3" in combined:
            musts["start_physical_target"] = 'Postojeći UZB stub (veza na PTS "Lisice 3")'

    if (
        musts.get("end_anchor_type") == "new_uzb_stub"
        and musts.get("must_use_outdoor_terminations")
        and musts.get("must_use_vertical_switch_disconnector")
    ):
        musts["end_anchor_type"] = "new_uzb_stub_exterior_connection"
        musts["end_mounting"] = "outdoor"
        musts["end_requires_vertical_switch_disconnector"] = True
        if musts.get("must_use_surge_arresters"):
            musts["end_requires_surge_arresters"] = True

    if (
        musts.get("end_anchor_type") == "existing_uzb_stub"
        and musts.get("must_use_outdoor_terminations")
        and musts.get("must_use_vertical_switch_disconnector")
    ):
        musts["end_anchor_type"] = "existing_uzb_stub_exterior_connection"
        musts["end_mounting"] = "outdoor"
        musts["end_requires_vertical_switch_disconnector"] = True
        if musts.get("must_use_surge_arresters"):
            musts["end_requires_surge_arresters"] = True
        if "end_physical_target" not in musts and "lisice 3" in combined:
            musts["end_physical_target"] = 'Postojeći UZB stub (veza na PTS "Lisice 3")'

    if (
        "lisice 3" in combined
        and musts.get("start_anchor_type") in {"existing_uzb_stub", "existing_uzb_stub_exterior_connection"}
    ):
        musts["start_physical_target"] = 'Postojeći UZB stub (veza na PTS "Lisice 3")'
        musts.setdefault("start_functional_target", 'PTS 10/0,4kV "Lisice 3"')
    if (
        "lisice 3" in combined
        and musts.get("end_anchor_type") in {"existing_uzb_stub", "existing_uzb_stub_exterior_connection"}
    ):
        musts["end_physical_target"] = 'Postojeći UZB stub (veza na PTS "Lisice 3")'
        musts.setdefault("end_functional_target", 'PTS 10/0,4kV "Lisice 3"')

    dv_target = _extract_named_network_target(combined, prefix="dv")
    if dv_target:
        musts["corridor_alignment_target"] = dv_target
    ts_target = _extract_named_network_target(combined, prefix="ts")
    if ts_target and "end_functional_target" not in musts and "end_anchor_type" in musts:
        musts["end_functional_target"] = ts_target
    pts_target = _extract_named_network_target(combined, prefix="pts")
    if pts_target:
        if musts.get("end_anchor_type") == "existing_uzb_stub":
            musts["end_functional_target"] = pts_target
        elif musts.get("start_anchor_type") == "existing_uzb_stub" and "start_functional_target" not in musts:
            musts["start_functional_target"] = pts_target
    mbts_target = _extract_named_network_target(combined, prefix="mbts")
    if mbts_target:
        musts["end_functional_target"] = mbts_target


def _extract_named_anchor_parcel_ko(name: str | None) -> tuple[str, str] | None:
    normalized = _normalize_search_text(name or "")
    if not normalized:
        return None
    match = re.search(
        r"\b(?:novi|postojeci)\s+dv\s+stub\s+10\s+kv\s+(?P<parcel>\d+(?:/\d+)?)\s+ko\s+(?P<ko>[a-z0-9]+)\b",
        normalized,
    )
    if not match:
        return None
    return match.group("parcel"), match.group("ko")


def _apply_named_anchor_parcel_semantics(output: OutputSpec, musts: dict) -> None:
    start_match = _extract_named_anchor_parcel_ko(output.start_point)
    if start_match:
        parcel, ko = start_match
        musts.setdefault("start_parcel_hint", parcel)
        musts.setdefault("start_ko_hint", ko)
        musts["must_start_on_named_anchor_parcel"] = True
        musts.setdefault("start_physical_target", output.start_point)

    end_match = _extract_named_anchor_parcel_ko(output.end_point)
    if end_match:
        parcel, ko = end_match
        musts.setdefault("end_parcel_hint", parcel)
        musts.setdefault("end_ko_hint", ko)
        musts["must_end_on_named_anchor_parcel"] = True
        musts.setdefault("end_physical_target", output.end_point)


def _extract_output_parcel_groups(text: str) -> list[dict[str, str]]:
    groups: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for parcel_blob, ko in re.findall(r"k\.?p\.?\s*([0-9/,\s]+?)\s*k\.?o\.?\s*([a-z0-9]+)", text):
        parcels = re.findall(r"\b\d+(?:/\d+)?\b", parcel_blob)
        for parcel in parcels:
            key = (parcel, ko)
            if key in seen:
                continue
            seen.add(key)
            groups.append({"parcel": parcel, "ko": ko})
    return groups


def _clean_output_parcel_groups(groups: list[dict[str, str]]) -> list[dict[str, str]]:
    cleaned: list[dict[str, str]] = []
    for group in groups:
        parcel = group["parcel"]
        ko = group["ko"]
        drop = False
        for other in groups:
            if other is group or other["ko"] != ko:
                continue
            other_parcel = other["parcel"]
            if other_parcel == parcel:
                continue
            if "/" not in parcel and other_parcel.startswith(parcel + "/"):
                drop = True
                break
            if "/" in parcel and len(parcel) < len(other_parcel) and other_parcel.endswith(parcel):
                drop = True
                break
            if len(parcel) <= 3 and len(other_parcel) > len(parcel) and parcel in other_parcel:
                drop = True
                break
        if not drop:
            cleaned.append(group)
    return cleaned


def _select_parcel_context_blob(output: OutputSpec, output_blob: str) -> str:
    code = (output.code or str(output.index)).strip().upper()
    marker = "nadzemni vod 10"
    split_idx = output_blob.find(marker)
    if split_idx < 0:
        return output_blob
    if code.endswith("A") or (output.route_mode or "").strip().lower() == "underground":
        return output_blob[:split_idx]
    if code.endswith("B") or (output.route_mode or "").strip().lower() == "overhead":
        return output_blob[split_idx:]
    return output_blob


def _apply_parcel_semantics(output: OutputSpec, output_blob: str, musts: dict) -> None:
    parcel_blob = _select_parcel_context_blob(output, output_blob)
    groups = _clean_output_parcel_groups(_extract_output_parcel_groups(parcel_blob))
    groups = _trim_transition_segment_parcel_tail(output, musts, groups)
    if not groups:
        return
    musts["must_follow_named_parcels"] = True
    musts["parcel_corridor"] = groups
    musts["parcel_corridor_labels"] = [f'{item["parcel"]} k.o. {item["ko"]}' for item in groups]

    start_anchor_type = musts.get("start_anchor_type")
    end_anchor_type = musts.get("end_anchor_type")
    if groups:
        musts.setdefault("start_parcel_hint", groups[0]["parcel"])
        musts.setdefault("start_ko_hint", groups[0]["ko"])
        musts.setdefault("end_parcel_hint", groups[-1]["parcel"])
        musts.setdefault("end_ko_hint", groups[-1]["ko"])
        if len(groups) > 1:
            musts.setdefault("start_next_parcel_hint", groups[1]["parcel"])
            musts.setdefault("start_next_ko_hint", groups[1]["ko"])
            musts.setdefault("end_prev_parcel_hint", groups[-2]["parcel"])
            musts.setdefault("end_prev_ko_hint", groups[-2]["ko"])

    if start_anchor_type == "ts_switch_cell" and len(groups) > 1:
        musts["start_parcel_hint"] = groups[0]["parcel"]
        musts["start_ko_hint"] = groups[0]["ko"]

    if end_anchor_type in {
        "existing_substation",
        "existing_substation_exterior_connection",
        "existing_uzb_stub",
        "existing_uzb_stub_exterior_connection",
        "new_uzb_stub",
        "new_uzb_stub_exterior_connection",
        "shaft_connection",
    }:
        musts["end_parcel_hint"] = groups[-1]["parcel"]
        musts["end_ko_hint"] = groups[-1]["ko"]


def _trim_transition_segment_parcel_tail(
    output: OutputSpec,
    musts: dict,
    groups: list[dict[str, str]],
) -> list[dict[str, str]]:
    if len(groups) < 3:
        return groups
    route_mode = (output.route_mode or "").strip().lower()
    start_type = str(musts.get("start_anchor_type") or "")
    end_type = str(musts.get("end_anchor_type") or "")
    if route_mode != "overhead":
        return groups
    if not start_type.startswith("new_uzb_stub") or not end_type.startswith("new_uzb_stub"):
        return groups

    start_ko = str(groups[0].get("ko") or "")
    if not start_ko:
        return groups

    left_origin = False
    last_valid_index = len(groups) - 1
    for idx, group in enumerate(groups[1:], start=1):
        ko = str(group.get("ko") or "")
        if not ko:
            continue
        if ko != start_ko:
            left_origin = True
            last_valid_index = idx
            continue
        if left_origin and ko == start_ko:
            return groups[:last_valid_index + 1]
        last_valid_index = idx
    return groups


def _reconcile_shared_anchor_transition_parcels(outputs: list[OutputSpec]) -> None:
    point_roles: dict[str, list[tuple[OutputSpec, str]]] = {}
    for output in outputs:
        for role, point_name in (("start", output.start_point), ("end", output.end_point)):
            key = _normalize_search_text(point_name or "")
            if not key:
                continue
            point_roles.setdefault(key, []).append((output, role))

    for key, entries in point_roles.items():
        if len(entries) < 2:
            continue
        if not any(role == "start" for _, role in entries) or not any(role == "end" for _, role in entries):
            continue
        start_hint_pairs: set[tuple[str, str]] = set()
        end_hint_pairs: set[tuple[str, str]] = set()
        for output, role in entries:
            musts = dict(getattr(output, "route_musts", {}) or {})
            parcel = musts.get(f"{role}_parcel_hint")
            ko = musts.get(f"{role}_ko_hint")
            if not parcel or not ko:
                continue
            pair = (str(parcel), str(ko))
            if role == "start":
                start_hint_pairs.add(pair)
            else:
                end_hint_pairs.add(pair)

        preferred_shared = start_hint_pairs & end_hint_pairs
        if preferred_shared:
            ordered_shared = sorted(preferred_shared, key=lambda item: (item[1], item[0]))
        else:
            parcel_sets = []
            for output, _role in entries:
                musts = dict(getattr(output, "route_musts", {}) or {})
                corridor = musts.get("parcel_corridor") or []
                normalized = {
                    (item.get("parcel"), item.get("ko"))
                    for item in corridor
                    if item.get("parcel") and item.get("ko")
                }
                if normalized:
                    parcel_sets.append(normalized)
            if len(parcel_sets) < 2:
                continue
            shared = set.intersection(*parcel_sets)
            if not shared:
                continue
            ordered_shared = sorted(shared, key=lambda item: (item[1], item[0]))
        for output, role in entries:
            musts = dict(getattr(output, "route_musts", {}) or {})
            musts["shared_anchor_parcel_candidates"] = [
                {"parcel": parcel, "ko": ko} for parcel, ko in ordered_shared
            ]
            musts["shared_anchor_parcel_candidate_labels"] = [
                f"{parcel} k.o. {ko}" for parcel, ko in ordered_shared
            ]
            if len(ordered_shared) == 1:
                parcel, ko = ordered_shared[0]
                musts[f"{role}_parcel_hint"] = parcel
                musts[f"{role}_ko_hint"] = ko
            output.route_musts = musts


def _apply_route_contract_semantics(output: OutputSpec, musts: dict) -> None:
    route_mode = (output.route_mode or "").strip().lower() or str(musts.get("must_respect_route_mode") or "").strip().lower()
    corridor_alignment_target = musts.get("corridor_alignment_target")
    parcel_corridor = list(musts.get("parcel_corridor") or [])
    start_anchor_type = str(musts.get("start_anchor_type") or "")
    end_anchor_type = str(musts.get("end_anchor_type") or "")
    start_has_named_parcel = bool(musts.get("start_parcel_hint") and musts.get("start_ko_hint"))
    end_has_named_parcel = bool(musts.get("end_parcel_hint") and musts.get("end_ko_hint"))

    musts["must_start_on_first_named_parcel"] = bool(parcel_corridor)
    musts["must_end_on_last_named_parcel"] = bool(parcel_corridor)
    musts["must_visit_parcels_in_order"] = bool(parcel_corridor)
    musts["must_start_on_named_anchor_parcel"] = bool(musts.get("must_start_on_named_anchor_parcel") or start_has_named_parcel)
    musts["must_end_on_named_anchor_parcel"] = bool(musts.get("must_end_on_named_anchor_parcel") or end_has_named_parcel)

    start_anchor_adjustable = start_anchor_type in {
        "new_uzb_stub",
        "new_uzb_stub_exterior_connection",
        "shaft_connection",
    }
    end_anchor_adjustable = end_anchor_type in {
        "new_uzb_stub",
        "new_uzb_stub_exterior_connection",
        "shaft_connection",
    }
    musts["start_anchor_adjustable"] = start_anchor_adjustable
    musts["end_anchor_adjustable"] = end_anchor_adjustable
    musts["start_anchor_mobility"] = "adjustable_projected" if start_anchor_adjustable else "fixed_existing"
    musts["end_anchor_mobility"] = "adjustable_projected" if end_anchor_adjustable else "fixed_existing"

    if corridor_alignment_target:
        musts["must_align_to_named_network_target"] = True

    if musts.get("must_follow_existing_overhead_branch") or musts.get("must_trace_existing_dv_alignment"):
        corridor_kind = "existing_dv_alignment"
    elif musts.get("must_use_transport_corridor") or musts.get("must_run_parallel_to_road"):
        corridor_kind = "road_transport_profile"
    elif musts.get("must_follow_existing_network"):
        corridor_kind = "existing_network_corridor"
    elif parcel_corridor:
        corridor_kind = "named_parcel_corridor"
    else:
        corridor_kind = "unspecified"

    transition_kind = None
    if route_mode == "underground" and end_anchor_adjustable and (
        musts.get("must_transition_overhead_underground")
        or musts.get("must_connect_to_existing_overhead_branch")
        or musts.get("must_follow_existing_overhead_branch")
    ):
        transition_kind = "underground_to_overhead_at_end"
    elif route_mode == "overhead" and start_anchor_adjustable:
        transition_kind = "overhead_starts_from_transition_anchor"
    elif route_mode == "underground" and end_anchor_type == "shaft_connection":
        transition_kind = "underground_terminates_in_new_shaft"

    route_contract = {
        "route_mode": route_mode or "unknown",
        "corridor_kind": corridor_kind,
        "named_network_target": corridor_alignment_target,
        "must_follow_named_parcels": bool(parcel_corridor),
        "must_visit_parcels_in_order": bool(parcel_corridor),
        "parcel_sequence": [
            {
                "parcel": item.get("parcel"),
                "ko": item.get("ko"),
                "label": f'{item.get("parcel")} k.o. {item.get("ko")}',
            }
            for item in parcel_corridor
        ],
        "start_anchor": {
            "type": start_anchor_type or None,
            "mobility": musts["start_anchor_mobility"],
            "adjustable": start_anchor_adjustable,
            "parcel": musts.get("start_parcel_hint"),
            "ko": musts.get("start_ko_hint"),
            "must_lie_on_named_parcel": musts["must_start_on_named_anchor_parcel"],
        },
        "end_anchor": {
            "type": end_anchor_type or None,
            "mobility": musts["end_anchor_mobility"],
            "adjustable": end_anchor_adjustable,
            "parcel": musts.get("end_parcel_hint"),
            "ko": musts.get("end_ko_hint"),
            "must_lie_on_named_parcel": musts["must_end_on_named_anchor_parcel"],
        },
        "transition_kind": transition_kind,
    }
    shared_candidates = list(musts.get("shared_anchor_parcel_candidates") or [])
    if shared_candidates:
        route_contract["shared_transition_parcel_candidates"] = shared_candidates
    musts["route_contract"] = route_contract


def _read_pdf_text(path: Path) -> str:
    parts: list[str] = []
    try:
        reader = PdfReader(str(path))
        for page in reader.pages:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                continue
    except Exception:
        return ""
    return "\n".join(parts)


def _load_cached_input_text(path: Path) -> str:
    cache_file = _input_text_cache_file(path)
    if not cache_file.exists():
        return ""
    try:
        payload = json.loads(cache_file.read_text(encoding="utf-8"))
    except Exception:
        return ""
    text = payload.get("text", "")
    return text if isinstance(text, str) else ""


def _store_cached_input_text(path: Path, text: str) -> None:
    if not text.strip():
        return
    cache_file = _input_text_cache_file(path)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": str(path.resolve()),
        "size": path.stat().st_size,
        "mtime_ns": path.stat().st_mtime_ns,
        "version": INPUT_TEXT_CACHE_VERSION,
        "text": text,
    }
    cache_file.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _input_text_cache_file(path: Path) -> Path:
    resolved = path.resolve()
    signature = f"{resolved}|{path.stat().st_size}|{path.stat().st_mtime_ns}|{INPUT_TEXT_CACHE_VERSION}"
    digest = hashlib.sha1(signature.encode("utf-8", errors="ignore")).hexdigest()
    INPUT_TEXT_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return INPUT_TEXT_CACHE_DIR / f"{digest}.json"


def _infer_input_kind(path: Path) -> str:
    lower = path.name.lower()
    if "zadat" in lower or "project" in lower or "projekt" in lower:
        return "project_task"
    return "condition"


def _is_good_enough_without_ocr(path: Path, text: str) -> bool:
    if not text.strip():
        return False
    kind = _infer_input_kind(path)
    readiness = assess_input_readiness(path, text, kind)
    if readiness.get("is_readable_enough"):
        return True
    stripped = " ".join(text.split())
    if len(stripped) < 400:
        return False
    found_signals = readiness.get("found_signals", [])
    useful = readiness.get("useful_text", False)
    return useful and len(found_signals) >= 2


def _run_windows_ocr(path: Path) -> str:
    script = Path(__file__).resolve().parent.parent / "windows_ocr.ps1"
    if not script.exists():
        return ""
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(script),
                "-Path",
                str(path),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=240,
            check=False,
        )
    except Exception:
        return ""
    return (completed.stdout or "").strip()


def _run_tesseract_ocr(path: Path) -> str:
    tesseract = _first_existing_path(TESSERACT_EXE_CANDIDATES)
    if tesseract is None:
        return ""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _run_tesseract_on_pdf(path, tesseract)
    return _run_tesseract_on_image(path, tesseract)


def _run_tesseract_on_pdf(path: Path, tesseract: Path) -> str:
    ghostscript = _first_existing_path(GHOSTSCRIPT_EXE_CANDIDATES)
    if ghostscript is None:
        return ""
    temp_path = _reset_ocr_subdir(f"pdf_{path.stem}")
    try:
        safe_pdf = _copy_to_ascii_temp_path(path, temp_path / "source.pdf")
        png_pattern = temp_path / "page-%03d.png"
        try:
            rendered = subprocess.run(
                [
                    str(ghostscript),
                    "-dNOPAUSE",
                    "-dBATCH",
                    "-sDEVICE=png16m",
                    "-r300",
                    f"-sOutputFile={png_pattern}",
                    str(safe_pdf),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=240,
                check=False,
            )
        except Exception:
            return ""
        if rendered.returncode != 0:
            return ""
        pages = sorted(temp_path.glob("page-*.png"))
        if not pages:
            return ""
        parts: list[str] = []
        for page in pages:
            text = _run_tesseract_on_image(page, tesseract)
            if text.strip():
                parts.append(text)
        return "\n".join(parts)
    finally:
        _safe_rmtree(temp_path)


def _run_tesseract_on_image(path: Path, tesseract: Path) -> str:
    temp_path = _reset_ocr_subdir(f"img_{path.stem}")
    try:
        out_base = temp_path / "ocr_out"
        tessdata_dir = _resolve_tessdata_dir()
        lang = _resolve_tesseract_languages(tessdata_dir)
        command = [
            str(tesseract),
            str(path),
            str(out_base),
            "-l",
            lang,
            "--psm",
            "6",
        ]
        if tessdata_dir is not None:
            command.extend(["--tessdata-dir", str(tessdata_dir)])
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=180,
                check=False,
            )
        except Exception:
            return ""
        if completed.returncode != 0:
            return ""
        txt_path = out_base.with_suffix(".txt")
        if not txt_path.exists():
            return ""
        return txt_path.read_text(encoding="utf-8", errors="ignore")
    finally:
        _safe_rmtree(temp_path)


def _looks_like_useful_text(text: str) -> bool:
    stripped = " ".join(text.split())
    if len(stripped) < 80:
        return False
    alpha_ratio = sum(ch.isalpha() for ch in stripped) / max(1, len(stripped))
    return alpha_ratio > 0.45


def assess_input_readiness(path: str | Path, text: str, kind: str) -> dict:
    resolved = resolve_existing_path(path)
    normalized = _normalize_search_text(text)
    stripped = " ".join(text.split())
    issues: list[str] = []
    blockers: list[str] = []

    if not stripped:
        blockers.append("nije izvucen nikakav tekst")
    elif len(stripped) < 200:
        blockers.append("izvuceni tekst je prekratak")
    elif not _looks_like_useful_text(text):
        issues.append("izvuceni tekst deluje sumnjivo ili OCR-sumovito")

    expected = _expected_input_signals(resolved, kind)
    found = [token for token in expected if token in normalized]
    missing = [token for token in expected if token not in normalized]
    if expected and not found:
        blockers.append("nisu pronadjeni ocekivani pojmovi za ovu vrstu dokumenta")
    elif expected and len(found) < max(1, min(2, len(expected))):
        issues.append("pronadjen je samo mali deo ocekivanih pojmova")

    if (
        kind != "project_task"
        and blockers == ["nisu pronadjeni ocekivani pojmovi za ovu vrstu dokumenta"]
        and _looks_like_useful_text(text)
        and len(stripped) >= 500
    ):
        issues.extend(blockers)
        blockers = []

    return {
        "path": str(resolved),
        "kind": kind,
        "chars": len(stripped),
        "useful_text": _looks_like_useful_text(text),
        "expected_signals": expected,
        "found_signals": found,
        "missing_signals": missing,
        "issues": issues,
        "blockers": blockers,
        "is_readable_enough": not blockers,
    }


def _expected_input_signals(path: Path, kind: str) -> list[str]:
    lower = path.name.lower()
    if kind == "project_task":
        return ["projektni", "zadatak", "prikljucna mesta", "izbor trase", "10 kv"]
    if "lokacijski" in lower:
        return ["izvod", "k03", "k04", "k05", "k06", "uzb"]
    if "putevi" in lower or "putevi" in str(path).lower() or "put" in lower:
        return ["put", "saobrac", "profil", "most"]
    if "gas" in lower:
        return ["gas", "gasovod", "ukrst"]
    if "telekom" in lower:
        return ["telekom", "opt", "kabl"]
    if "vodni" in lower:
        return ["vod", "ukrst", "zastit"]
    if "komunalac" in lower:
        return ["vod", "kanal", "uslov"]
    if "informacija" in lower and "lokaciji" in lower:
        return ["lokacij", "katast", "parcela"]
    return ["uslov"]


def _find_txt_fallback(pdf_path: Path) -> Path | None:
    same_dir = pdf_path.with_suffix(".txt")
    if same_dir.exists():
        return same_dir

    try:
        sibling_candidates = sorted(pdf_path.parent.glob("*.txt"))
    except Exception:
        sibling_candidates = []
    stem = pdf_path.stem.lower()
    best_candidate = None
    best_score = 0
    for candidate in sibling_candidates:
        candidate_stem = candidate.stem.lower()
        if candidate_stem == stem or stem in candidate_stem or candidate_stem in stem:
            return candidate
        score = _stem_match_score(stem, candidate_stem)
        if score > best_score:
            best_score = score
            best_candidate = candidate
    if best_score >= 2:
        return best_candidate
    return None


def _first_existing_path(candidates: list[Path]) -> Path | None:
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def _best_text_candidate(candidates: list[str]) -> str:
    ranked = [item for item in candidates if item and item.strip()]
    if not ranked:
        return ""
    ranked.sort(key=lambda item: _text_quality_score(item), reverse=True)
    return ranked[0]


def _text_quality_score(text: str) -> float:
    stripped = " ".join(text.split())
    if not stripped:
        return 0.0
    alpha = sum(ch.isalpha() for ch in stripped)
    digit = sum(ch.isdigit() for ch in stripped)
    spaces = stripped.count(" ")
    return len(stripped) * 0.2 + alpha * 1.0 + digit * 0.15 + spaces * 0.05


def _safe_rmtree(path: Path) -> None:
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


def _copy_to_ascii_temp_path(source: Path, target: Path) -> Path:
    try:
        shutil.copy2(source, target)
        return target
    except Exception:
        try:
            shutil.copyfile(source, target)
            return target
        except Exception:
            return source


def _ensure_ocr_work_root() -> Path:
    OCR_WORK_ROOT.mkdir(exist_ok=True)
    return OCR_WORK_ROOT


def _reset_ocr_subdir(name: str) -> Path:
    root = _ensure_ocr_work_root()
    safe_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name).strip("_") or "ocr"
    path = root / f"{safe_name}_{uuid4().hex}"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _resolve_tessdata_dir() -> Path | None:
    if LOCAL_TESSDATA_DIR.exists():
        return LOCAL_TESSDATA_DIR
    default = Path(r"C:\Program Files\Tesseract-OCR\tessdata")
    if default.exists():
        return default
    return None


def _resolve_tesseract_languages(tessdata_dir: Path | None) -> str:
    if tessdata_dir is None:
        return "eng"
    srp = tessdata_dir / "srp.traineddata"
    eng = tessdata_dir / "eng.traineddata"
    if srp.exists() and eng.exists():
        return "srp+eng"
    if srp.exists():
        return "srp"
    return "eng"


def _stem_match_score(left: str, right: str) -> int:
    left_tokens = {_normalize_token(token) for token in re.split(r"[^a-z0-9]+", left) if token}
    right_tokens = {_normalize_token(token) for token in re.split(r"[^a-z0-9]+", right) if token}
    left_tokens.discard("")
    right_tokens.discard("")
    return len(left_tokens & right_tokens)


def _normalize_token(token: str) -> str:
    token = token.lower()
    for suffix in ("a", "e", "i", "o", "u", "no", "ni", "na"):
        if len(token) > 4 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def _normalize_search_text(text: str) -> str:
    transliterated = text.translate(CYRILLIC_TO_LATIN)
    transliterated = transliterated.replace("Ø", "o").replace("°", "")
    normalized = unicodedata.normalize("NFKD", transliterated)
    normalized = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    normalized = normalized.lower()
    normalized = normalized.replace("kv", " kv ")
    normalized = re.sub(r"[^a-z0-9/.,:()\\-\\s]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def _extract_min_parallel_road_offset(constraints: list[ConstraintSpec]) -> float | None:
    for constraint in constraints:
        if constraint.category != "roads":
            continue
        value = _extract_first_number(constraint.description)
        if value is not None:
            return value
    return None


def _extract_numeric_constraint(
    constraints: list[ConstraintSpec],
    category: str,
    required_terms: tuple[str, ...],
) -> float | None:
    for constraint in constraints:
        if constraint.category != category:
            continue
        lower = constraint.description.lower()
        if not all(term in lower for term in required_terms):
            continue
        value = _extract_first_number(constraint.description)
        if value is not None:
            return value
    return None


def _extract_first_number(text: str) -> float | None:
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*m", text.lower())
    if not match:
        return None
    return float(match.group(1).replace(",", "."))


def _extract_output_context_blob(normalized_text: str, output: OutputSpec) -> str:
    index = output.index
    tokens = [f"izvod {index}:"]
    if index == 1:
        tokens.append("izvod 1:")
    spans: list[tuple[int, int]] = []
    for token in tokens:
        cursor = 0
        while True:
            start = normalized_text.find(token, cursor)
            if start < 0:
                break
            next_markers = []
            for probe in range(1, 8):
                marker = normalized_text.find(f"izvod {probe}:", start + len(token))
                if marker >= 0:
                    next_markers.append(marker)
            end = min(next_markers) if next_markers else min(len(normalized_text), start + 2400)
            spans.append((start, end))
            cursor = start + len(token)
    if not spans:
        search_terms = [item for item in [output.start_point, output.end_point] if item]
        for term in search_terms:
            normalized_term = _normalize_search_text(term)
            cursor = 0
            while True:
                start = normalized_text.find(normalized_term, cursor)
                if start < 0:
                    break
                end = min(len(normalized_text), start + 1800)
                spans.append((start, end))
                cursor = start + max(1, len(normalized_term))
    if not spans:
        return ""
    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    parts = [normalized_text[start:end] for start, end in merged]
    return "\n".join(parts)


def _extract_switch_cell(text: str) -> str | None:
    match = re.search(r"izvodnu celiju broj\s+(k0[0-9])", text)
    if not match:
        match = re.search(r"\b(k0[0-9])\b", text)
    if not match:
        return None
    return match.group(1)


def _extract_named_network_target(text: str, prefix: str) -> str | None:
    patterns = {
        "dv": r'dv\s+10\s*kv\s+"?([a-z0-9 \-/]+)"?',
        "ts": r'ts\s+10/0,4kv\s+"?([a-z0-9 \-/]+)"?',
        "pts": r'pts\s+10/0,4kv\s+"?([a-z0-9 \-/]+)"?',
        "mbts": r'mbts\s+10/0,4kv\s+"?([a-z0-9 \-/]+)"?',
    }
    pattern = patterns.get(prefix)
    if not pattern:
        return None
    match = re.search(pattern, text)
    if not match:
        return None
    suffix = match.group(1).strip(" -)")
    label = prefix.upper() if prefix != "ts" else "TS"
    if prefix == "pts":
        label = "PTS"
    if prefix == "mbts":
        label = "MBTS"
    if prefix == "dv":
        label = "DV 10kV"
        return f'{label} "{suffix}"'
    if prefix in {"ts", "pts", "mbts"}:
        return f'{label} 10/0,4kV "{suffix}"'
    return None


def _format_stub_target(name: str | None, kind: str) -> str | None:
    normalized = _normalize_search_text(name or "")
    if not normalized:
        return None
    number_match = re.search(r"stub(?:\s+broj)?\s+([0-9]+)", normalized)
    if number_match:
        prefix = "Novi UZB stub broj" if kind == "new" else "Postojeći UZB stub broj"
        return f"{prefix} {number_match.group(1)}"
    if "lisice 3" in normalized and kind == "existing":
        return 'Postojeći UZB stub (veza na PTS "Lisice 3")'
    if "lisice 1" in normalized and kind == "new":
        return 'Novi UZB stub (veza za TS "Lisice 1")'
    return name


def _extract_role_stub_target(text: str, role: str, kind: str) -> str | None:
    normalized = _normalize_search_text(text)
    if not normalized:
        return None
    patterns: list[str] = []
    if kind == "new":
        if role == "start":
            patterns = [
                r"od novog uzb stuba broj\s+([0-9]+)",
                r"mesto prikljucenja .*? na novom uzb stubu .*? broj\s+([0-9]+)",
            ]
        else:
            patterns = [
                r"do novog uzb stuba broj\s+([0-9]+)",
                r"krajnja tacka .*? na novom uzb stubu .*? broj\s+([0-9]+)",
                r"novi uzb stub broj\s+([0-9]+)",
            ]
    else:
        if "lisice 3" in normalized:
            return 'Postojeći UZB stub (veza na PTS "Lisice 3")'
        patterns = [
            r"od postojeceg uzb stuba broj\s+([0-9]+)",
            r"do postojeceg uzb stuba broj\s+([0-9]+)",
            r"postojeci uzb stub broj\s+([0-9]+)",
        ]
    for pattern in patterns:
        match = re.search(pattern, normalized)
        if not match:
            continue
        number = match.group(1)
        if kind == "new":
            return f"Novi UZB stub broj {number}"
        return f"Postojeći UZB stub broj {number}"
    return None
