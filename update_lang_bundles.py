#!/usr/bin/env python3
"""
Reads lang_bund.xlsx and updates translation JSON files under the modules folder.

Excel format:
  Column 1: module (e.g. "localization.reporting" → modules/reporting)
  Column 2: key
  Column 3+: one column per language code (e.g. "en", "fr"), value is the translation
"""

import json
import sys
from pathlib import Path
import openpyxl

EXCEL_FILE = Path(__file__).parent / "lang_bund.xlsx"
MODULES_DIR = Path(__file__).parent / "modules"
MODULE_PREFIX = "localization."


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
    wb = openpyxl.load_workbook(EXCEL_FILE, data_only=True)
    ws = wb.active

    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        print("Excel file is empty.")
        return

    header = rows[0]
    if len(header) < 3:
        sys.exit("Expected at least 3 columns: module, key, <language...>")

    # Column indices (0-based in the list)
    # header[0] = "module" label, header[1] = "key" label, header[2:] = language codes
    lang_codes = [str(col).strip() for col in header[2:] if col is not None]

    # Cache: (module_dir, lang) -> dict
    bundles: dict[tuple[Path, str], dict] = {}

    for row_num, row in enumerate(rows[1:], start=2):
        if not row or row[0] is None:
            continue  # skip empty rows

        raw_module = str(row[0]).strip()
        key = str(row[1]).strip() if row[1] is not None else ""

        if not raw_module or not key:
            print(f"Row {row_num}: skipping — missing module or key")
            continue

        # Strip prefix to get folder name
        if raw_module.startswith(MODULE_PREFIX):
            folder_name = raw_module[len(MODULE_PREFIX):]
        else:
            folder_name = raw_module

        module_dir = MODULES_DIR / folder_name

        for i, lang in enumerate(lang_codes):
            col_index = 2 + i
            value = row[col_index] if col_index < len(row) else None
            if value is None:
                continue  # no translation provided for this language

            cache_key = (module_dir, lang)
            if cache_key not in bundles:
                json_path = module_dir / f"{lang}.json"
                bundles[cache_key] = load_json(json_path)

            bundles[cache_key][key] = value

    # Write all modified bundles
    for (module_dir, lang), data in bundles.items():
        json_path = module_dir / f"{lang}.json"
        save_json(json_path, data)

    print("Done.")


if __name__ == "__main__":
    main()
