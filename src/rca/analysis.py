"""Analysis, data-quality checks, statistics, and charts for the RCA case."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import norm

from rca.generator import AS_OF_TIMESTAMP, RELEASE_DATE

ROOT = Path(__file__).resolve().parents[2]
SQL_DIR = ROOT / "sql"
LOCAL_FILTER = {
    "platform": "Android",
    "city": "Moscow",
    "acquisition_channel": "paid_search",
}
PALETTE = {
    "ink": "#1F2937",
    "grid": "#D1D5DB",
    "blue": "#1666B0",
    "blue_light": "#93C5FD",
    "orange": "#D97706",
    "pink": "#C2416C",
    "grey": "#6B7280",
}


def _read_sql(name: str, data_dir: Path) -> str:
    """Read a SQL template and replace only paths controlled by this pipeline."""
    sql = (SQL_DIR / name).read_text(encoding="utf-8")
    replacement = {
        "{{EVENTS_PATH}}": str((data_dir / "events.csv").resolve()).replace("'", "''"),
        "{{SESSIONS_PATH}}": str((data_dir / "session_attributes.csv").resolve()).replace("'", "''"),
        "{{RELEASES_PATH}}": str((data_dir / "release_calendar.csv").resolve()).replace("'", "''"),
    }
    for placeholder, path in replacement.items():
        sql = sql.replace(placeholder, path)
    return sql


def load_raw_data(data_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Load the synthetic source files with timestamp parsing."""
    data_dir = Path(data_dir)
    events = pd.read_csv(data_dir / "events.csv", parse_dates=["event_ts", "ingested_at"])
    attributes = pd.read_csv(data_dir / "session_attributes.csv", parse_dates=["session_started_at"])
    releases = pd.read_csv(data_dir / "release_calendar.csv", parse_dates=["released_at", "rollout_start"])
    attributes["event_date"] = pd.to_datetime(attributes["event_date"])
    return events, attributes, releases


def build_funnel_with_pandas(events: pd.DataFrame, attributes: pd.DataFrame) -> pd.DataFrame:
    """Independent pandas path used to cross-check SQL output and calculations."""
    relevant = events.loc[
        events["event_name"].isin(["search_results_viewed", "booking_created"])
    ].copy()
    flags = (
        relevant.assign(
            is_search=relevant["event_name"].eq("search_results_viewed").astype(int),
            is_booking=relevant["event_name"].eq("booking_created").astype(int),
        )
        .groupby("session_id", as_index=False)
        .agg(has_search=("is_search", "max"), has_booking=("is_booking", "max"))
    )
    search_times = (
        relevant.loc[relevant["event_name"].eq("search_results_viewed")]
        .groupby("session_id", as_index=False)["event_ts"]
        .min()
        .rename(columns={"event_ts": "search_ts"})
    )
    booking_times = (
        relevant.loc[relevant["event_name"].eq("booking_created")]
        .groupby("session_id", as_index=False)["event_ts"]
        .min()
        .rename(columns={"event_ts": "booking_ts"})
    )
    event_rollup = flags.merge(search_times, on="session_id", how="left").merge(
        booking_times, on="session_id", how="left"
    )
    funnel = attributes.merge(event_rollup, on="session_id", how="left", validate="one_to_one")
    funnel = funnel.loc[funnel["has_search"].eq(1)].copy()
    funnel["period"] = np.where(funnel["event_date"] < RELEASE_DATE, "before", "after")
    return funnel


