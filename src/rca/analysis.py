"""Analysis, data-quality checks, statistics, and charts for the RCA case."""

from __future__ import annotations

import json
import math
from html import escape
from pathlib import Path
from typing import Any

import duckdb
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


def _svg_number(value: float) -> str:
    """Render an SVG coordinate at a stable, reviewable precision."""
    return f"{float(value):.3f}".rstrip("0").rstrip(".")


def _svg_text(
    x: float,
    y: float,
    label: str,
    *,
    size: int = 13,
    fill: str = PALETTE["ink"],
    anchor: str = "start",
    weight: str = "normal",
) -> str:
    """Create a text element without host-specific renderer metadata."""
    return (
        f'<text x="{_svg_number(x)}" y="{_svg_number(y)}" '
        f'font-family="DejaVu Sans, Arial, sans-serif" font-size="{size}" '
        f'fill="{fill}" text-anchor="{anchor}" font-weight="{weight}">{escape(label)}</text>'
    )


def _write_svg(path: Path, width: int, height: int, elements: list[str]) -> None:
    """Write a text SVG deterministically; no raster renderer or timestamp is involved."""
    document = "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img">',
            "  <title>Product analytics RCA chart</title>",
            *[f"  {element}" for element in elements],
            "</svg>",
            "",
        ]
    )
    path.write_text(document, encoding="utf-8")


def _scale(value: float, domain_low: float, domain_high: float, range_low: float, range_high: float) -> float:
    """Map a value into chart coordinates."""
    return range_low + (value - domain_low) * (range_high - range_low) / (domain_high - domain_low)


def _bar_rect(zero: float, endpoint: float, y: float, height: float, color: str) -> str:
    """Render one horizontal contribution bar around a zero baseline."""
    return (
        f'<rect x="{_svg_number(min(zero, endpoint))}" y="{_svg_number(y)}" '
        f'width="{_svg_number(abs(endpoint - zero))}" height="{_svg_number(height)}" fill="{color}"/>'
    )


def _daily_conversion_svg(mart: pd.DataFrame, destination: Path) -> None:
    daily = (
        mart.groupby("event_date", as_index=False)
        .agg(search_sessions=("session_id", "size"), bookings=("has_booking", "sum"))
        .assign(conversion=lambda frame: frame["bookings"] / frame["search_sessions"])
    )
    width, height = 960, 440
    left, right, top, bottom = 78, 32, 58, 76
    plot_right, plot_bottom = width - right, height - bottom
    values = (daily["conversion"] * 100).tolist()
    y_low = float(math.floor(min(values) - 1))
    y_high = float(math.ceil(max(values) + 1))
    def y_position(value: float) -> float:
        return _scale(value, y_low, y_high, plot_bottom, top)

    def x_position(index: int) -> float:
        return _scale(index, 0, len(daily) - 1, left, plot_right)
    elements = [
        f'<rect width="{width}" height="{height}" fill="white"/>',
        _svg_text(left, 30, "Daily search-to-book conversion", size=18, weight="bold"),
    ]
    for tick_index in range(5):
        value = y_low + (y_high - y_low) * tick_index / 4
        y = y_position(value)
        elements.extend(
            [
                f'<line x1="{left}" y1="{_svg_number(y)}" x2="{plot_right}" y2="{_svg_number(y)}" stroke="{PALETTE["grid"]}" stroke-width="1"/>',
                _svg_text(left - 10, y + 4, f"{value:.1f}%", size=11, fill=PALETTE["grey"], anchor="end"),
            ]
        )
    points = " ".join(
        f"{_svg_number(x_position(index))},{_svg_number(y_position(value))}"
        for index, value in enumerate(values)
    )
    release_index = next(
        index for index, event_date in enumerate(daily["event_date"]) if event_date >= RELEASE_DATE
    )
    release_x = x_position(release_index)
    elements.extend(
        [
            f'<polyline points="{points}" fill="none" stroke="{PALETTE["blue"]}" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>',
            f'<line x1="{_svg_number(release_x)}" y1="{top}" x2="{_svg_number(release_x)}" y2="{plot_bottom}" stroke="{PALETTE["orange"]}" stroke-width="2" stroke-dasharray="6 5"/>',
            _svg_text(release_x + 8, top + 18, "Release 5.14", size=12, fill=PALETTE["orange"], weight="bold"),
            f'<line x1="{left}" y1="{plot_bottom}" x2="{plot_right}" y2="{plot_bottom}" stroke="{PALETTE["ink"]}" stroke-width="1.2"/>',
            _svg_text(18, (top + plot_bottom) / 2, "Conversion, %", size=12, fill=PALETTE["grey"]),
        ]
    )
    for index in [0, release_index, len(daily) - 1]:
        date_label = pd.Timestamp(daily.iloc[index]["event_date"]).strftime("%d %b")
        elements.append(_svg_text(x_position(index), plot_bottom + 28, date_label, size=11, fill=PALETTE["grey"], anchor="middle"))
    elements.append(_svg_text((left + plot_right) / 2, height - 22, "Search-session date (UTC)", size=12, fill=PALETTE["grey"], anchor="middle"))
    _write_svg(destination, width, height, elements)


