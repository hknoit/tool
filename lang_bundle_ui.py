#!/usr/bin/env python3
"""
Desktop UI for updating language bundle JSON files from an Excel file.
Uses only Python standard library (tkinter, zipfile, xml, json, pathlib).
"""

import json
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, scrolledtext

MODULE_PREFIX = "localization."
NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


# ── xlsx reader (stdlib only) ──────────────────────────────────────────────────

def col_letter_to_index(col: str) -> int:
    index = 0
    for ch in col:
        index = index * 26 + (ord(ch.upper()) - ord("A") + 1)
    return index - 1


def read_xlsx(path: Path):
    with zipfile.ZipFile(path) as zf:
        shared_strings = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.parse(zf.open("xl/sharedStrings.xml")).getroot()
            for si in root.findall(f"{{{NS}}}si"):
                text = "".join(t.text or "" for t in si.iter(f"{{{NS}}}t"))
                shared_strings.append(text)

        root = ET.parse(zf.open("xl/worksheets/sheet1.xml")).getroot()
        rows_dict: dict[int, dict[int, str]] = {}

        for row_el in root.findall(f".//{{{NS}}}row"):
            row_num = int(row_el.attrib["r"])
            cols: dict[int, str] = {}
            for c in row_el.findall(f"{{{NS}}}c"):
                ref = c.attrib["r"]
                col_letters = "".join(ch for ch in ref if ch.isalpha())
                col_idx = col_letter_to_index(col_letters)
                cell_type = c.attrib.get("t", "")
                v_el = c.find(f"{{{NS}}}v")
                if v_el is None or v_el.text is None:
                    cols[col_idx] = ""
                    continue
                cols[col_idx] = shared_strings[int(v_el.text)] if cell_type == "s" else v_el.text
            rows_dict[row_num] = cols

        if not rows_dict:
            return []

        max_row = max(rows_dict)
        max_col = max((max(cols.keys()) for cols in rows_dict.values() if cols), default=0)
        return [
            [rows_dict.get(r, {}).get(c, "") for c in range(max_col + 1)]
            for r in range(1, max_row + 1)
        ]


# ── core logic ─────────────────────────────────────────────────────────────────

def load_json(path: Path) -> dict:
    if path.exists() and path.stat().st_size > 0:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def process(excel_path: Path, modules_dir: Path, log):
    rows = read_xlsx(excel_path)
    if not rows:
        log("Excel file is empty.")
        return

    header = rows[0]
    if len(header) < 3:
        log("ERROR: Expected at least 3 columns: module, key, <language...>")
        return

    lang_codes = [str(col).strip() for col in header[2:] if col]
    bundles: dict[tuple, dict] = {}

    for row_num, row in enumerate(rows[1:], start=2):
        if not row or not row[0]:
            continue

        raw_module = str(row[0]).strip()
        key = str(row[1]).strip() if len(row) > 1 else ""

        if not raw_module or not key:
            log(f"Row {row_num}: skipping — missing module or key")
            continue

        folder_name = raw_module[len(MODULE_PREFIX):] if raw_module.startswith(MODULE_PREFIX) else raw_module
        module_dir = modules_dir / folder_name

        for i, lang in enumerate(lang_codes):
            col_index = 2 + i
            value = row[col_index] if col_index < len(row) else ""
            if not value:
                continue

            cache_key = (module_dir, lang)
            if cache_key not in bundles:
                bundles[cache_key] = load_json(module_dir / f"{lang}.json")

            bundles[cache_key][key] = value

    for (module_dir, lang), data in bundles.items():
        json_path = module_dir / f"{lang}.json"
        save_json(json_path, data)
        log(f"Wrote {json_path}")

    log("Done.")


# ── UI ─────────────────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Language Bundle Updater")
        self.resizable(False, False)
        self._build_ui()

    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        # Excel file row
        tk.Label(self, text="Excel file:").grid(row=0, column=0, sticky="e", **pad)
        self.excel_var = tk.StringVar()
        tk.Entry(self, textvariable=self.excel_var, width=50).grid(row=0, column=1, **pad)
        tk.Button(self, text="Browse…", command=self._browse_excel).grid(row=0, column=2, **pad)

        # Modules folder row
        tk.Label(self, text="Modules folder:").grid(row=1, column=0, sticky="e", **pad)
        self.modules_var = tk.StringVar()
        tk.Entry(self, textvariable=self.modules_var, width=50).grid(row=1, column=1, **pad)
        tk.Button(self, text="Browse…", command=self._browse_modules).grid(row=1, column=2, **pad)

        # Run button
        tk.Button(self, text="Run", width=12, command=self._run).grid(row=2, column=1, pady=4)

        # Log output
        self.log_box = scrolledtext.ScrolledText(self, width=70, height=14, state="disabled")
        self.log_box.grid(row=3, column=0, columnspan=3, padx=10, pady=(0, 10))

    def _browse_excel(self):
        path = filedialog.askopenfilename(filetypes=[("Excel files", "*.xlsx")])
        if path:
            self.excel_var.set(path)

    def _browse_modules(self):
        path = filedialog.askdirectory()
        if path:
            self.modules_var.set(path)

    def _log(self, message: str):
        self.log_box.config(state="normal")
        self.log_box.insert(tk.END, message + "\n")
        self.log_box.see(tk.END)
        self.log_box.config(state="disabled")

    def _run(self):
        excel_path = self.excel_var.get().strip()
        modules_dir = self.modules_var.get().strip()

        if not excel_path:
            messagebox.showerror("Missing input", "Please select an Excel file.")
            return
        if not modules_dir:
            messagebox.showerror("Missing input", "Please select the modules folder.")
            return

        self.log_box.config(state="normal")
        self.log_box.delete("1.0", tk.END)
        self.log_box.config(state="disabled")

        try:
            process(Path(excel_path), Path(modules_dir), self._log)
        except Exception as e:
            self._log(f"ERROR: {e}")


if __name__ == "__main__":
    App().mainloop()
