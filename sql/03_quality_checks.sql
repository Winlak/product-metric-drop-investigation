-- Compact SQL checks used alongside pandas checks; values should be zero unless stated otherwise.
WITH event_counts AS (
    SELECT
        COUNT(*) AS events,
        COUNT(DISTINCT event_id) AS distinct_event_ids,
        COUNT(DISTINCT session_id) AS sessions_with_events
    FROM read_csv_auto('{{EVENTS_PATH}}')
),
search_counts AS (
    SELECT
        session_id,
        COUNT(*) FILTER (WHERE event_name = 'search_results_viewed') AS search_events,
        COUNT(*) FILTER (WHERE event_name = 'booking_created') AS booking_events
    FROM read_csv_auto('{{EVENTS_PATH}}')
    GROUP BY session_id
)
SELECT
    event_counts.events - event_counts.distinct_event_ids AS duplicate_event_ids,
    COUNT(*) FILTER (WHERE search_events <> 1) AS sessions_without_exactly_one_search,
    COUNT(*) FILTER (WHERE booking_events > 1) AS sessions_with_multiple_bookings,
    event_counts.sessions_with_events AS sessions_with_events
FROM event_counts
CROSS JOIN search_counts
GROUP BY event_counts.events, event_counts.distinct_event_ids, event_counts.sessions_with_events;

