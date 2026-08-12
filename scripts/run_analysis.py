#!/usr/bin/env python3
"""Run the SQL + pandas RCA pipeline and save reports, tables, and charts."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from rca.analysis import run_analysis  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data/generated"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports"))
    args = parser.parse_args()
    result = run_analysis(args.data_dir, args.output_dir)
    metrics = result["metrics"]
    print(f"Global delta: {metrics['global_delta'] * 100:.2f} pp")
    print(f"Mix effect: {metrics['decomposition']['mix_effect'] * 100:.2f} pp")
    print(f"Quality checks passed: {result['quality']['all_passed']}")


if __name__ == "__main__":
    main()
