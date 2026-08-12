from __future__ import annotations

from pathlib import Path

import pandas as pd

from rca.generator import ScenarioConfig, generate_synthetic_data


def test_generator_is_deterministic_and_has_valid_event_order(tmp_path: Path) -> None:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"
    config = ScenarioConfig(sessions_per_day_scale=0.10)
    generate_synthetic_data(first_dir, config)
    generate_synthetic_data(second_dir, config)

    for filename in ["events.csv", "session_attributes.csv", "release_calendar.csv"]:
        first = pd.read_csv(first_dir / filename)
        second = pd.read_csv(second_dir / filename)
        pd.testing.assert_frame_equal(first, second)

    events = pd.read_csv(first_dir / "events.csv", parse_dates=["event_ts"])
    event_times = events.pivot(index="session_id", columns="event_name", values="event_ts")
    booked = event_times.dropna(subset=["booking_created"])
    assert (booked["booking_created"] > booked["search_results_viewed"]).all()

