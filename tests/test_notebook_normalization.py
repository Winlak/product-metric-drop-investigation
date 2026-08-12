from __future__ import annotations

import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import nbformat

ROOT = Path(__file__).resolve().parents[1]
NORMALIZER = ROOT / "scripts" / "normalize_notebook.py"
SOURCE_NOTEBOOK = ROOT / "notebooks" / "conversion_drop_rca.ipynb"


def test_notebook_normalizer_removes_execution_timestamps_and_preserves_outputs(tmp_path: Path) -> None:
    destination = tmp_path / "copy.ipynb"
    notebook = nbformat.read(SOURCE_NOTEBOOK, as_version=4)
    output_before = deepcopy(notebook.cells[4]["outputs"])
    notebook.metadata["execution"] = {"timestamp": "2026-08-12T00:00:00Z"}
    notebook.cells[4]["metadata"]["execution"] = {
        "iopub.status.busy": "2026-08-12T00:00:00Z",
        "shell.execute_reply": "2026-08-12T00:00:01Z",
    }
    nbformat.write(notebook, destination)

    subprocess.run(
        [sys.executable, str(NORMALIZER), "--notebook", str(destination)],
        check=True,
        cwd=ROOT,
    )

    normalized = nbformat.read(destination, as_version=4)
    assert "execution" not in normalized.metadata
    assert "execution" not in normalized.cells[4]["metadata"]
    assert normalized.cells[4]["outputs"] == output_before
    subprocess.run(
        [sys.executable, str(NORMALIZER), "--notebook", str(destination), "--check"],
        check=True,
        cwd=ROOT,
    )
