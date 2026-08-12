#!/usr/bin/env python3
"""CLI entrypoint for deterministic synthetic event generation."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from rca.generator import ScenarioConfig, generate_synthetic_data  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("data/generated"))
    parser.add_argument("--scale", type=float, default=1.0, help="Session-volume multiplier in (0, 1].")
    args = parser.parse_args()
    paths = generate_synthetic_data(args.output_dir, ScenarioConfig(sessions_per_day_scale=args.scale))
    for name, path in paths.items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()
