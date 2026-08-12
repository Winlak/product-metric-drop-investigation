#!/usr/bin/env python3
"""Remove volatile Jupyter execution metadata while keeping executed outputs."""

from __future__ import annotations

import argparse
import io
from pathlib import Path
from typing import Any

import nbformat

VOLATILE_METADATA_KEYS = {
    "ExecuteTime",
    "execution",
    "executionInfo",
    "lastExecuted",
    "timestamp",
    "trusted",
}


def _strip_volatile_metadata(metadata: dict[str, Any]) -> None:
    """Remove timestamps and host-specific execution details, preserving useful metadata."""
    for key in VOLATILE_METADATA_KEYS:
        metadata.pop(key, None)


def normalize_notebook(notebook: nbformat.NotebookNode) -> nbformat.NotebookNode:
    """Normalize metadata in-place without clearing cells, outputs, or execution counts."""
    _strip_volatile_metadata(notebook.metadata)
    for cell in notebook.cells:
        _strip_volatile_metadata(cell.metadata)
        for output in cell.get("outputs", []):
            _strip_volatile_metadata(output.get("metadata", {}))
    return notebook


def _serialize(notebook: nbformat.NotebookNode) -> str:
    buffer = io.StringIO()
    nbformat.write(notebook, buffer)
    return buffer.getvalue()


def normalize_file(path: Path, check: bool = False) -> bool:
    """Normalize a notebook; return whether its canonical content changed."""
    original = path.read_text(encoding="utf-8")
    notebook = nbformat.reads(original, as_version=4)
    nbformat.validate(notebook)
    canonical = _serialize(normalize_notebook(notebook))
    changed = canonical != original
    if changed and not check:
        path.write_text(canonical, encoding="utf-8")
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--notebook", type=Path, required=True)
    parser.add_argument("--check", action="store_true", help="Fail if normalization would change the file.")
    args = parser.parse_args()
    changed = normalize_file(args.notebook, check=args.check)
    if args.check and changed:
        raise SystemExit(f"Notebook requires normalization: {args.notebook}")
    print(f"{'Normalized' if changed else 'Already normalized'}: {args.notebook}")


if __name__ == "__main__":
    main()
