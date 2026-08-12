-- Session-grain funnel mart. Parameters are replaced by scripts/run_analysis.py.
-- A raw-event rollup prevents an event-level denominator from inflating conversion.
CREATE OR REPLACE TABLE funnel_session_mart AS
WITH event_rollup AS (
    SELECT
        session_id,
        MAX(CASE WHEN event_name = 'search_results_viewed' THEN 1 ELSE 0 END) AS has_search,
        MAX(CASE WHEN event_name = 'booking_created' THEN 1 ELSE 0 END) AS has_booking,
        MIN(CASE WHEN event_name = 'search_results_viewed' THEN event_ts END) AS search_ts,
        MIN(CASE WHEN event_name = 'booking_created' THEN event_ts END) AS booking_ts
    FROM read_csv_auto('{{EVENTS_PATH}}')
    WHERE event_name IN ('search_results_viewed', 'booking_created')
    GROUP BY session_id
),
session_with_dimensions AS (
    SELECT
        s.session_id,
        s.user_id,
        CAST(s.event_date AS DATE) AS event_date,
        s.city,
        s.platform,
        s.app_version,
        s.acquisition_channel,
        s.session_quality,
        e.has_search,
        e.has_booking,
        e.search_ts,
        e.booking_ts,
        r.release_id,
        r.rollout_start
    FROM read_csv_auto('{{SESSIONS_PATH}}') AS s
    LEFT JOIN event_rollup AS e USING (session_id)
    LEFT JOIN read_csv_auto('{{RELEASES_PATH}}') AS r
        ON s.platform = r.platform
       AND s.app_version = r.app_version
),
periodized AS (
    SELECT
        *,
        CASE WHEN event_date < DATE '2026-07-20' THEN 'before' ELSE 'after' END AS period,
        DATE_DIFF('day', DATE '2026-07-20', event_date) AS days_from_release
    FROM session_with_dimensions
    WHERE has_search = 1
)
SELECT
    *,
    ROW_NUMBER() OVER (PARTITION BY event_date, platform ORDER BY search_ts, session_id) AS session_sequence
FROM periodized;

