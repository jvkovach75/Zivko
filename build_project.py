from __future__ import annotations

import argparse
import json
from pathlib import Path

from electro_alg import (
    apply_design_to_dxf,
    apply_design_to_dwg,
    build_design_model,
    build_report_from_quantities,
    ensure_design_drawable,
    extract_quantities,
    render_report_markdown,
)
from electro_alg.run_state import write_shared_run_state


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Electrical project base pipeline: task + conditions + base drawing -> drawing -> quantities -> report.")
    sub = parser.add_subparsers(dest="command", required=True)

    design_cmd = sub.add_parser("design", help="Build an internal design model from project task, conditions and base DXF.")
    design_cmd.add_argument("--dxf", required=True, help="Input base DWG or DXF.")
    design_cmd.add_argument("--project-task", required=True, help="Project task text file.")
    design_cmd.add_argument("--condition-text", action="append", default=[], help="Condition text file. Repeatable.")
    design_cmd.add_argument("--anchors-json", help="Optional manual anchors JSON.")
    design_cmd.add_argument("--output-json", required=True, help="Output JSON path.")

    draw_cmd = sub.add_parser("draw", help="Generate a working project DXF from the design model inputs.")
    draw_cmd.add_argument("--dxf", required=True, help="Input base DWG or DXF.")
    draw_cmd.add_argument("--project-task", required=True, help="Project task text file.")
    draw_cmd.add_argument("--condition-text", action="append", default=[], help="Condition text file. Repeatable.")
    draw_cmd.add_argument("--anchors-json", help="Optional manual anchors JSON.")
    draw_cmd.add_argument("--output-dxf", required=True, help="Output DXF path.")
    draw_cmd.add_argument("--output-dwg", help="Optional final DWG path.")
    draw_cmd.add_argument("--output-json", required=True, help="Output design JSON path.")

    qty_cmd = sub.add_parser("quantify", help="Generate quantities from the generated design model.")
    qty_cmd.add_argument("--dxf", required=True, help="Input base DWG or DXF.")
    qty_cmd.add_argument("--project-task", required=True, help="Project task text file.")
    qty_cmd.add_argument("--condition-text", action="append", default=[], help="Condition text file. Repeatable.")
    qty_cmd.add_argument("--anchors-json", help="Optional manual anchors JSON.")
    qty_cmd.add_argument("--output-json", required=True, help="Output quantities JSON path.")

    report_cmd = sub.add_parser("report", help="Generate a draft report payload from generated quantities.")
    report_cmd.add_argument("--dxf", required=True, help="Input base DWG or DXF.")
    report_cmd.add_argument("--project-task", required=True, help="Project task text file.")
    report_cmd.add_argument("--condition-text", action="append", default=[], help="Condition text file. Repeatable.")
    report_cmd.add_argument("--anchors-json", help="Optional manual anchors JSON.")
    report_cmd.add_argument("--output-json", required=True, help="Output report JSON path.")
    report_cmd.add_argument("--output-md", help="Optional Markdown draft path for main book tables.")

    return parser


def _collect_run_inputs(args: argparse.Namespace) -> dict[str, object]:
    return {
        "dxf": args.dxf,
        "project_task": args.project_task,
        "condition_paths": list(args.condition_text or []),
        "anchors_json": getattr(args, "anchors_json", None) or "",
    }


def _collect_run_outputs(args: argparse.Namespace) -> dict[str, object]:
    outputs: dict[str, object] = {}
    if getattr(args, "output_json", None):
        if args.command == "design":
            outputs["design_json"] = args.output_json
        elif args.command == "draw":
            outputs["draw_json"] = args.output_json
        elif args.command == "quantify":
            outputs["quantities_json"] = args.output_json
        elif args.command == "report":
            outputs["report_json"] = args.output_json
    if getattr(args, "output_dxf", None):
        outputs["draw_dxf"] = args.output_dxf
    if getattr(args, "output_dwg", None):
        outputs["draw_dwg"] = args.output_dwg
    if getattr(args, "output_md", None):
        outputs["report_md"] = args.output_md
    return outputs