def run_sql_models(data_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Build the mart and decomposition with DuckDB from raw CSV inputs."""
    connection = duckdb.connect(database=":memory:")
    try:
        connection.execute(_read_sql("01_create_funnel_mart.sql", data_dir))
        connection.execute(_read_sql("02_contribution_decomposition.sql", data_dir))
        mart = connection.execute("SELECT * FROM funnel_session_mart ORDER BY session_id").df()
        contribution = connection.execute(
            "SELECT * FROM contribution_decomposition ORDER BY mix_impact_rank, acquisition_channel, session_quality"
        ).df()
        sql_quality = connection.execute(_read_sql("03_quality_checks.sql", data_dir)).df()
    finally:
        connection.close()
    mart["event_date"] = pd.to_datetime(mart["event_date"])
    mart["search_ts"] = pd.to_datetime(mart["search_ts"])
    mart["booking_ts"] = pd.to_datetime(mart["booking_ts"])
    return mart, contribution, sql_quality


def two_proportion_test(before_bookings: int, before_n: int, after_bookings: int, after_n: int) -> dict[str, float]:
    """Two-sided pooled z-test plus unpooled 95% CI for (after - before)."""
    before_rate = before_bookings / before_n
    after_rate = after_bookings / after_n
    difference = after_rate - before_rate
    pooled = (before_bookings + after_bookings) / (before_n + after_n)
    pooled_se = np.sqrt(pooled * (1 - pooled) * (1 / before_n + 1 / after_n))
    z_score = difference / pooled_se
    p_value = 2 * norm.sf(abs(z_score))
    unpooled_se = np.sqrt(
        before_rate * (1 - before_rate) / before_n + after_rate * (1 - after_rate) / after_n
    )
    ci_radius = norm.ppf(0.975) * unpooled_se
    return {
        "before_rate": before_rate,
        "after_rate": after_rate,
        "difference": difference,
        "ci_low": difference - ci_radius,
        "ci_high": difference + ci_radius,
        "z_score": z_score,
        "p_value": p_value,
    }


def _rate_ci(bookings: int, sessions: int) -> tuple[float, float]:
    """Wilson interval for a binomial conversion rate."""
    if sessions == 0:
        return np.nan, np.nan
    z = norm.ppf(0.975)
    rate = bookings / sessions
    denominator = 1 + z**2 / sessions
    centre = (rate + z**2 / (2 * sessions)) / denominator
    radius = z * np.sqrt(rate * (1 - rate) / sessions + z**2 / (4 * sessions**2)) / denominator
    return centre - radius, centre + radius


def data_quality_checks(
    events: pd.DataFrame,
    attributes: pd.DataFrame,
    releases: pd.DataFrame,
    mart: pd.DataFrame,
    sql_quality: pd.DataFrame,
) -> dict[str, Any]:
    """Profile the event data at its intended session grain."""
    required = [
        "session_id",
        "user_id",
        "event_date",
        "city",
        "platform",
        "app_version",
        "acquisition_channel",
        "session_quality",
    ]
    event_order = (
        events.pivot_table(index="session_id", columns="event_name", values="event_ts", aggfunc="min")
        .rename_axis(None, axis=1)
        .reset_index()
    )
    booking_before_search = int(
        (
            event_order.get("booking_created").notna()
            & (event_order["booking_created"] <= event_order["search_results_viewed"])
        ).sum()
    )
    null_count = int(attributes[required].isna().sum().sum())
    source_session_ids = set(attributes["session_id"])
    mart_session_ids = set(mart["session_id"])
    expected_days = pd.date_range(attributes["event_date"].min(), attributes["event_date"].max(), freq="D")
    observed_days = pd.DatetimeIndex(attributes["event_date"].drop_duplicates().sort_values())
    freshness_hours = float((AS_OF_TIMESTAMP - events["ingested_at"].max()).total_seconds() / 3600)
    checks = {
        "event_id_uniqueness": {
            "passed": bool(events["event_id"].is_unique),
            "value": int(events["event_id"].duplicated().sum()),
            "rule": "0 duplicate event_id values",
        },
        "session_key_uniqueness": {
            "passed": bool(attributes["session_id"].is_unique),
            "value": int(attributes["session_id"].duplicated().sum()),
            "rule": "0 duplicate session_id values in the session dimension",
        },
        "required_attributes": {
            "passed": null_count == 0,
            "value": null_count,
            "rule": "0 nulls in session-grain dimensions",
        },
        "event_order": {
            "passed": booking_before_search == 0,
            "value": booking_before_search,
            "rule": "booking_created cannot precede search_results_viewed",
        },
        "join_coverage": {
            "passed": source_session_ids == mart_session_ids,
            "value": int(len(source_session_ids.symmetric_difference(mart_session_ids))),
            "rule": "every session attribute row maps to exactly one funnel row",
        },
        "date_coverage": {
            "passed": expected_days.equals(observed_days),
            "value": int(len(expected_days) - len(observed_days)),
            "rule": "no missing daily partitions in the analysis window",
        },
        "freshness": {
            "passed": 0 <= freshness_hours <= 24,
            "value": round(freshness_hours, 2),
            "rule": "latest ingestion is within 24 hours of the as-of timestamp",
        },
        "sql_event_grain": {
            "passed": bool(
                int(sql_quality.loc[0, "duplicate_event_ids"]) == 0
                and int(sql_quality.loc[0, "sessions_without_exactly_one_search"]) == 0
                and int(sql_quality.loc[0, "sessions_with_multiple_bookings"]) == 0
            ),
            "value": {
                key: int(sql_quality.loc[0, key])
                for key in [
                    "duplicate_event_ids",
                    "sessions_without_exactly_one_search",
                    "sessions_with_multiple_bookings",
                ]
            },
            "rule": "SQL event-grain checks are all zero",
        },
    }
    return {
        "dataset": "reproducibly generated synthetic events",
        "grain": "one row per search session in funnel_session_mart",
        "as_of_timestamp": AS_OF_TIMESTAMP.isoformat(),
        "row_counts": {
            "events": int(len(events)),
            "session_attributes": int(len(attributes)),
            "funnel_sessions": int(len(mart)),
            "release_rows": int(len(releases)),
        },
        "checks": checks,
        "all_passed": bool(all(item["passed"] for item in checks.values())),
    }


def _period_totals(mart: pd.DataFrame) -> pd.DataFrame:
    return (
        mart.groupby("period", as_index=False)
        .agg(search_sessions=("session_id", "size"), bookings=("has_booking", "sum"))
        .assign(conversion=lambda frame: frame["bookings"] / frame["search_sessions"])
        .sort_values("period")
    )


def _local_slice(mart: pd.DataFrame, period: str, version: str | None = None) -> pd.DataFrame:
    mask = mart["period"].eq(period)
    for column, value in LOCAL_FILTER.items():
        mask &= mart[column].eq(value)
    if version is not None:
        mask &= mart["app_version"].eq(version)
    return mart.loc[mask].copy()


def calculate_metrics(mart: pd.DataFrame, contribution: pd.DataFrame) -> dict[str, Any]:
    """Calculate top-line, decomposition, and focused local-regression metrics."""
    totals = _period_totals(mart).set_index("period")
    before = totals.loc["before"]
    after = totals.loc["after"]
    global_delta = float(after["conversion"] - before["conversion"])
    mix_effect = float(contribution["mix_effect"].sum())
    rate_effect = float(contribution["rate_effect"].sum())
    total_effect = float(contribution["total_effect"].sum())

    local_before = _local_slice(mart, "before")
    local_after_new = _local_slice(mart, "after", "5.14.0")
    local_after_old = _local_slice(mart, "after").loc[
        lambda frame: frame["app_version"].isin(["5.13.1", "5.13.2"])
    ]
    local_test = two_proportion_test(
        int(local_before["has_booking"].sum()),
        int(len(local_before)),
        int(local_after_new["has_booking"].sum()),
        int(len(local_after_new)),
    )

    local_quality = []
    for quality in ["high", "standard", "low"]:
        before_quality = local_before.loc[local_before["session_quality"].eq(quality)]
        after_quality = local_after_new.loc[local_after_new["session_quality"].eq(quality)]
        if len(before_quality) and len(after_quality):
            comparison = two_proportion_test(
                int(before_quality["has_booking"].sum()),
                int(len(before_quality)),
                int(after_quality["has_booking"].sum()),
                int(len(after_quality)),
            )
            local_quality.append({"session_quality": quality, **comparison})

    top_mix = (
        contribution.assign(segment=lambda frame: frame["acquisition_channel"] + " × " + frame["session_quality"])
        .sort_values("mix_effect")
        .loc[:, ["segment", "mix_effect", "rate_effect", "total_effect", "before_share", "after_share"]]
        .head(5)
        .to_dict(orient="records")
    )
    return {
        "window": {
            "before": "2026-06-29 to 2026-07-19",
            "after": "2026-07-20 to 2026-08-09",
            "release_date": RELEASE_DATE.date().isoformat(),
            "timezone": "UTC (synthetic event timestamps)",
        },
        "period_totals": {
            period: {
                "search_sessions": int(totals.loc[period, "search_sessions"]),
                "bookings": int(totals.loc[period, "bookings"]),
                "conversion": float(totals.loc[period, "conversion"]),
            }
            for period in ["before", "after"]
        },
        "global_delta": global_delta,
        "decomposition": {
            "mix_effect": mix_effect,
            "rate_effect": rate_effect,
            "total_effect": total_effect,
            "reconciliation_error": float(global_delta - total_effect),
            "mix_share_of_absolute_delta": abs(mix_effect) / abs(global_delta),
        },
        "local_release_slice": {
            "definition": "Android × Moscow × paid_search; post slice is app_version 5.14.0",
            "before_sessions": int(len(local_before)),
            "before_bookings": int(local_before["has_booking"].sum()),
            "after_514_sessions": int(len(local_after_new)),
            "after_514_bookings": int(local_after_new["has_booking"].sum()),
            "after_old_sessions": int(len(local_after_old)),
            "after_old_bookings": int(local_after_old["has_booking"].sum()),
            "after_old_conversion": float(local_after_old["has_booking"].mean()),
            **local_test,
            "by_quality": local_quality,
        },
        "top_mix_contributors": top_mix,
    }


def make_figures(mart: pd.DataFrame, contribution: pd.DataFrame, metrics: dict[str, Any], figure_dir: Path) -> None:
    """Render three audited static charts for the report and README."""
    figure_dir.mkdir(parents=True, exist_ok=True)
    plt.rcParams.update({"font.family": "DejaVu Sans", "axes.titleweight": "bold"})

    daily = (
        mart.groupby("event_date", as_index=False)
        .agg(search_sessions=("session_id", "size"), bookings=("has_booking", "sum"))
        .assign(conversion=lambda frame: frame["bookings"] / frame["search_sessions"])
    )
    fig, ax = plt.subplots(figsize=(10, 4.8))
    ax.plot(daily["event_date"], daily["conversion"] * 100, color=PALETTE["blue"], linewidth=2.2)
    ax.axvline(RELEASE_DATE, color=PALETTE["orange"], linestyle="--", linewidth=1.6, label="Release 5.14")
    ax.set_title("Daily search-to-book conversion")
    ax.set_ylabel("Conversion, %")
    ax.set_xlabel("Search-session date (UTC)")
    ax.yaxis.grid(True, color=PALETTE["grid"], alpha=0.7)
    ax.legend(frameon=False, loc="lower left")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(figure_dir / "daily_conversion.png", dpi=180, bbox_inches="tight")
    plt.close(fig)

    display_contribution = contribution.assign(
        segment=lambda frame: frame["acquisition_channel"] + " × " + frame["session_quality"],
        mix_pp=lambda frame: frame["mix_effect"] * 100,
        rate_pp=lambda frame: frame["rate_effect"] * 100,
    ).sort_values("mix_pp")
    fig, ax = plt.subplots(figsize=(10, 5.4))
    positions = np.arange(len(display_contribution))
    ax.barh(positions - 0.18, display_contribution["mix_pp"], height=0.34, color=PALETTE["pink"], label="Mix effect")
    ax.barh(positions + 0.18, display_contribution["rate_pp"], height=0.34, color=PALETTE["blue"], label="Within-cell rate effect")
    ax.axvline(0, color=PALETTE["ink"], linewidth=0.9)
    ax.set_yticks(positions, display_contribution["segment"])
    ax.set_title("Contribution to the aggregate conversion change")
    ax.set_xlabel("Contribution, percentage points")
    ax.xaxis.grid(True, color=PALETTE["grid"], alpha=0.7)
    ax.legend(frameon=False, loc="lower right")
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(figure_dir / "mix_decomposition.png", dpi=180, bbox_inches="tight")
    plt.close(fig)

    local = metrics["local_release_slice"]
    groups = [
        ("Before\n(all versions)", local["before_bookings"], local["before_sessions"], PALETTE["blue_light"]),
        ("After\n5.14.0", local["after_514_bookings"], local["after_514_sessions"], PALETTE["orange"]),
        ("After\nold versions", local["after_old_bookings"], local["after_old_sessions"], PALETTE["grey"]),
    ]
    rates = np.array([bookings / sessions for _, bookings, sessions, _ in groups])
    errors = np.array([
        [rate - _rate_ci(bookings, sessions)[0], _rate_ci(bookings, sessions)[1] - rate]
        for rate, (_, bookings, sessions, _) in zip(rates, groups)
    ]).T
    fig, ax = plt.subplots(figsize=(8.6, 4.8))
    ax.bar(
        [item[0] for item in groups],
        rates * 100,
        yerr=errors * 100,
        color=[item[3] for item in groups],
        capsize=4,
        width=0.58,
    )
    ax.set_title("Focused Android–Moscow–paid-search slice")
    ax.set_ylabel("Conversion, % (95% Wilson CI)")
    ax.yaxis.grid(True, color=PALETTE["grid"], alpha=0.7)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(figure_dir / "local_regression.png", dpi=180, bbox_inches="tight")
    plt.close(fig)


def run_analysis(data_dir: Path, output_dir: Path) -> dict[str, Any]:
    """Execute the full, reproducible analytical path and persist inspectable outputs."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "figures").mkdir(exist_ok=True)
    (output_dir / "tables").mkdir(exist_ok=True)

    events, attributes, releases = load_raw_data(data_dir)
    mart, contribution, sql_quality = run_sql_models(data_dir)
    pandas_mart = build_funnel_with_pandas(events, attributes)
    pandas_totals = _period_totals(pandas_mart).set_index("period")
    sql_totals = _period_totals(mart).set_index("period")
    cross_check_error = float((pandas_totals["conversion"] - sql_totals["conversion"]).abs().max())
    if cross_check_error > 1e-12:
        raise ValueError(f"Pandas and SQL conversion disagree by {cross_check_error}.")

    quality = data_quality_checks(events, attributes, releases, mart, sql_quality)
    metrics = calculate_metrics(mart, contribution)
    metrics["validation"] = {
        "pandas_sql_conversion_max_abs_error": cross_check_error,
        "decomposition_reconciles": abs(metrics["decomposition"]["reconciliation_error"]) < 1e-12,
    }
    make_figures(mart, contribution, metrics, output_dir / "figures")
    contribution.to_csv(output_dir / "tables" / "contribution_decomposition.csv", index=False)
    _period_totals(mart).to_csv(output_dir / "tables" / "period_totals.csv", index=False)
    with (output_dir / "metrics.json").open("w", encoding="utf-8") as output:
        json.dump(metrics, output, ensure_ascii=False, indent=2)
    with (output_dir / "quality_checks.json").open("w", encoding="utf-8") as output:
        json.dump(quality, output, ensure_ascii=False, indent=2)
    return {"metrics": metrics, "quality": quality}
