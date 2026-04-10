#!/usr/bin/env python3
"""
Web UI for updating language bundle JSON files from an Excel file.
Uses only Python standard library — opens automatically in your browser.
"""

import json
import re
import zipfile
import xml.etree.ElementTree as ET
import http.server
import threading
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs

MODULE_PREFIX = "localization."
NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
PORT = 8765
WEB_DIR = Path(__file__).parent / "web"

MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
}


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

def _detect_indent(content: str) -> str:
    """Return the leading whitespace of the first key line."""
    for line in content.splitlines():
        stripped = line.lstrip()
        if stripped.startswith('"'):
            return line[: len(line) - len(stripped)]
    return "  "


def update_json_inplace(path: Path, updates: dict) -> None:
    """Update existing keys in-place and append new keys at the end,
    preserving the original file's formatting and empty lines."""
    path.parent.mkdir(parents=True, exist_ok=True)

    if path.exists() and path.stat().st_size > 0:
        content = path.read_text(encoding="utf-8")
        try:
            existing = json.loads(content)
        except json.JSONDecodeError:
            existing = {}
    else:
        content = "{}\n"
        existing = {}

    indent = _detect_indent(content)
    to_update = {k: v for k, v in updates.items() if k in existing}
    to_append = {k: v for k, v in updates.items() if k not in existing}

    # Update existing values in-place
    for key, value in to_update.items():
        pattern = r'("' + re.escape(key) + r'"\s*:\s*)"(?:[^"\\]|\\.)*"'
        replacement = r'\g<1>' + json.dumps(value, ensure_ascii=False)
        content = re.sub(pattern, replacement, content)

    # Append new key-value pairs before the closing }
    if to_append:
        last_brace = content.rfind('}')
        before = content[:last_brace].rstrip()
        insert = ""
        if existing and not before.endswith(','):
            insert += ","
        for key, value in to_append.items():
            insert += f'\n{indent}{json.dumps(key, ensure_ascii=False)}: {json.dumps(value, ensure_ascii=False)}'
        content = before + insert + "\n" + content[last_brace:]

    path.write_text(content, encoding="utf-8")


def process(excel_path: Path, modules_dir: Path) -> str:
    rows = read_xlsx(excel_path)
    if not rows:
        return "Excel file is empty."

    header = rows[0]
    if len(header) < 3:
        return "ERROR: Expected at least 3 columns: module, key, <language...>"

    lang_codes = [str(col).strip() for col in header[2:] if col]
    # Collect all updates per file first, then write once per file
    file_updates: dict[Path, dict] = {}
    messages = []

    for row_num, row in enumerate(rows[1:], start=2):
        if not row or not row[0]:
            continue

        raw_module = str(row[0]).strip()
        key = str(row[1]).strip() if len(row) > 1 else ""

        if not raw_module or not key:
            messages.append(f"Row {row_num}: skipping — missing module or key")
            continue

        folder_name = raw_module[len(MODULE_PREFIX):] if raw_module.startswith(MODULE_PREFIX) else raw_module
        module_dir = modules_dir / folder_name

        for i, lang in enumerate(lang_codes):
            col_index = 2 + i
            value = row[col_index] if col_index < len(row) else ""
            if not value:
                continue

            json_path = module_dir / f"{lang}.json"
            file_updates.setdefault(json_path, {})[key] = value

    for json_path, updates in file_updates.items():
        update_json_inplace(json_path, updates)
        messages.append(f"Wrote {json_path}")

    messages.append("Done.")
    return "\n".join(messages)


# ── HTTP server ────────────────────────────────────────────────────────────────

class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # suppress request logs

    def do_GET(self):
        # Serve files from the web/ folder
        url_path = self.path.split("?")[0]
        if url_path == "/":
            url_path = "/index.html"
        file_path = WEB_DIR / url_path.lstrip("/")
        if not file_path.exists() or not file_path.is_file():
            self.send_response(404)
            self.end_headers()
            return
        mime = MIME_TYPES.get(file_path.suffix, "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.end_headers()
        self.wfile.write(file_path.read_bytes())

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode()
        params = parse_qs(body)
        excel = params.get("excel", [""])[0].strip()
        modules = params.get("modules", [""])[0].strip()

        if not excel or not modules:
            self._respond(400, "ERROR: Both fields are required.")
            return

        excel_path = Path(excel)
        modules_dir = Path(modules)

        if not excel_path.exists():
            self._respond(400, f"ERROR: Excel file not found: {excel_path}")
            return
        if not modules_dir.is_dir():
            self._respond(400, f"ERROR: Modules folder not found: {modules_dir}")
            return

        try:
            result = process(excel_path, modules_dir)
            self._respond(200, result)
        except Exception as e:
            self._respond(500, f"ERROR: {e}")

    def _respond(self, code: int, text: str):
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(text.encode())


if __name__ == "__main__":
    server = http.server.HTTPServer(("localhost", PORT), Handler)
    url = f"http://localhost:{PORT}"
    print(f"Opening {url}  (Ctrl+C to quit)")
    threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    server.serve_forever()
