from __future__ import annotations

from pathlib import Path

import ezdxf
from ezdxf.math import Vec2
from ezdxf.render.mleader import ConnectionSide, HorizontalConnection, LeaderType, TextAlignment


ROOT = Path(__file__).resolve().parent
OUT_DXF = ROOT / "leader_test_variants.dxf"


def ensure_layers(doc: ezdxf.EzDxf) -> None:
    if "Text Zivko" not in doc.layers:
        doc.layers.add("Text Zivko", dxfattribs={"color": 7, "linetype": "Continuous", "lineweight": -3})
    if "s_LIDER" not in doc.layers:
        doc.layers.add("s_LIDER", dxfattribs={"color": 7, "linetype": "Continuous", "lineweight": 18})
    if "ROUTE_TEST" not in doc.layers:
        doc.layers.add("ROUTE_TEST", dxfattribs={"color": 1, "linetype": "Continuous", "lineweight": 35})

    if "VIP" not in {style.dxf.name for style in doc.styles}:
        doc.styles.add("VIP", font="arial.ttf", dxfattribs={"width": 1.0})


def add_route(msp) -> None:
    msp.add_lwpolyline(
        [(0, 0), (160, 0), (220, 40), (300, 40)],
        dxfattribs={"layer": "ROUTE_TEST", "color": 1, "lineweight": 35},
    )


def add_classic_leader(msp) -> None:
    note = "A - MTEXT + LEADER\\PPomeri tekst i vidi da li lider prati"
    insert = (40, 95)
    target = (70, 0)
    mtext = msp.add_mtext(
        note,
        dxfattribs={
            "layer": "Text Zivko",
            "color": 4,
            "char_height": 6.0,
            "style": "VIP",
            "attachment_point": 7,
        },
    )
    mtext.dxf.insert = insert
    leader = msp.add_leader(
        [target, (115, 94)],
        dimstyle="VIP",
        dxfattribs={
            "layer": "s_LIDER",
            "color": 1,
            "dimstyle": "VIP",
            "annotation_type": 0,
            "text_height": 6.989058,
            "text_width": 120.0,
        },
    )
    leader.dxf.dimstyle = "VIP"
    leader.dxf.annotation_handle = mtext.dxf.handle
    leader.dxf.has_arrowhead = 1
    leader.dxf.has_hookline = 1
    leader.dxf.path_type = 0
    leader.dxf.hookline_direction = 1
    leader.dxf.horizontal_direction = (1.0, 0.0, 0.0)
    leader.dxf.normal_vector = (0.0, 0.0, 1.0)


def add_mleader(msp) -> None:
    note = "B - MULTILEADER\\PPomeri tekst i vidi ponasanje"
    note_point = Vec2((40, 65))
    target_point = Vec2((145, 0))
    builder = msp.add_multileader_mtext("Standard", dxfattribs={"layer": "s_LIDER"})
    builder.set_overall_scaling(1.0)
    builder.set_leader_properties(
        color=1,
        linetype="BYLAYER",
        lineweight=18,
        leader_type=LeaderType.straight_lines,
    )
    builder.set_arrow_properties(name="", size=6.0)
    builder.set_connection_properties(landing_gap=0.0, dogleg_length=18.0)
    builder.set_connection_types(
        left=HorizontalConnection.bottom_of_top_line_underline_all,
        right=HorizontalConnection.bottom_of_top_line_underline_all,
    )
    builder.set_content(
        note,
        color=4,
        char_height=6.0,
        alignment=TextAlignment.left,
        style="VIP",
    )
    builder.add_leader_line(ConnectionSide.left, [target_point, Vec2((110, 64))])
    builder.build(note_point)


def add_block_callout(doc, msp) -> None:
    block_name = "_TEST_BLOCK_NOTE"
    if block_name not in doc.blocks:
        blk = doc.blocks.new(block_name)
        blk.add_mtext(
            "C - BLOK\\PPomera se sve zajedno",
            dxfattribs={
                "layer": "Text Zivko",
                "color": 4,
                "char_height": 6.0,
                "style": "VIP",
                "attachment_point": 7,
                "insert": (0, 0),
            },
        )
        blk.add_lwpolyline(
            [(0, -1), (90, -1), (110, -15), (130, -15)],
            dxfattribs={"layer": "s_LIDER", "color": 1, "lineweight": 18},
        )
    msp.add_blockref(block_name, (40, 35), dxfattribs={"layer": "0"})


def add_labels(msp) -> None:
    labels = [
        ("Strelica cilj", (62, -12)),
        ("Strelica cilj", (137, -12)),
        ("Strelica cilj", (257, -12)),
    ]
    for text, point in labels:
        msp.add_text(text, dxfattribs={"height": 3.5, "layer": "0", "color": 2}).set_placement(point)


def main() -> None:
    doc = ezdxf.new("R2018")
    ensure_layers(doc)
    msp = doc.modelspace()
    add_route(msp)
    add_classic_leader(msp)
    add_mleader(msp)
    add_block_callout(doc, msp)
    add_labels(msp)
    doc.saveas(OUT_DXF)
    print(OUT_DXF)


if __name__ == "__main__":
    main()