def main() -> int:
    parser = make_parser()
    args = parser.parse_args()
    run_inputs = _collect_run_inputs(args)
    run_outputs = _collect_run_outputs(args)

    write_shared_run_state(
        source="build_project.py",
        command=args.command,
        status="running",
        inputs=run_inputs,
        outputs=run_outputs,
    )

    try:
        design = build_design_model(
            source_dxf=args.dxf,
            project_task_text_path=args.project_task,
            condition_paths=args.condition_text,
            anchors_path=getattr(args, "anchors_json", None),
        )

        if args.command == "design":
            Path(args.output_json).write_text(json.dumps(design.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            write_shared_run_state(
                source="build_project.py",
                command=args.command,
                status="completed",
                inputs=run_inputs,
                outputs=run_outputs,
                metadata={
                    "algorithm_profile": getattr(design, "algorithm_profile", "full_latest"),
                    "algorithm_checks": getattr(design, "algorithm_checks", []),
                    "warnings": getattr(design, "warnings", []),
                },
            )
            print(f"Design model written to {args.output_json}")
            return 0

        if args.command == "draw":
            ensure_design_drawable(design)
            result = apply_design_to_dwg(
                source_dxf=args.dxf,
                design=design,
                output_dxf=args.output_dxf,
                output_dwg=getattr(args, "output_dwg", None),
                output_json=args.output_json,
            )
            final_outputs = dict(run_outputs)
            if result.get("output_dwg"):
                final_outputs["draw_dwg"] = result["output_dwg"]
            write_shared_run_state(
                source="build_project.py",
                command=args.command,
                status="completed",
                inputs=run_inputs,
                outputs=final_outputs,
                metadata={
                    "algorithm_profile": getattr(design, "algorithm_profile", "full_latest"),
                    "algorithm_checks": getattr(design, "algorithm_checks", []),
                    "warnings": getattr(design, "warnings", []),
                    "dwg_warning": result.get("dwg_warning", ""),
                },
            )
            print(f"Working DXF written to {args.output_dxf}")
            if result.get("output_dwg"):
                print(f"Final DWG written to {result['output_dwg']}")
            elif getattr(args, "output_dwg", None):
                print(f"Final DWG was not produced automatically. Use DWG FastView to Save As from: {args.output_dxf}")
                print(result.get("dwg_warning", "Unknown DWG conversion failure."))
            print(f"Design JSON written to {args.output_json}")
            return 0

        quantities = extract_quantities(design)

        if args.command == "quantify":
            ensure_design_drawable(design)
            Path(args.output_json).write_text(json.dumps(quantities.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            write_shared_run_state(
                source="build_project.py",
                command=args.command,
                status="completed",
                inputs=run_inputs,
                outputs=run_outputs,
                metadata={
                    "algorithm_profile": getattr(design, "algorithm_profile", "full_latest"),
                    "algorithm_checks": getattr(design, "algorithm_checks", []),
                    "warnings": getattr(design, "warnings", []),
                },
            )
            print(f"Quantities written to {args.output_json}")
            return 0

        ensure_design_drawable(design)
        report = build_report_from_quantities(design, quantities)
        Path(args.output_json).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        if getattr(args, "output_md", None):
            Path(args.output_md).write_text(render_report_markdown(report), encoding="utf-8")
        write_shared_run_state(
            source="build_project.py",
            command=args.command,
            status="completed",
            inputs=run_inputs,
            outputs=run_outputs,
            metadata={
                "algorithm_profile": getattr(design, "algorithm_profile", "full_latest"),
                "algorithm_checks": getattr(design, "algorithm_checks", []),
                "warnings": getattr(design, "warnings", []),
            },
        )
        print(f"Report payload written to {args.output_json}")
        if getattr(args, "output_md", None):
            print(f"Report markdown written to {args.output_md}")
        return 0
    except Exception as exc:
        write_shared_run_state(
            source="build_project.py",
            command=args.command,
            status="failed",
            inputs=run_inputs,
            outputs=run_outputs,
            error=str(exc),
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
