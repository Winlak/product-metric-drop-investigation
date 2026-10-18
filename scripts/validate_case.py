#!/usr/bin/env python3
"""Independent final checks for the synthetic RCA case."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from rca.analysis import (  # noqa: E402
    _period_totals,
    build_funnel_with_pandas,
    load_raw_data,
    run_sql_models,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--reports-dir", type=Path, required=True)
    args = parser.parse_args()
    metrics = json.loads((args.reports_dir / "metrics.json").read_text(encoding="utf-8"))
    quality = json.loads((args.reports_dir / "quality_checks.json").read_text(encoding="utf-8"))
    events, attributes, _ = load_raw_data(args.data_dir)
    mart, contribution, _ = run_sql_models(args.data_dir)
    pandas_mart = build_funnel_with_pandas(events, attributes)
    pandas_conversion = _period_totals(pandas_mart).set_index("period")["conversion"]
    sql_conversion = _period_totals(mart).set_index("period")["conversion"]
    pandas_sql_error = float((pandas_conversion - sql_conversion).abs().max())

    required_figures = [
        args.reports_dir / "figures" / "daily_conversion.svg",
        args.reports_dir / "figures" / "mix_decomposition.svg",
        args.reports_dir / "figures" / "local_regression.svg",
    ]
    required_files = required_figures + [
        args.reports_dir / "analysis_report.md",
        args.reports_dir / "tables" / "contribution_decomposition.csv",
    ]
    report_text = (args.reports_dir / "analysis_report.md").read_text(encoding="utf-8")
    checks = {
        "data_quality": quality["all_passed"],
        "pandas_matches_sql": pandas_sql_error < 1e-12,
        "decomposition_reconciles": abs(metrics["decomposition"]["reconciliation_error"]) < 1e-12,
        "scenario_has_topline_drop": metrics["global_delta"] < -0.02,
        "headline_global_delta_matches_fixture": math.isclose(
            metrics["global_delta"], -0.03486118003505012, abs_tol=1e-12
        ),
        "headline_mix_effect_matches_fixture": math.isclose(
            metrics["decomposition"]["mix_effect"], -0.035909751761690395, abs_tol=1e-12
        ),
        "mix_is_larger_than_rate_effect": abs(metrics["decomposition"]["mix_effect"])
        > abs(metrics["decomposition"]["rate_effect"]),
        "local_slice_has_material_drop": metrics["local_release_slice"]["difference"] < -0.04,
        "no_outcome_leakage_in_attributes": "booked" not in attributes.columns,
        "reader_artifacts_exist": all(path.exists() and path.stat().st_size > 0 for path in required_files),
        "causal_caveat_is_visible": "не доказывает причинность" in report_text
        and "не causal effect" in report_text,
        "source_is_labeled_synthetic": "синтетические" in report_text.lower(),
    }
    if not all(checks.values()):
        failed = [name for name, passed in checks.items() if not passed]
        raise SystemExit(f"Validation failed: {', '.join(failed)}")

    validation_report = f"""# Проверка результатов

### Результаты автоматических проверок

### Методика
Вопрос — объяснить падение session-level search-to-book conversion после synthetic rollout. Окна имеют одинаковую длину; знаменатель — search-сессии, а не события. Сценарий и данные явно помечены как синтетические. Наблюдательная temporal связь отделена от causal claim.

### Ограничения дизайна
1. Before/after не является экспериментом. В отчёте это ограничение видно рядом с локальным выводом; для causal решения предложен randomised holdback.
2. Версия приложения появляется в post-period, поэтому version effect не полностью отделим от времени и rollout selection.

### Контрольные пересчёты
- Pandas vs DuckDB conversion: Verified — max absolute difference {pandas_sql_error:.1e}.
- Contribution reconciliation: Verified — residual {metrics['decomposition']['reconciliation_error']:.1e}.
- Headline fixture: Verified — global delta {metrics['global_delta'] * 100:.2f} pp; mix effect {metrics['decomposition']['mix_effect'] * 100:.2f} pp.
- Event grain / freshness / joins: Verified — {len(quality['checks'])} of {len(quality['checks'])} checks passed.
- Leakage: Verified — session-attribute input has no outcome field; booking is inferred only from raw event rollup.

### Файлы графиков
Проверено наличие трёх непустых SVG-файлов: дневного тренда, разложения изменения конверсии и локального среза с 95% Wilson CI. Автоматическая проверка файлов не подтверждает читаемость графиков или корректность их визуального оформления.

### Что проверить дальше
1. В production добавить availability, pricing, latency и crash telemetry, затем провести pre-registered holdback.

### Ограничения интерпретации
- Synthetic scenario иллюстрирует метод, а не состояние реального бизнеса.
- Статистическая разница в сегменте не доказывает эффект релиза без дизайна причинной идентификации.
"""
    (args.reports_dir / "validation_report.md").write_text(validation_report, encoding="utf-8")
    print("Validation passed")


if __name__ == "__main__":
    main()
