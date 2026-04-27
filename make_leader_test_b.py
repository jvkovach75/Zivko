from __future__ import annotations

from pathlib import Path

import ezdxf
from ezdxf.math import Vec2
from ezdxf.render.mleader import ConnectionSide, HorizontalConnection, LeaderType, TextAlignment


ROOT = Path(__file__).resolve().parent
OUT_DXF = ROOT / "leader_test_b_one_bend.dxf"
OUT_DWG = ROOT / "leader_test_b_one_bend.dwg"


def ensure_layers(doc):
    if "Text Zivko" not in doc.layers:
        doc.layers.add("Text Zivko", dxfattribs={"color": 7, "linetype": "Continuous", "lineweight": -3})
    if "s_LIDER" not in doc.layers:
        doc.layers.add("s_LIDER", dxfattribs={"color": 7, "linetype": "Continuous", "lineweight": 18})
    if "ROUTE_TEST" not in doc.layers:
        doc.layers.add("ROUTE_TEST", dxfattribs={"color": 1, "linetype": "Continuous", "lineweight": 35})
    if "VIP" not in {style.dxf.name for style in doc.styles}:
        doc.styles.add("VIP", font="arial.ttf", dxfattribs={"width": 1.0})


def add_scene(msp):
    msp.add_lwpolyline(
        [(0, 0), (160, 0), (220, 40), (300, 40)],
        dxfattribs={"layer": "ROUTE_TEST", "color": 1, "lineweight": 35},
    )
    msp.add_text("Ciljna tacka", dxfattribs={"height": 3.5, "layer": "0", "color": 2}).set_placement((132, -12))


def add_one_bend_mleader(msp):
    note = "MULTILEADER B\\PJedan prelom\\PTest pomeranja teksta"
    note_point = Vec2((34, 68))
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
        left=HorizontalConnection.bottom_of_bottom_line_underline,
        right=HorizontalConnection.bottom_of_bottom_line_underline,
    )
    builder.set_content(
        note,
        color=4,
        char_height=6.0,
        alignment=TextAlignment.left,
        style="VIP",
    )
    builder.add_leader_line(ConnectionSide.left, [target_point])
    builder.build(note_point)


def main():
    doc = ezdxf.new("R2018")
    ensure_layers(doc)
    msp = doc.modelspace()
    add_scene(msp)
    add_one_bend_mleader(msp)
    doc.saveas(OUT_DXF)
    print(OUT_DXF)
    try:
        from electro_alg.dxf_ops import convert_dxf_to_dwg

        produced = convert_dxf_to_dwg(OUT_DXF, OUT_DWG)
        print(produced)
    except Exception as exc:
        print(f"DWG export failed: {exc}")


if __name__ == "__main__":
    main()
