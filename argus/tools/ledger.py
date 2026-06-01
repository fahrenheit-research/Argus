"""LEDGER — spreadsheet authoring for ARGUS.

WHY this module exists:
  Half the work the user delegates to ARGUS is numerical: pull this data,
  pivot by week, total by region, write me a sheet. Without dedicated
  spreadsheet tools, the LLM resorts to printing a markdown table that
  the user has to manually paste into Excel. LEDGER closes that gap by
  emitting real .xlsx / .csv bytes with ARGUS brand formatting
  (Electric Magenta header, Gold totals, Ice Cyan title row).

Registered tools (toolset "documents"):
  - ledger_xlsx       list of row-dicts -> XlsxWriter -> .xlsx with formatting
  - ledger_csv        list of row-dicts -> stdlib csv  -> .csv (no deps)
  - ledger_pivot      .xlsx -> pandas pivot -> new .xlsx

Design rules:
  - Heavy deps (xlsxwriter, openpyxl, pandas) are LAZY-imported.
  - All writes are sandboxed to ~/argus-workspace via _safe_path().
  - Blocking I/O wrapped in asyncio.to_thread.
  - "Total" / "TOTAL" / "Grand Total" rows auto-styled in gold.
"""

from __future__ import annotations

import asyncio
import csv
import os
from pathlib import Path
from typing import Any

from argus.providers.base import ToolSpec
from argus.tools.registry import ToolImpl, register


# ── Workspace sandbox ────────────────────────────────────────────────────────


WORKSPACE = Path(os.path.expanduser("~/argus-workspace"))


def _safe_path(filename: str, expected_ext: str) -> Path | None:
    WORKSPACE.mkdir(exist_ok=True)
    name = (filename or "").strip().lstrip("/\\")
    if not name:
        return None
    if not name.lower().endswith(expected_ext.lower()):
        name = name + expected_ext
    target = (WORKSPACE / name).resolve()
    base = WORKSPACE.resolve()
    if base != target and base not in target.parents:
        return None
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def _err(exc: BaseException, limit: int = 200) -> str:
    msg = f"{type(exc).__name__}: {exc}"
    return msg if len(msg) <= limit else msg[: limit - 3] + "..."


def _normalize_rows(rows: Any) -> tuple[list[str], list[dict]]:
    """Accept rows as list-of-dicts OR list-of-lists with a header row.
    Returns (column_names, list_of_dicts)."""
    if not isinstance(rows, list) or not rows:
        return [], []
    first = rows[0]
    if isinstance(first, dict):
        # Union of keys preserving first-seen order
        cols: list[str] = []
        seen: set[str] = set()
        for r in rows:
            for k in r.keys():
                if k not in seen:
                    seen.add(k)
                    cols.append(str(k))
        return cols, [dict(r) for r in rows]
    if isinstance(first, list):
        if len(rows) < 2:
            return [str(c) for c in first], []
        cols = [str(c) for c in first]
        body = [dict(zip(cols, r)) for r in rows[1:]]
        return cols, body
    return [], []


def _is_total_row(row: dict, first_col: str) -> bool:
    val = row.get(first_col, "")
    if not isinstance(val, str):
        return False
    s = val.strip().lower()
    return s in {"total", "totals", "grand total", "subtotal", "sum"}


# ── Tool 1: ledger_xlsx ─────────────────────────────────────────────────────