def _contribution_svg(contribution: pd.DataFrame, destination: Path) -> None:
    display_contribution = contribution.assign(
        segment=lambda frame: frame["acquisition_channel"] + " × " + frame["session_quality"],
        mix_pp=lambda frame: frame["mix_effect"] * 100,
        rate_pp=lambda frame: frame["rate_effect"] * 100,
    ).sort_values("mix_pp", kind="mergesort")
    width, height = 1000, 560
    left, right, top, bottom = 240, 36, 66, 60
    plot_right, plot_bottom = width - right, height - bottom
    maximum = max(display_contribution[["mix_pp", "rate_pp"]].abs().max())
    extent = math.ceil(float(maximum) * 2) / 2 + 0.5
    def x_position(value: float) -> float:
        return _scale(value, -extent, extent, left, plot_right)

    zero = x_position(0)
    row_height = (plot_bottom - top) / len(display_contribution)
    elements = [
        f'<rect width="{width}" height="{height}" fill="white"/>',
        _svg_text(left, 30, "Contribution to the aggregate conversion change", size=18, weight="bold"),
        f'<rect x="{left}" y="{top}" width="14" height="14" fill="{PALETTE["pink"]}"/>',
        _svg_text(left + 20, top + 12, "Mix effect", size=12),
        f'<rect x="{left + 130}" y="{top}" width="14" height="14" fill="{PALETTE["blue"]}"/>',
        _svg_text(left + 150, top + 12, "Within-cell rate effect", size=12),
    ]
    for tick_index in range(5):
        value = -extent + 2 * extent * tick_index / 4
        x = x_position(value)
        elements.extend(
            [
                f'<line x1="{_svg_number(x)}" y1="{top + 26}" x2="{_svg_number(x)}" y2="{plot_bottom}" stroke="{PALETTE["grid"]}" stroke-width="1"/>',
                _svg_text(x, plot_bottom + 24, f"{value:.1f}", size=11, fill=PALETTE["grey"], anchor="middle"),
            ]
        )
    elements.append(f'<line x1="{_svg_number(zero)}" y1="{top + 26}" x2="{_svg_number(zero)}" y2="{plot_bottom}" stroke="{PALETTE["ink"]}" stroke-width="1.4"/>')
    for index, row in enumerate(display_contribution.to_dict("records")):
        center = top + 26 + row_height * (index + 0.5)
        elements.extend(
            [
                _svg_text(left - 12, center + 4, row["segment"], size=11, anchor="end"),
                _bar_rect(zero, x_position(row["mix_pp"]), center - 13, 10, PALETTE["pink"]),
                _bar_rect(zero, x_position(row["rate_pp"]), center + 3, 10, PALETTE["blue"]),
            ]
        )
    elements.append(_svg_text((left + plot_right) / 2, height - 15, "Contribution, percentage points", size=12, fill=PALETTE["grey"], anchor="middle"))
    _write_svg(destination, width, height, elements)


