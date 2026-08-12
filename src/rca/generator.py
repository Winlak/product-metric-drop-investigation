"""Deterministic synthetic event data for a conversion RCA case study.

The scenario deliberately contains two signals:
1. A sharp change in acquisition/channel-quality mix that can mislead a
   topline before/after read (Simpson-style aggregation risk).
2. A small, real conversion penalty encoded in the Android 5.14.0,
   Moscow, paid-search path after the release.

The analysis code never reads the constants that encode the scenario. They are
kept here only so the synthetic data can be regenerated exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

SEED = 20_260_812
RELEASE_DATE = pd.Timestamp("2026-07-20")
ANALYSIS_START = pd.Timestamp("2026-06-29")
ANALYSIS_END = pd.Timestamp("2026-08-09")
AS_OF_TIMESTAMP = pd.Timestamp("2026-08-11 12:00:00")


@dataclass(frozen=True)
class ScenarioConfig:
    """Fixed configuration that makes the synthetic setup inspectable."""

    seed: int = SEED
    release_date: pd.Timestamp = RELEASE_DATE
    analysis_start: pd.Timestamp = ANALYSIS_START
    analysis_end: pd.Timestamp = ANALYSIS_END
    as_of_timestamp: pd.Timestamp = AS_OF_TIMESTAMP
    sessions_per_day_scale: float = 1.0


def _pick(rng: np.random.Generator, values: list[str], probabilities: list[float], size: int) -> np.ndarray:
    return rng.choice(values, p=np.asarray(probabilities, dtype=float), size=size)


def _quality_for_channel(
    rng: np.random.Generator, channels: np.ndarray, is_post: bool
) -> np.ndarray:
    quality = np.empty(len(channels), dtype=object)
    weights = {
        "organic": (["high", "standard", "low"], [0.40, 0.45, 0.15]),
        "paid_search": (["high", "standard", "low"], [0.25, 0.50, 0.25 if not is_post else 0.33]),
        "paid_social": (
            ["high", "standard", "low"],
            [0.08, 0.35, 0.57] if not is_post else [0.05, 0.23, 0.72],
        ),
        "referral": (["high", "standard", "low"], [0.35, 0.50, 0.15]),
    }
    # Paid-search changes its low-quality share post-release; adjust the other
    # two weights so each probability vector remains a valid distribution.
    if is_post:
        weights["paid_search"] = (["high", "standard", "low"], [0.23, 0.44, 0.33])
    for channel, (values, probabilities) in weights.items():
        mask = channels == channel
        quality[mask] = _pick(rng, values, probabilities, int(mask.sum()))
    return quality.astype(str)


def _versions(
    rng: np.random.Generator, platforms: np.ndarray, date: pd.Timestamp, is_post: bool
) -> np.ndarray:
    versions = np.empty(len(platforms), dtype=object)
    days_after_release = max((date - RELEASE_DATE).days, 0)
    android_new_share = min(0.50 + 0.024 * days_after_release, 0.96)
    ios_new_share = min(0.35 + 0.026 * days_after_release, 0.93)

    for platform in ("Android", "iOS", "Web"):
        mask = platforms == platform
        count = int(mask.sum())
        if platform == "Web":
            versions[mask] = "2026.28" if is_post else "2026.27"
        elif platform == "Android" and is_post:
            versions[mask] = _pick(
                rng,
                ["5.14.0", "5.13.2", "5.13.1"],
                [android_new_share, (1 - android_new_share) * 0.30, (1 - android_new_share) * 0.70],
                count,
            )
        elif platform == "iOS" and is_post:
            versions[mask] = _pick(
                rng,
                ["5.14.0", "5.13.2", "5.13.1"],
                [ios_new_share, (1 - ios_new_share) * 0.30, (1 - ios_new_share) * 0.70],
                count,
            )
        elif platform == "Android":
            versions[mask] = _pick(rng, ["5.13.2", "5.13.1"], [0.30, 0.70], count)
        else:
            versions[mask] = _pick(rng, ["5.13.2", "5.13.1"], [0.45, 0.55], count)
    return versions.astype(str)


def _booking_probability(
    platforms: np.ndarray,
    cities: np.ndarray,
    channels: np.ndarray,
    qualities: np.ndarray,
    versions: np.ndarray,
    is_post: bool,
) -> np.ndarray:
    quality_effect = {"high": 0.245, "standard": 0.140, "low": 0.037}
    platform_effect = {"iOS": 0.015, "Android": 0.005, "Web": -0.015}
    city_effect = {"Moscow": 0.005, "Saint Petersburg": 0.000, "Kazan": -0.010}
    channel_effect = {"organic": 0.012, "paid_search": 0.004, "paid_social": -0.012, "referral": 0.008}

    probability = np.asarray([quality_effect[item] for item in qualities], dtype=float)
    probability += np.asarray([platform_effect[item] for item in platforms], dtype=float)
    probability += np.asarray([city_effect[item] for item in cities], dtype=float)
    probability += np.asarray([channel_effect[item] for item in channels], dtype=float)

    # Small relevance gain applies everywhere after the release except the
    # affected path. It makes an aggregate-only conclusion especially unsafe.
    if is_post:
        probability += 0.003
        affected_path = (
            (platforms == "Android")
            & (cities == "Moscow")
            & (channels == "paid_search")
            & (versions == "5.14.0")
        )
        probability[affected_path] -= 0.065
    return np.clip(probability, 0.005, 0.55)


def generate_synthetic_data(output_dir: Path, config: ScenarioConfig | None = None) -> dict[str, Path]:
    """Generate deterministic raw events, session attributes, and release metadata."""
    config = config or ScenarioConfig()
    if not 0 < config.sessions_per_day_scale <= 1:
        raise ValueError("sessions_per_day_scale must be in (0, 1].")

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(config.seed)
    sessions: list[pd.DataFrame] = []
    next_session = 1

    for date in pd.date_range(config.analysis_start, config.analysis_end, freq="D"):
        is_post = date >= config.release_date
        weekday_factor = 0.91 if date.dayofweek >= 5 else 1.0
        base_sessions = 2_450 if not is_post else 2_850
        seasonality = 1 + 0.035 * np.sin((date - config.analysis_start).days / 3.0)
        count = max(100, int(round(base_sessions * weekday_factor * seasonality * config.sessions_per_day_scale)))

        platforms = _pick(
            rng,
            ["iOS", "Android", "Web"],
            [0.45, 0.40, 0.15] if not is_post else [0.37, 0.48, 0.15],
            count,
        )
        cities = _pick(
            rng,
            ["Moscow", "Saint Petersburg", "Kazan"],
            [0.55, 0.25, 0.20] if not is_post else [0.57, 0.23, 0.20],
            count,
        )
        channels = _pick(
            rng,
            ["organic", "paid_search", "paid_social", "referral"],
            [0.50, 0.25, 0.15, 0.10] if not is_post else [0.33, 0.16, 0.43, 0.08],
            count,
        )
        qualities = _quality_for_channel(rng, channels, is_post)
        versions = _versions(rng, platforms, date, is_post)
        probability = _booking_probability(platforms, cities, channels, qualities, versions, is_post)
        booked = rng.random(count) < probability

        hour = rng.integers(8, 22, size=count)
        minute = rng.integers(0, 60, size=count)
        second = rng.integers(0, 60, size=count)
        event_timestamp = date + pd.to_timedelta(hour, unit="h") + pd.to_timedelta(minute, unit="m") + pd.to_timedelta(second, unit="s")
        session_id = np.arange(next_session, next_session + count)
        sessions.append(
            pd.DataFrame(
                {
                    "session_id": [f"s_{item:07d}" for item in session_id],
                    "user_id": [f"u_{item:07d}" for item in session_id],
                    "event_date": date.date().isoformat(),
                    "session_started_at": event_timestamp,
                    "city": cities,
                    "platform": platforms,
                    "app_version": versions,
                    "acquisition_channel": channels,
                    "session_quality": qualities,
                    "booked": booked.astype(int),
                }
            )
        )
        next_session += count

    session_attributes = pd.concat(sessions, ignore_index=True)
    search_events = session_attributes.loc[:, ["session_id", "user_id", "session_started_at"]].copy()
    search_events["event_id"] = [f"e_search_{item:07d}" for item in range(1, len(search_events) + 1)]
    search_events["event_name"] = "search_results_viewed"
    search_events["event_ts"] = search_events.pop("session_started_at")
    search_events["ingested_at"] = search_events["event_ts"] + pd.to_timedelta(
        rng.integers(1, 18, size=len(search_events)), unit="h"
    )

    booked_sessions = session_attributes.loc[session_attributes["booked"].eq(1), ["session_id", "user_id", "session_started_at"]].copy()
    booked_sessions["event_id"] = [f"e_booking_{item:07d}" for item in range(1, len(booked_sessions) + 1)]
    booked_sessions["event_name"] = "booking_created"
    booked_sessions["event_ts"] = booked_sessions.pop("session_started_at") + pd.to_timedelta(
        rng.integers(5, 91, size=len(booked_sessions)), unit="m"
    )
    booked_sessions["ingested_at"] = booked_sessions["event_ts"] + pd.to_timedelta(
        rng.integers(1, 18, size=len(booked_sessions)), unit="h"
    )
    events = pd.concat([search_events, booked_sessions], ignore_index=True).sort_values(
        ["event_ts", "event_id"], ignore_index=True
    )

    release_calendar = pd.DataFrame(
        [
            {
                "release_id": "rel_5_14_android",
                "platform": "Android",
                "app_version": "5.14.0",
                "released_at": "2026-07-20T07:00:00",
                "rollout_start": "2026-07-20T09:00:00",
                "release_note": "Synthetic search-result card update",
            },
            {
                "release_id": "rel_5_14_ios",
                "platform": "iOS",
                "app_version": "5.14.0",
                "released_at": "2026-07-20T07:00:00",
                "rollout_start": "2026-07-20T09:00:00",
                "release_note": "Synthetic search-result card update",
            },
            {
                "release_id": "rel_2026_28_web",
                "platform": "Web",
                "app_version": "2026.28",
                "released_at": "2026-07-20T07:00:00",
                "rollout_start": "2026-07-20T09:00:00",
                "release_note": "Synthetic web parity release",
            },
        ]
    )

    paths = {
        "events": output_dir / "events.csv",
        "sessions": output_dir / "session_attributes.csv",
        "releases": output_dir / "release_calendar.csv",
    }
    events.to_csv(paths["events"], index=False)
    session_attributes.drop(columns=["booked"]).to_csv(paths["sessions"], index=False)
    release_calendar.to_csv(paths["releases"], index=False)
    return paths