async def _ledger_xlsx(args: dict[str, Any]) -> str:
    raw_rows = args.get("rows")
    sheet_name = (args.get("sheet_name") or "Sheet1").strip()[:31]
    output = (args.get("output_filename") or "ledger.xlsx").strip()
    title = (args.get("title") or "").strip()

    cols, rows = _normalize_rows(raw_rows)
    if not cols:
        return "ERROR: rows required (list of dicts or list-of-lists with header)"

    target = _safe_path(output, ".xlsx")
    if target is None:
        return f"ERROR: filename '{output}' escapes the workspace"

    try:
        import xlsxwriter  # type: ignore
    except ImportError:
        return ("ERROR: XlsxWriter not installed. Run: "
                "uv sync --extra office  (or pip install XlsxWriter)")

    def _build() -> Path:
        wb = xlsxwriter.Workbook(str(target))
        ws = wb.add_worksheet(sheet_name)

        # Branded formats
        title_fmt = wb.add_format({
            "bold": True, "font_size": 14, "font_color": "#FFFFFF",
            "bg_color": "#42E8F5", "align": "left", "valign": "vcenter",
            "border": 0,
        })
        header_fmt = wb.add_format({
            "bold": True, "font_color": "#FFFFFF",
            "bg_color": "#FF38D1", "align": "left", "valign": "vcenter",
            "border": 1, "border_color": "#1A1A1A",
        })
        body_fmt = wb.add_format({
            "font_color": "#1A1A1A", "align": "left", "valign": "vcenter",
            "border": 1, "border_color": "#E0E0E0",
        })
        body_num_fmt = wb.add_format({
            "font_color": "#1A1A1A", "align": "right", "valign": "vcenter",
            "border": 1, "border_color": "#E0E0E0", "num_format": "#,##0.##",
        })
        total_fmt = wb.add_format({
            "bold": True, "font_color": "#1A1A1A",
            "bg_color": "#FFC247", "align": "left", "valign": "vcenter",
            "border": 1, "border_color": "#1A1A1A",
        })
        total_num_fmt = wb.add_format({
            "bold": True, "font_color": "#1A1A1A",
            "bg_color": "#FFC247", "align": "right", "valign": "vcenter",
            "border": 1, "border_color": "#1A1A1A", "num_format": "#,##0.##",
        })

        # Optional title row
        row_offset = 0
        if title:
            ws.merge_range(0, 0, 0, max(0, len(cols) - 1), title, title_fmt)
            ws.set_row(0, 24)
            row_offset = 1

        # Header
        for c, name in enumerate(cols):
            ws.write(row_offset, c, name, header_fmt)
        ws.set_row(row_offset, 20)

        first_col = cols[0]

        # Body — track column widths
        col_widths = [max(10, len(name) + 2) for name in cols]
        for r, row in enumerate(rows, start=row_offset + 1):
            is_total = _is_total_row(row, first_col)
            for c, name in enumerate(cols):
                val = row.get(name, "")
                if isinstance(val, (int, float)) and not isinstance(val, bool):
                    fmt = total_num_fmt if is_total else body_num_fmt
                    ws.write_number(r, c, val, fmt)
                else:
                    fmt = total_fmt if is_total else body_fmt
                    ws.write(r, c, "" if val is None else str(val), fmt)
                col_widths[c] = max(col_widths[c], len(str(val)) + 2)

        # Apply column widths (cap at 50)
        for c, w in enumerate(col_widths):
            ws.set_column(c, c, min(50, w))

        # Freeze header row
        ws.freeze_panes(row_offset + 1, 0)

        wb.close()
        return target

    try:
        path = await asyncio.to_thread(_build)
    except Exception as e:  # noqa: BLE001
        return f"ERROR: xlsx build failed - {_err(e)}"

    from argus.tools.file_delivery import deliver_file
    extra = f"{len(rows)} rows · {len(cols)} cols · sheet '{sheet_name}'"
    return await deliver_file(path, title=title or f"Spreadsheet: {sheet_name}",
                               extra=extra)


# ── Tool 2: ledger_csv ──────────────────────────────────────────────────────


async def _ledger_csv(args: dict[str, Any]) -> str:
    raw_rows = args.get("rows")
    output = (args.get("output_filename") or "ledger.csv").strip()

    cols, rows = _normalize_rows(raw_rows)
    if not cols:
        return "ERROR: rows required (list of dicts or list-of-lists with header)"

    target = _safe_path(output, ".csv")
    if target is None:
        return f"ERROR: filename '{output}' escapes the workspace"

    def _write() -> Path:
        with target.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
            writer.writeheader()
            for r in rows:
                writer.writerow({k: ("" if v is None else v) for k, v in r.items()})
        return target

    try:
        path = await asyncio.to_thread(_write)
    except Exception as e:  # noqa: BLE001
        return f"ERROR: csv write failed - {_err(e)}"

    from argus.tools.file_delivery import deliver_file
    return await deliver_file(path, extra=f"{len(rows)} rows · {len(cols)} cols")


# ── Tool 3: ledger_pivot ────────────────────────────────────────────────────


_VALID_AGG = {"sum", "mean", "count", "max", "min", "median"}