def _local_regression_svg(metrics: dict[str, Any], destination: Path) -> None:
    local = metrics["local_release_slice"]
    groups = [
        ("Before / all versions", local["before_bookings"], local["before_sessions"], PALETTE["blue_light"]),
        ("After / 5.14.0", local["after_514_bookings"], local["after_514_sessions"], PALETTE["orange"]),
        ("After / old versions", local["after_old_bookings"], local["after_old_sessions"], PALETTE["grey"]),
    ]
    intervals = [_rate_ci(bookings, sessions) for _, bookings, sessions, _ in groups]
    values = [bookings / sessions * 100 for _, bookings, sessions, _ in groups]
    y_high = math.ceil(max(interval[1] * 100 for interval in intervals) * 1.2 / 5) * 5
    width, height = 840, 460
    left, right, top, bottom = 82, 34, 60, 100
    plot_right, plot_bottom = width - right, height - bottom
    def y_position(value: float) -> float:
        return _scale(value, 0, y_high, plot_bottom, top)

    elements = [
        f'<rect width="{width}" height="{height}" fill="white"/>',
        _svg_text(left, 30, "Focused Android–Moscow–paid-search slice", size=18, weight="bold"),
    ]
    for tick_index in range(5):
        value = y_high * tick_index / 4
        y = y_position(value)
        elements.extend(
            [
                f'<line x1="{left}" y1="{_svg_number(y)}" x2="{plot_right}" y2="{_svg_number(y)}" stroke="{PALETTE["grid"]}" stroke-width="1"/>',
                _svg_text(left - 10, y + 4, f"{value:.1f}%", size=11, fill=PALETTE["grey"], anchor="end"),
            ]
        )
    spacing = (plot_right - left) / len(groups)
    for index, ((label, _, _, color), value, interval) in enumerate(zip(groups, values, intervals)):
        center = left + spacing * (index + 0.5)
        bar_width = 100
        y = y_position(value)
        low_y = y_position(interval[0] * 100)
        high_y = y_position(interval[1] * 100)
        elements.extend(
            [
                f'<rect x="{_svg_number(center - bar_width / 2)}" y="{_svg_number(y)}" width="{bar_width}" height="{_svg_number(plot_bottom - y)}" fill="{color}"/>',
                f'<line x1="{_svg_number(center)}" y1="{_svg_number(low_y)}" x2="{_svg_number(center)}" y2="{_svg_number(high_y)}" stroke="{PALETTE["ink"]}" stroke-width="2"/>',
                f'<line x1="{_svg_number(center - 7)}" y1="{_svg_number(low_y)}" x2="{_svg_number(center + 7)}" y2="{_svg_number(low_y)}" stroke="{PALETTE["ink"]}" stroke-width="2"/>',
                f'<line x1="{_svg_number(center - 7)}" y1="{_svg_number(high_y)}" x2="{_svg_number(center + 7)}" y2="{_svg_number(high_y)}" stroke="{PALETTE["ink"]}" stroke-width="2"/>',
                _svg_text(center, plot_bottom + 27, label, size=11, fill=PALETTE["grey"], anchor="middle"),
                _svg_text(center, y - 9, f"{value:.1f}%", size=12, weight="bold", anchor="middle"),
            ]
        )
    elements.extend(
        [
            f'<line x1="{left}" y1="{plot_bottom}" x2="{plot_right}" y2="{plot_bottom}" stroke="{PALETTE["ink"]}" stroke-width="1.2"/>',
            _svg_text(18, (top + plot_bottom) / 2, "Conversion, % (95% Wilson CI)", size=12, fill=PALETTE["grey"]),
        ]
    )
    _write_svg(destination, width, height, elements)


def make_figures(mart: pd.DataFrame, contribution: pd.DataFrame, metrics: dict[str, Any], figure_dir: Path) -> None:
    """Render three deterministic SVG charts for the report and README."""
    figure_dir.mkdir(parents=True, exist_ok=True)
    _daily_conversion_svg(mart, figure_dir / "daily_conversion.svg")
    _contribution_svg(contribution, figure_dir / "mix_decomposition.svg")
    _local_regression_svg(metrics, figure_dir / "local_regression.svg")


def _canonicalize_for_artifact(value: Any) -> Any:
    """Round floating tails that vary at machine precision before persistence."""
    if isinstance(value, (float, np.floating)):
        return float(format(float(value), ".15g"))
    if isinstance(value, dict):
        return {key: _canonicalize_for_artifact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_canonicalize_for_artifact(item) for item in value]
    return value


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
    contribution.to_csv(
        output_dir / "tables" / "contribution_decomposition.csv",
        index=False,
        float_format="%.15g",
    )
    _period_totals(mart).to_csv(
        output_dir / "tables" / "period_totals.csv",
        index=False,
        float_format="%.15g",
    )
    artifact_metrics = _canonicalize_for_artifact(metrics)
    artifact_quality = _canonicalize_for_artifact(quality)
    with (output_dir / "metrics.json").open("w", encoding="utf-8") as output:
        json.dump(artifact_metrics, output, ensure_ascii=False, indent=2)
    with (output_dir / "quality_checks.json").open("w", encoding="utf-8") as output:
        json.dump(artifact_quality, output, ensure_ascii=False, indent=2)
    return {"metrics": artifact_metrics, "quality": artifact_quality}
