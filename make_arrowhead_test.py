from __future__ import annotations

from pathlib import Path

import ezdxf
from ezdxf.math import Vec2
from ezdxf.render.mleader import ConnectionSide, HorizontalConnection, LeaderType, TextAlignment


ROOT = Path(__file__).resolve().parent
OUT_DXF = ROOT / "arrowhead_test.dxf"
OUT_DWG = ROOT / "arrowhead_test.dwg"


def ensure_layers(doc):
    if "Text Zivko" not in doc.layers:
        doc.layers.add("Text Zivko", dxfattribs={"color": 7, "linetype": "Continuous", "lineweight": -3})
    if "s_LIDER" not in doc.layers:
        doc.layers.add("s_LIDER", dxfattribs={"color": 7, "linetype": "Continuous", "lineweight": 18})
    if "ROUTE_TEST" not in doc.layers:
        doc.layers.add("ROUTE_TEST", dxfattribs={"color": 1, "linetype": "Continuous", "lineweight": 35})
    if "VIP" not in {style.dxf.name for style in doc.styles}:
        doc.styles.add("VIP", font="arial.ttf", dxfattribs={"width": 1.0})


def add_route(msp):
    msp.add_lwpolyline(
        [(0, 0), (360, 0)],
        dxfattribs={"layer": "ROUTE_TEST", "color": 1, "lineweight": 35},
    )


def add_test_mleader(msp, note_point, target_point, arrow_name: str, title: str):
    builder = msp.add_multileader_mtext("Standard", dxfattribs={"layer": "s_LIDER"})
    builder.set_overall_scaling(1.0)
    builder.set_leader_properties(
        color=1,
        linetype="BYLAYER",
        lineweight=18,
        leader_type=LeaderType.straight_lines,
    )
    builder.set_arrow_properties(name=arrow_name, size=6.0)
    builder.set_connection_properties(landing_gap=0.0, dogleg_length=18.0)
    builder.set_connection_types(
        left=HorizontalConnection.bottom_of_bottom_line_underline,
        right=HorizontalConnection.bottom_of_bottom_line_underline,
    )
    builder.set_content(
        f"{title}\\P{arrow_name}",
        color=4,
        char_height=6.0,
        alignment=TextAlignment.left,
        style="VIP",
    )
    side = ConnectionSide.left if target_point[0] <= note_point[0] else ConnectionSide.right
    builder.add_leader_line(side, [Vec2(target_point)])
    builder.build(Vec2(note_point))


def main():
    doc = ezdxf.new("R2018")
    ensure_layers(doc)
    msp = doc.modelspace()
    add_route(msp)
    add_test_mleader(msp, (30, 80), (80, 0), "CLOSED", "A - CLOSED")
    add_test_mleader(msp, (130, 80), (180, 0), "EZ_ARROW_FILLED", "B - EZ_ARROW_FILLED")
    add_test_mleader(msp, (250, 80), (300, 0), "DATUMFILLED", "C - DATUMFILLED")
    doc.saveas(OUT_DXF)
    print(OUT_DXF)
    try:
        from electro_alg.dxf_ops import convert_dxf_to_dwg

        print(convert_dxf_to_dwg(OUT_DXF, OUT_DWG))
    except Exception as exc:
        print(f"DWG export failed: {exc}")


if __name__ == "__main__":
    main()