async def _ledger_pivot(args: dict[str, Any]) -> str:
    input_xlsx = (args.get("input_xlsx") or "").strip()
    index_col = (args.get("index_col") or "").strip()
    value_col = (args.get("value_col") or "").strip()
    agg = (args.get("agg") or "sum").strip().lower()
    output = (args.get("output_filename") or "pivot.xlsx").strip()
    columns_col = (args.get("columns_col") or "").strip()

    if not input_xlsx:
        return "ERROR: input_xlsx required"
    if not index_col or not value_col:
        return "ERROR: index_col and value_col required"
    if agg not in _VALID_AGG:
        return f"ERROR: agg must be one of {sorted(_VALID_AGG)}"

    src = _safe_path(input_xlsx, ".xlsx")
    if src is None or not src.exists():
        return f"ERROR: input not found in workspace: {input_xlsx}"

    target = _safe_path(output, ".xlsx")
    if target is None:
        return f"ERROR: output filename escapes workspace"

    try:
        import pandas as pd  # type: ignore
    except ImportError:
        return ("ERROR: pandas not installed. Run: "
                "uv sync --extra office  (or pip install 'pandas[excel]')")

    def _build() -> tuple[Path, int, int]:
        df = pd.read_excel(str(src))
        for col in (index_col, value_col):
            if col not in df.columns:
                raise ValueError(f"column '{col}' not in {list(df.columns)}")
        if columns_col and columns_col not in df.columns:
            raise ValueError(f"columns_col '{columns_col}' not in {list(df.columns)}")

        kwargs: dict[str, Any] = {
            "index": index_col, "values": value_col, "aggfunc": agg,
        }
        if columns_col:
            kwargs["columns"] = columns_col

        pivot = pd.pivot_table(df, **kwargs)

        # Append a grand-total row
        try:
            totals = pivot.sum(numeric_only=True)
            totals.name = "Total"
            pivot = pd.concat([pivot, totals.to_frame().T])
        except Exception:
            pass

        with pd.ExcelWriter(str(target), engine="openpyxl") as writer:
            pivot.to_excel(writer, sheet_name="Pivot")
        return target, len(pivot), len(pivot.columns)

    try:
        path, n_rows, n_cols = await asyncio.to_thread(_build)
    except Exception as e:  # noqa: BLE001
        return f"ERROR: pivot failed - {_err(e)}"

    return (f"OK: pivoted -> {path} ({path.stat().st_size:,} bytes, "
            f"{n_rows} rows x {n_cols} cols, agg='{agg}')")


# ── Registration ────────────────────────────────────────────────────────────


def register_ledger_tools() -> None:
    register(ToolImpl("documents", ToolSpec(
        name="ledger_xlsx",
        description=(
            "Author a branded Excel (.xlsx) workbook from row data. Use when "
            "the user asks for a spreadsheet, tracker, dashboard, or any "
            "structured table. Header row is bold on Electric Magenta; rows "
            "where the first cell is 'Total'/'Subtotal' auto-style on Acid "
            "Gold. Pass `rows` as a list of dicts (each dict is one row, "
            "keys are column names). Writes to ~/argus-workspace."
        ),
        parameters={
            "type": "object",
            "properties": {
                "rows":            {"type": "array",
                                     "description": "List of row dicts. e.g. [{'Region':'US','Q1':100}, ...]"},
                "sheet_name":      {"type": "string"},
                "title":           {"type": "string",
                                     "description": "Optional title row at the top (cyan banner)"},
                "output_filename": {"type": "string"},
            },
            "required": ["rows", "output_filename"],
        },
    ), _ledger_xlsx))

    register(ToolImpl("documents", ToolSpec(
        name="ledger_csv",
        description=(
            "Write a CSV file from row data. No pip deps required. Use when "
            "the user wants plain comma-separated output for import elsewhere."
        ),
        parameters={
            "type": "object",
            "properties": {
                "rows":            {"type": "array"},
                "output_filename": {"type": "string"},
            },
            "required": ["rows", "output_filename"],
        },
    ), _ledger_csv))

    register(ToolImpl("documents", ToolSpec(
        name="ledger_pivot",
        description=(
            "Read an existing workspace .xlsx, pivot it, and write a new "
            ".xlsx. Use when the user has a sheet and wants 'group by X, "
            "sum Y' or similar. Aggs: sum | mean | count | max | min | median."
        ),
        parameters={
            "type": "object",
            "properties": {
                "input_xlsx":      {"type": "string",
                                     "description": "Existing .xlsx in workspace"},
                "index_col":       {"type": "string", "description": "Column to group by"},
                "value_col":       {"type": "string", "description": "Column to aggregate"},
                "agg":             {"type": "string", "enum": list(_VALID_AGG)},
                "columns_col":     {"type": "string",
                                     "description": "Optional second grouping for wide pivots"},
                "output_filename": {"type": "string"},
            },
            "required": ["input_xlsx", "index_col", "value_col", "output_filename"],
        },
    ), _ledger_pivot))
