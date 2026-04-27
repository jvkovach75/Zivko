from __future__ import annotations

import json
import threading
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from electro_alg import (
    apply_design_to_dwg,
    build_design_model,
    build_report_from_quantities,
    ensure_design_drawable,
    extract_quantities,
    render_report_markdown,
)
from electro_alg.run_state import read_shared_run_state, shared_run_state_path, write_shared_run_state


ROOT_DIR = Path(__file__).resolve().parent
RUN_STATE_POLL_MS = 1500


class ElectroGui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Electro Project GUI")
        self.geometry("980x880")
        self.minsize(860, 720)

        self._busy = False
        self._run_state_mtime_ns: int | None = None

        self.dxf_var = tk.StringVar(value=r"D:\OneDrive\Radna povr?ina\Zivko\KTP-K.O.Licice,K.O.Krstac-Lu?ani za potrebe projektovanja 10kv-dopuna 7.8.2025 SN.dwg")
        self.project_task_var = tk.StringVar(value=r"D:\OneDrive\Radna povr?ina\Zivko\Projektni zadatka_VALIDNO.pdf")
        self.anchors_var = tk.StringVar(value="")
        self.condition_vars = [
            tk.StringVar(value=r"D:\OneDrive\Radna površina\Zivko\Uslovi JP\1 Informacija o lokaciji.pdf"),
            tk.StringVar(value=r"D:\OneDrive\Radna površina\Zivko\Uslovi JP\2 Lokacijski uslovi.pdf"),
            tk.StringVar(value=r"D:\OneDrive\Radna površina\Zivko\Uslovi JP\3 GAS YUGOROSGAZ.pdf"),
            tk.StringVar(value=r"D:\OneDrive\Radna površina\Zivko\Uslovi JP\4 JKP Komunalac Lučani.pdf"),
            tk.StringVar(value=r"D:\OneDrive\Radna površina\Zivko\Uslovi JP\5 Telekom.pdf"),
            tk.StringVar(value=r"D:\OneDrive\Radna površina\Zivko\Uslovi JP\6 Uslovi Putevi.pdf"),
            tk.StringVar(value=r"D:\OneDrive\Radna površina\Zivko\Uslovi JP\7 Vodni uslovi.pdf"),
        ]
        self.design_json_var = tk.StringVar(value=str(ROOT_DIR / "design_model.json"))
        self.draw_dxf_var = tk.StringVar(value=str(ROOT_DIR / "generated_project.dxf"))
        self.draw_dwg_var = tk.StringVar(value=str(ROOT_DIR / "generated_project.dwg"))
        self.draw_json_var = tk.StringVar(value=str(ROOT_DIR / "generated_project.json"))
        self.quantities_var = tk.StringVar(value=str(ROOT_DIR / "quantities_rich.json"))
        self.report_json_var = tk.StringVar(value=str(ROOT_DIR / "report_payload.json"))
        self.report_md_var = tk.StringVar(value=str(ROOT_DIR / "glavna_sveska_draft.md"))
        self.status_var = tk.StringVar(value="Spreman.")

        self._build_ui()
        self._load_shared_run_state(initial=True)
        self.after(RUN_STATE_POLL_MS, self._poll_shared_run_state)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)

        inputs = ttk.LabelFrame(outer, text="Ulazi", padding=12)
        inputs.grid(row=0, column=0, sticky="nsew")
        inputs.columnconfigure(1, weight=1)

        drawing_types = [
            ("CAD files", "*.dwg *.dxf"),
            ("DWG files", "*.dwg"),
            ("DXF files", "*.dxf"),
            ("All files", "*.*"),
        ]
        doc_types = [
            ("Documents", "*.txt *.pdf"),
            ("Text files", "*.txt"),
            ("PDF files", "*.pdf"),
            ("All files", "*.*"),
        ]

        self._path_row(inputs, 0, "DWG/DXF podloga", self.dxf_var, filetypes=drawing_types)
        self._path_row(inputs, 1, "Projektni zadatak", self.project_task_var, filetypes=doc_types)
        self._path_row(inputs, 2, "Anchors JSON", self.anchors_var, filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        for index, condition_var in enumerate(self.condition_vars, start=1):
            self._path_row(inputs, 2 + index, f"Uslov {index}", condition_var, filetypes=doc_types)

        outputs = ttk.LabelFrame(outer, text="Izlazi", padding=12)
        outputs.grid(row=1, column=0, sticky="nsew", pady=(12, 0))
        outputs.columnconfigure(1, weight=1)

        self._path_row(outputs, 0, "Design JSON", self.design_json_var, save=True, filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        self._path_row(outputs, 1, "Radni DXF", self.draw_dxf_var, save=True, filetypes=[("DXF files", "*.dxf"), ("All files", "*.*")])
        self._path_row(outputs, 2, "Finalni DWG", self.draw_dwg_var, save=True, filetypes=[("DWG files", "*.dwg"), ("All files", "*.*")])
        self._path_row(outputs, 3, "Draw JSON", self.draw_json_var, save=True, filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        self._path_row(outputs, 4, "Kolicine JSON", self.quantities_var, save=True, filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        self._path_row(outputs, 5, "Report JSON", self.report_json_var, save=True, filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        self._path_row(outputs, 6, "Draft MD", self.report_md_var, save=True, filetypes=[("Markdown files", "*.md"), ("All files", "*.*")])

        middle = ttk.Panedwindow(outer, orient="vertical")
        middle.grid(row=2, column=0, sticky="nsew", pady=(12, 0))

        actions = ttk.LabelFrame(middle, text="Akcije", padding=12)
        preview = ttk.LabelFrame(middle, text="Pregled", padding=12)
        middle.add(actions, weight=0)
        middle.add(preview, weight=1)

        for idx in range(5):
            actions.columnconfigure(idx, weight=1)

        ttk.Button(actions, text="Design", command=lambda: self._run_async(self._run_design)).grid(row=0, column=0, sticky="ew", padx=(0, 8))
        ttk.Button(actions, text="Draw", command=lambda: self._run_async(self._run_draw)).grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(actions, text="Quantify", command=lambda: self._run_async(self._run_quantify)).grid(row=0, column=2, sticky="ew", padx=(0, 8))
        ttk.Button(actions, text="Report", command=lambda: self._run_async(self._run_report)).grid(row=0, column=3, sticky="ew", padx=(0, 8))
        ttk.Button(actions, text="Sve", command=lambda: self._run_async(self._run_all)).grid(row=0, column=4, sticky="ew")

        ttk.Button(actions, text="Ucitaj draft MD", command=self._load_markdown_preview).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0), padx=(0, 8))
        ttk.Button(actions, text="Ucitaj report JSON", command=self._load_report_preview).grid(row=1, column=2, columnspan=2, sticky="ew", pady=(10, 0), padx=(0, 8))
        ttk.Button(actions, text="Otvori folder", command=self._open_output_folder).grid(row=1, column=4, sticky="ew", pady=(10, 0))

        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(0, weight=1)
        self.preview_text = tk.Text(preview, wrap="word", font=("Consolas", 10))
        self.preview_text.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(preview, orient="vertical", command=self.preview_text.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.preview_text.configure(yscrollcommand=scroll.set)

        status = ttk.Label(outer, textvariable=self.status_var, anchor="w")
        status.grid(row=3, column=0, sticky="ew", pady=(10, 0))

        self._set_preview(
            "GUI je spreman.\n\n"
            "Ovo je sada DWG-first tok:\n"
            "- podloga moze biti DWG ili DXF\n"
            "- radni DXF je interni izlaz\n"
            "- finalni DWG je ciljani izlaz\n\n"
            "Ako automatski DWG export ne uspe, dobices spreman radni DXF i jasnu poruku da ga otvoris u DWG FastView i uradis Save As.\n\n"
            "Tipican redosled:\n"
            "1. Design\n"
            "2. Draw\n"
            "3. Quantify\n"
            "4. Report\n"
        )

    def _path_row(self, parent, row: int, label: str, variable: tk.StringVar, *, save: bool = False, filetypes=None) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=4)
        entry = ttk.Entry(parent, textvariable=variable)
        entry.grid(row=row, column=1, sticky="ew", padx=(8, 8), pady=4)
        ttk.Button(
            parent,
            text="...",
            width=4,
            command=lambda: self._browse(variable, save=save, filetypes=filetypes),
        ).grid(row=row, column=2, sticky="e", pady=4)

    def _browse(self, variable: tk.StringVar, *, save: bool, filetypes) -> None:
        current = Path(variable.get()).expanduser()
        initial_dir = str(current.parent if current.parent.exists() else ROOT_DIR)
        if save:
            path = filedialog.asksaveasfilename(initialdir=initial_dir, initialfile=current.name, filetypes=filetypes)
        else:
            path = filedialog.askopenfilename(initialdir=initial_dir, filetypes=filetypes)
        if path:
            variable.set(path)

    def _get_condition_paths(self) -> list[str]:
        return [var.get().strip() for var in self.condition_vars if var.get().strip()]

    def _collect_current_inputs(self) -> dict[str, object]:
        return {
            "dxf": self.dxf_var.get().strip(),
            "project_task": self.project_task_var.get().strip(),
            "anchors_json": self.anchors_var.get().strip(),
            "condition_paths": self._get_condition_paths(),
        }

    def _collect_current_outputs(self) -> dict[str, object]:
        return {
            "design_json": self.design_json_var.get().strip(),
            "draw_dxf": self.draw_dxf_var.get().strip(),
            "draw_dwg": self.draw_dwg_var.get().strip(),
            "draw_json": self.draw_json_var.get().strip(),
            "quantities_json": self.quantities_var.get().strip(),
            "report_json": self.report_json_var.get().strip(),
            "report_md": self.report_md_var.get().strip(),
        }

    def _write_gui_run_state(self, *, command: str, status: str, error: str | None = None, metadata: dict | None = None) -> None:
        write_shared_run_state(
            source="gui_app.py",
            command=command,
            status=status,
            inputs=self._collect_current_inputs(),
            outputs=self._collect_current_outputs(),
            error=error,
            metadata=metadata,
        )

    def _apply_shared_run_state(self, state: dict, *, initial: bool = False) -> None:
        inputs = state.get("inputs") or {}
        outputs = state.get("outputs") or {}
        condition_paths = inputs.get("condition_paths")

        if isinstance(inputs.get("dxf"), str) and inputs["dxf"]:
            self.dxf_var.set(inputs["dxf"])
        if isinstance(inputs.get("project_task"), str) and inputs["project_task"]:
            self.project_task_var.set(inputs["project_task"])
        if isinstance(inputs.get("anchors_json"), str):
            self.anchors_var.set(inputs["anchors_json"])
        if isinstance(condition_paths, list):
            for index, variable in enumerate(self.condition_vars):
                variable.set(str(condition_paths[index]) if index < len(condition_paths) else "")

        field_pairs = [
            ("design_json", self.design_json_var),
            ("draw_dxf", self.draw_dxf_var),
            ("draw_dwg", self.draw_dwg_var),
            ("draw_json", self.draw_json_var),
            ("quantities_json", self.quantities_var),
            ("report_json", self.report_json_var),
            ("report_md", self.report_md_var),
        ]
        for key, variable in field_pairs:
            value = outputs.get(key)
            if isinstance(value, str) and value:
                variable.set(value)

        if initial:
            self.status_var.set("GUI ucitao poslednje background inpute.")
            return

        command = state.get("command") or "run"
        status = state.get("status") or "unknown"
        source = state.get("source") or "background"
        self.status_var.set(f"GUI osvezen iz {source}: {command} [{status}]")

    def _load_shared_run_state(self, *, initial: bool = False) -> None:
        state = read_shared_run_state()
        if not state:
            return
        try:
            state_path = shared_run_state_path()
            if state_path.exists():
                self._run_state_mtime_ns = state_path.stat().st_mtime_ns
        except Exception:
            pass
        self._apply_shared_run_state(state, initial=initial)

    def _poll_shared_run_state(self) -> None:
        try:
            state_path = shared_run_state_path()
            if state_path.exists():
                current_mtime = state_path.stat().st_mtime_ns
                if self._run_state_mtime_ns is None or current_mtime > self._run_state_mtime_ns:
                    self._run_state_mtime_ns = current_mtime
                    state = read_shared_run_state()
                    if state:
                        self._apply_shared_run_state(state)
        finally:
            self.after(RUN_STATE_POLL_MS, self._poll_shared_run_state)

    def _validate_inputs(self) -> None:
        required = [
            ("DWG/DXF podloga", self.dxf_var.get()),
            ("Projektni zadatak", self.project_task_var.get()),
        ]
        missing = [label for label, value in required if not value.strip()]
        if missing:
            raise ValueError("Nedostaje: " + ", ".join(missing))

    def _build_design(self):
        self._validate_inputs()
        return build_design_model(
            source_dxf=self.dxf_var.get().strip(),
            project_task_text_path=self.project_task_var.get().strip(),
            condition_paths=self._get_condition_paths(),
            anchors_path=self.anchors_var.get().strip() or None,
        )

    def _run_async(self, func) -> None:
        if self._busy:
            messagebox.showinfo("Sacekaj", "Jedna akcija je vec u toku.")
            return

        self._busy = True
        self.status_var.set("Radim...")

        def worker():
            try:
                result = func()
                self.after(0, lambda: self._on_success(result))
            except Exception as exc:
                command = func.__name__.replace("_run_", "", 1)
                try:
                    self._write_gui_run_state(command=command, status="failed", error=str(exc))
                except Exception:
                    pass
                details = "".join(traceback.format_exception(exc))
                self.after(0, lambda: self._on_error(details))

        threading.Thread(target=worker, daemon=True).start()

    def _on_success(self, result: str | None) -> None:
        self._busy = False
        self.status_var.set("Zavrseno.")
        if result:
            self._set_preview(result)

    def _on_error(self, details: str) -> None:
        self._busy = False
        self.status_var.set("Greska.")
        self._set_preview(details)
        messagebox.showerror("Greska", "Akcija nije uspela. Pogledaj detalje u prozoru.")

    def _run_design(self) -> str:
        self._write_gui_run_state(command="design", status="running")
        design = self._build_design()
        output = Path(self.design_json_var.get().strip())
        output.write_text(json.dumps(design.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        self._write_gui_run_state(
            command="design",
            status="completed",
            metadata={
                "algorithm_profile": getattr(design, "algorithm_profile", "full_latest"),
                "algorithm_checks": getattr(design, "algorithm_checks", []),
                "warnings": getattr(design, "warnings", []),
            },
        )
        return f"Design model snimljen u:\n{output}"

    def _run_draw(self) -> str:
        self._write_gui_run_state(command="draw", status="running")
        design = self._build_design()
        ensure_design_drawable(design)
        result = apply_design_to_dwg(
            source_dxf=self.dxf_var.get().strip(),
            design=design,
            output_dxf=self.draw_dxf_var.get().strip(),
            output_dwg=self.draw_dwg_var.get().strip() or None,
            output_json=self.draw_json_var.get().strip(),
        )
        lines = [
            f"Radni DXF snimljen u:\n{self.draw_dxf_var.get().strip()}",
            f"Design JSON:\n{self.draw_json_var.get().strip()}",
        ]
        if result.get("output_dwg"):
            lines.insert(1, f"Finalni DWG snimljen u:\n{result['output_dwg']}")
        elif self.draw_dwg_var.get().strip():
            lines.insert(
                1,
                "Finalni DWG nije napravljen automatski.\n"
                f"Otvori radni DXF u DWG FastView i uradi Save As ->\n{self.draw_dwg_var.get().strip()}",
            )
            if result.get("dwg_warning"):
                lines.append(f"DWG upozorenje:\n{result['dwg_warning']}")
        self._write_gui_run_state(
            command="draw",
            status="completed",
            metadata={
                "algorithm_profile": getattr(design, "algorithm_profile", "full_latest"),
                "algorithm_checks": getattr(design, "algorithm_checks", []),
                "warnings": getattr(design, "warnings", []),
                "dwg_warning": result.get("dwg_warning", ""),
            },
        )
        return "\n\n".join(lines)

    def _run_quantify(self) -> str:
        self._write_gui_run_state(command="quantify", status="running")
        design = self._build_design()
        ensure_design_drawable(design)
        quantities = extract_quantities(design)
        output = Path(self.quantities_var.get().strip())
        output.write_text(json.dumps(quantities.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        self._write_gui_run_state(
            command="quantify",
            status="completed",
            metadata={
                "algorithm_profile": getattr(design, "algorithm_profile", "full_latest"),
                "algorithm_checks": getattr(design, "algorithm_checks", []),
                "warnings": getattr(design, "warnings", []),
            },
        )
        return f"Kolicine snimljene u:\n{output}\n\nUkupno stavki: {len(quantities.items)}"

    def _run_report(self) -> str:
        self._write_gui_run_state(command="report", status="running")
        design = self._build_design()
        ensure_design_drawable(design)
        quantities = extract_quantities(design)
        report = build_report_from_quantities(design, quantities)

        report_json = Path(self.report_json_var.get().strip())
        report_md = Path(self.report_md_var.get().strip())
        report_json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        report_md.write_text(render_report_markdown(report), encoding="utf-8")
        self._write_gui_run_state(
            command="report",
            status="completed",
            metadata={
                "algorithm_profile": getattr(design, "algorithm_profile", "full_latest"),
                "algorithm_checks": getattr(design, "algorithm_checks", []),
                "warnings": getattr(design, "warnings", []),
            },
        )

        return (
            f"Report JSON:\n{report_json}\n\n"
            f"Draft Markdown:\n{report_md}\n\n"
            f"Ukupna trasa: {report['main_book_tables']['totals'].get('TOTAL-ROUTE', 0):.2f} m"
        )

    def _run_all(self) -> str:
        self._write_gui_run_state(command="all", status="running")
        design = self._build_design()
        ensure_design_drawable(design)

        design_json = Path(self.design_json_var.get().strip())
        design_json.write_text(json.dumps(design.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

        draw_result = apply_design_to_dwg(
            source_dxf=self.dxf_var.get().strip(),
            design=design,
            output_dxf=self.draw_dxf_var.get().strip(),
            output_dwg=self.draw_dwg_var.get().strip() or None,
            output_json=self.draw_json_var.get().strip(),
        )

        quantities = extract_quantities(design)
        Path(self.quantities_var.get().strip()).write_text(
            json.dumps(quantities.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        report = build_report_from_quantities(design, quantities)
        Path(self.report_json_var.get().strip()).write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        Path(self.report_md_var.get().strip()).write_text(
            render_report_markdown(report),
            encoding="utf-8",
        )

        lines = [
            "Sve je zavrseno.",
            f"Design JSON: {self.design_json_var.get().strip()}",
            f"Radni DXF: {self.draw_dxf_var.get().strip()}",
        ]
        if draw_result.get("output_dwg"):
            lines.append(f"Finalni DWG: {draw_result['output_dwg']}")
        elif self.draw_dwg_var.get().strip():
            lines.append(
                "Finalni DWG: nije automatski napravljen, koristi DWG FastView Save As -> "
                + self.draw_dwg_var.get().strip()
            )
        lines.extend(
            [
                f"Kolicine: {self.quantities_var.get().strip()}",
                f"Report JSON: {self.report_json_var.get().strip()}",
                f"Draft MD: {self.report_md_var.get().strip()}",
            ]
        )
        self._write_gui_run_state(
            command="all",
            status="completed",
            metadata={
                "algorithm_profile": getattr(design, "algorithm_profile", "full_latest"),
                "algorithm_checks": getattr(design, "algorithm_checks", []),
                "warnings": getattr(design, "warnings", []),
            },
        )
        return "\n".join(lines)

    def _load_markdown_preview(self) -> None:
        path = Path(self.report_md_var.get().strip())
        if not path.exists():
            messagebox.showinfo("Nema fajla", "Draft markdown jos ne postoji.")
            return
        self._set_preview(path.read_text(encoding="utf-8", errors="ignore"))
        self.status_var.set(f"Ucitan preview: {path.name}")

    def _load_report_preview(self) -> None:
        path = Path(self.report_json_var.get().strip())
        if not path.exists():
            messagebox.showinfo("Nema fajla", "Report JSON jos ne postoji.")
            return
        self._set_preview(path.read_text(encoding="utf-8", errors="ignore"))
        self.status_var.set(f"Ucitan preview: {path.name}")

    def _open_output_folder(self) -> None:
        folder = ROOT_DIR
        try:
            import os

            os.startfile(folder)  # type: ignore[attr-defined]
        except Exception as exc:
            messagebox.showerror("Greska", f"Ne mogu da otvorim folder:\n{exc}")

    def _set_preview(self, content: str) -> None:
        self.preview_text.delete("1.0", "end")
        self.preview_text.insert("1.0", content)


def main() -> None:
    app = ElectroGui()
    app.mainloop()


if __name__ == "__main__":
    main()
