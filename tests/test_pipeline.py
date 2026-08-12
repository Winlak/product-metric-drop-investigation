from __future__ import annotations

from pathlib import Path

from rca.analysis import run_analysis
from rca.generator import ScenarioConfig, generate_synthetic_data


def test_pipeline_reconciles_mix_and_keeps_local_regression(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    reports_dir = tmp_path / "reports"
    generate_synthetic_data(data_dir, ScenarioConfig(sessions_per_day_scale=0.35))
    result = run_analysis(data_dir, reports_dir)
    metrics = result["metrics"]

    assert result["quality"]["all_passed"]
    assert metrics["validation"]["pandas_sql_conversion_max_abs_error"] < 1e-12
    assert abs(metrics["decomposition"]["reconciliation_error"]) < 1e-12
    assert metrics["global_delta"] < -0.02
    assert abs(metrics["decomposition"]["mix_effect"]) > abs(metrics["decomposition"]["rate_effect"])
    assert metrics["local_release_slice"]["difference"] < -0.04
    assert metrics["local_release_slice"]["ci_high"] < 0
    for filename in ["daily_conversion.png", "mix_decomposition.png", "local_regression.png"]:
        assert (reports_dir / "figures" / filename).stat().st_size > 0

