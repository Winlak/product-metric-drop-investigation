-- Oaxaca-style rate decomposition on mutually exclusive channel × quality cells.
-- Δ conversion = mix effect + within-cell rate effect; all effects are in rate units.
CREATE OR REPLACE TABLE contribution_decomposition AS
WITH segment_period AS (
    SELECT
        period,
        acquisition_channel,
        session_quality,
        COUNT(*) AS search_sessions,
        SUM(has_booking) AS bookings,
        SUM(has_booking)::DOUBLE / COUNT(*) AS conversion
    FROM funnel_session_mart
    GROUP BY period, acquisition_channel, session_quality
),
with_period_totals AS (
    SELECT
        *,
        SUM(search_sessions) OVER (PARTITION BY period) AS total_search_sessions,
        SUM(bookings) OVER (PARTITION BY period) AS total_bookings
    FROM segment_period
),
paired AS (
    SELECT
        before.acquisition_channel,
        before.session_quality,
        before.search_sessions AS before_sessions,
        after.search_sessions AS after_sessions,
        before.bookings AS before_bookings,
        after.bookings AS after_bookings,
        before.conversion AS before_conversion,
        after.conversion AS after_conversion,
        before.search_sessions::DOUBLE / before.total_search_sessions AS before_share,
        after.search_sessions::DOUBLE / after.total_search_sessions AS after_share
    FROM with_period_totals AS before
    INNER JOIN with_period_totals AS after
        ON before.acquisition_channel = after.acquisition_channel
       AND before.session_quality = after.session_quality
    WHERE before.period = 'before'
      AND after.period = 'after'
)
SELECT
    *,
    (after_share - before_share) * before_conversion AS mix_effect,
    after_share * (after_conversion - before_conversion) AS rate_effect,
    (after_share * after_conversion) - (before_share * before_conversion) AS total_effect,
    RANK() OVER (
        ORDER BY ABS((after_share - before_share) * before_conversion) DESC
    ) AS mix_impact_rank
FROM paired;

