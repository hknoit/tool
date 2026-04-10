#!/usr/bin/env python3
"""
Reads lang_bund.xlsx and updates translation JSON files under the modules folder.
Uses only Python standard library (zipfile + xml.etree.ElementTree) — no pip installs needed.

Excel format:
  Column 1: module (e.g. "localization.reporting" → modules/reporting)
  Column 2: key
  Column 3+: one column per language code (e.g. "en", "fr"), value is the translation
"""

import json
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

EXCEL_FILE = Path(__file__).parent / "lang_bund.xlsx"
MODULES_DIR = Path(__file__).parent / "modules"
MODULE_PREFIX = "localization."
NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def col_letter_to_index(col: str) -> int:
    index = 0
    for ch in col:
        index = index * 26 + (ord(ch.upper()) - ord("A") + 1)
    return index - 1


def read_xlsx(path: Path):
    """Return worksheet rows as list of lists using only stdlib."""
    with zipfile.ZipFile(path) as zf:
        # Load shared strings table (string values are stored here)
        shared_strings = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.parse(zf.open("xl/sharedStrings.xml")).getroot()
            for si in root.findall(f"{{{NS}}}si"):
                # Handles both plain <t> and rich text <r><t>
                text = "".join(t.text or "" for t in si.iter(f"{{{NS}}}t"))
                shared_strings.append(text)

        # Parse first worksheet
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
                if cell_type == "s":
                    cols[col_idx] = shared_strings[int(v_el.text)]
                else:
                    cols[col_idx] = v_el.text
            rows_dict[row_num] = cols

        if not rows_dict:
            return []

        max_row = max(rows_dict)
        max_col = max((max(cols.keys()) for cols in rows_dict.values() if cols), default=0)
        return [
            [rows_dict.get(r, {}).get(c, "") for c in range(max_col + 1)]
            for r in range(1, max_row + 1)
        ]


def load_json(path: Path) -> dict:
    if path.exists() and path.stat().st_size > 0:
        with path.open(encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  Wrote {path}")


def main():
    rows = read_xlsx(EXCEL_FILE)
    if not rows:
        print("Excel file is empty.")
        return

    header = rows[0]
    if len(header) < 3:
        sys.exit("Expected at least 3 columns: module, key, <language...>")

    lang_codes = [str(col).strip() for col in header[2:] if col]

    # Cache: (module_dir, lang) -> dict
    bundles: dict[tuple, dict] = {}

    for row_num, row in enumerate(rows[1:], start=2):
        if not row or not row[0]:
            continue

        raw_module = str(row[0]).strip()
        key = str(row[1]).strip() if len(row) > 1 else ""

        if not raw_module or not key:
            print(f"Row {row_num}: skipping — missing module or key")
            continue

        folder_name = raw_module[len(MODULE_PREFIX):] if raw_module.startswith(MODULE_PREFIX) else raw_module
        module_dir = MODULES_DIR / folder_name

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
        save_json(module_dir / f"{lang}.json", data)

    print("Done.")


if __name__ == "__main__":
    main()
